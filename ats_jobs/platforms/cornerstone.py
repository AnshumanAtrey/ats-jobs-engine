"""
Cornerstone OnDemand (csod.com) career sites, read the way their own page reads them.

The page https://<corp>.csod.com/ux/ats/careersite/<site>/home?c=<corp> embeds `csod.context`: a two-hour
anonymous token and the regional API host (us, uk, eu-fra, eu-cdg, au...). With them:

- POST <cloud>rec-job-search/external/jobs: the jobs of one career site, newest first, up to 500 a page, with
  searchText (full text, title and description) and postingsWithinDays. Dates come in the language asked for,
  and the site's own language is no guide (a ca-ES site answers M/D/YYYY), so every call asks for en-US.
- GET <corp>.csod.com/services/x/job-requisition/v2/requisitions/<id>/jobDetails: the full HTML description,
  address, exact opening time and apply link. requisitionStatusId 2 is open; any other is closed; 404 is no job.

Career sites are noindex with no JobPosting markup, so a site: search finds little of them.
"""
import html as htmllib
import json
import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from ..http import BoardError, send
from .base import Board, Platform

HOME = 'https://{corp}.csod.com/ux/ats/careersite/{site}/home?c={corp}'
JOB_LINK = 'https://{corp}.csod.com/ux/ats/careersite/{site}/home/requisition/{job}?c={corp}'
SEARCH = 'rec-job-search/external/jobs'
DETAILS = 'https://{corp}.csod.com/services/x/job-requisition/v2/requisitions/{job}/jobDetails'
CONTEXT = re.compile(r'csod\.context=(\{.*?\});</script>', re.S)
HOST = re.compile(r'^([a-z0-9-]+)\.csod\.com$', re.I)
PATH = re.compile(r'/careersite/(\d+)(?:/home(?:/requisition/(\d+))?)?', re.I)
CLASSIC_JOB = re.compile(r'jobdetails\.aspx', re.I)
SLUG = re.compile(r'^[a-z0-9][a-z0-9-]*$', re.I)
NO_SUCH_HOST = re.compile(r'name or service not known|nodename nor servname|name does not resolve|no address associated', re.I)
PAGE_SIZE = 100
EN_US = {'cultureId': 1, 'cultureName': 'en-US'}
OPEN = 2


def text_of(html: str | None) -> str | None:
    """HTML -> the plain text a reader sees, one line per block."""
    if not html:
        return None
    text = re.sub(r'<\s*(?:br|/p|/div|/li|/h[1-6]|/tr)\s*/?\s*>', '\n', html, flags=re.I)
    text = htmllib.unescape(re.sub(r'<[^>]+>', ' ', text))
    lines = (' '.join(line.split()) for line in text.splitlines())
    return '\n'.join(line for line in lines if line) or None


def us_date(value: str | None) -> str | None:
    """'10/29/2026' -> '2026-10-29'; '-' and empty -> None."""
    try:
        return datetime.strptime((value or '').strip(), '%m/%d/%Y').date().isoformat()
    except ValueError:
        return None


def place(entry: dict) -> dict | None:
    city, region, country = (entry.get(k) for k in ('city', 'state', 'country'))
    name = ', '.join(p for p in (city, region, country) if p)
    return {'name': name, 'city': city, 'region': region, 'country': country} if name else None


