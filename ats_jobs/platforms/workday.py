"""
Workday (myworkdayjobs.com) career sites, read the way their own page reads them. No login and no token.

- POST https://<tenant>.<pod>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs with {appliedFacets, limit, offset,
  searchText}: 20 jobs a page, newest first, full-text search; total stops at 2,000. Dates are relative ("Posted 3 Days
  Ago", "Posted 30+ Days Ago"), and a job in several places says only "2 Locations".
- GET .../wday/cxs/<tenant>/<site>/job/<path>: the description, exact start date, time type, requisition, country and
  every location. A posting that is gone answers 404.

Sites sit on pods (wd1, wd5, wd103...) behind one certificate, so certificate logs do not list them; the Wayback
Machine does.
"""
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse

from ..http import BoardError, BoardMissing, send
from .base import Board, Platform
from .cornerstone import text_of

BASE = 'https://{tenant}.{pod}.myworkdayjobs.com'
API = BASE + '/wday/cxs/{tenant}/{site}'
PAGE_LINK = BASE + '/{site}'
HOST = re.compile(r'^([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com$', re.I)
LOCALE = re.compile(r'^[a-z]{2}-[a-z]{2}$', re.I)
AGO = re.compile(r'(\d+)\+?\s*days?\s*ago', re.I)
MANY_PLACES = re.compile(r'^\d+\s+locations?$', re.I)
PAGE_SIZE = 20
TIME_TYPE = {'full time': 'full-time', 'full-time': 'full-time', 'part time': 'part-time', 'part-time': 'part-time'}
REMOTE_TYPE = {'remote': 'remote', 'fully remote': 'remote', 'hybrid': 'hybrid', 'flexible': 'hybrid', 'on-site': 'onsite',
               'onsite': 'onsite', 'on site': 'onsite'}


def posted_on(text: str | None, today=None) -> str | None:
    """'Posted Today', 'Posted Yesterday', 'Posted 3 Days Ago' -> a date. 'Posted 30+ Days Ago' says only that it is old."""
    today = today or datetime.now(timezone.utc).date()
    value = (text or '').lower()
    if '+' in value:
        return None
    if 'today' in value:
        return today.isoformat()
    if 'yesterday' in value:
        return (today - timedelta(days=1)).isoformat()
    days = AGO.search(value)
    return (today - timedelta(days=int(days.group(1)))).isoformat() if days else None


class Workday(Platform):
    key = 'workday'
    name = 'Workday'
    site_word = 'career site'
    actor = 'workday-job-search'
    board_example = 'https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite'
    job_example = 'https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Senior-Engineer_JR1'
    concurrency = 64             # 128 kept the same 9 to 10 calls a second, doubled the wait and added connection errors
    server_search = True
    newest_first = True
    ranked_search = True
    page_cap = 5                 # the 100 newest answers a site has for one word
    never = ('department', 'team', 'salary', 'salaryMin', 'salaryMax', 'salaryCurrency', 'salaryPeriod', 'offersEquity',
             'companyWebsite')

    def aliases(self, board: Board) -> list[str]:
        return [board.key.split('/')[0]]

    def read_link(self, text):
        link = text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}'
        parsed = urlparse(link)
        host = HOST.match(parsed.hostname or '')
        if not host:
            return None
        parts = [p for p in parsed.path.split('/') if p]
        parts = parts[1:] if parts and LOCALE.match(parts[0]) else parts
        if not parts:
            return host.group(1).lower(), None
        board = f'{host.group(1).lower()}/{host.group(2).lower()}/{parts[0]}'
        return board, '/'.join(parts[2:]) if len(parts) > 2 and parts[1] == 'job' else None

    async def open(self, http, board):
        tenant, pod, site = board.key.split('/', 2)
        return {'tenant': tenant, 'pod': pod, 'site': site, 'api': API.format(tenant=tenant, pod=pod, site=site),
                'home': PAGE_LINK.format(tenant=tenant, pod=pod, site=site)}

    def job(self, session: dict, record: dict) -> dict:
        path = record.get('externalPath') or ''
        where = (record.get('locationsText') or '').strip()
        url = session['home'] + path
        return {
            'id': path.removeprefix('/job/'),
            'title': (record.get('title') or '').strip(),
            'location': where or None,
            'places': [{'name': where}] if where and not MANY_PLACES.match(where) else [],
            'postedAt': posted_on(record.get('postedOn')),
            'dateOnly': True,
            'jobUrl': url,
            'applyUrl': url,
            'reference': next(iter(record.get('bulletFields') or []), None),
        }

    def vague_place(self, job):
        return bool(MANY_PLACES.match(job.get('location') or ''))

    async def pages(self, http, session, term, days):
        offset = 0
        while True:
            response = await send(http, 'POST', f'{session["api"]}/jobs', json={
                'appliedFacets': {}, 'limit': PAGE_SIZE, 'offset': offset, 'searchText': term or ''})
            if response.status_code in (404, 422):
                raise BoardMissing(session['home'])
            if response.status_code != 200:
                raise BoardError(f'HTTP {response.status_code}')
            try:
                data = response.json()
            except ValueError as exc:
                raise BoardError('the job list was not JSON') from exc
            records = [r for r in data.get('jobPostings') or [] if r.get('externalPath')]
            yield [self.job(session, r) for r in records]
            offset += PAGE_SIZE
            if not records or offset >= (data.get('total') or 0):
                return

    def enrich(self, job: dict, info: dict) -> dict:
        country = (info.get('country') or {}).get('descriptor')
        names = [n for n in (info.get('location'), *(info.get('additionalLocations') or [])) if n]
        places = [{'name': n, 'country': country if i == 0 else None} for i, n in enumerate(names)]
        kind = REMOTE_TYPE.get((info.get('remoteType') or '').strip().lower())
        html = info.get('jobDescription')
        return {
            **job,
            'title': (info.get('title') or job.get('title') or '').strip(),
            'location': names[0] if names else job.get('location'),
            'otherLocations': names[1:],
            'places': places or job.get('places') or [],
            'postedAt': info.get('startDate') or job.get('postedAt'),
            'expiresAt': info.get('endDate'),
            'employmentType': TIME_TYPE.get((info.get('timeType') or '').strip().lower()),
            'workType': kind or job.get('workType'),
            'reference': info.get('jobReqId') or job.get('reference'),
            'applyUrl': info.get('externalUrl') or job.get('applyUrl'),
            'description': text_of(html),
            'descriptionHtml': html,
        }

    async def _detail(self, http, session, job_id: str) -> tuple[int, dict]:
        response = await send(http, 'GET', f'{session["api"]}/job/{quote(job_id, safe="/")}')
        if response.status_code != 200:
            return response.status_code, {}
        try:
            return 200, response.json().get('jobPostingInfo') or {}
        except ValueError as exc:
            raise BoardError('the job details were not JSON') from exc

    async def details(self, http, session, job):
        status, info = await self._detail(http, session, job['id'])
        return self.enrich(job, info) if status == 200 and info else job

    async def lookup(self, http, session, job_id):
        status, info = await self._detail(http, session, job_id)
        if status in (404, 410):
            return 'closed', None
        if status != 200:
            raise BoardError(f'HTTP {status}')
        if info.get('canApply') is False:
            return 'closed', None
        url = f'{session["home"]}/job/{job_id}'
        return 'open', self.enrich({'id': job_id, 'title': '', 'jobUrl': url, 'applyUrl': url, 'dateOnly': True}, info)