class Cornerstone(Platform):
    key = 'cornerstone'
    name = 'Cornerstone'
    site_word = 'career site'
    actor = 'cornerstone-jobs-search'
    board_example = 'https://newcold.csod.com/ux/ats/careersite/5/home?c=newcold'
    job_example = 'https://newcold.csod.com/ux/ats/careersite/5/home/requisition/3110?c=newcold'
    concurrency = 64
    server_search = True
    newest_first = True
    never = ('employmentType', 'department', 'team', 'salary', 'salaryMin', 'salaryMax', 'salaryCurrency',
             'salaryPeriod', 'offersEquity', 'companyWebsite')

    def aliases(self, board: Board) -> list[str]:
        return [board.key.split('/')[0]]

    def read_link(self, text):
        link = text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}'
        parsed = urlparse(link)
        host = HOST.match(parsed.hostname or '')
        if not host:
            return None
        corp = host.group(1).lower()
        query = parse_qs(parsed.query)
        if CLASSIC_JOB.search(parsed.path) and query.get('site') and query.get('id'):
            return f'{corp}/{query["site"][0]}', query['id'][0]
        path = PATH.search(parsed.path)
        if path:
            return f'{corp}/{path.group(1)}', path.group(2)
        return corp, None

    def guess_board(self, name):
        return f'{name.lower()}/1' if SLUG.match(name) else None

    async def open(self, http, board):
        corp, _, site = board.key.partition('/')
        try:
            response = await send(http, 'GET', HOME.format(corp=corp, site=site or 1))
        except BoardError as exc:
            if NO_SUCH_HOST.search(str(exc)):       # every company has its own subdomain: no address, no company
                return None
            raise
        found = CONTEXT.search(response.text) if response.status_code == 200 else None
        if not found:
            return None
        context = json.loads(found.group(1))
        return {'corp': corp, 'site': int(site or 1), 'cloud': context['endpoints']['cloud'],
                'headers': {'Authorization': f'Bearer {context["token"]}'}}

    @staticmethod
    def is_template(record: dict) -> bool:
        """A posting nobody filled in: its title is still the template's <<ENTER DISPLAY JOB TITLE>>."""
        return (record.get('displayJobTitle') or '').lstrip().startswith(('<<', '&lt;&lt;'))

    def job(self, session: dict, record: dict) -> dict:
        job_id = str(record.get('requisitionId'))
        places = [p for p in (place(e) for e in record.get('locations') or []) if p]
        # a site often lists the city and then the bare country of the same job
        cities = {p['country'] for p in places if p['city']}
        places = [p for p in places if p['city'] or p['country'] not in cities]
        url = JOB_LINK.format(corp=session['corp'], site=session['site'], job=job_id)
        return {
            'id': job_id,
            'title': htmllib.unescape(record.get('displayJobTitle') or '').strip(),
            'location': places[0]['name'] if places else None,
            'otherLocations': [p['name'] for p in places[1:]],
            'places': places,
            'postedAt': us_date(record.get('postingEffectiveDate')),
            'expiresAt': us_date(record.get('postingExpirationDate')),
            'dateOnly': True,
            'jobUrl': url,
            'applyUrl': url,
            'description': text_of(record.get('externalDescription')),
        }

    async def pages(self, http, session, term, days):
        body = {'careerSiteId': session['site'], 'careerSitePageId': session['site'], 'pageSize': PAGE_SIZE,
                'searchText': term or '', 'postingsWithinDays': days, 'states': [], 'countryCodes': [], 'cities': [],
                'placeID': '', 'radius': None, 'customFieldCheckboxKeys': [], 'customFieldDropdowns': [],
                'customFieldRadios': [], **EN_US}
        page = 1
        while True:
            response = await send(http, 'POST', session['cloud'] + SEARCH, headers=session['headers'],
                                  json={**body, 'pageNumber': page})
            if response.status_code != 200:
                raise BoardError(f'HTTP {response.status_code}')
            try:
                data = response.json().get('data') or {}
            except ValueError as exc:
                raise BoardError('the job list was not JSON') from exc
            records = data.get('requisitions') or []
            yield [self.job(session, r) for r in records if r.get('requisitionId') and not self.is_template(r)]
            if not records or page * PAGE_SIZE >= (data.get('totalCount') or 0):
                return
            page += 1

    async def _detail(self, http, session, job_id: str) -> tuple[int, dict]:
        response = await send(http, 'GET', DETAILS.format(corp=session['corp'], job=job_id),
                              params={'cultureId': 1}, headers=session['headers'])
        if response.status_code != 200:
            return response.status_code, {}
        try:
            return 200, response.json().get('data') or {}
        except ValueError as exc:
            raise BoardError('the job details were not JSON') from exc

    def enrich(self, job: dict, data: dict) -> dict:
        html = data.get('externalDescription')
        first = place(data.get('primaryLocation') or {})
        if first:
            others = [p for p in job.get('places') or [] if p['name'] != first['name'] and p['city'] != first['city']]
            job.update(places=[first, *others], location=first['name'], otherLocations=[p['name'] for p in others])
        opened = (data.get('openDate') or '')[:19]
        if opened:
            job.update(postedAt=f'{opened}Z', dateOnly=False)
        return {**job, 'description': text_of(html) or job.get('description'), 'descriptionHtml': html or None,
                'reference': data.get('ref'), 'applyUrl': data.get('companyApplyUrl') or job.get('jobUrl')}

    async def details(self, http, session, job):
        status, data = await self._detail(http, session, job['id'])
        return self.enrich(job, data) if status == 200 else job

    async def lookup(self, http, session, job_id):
        status, data = await self._detail(http, session, job_id)
        if status == 404:
            return 'not found', None
        if status != 200:
            raise BoardError(f'HTTP {status}')
        if int(data.get('requisitionStatusId') or 0) != OPEN:
            return 'closed', None
        url = JOB_LINK.format(corp=session['corp'], site=session['site'], job=job_id)
        title = htmllib.unescape(data.get('displayTitle') or '').strip()
        return 'open', self.enrich({'id': job_id, 'title': title, 'jobUrl': url, 'applyUrl': url, 'places': []}, data)
