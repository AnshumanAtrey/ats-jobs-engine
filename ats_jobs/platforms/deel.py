"""
Deel ATS career pages (jobs.deel.com/<slug>), read through the public guest API their own pages are built from. No key.

- GET <API>/<slug>/career_page_settings: the company's name and the ids of its organization and board. A slug keeps its
  case (Range works, range does not), and 404 is no such page. It also answers for Deel itself, whose page redirects to
  www.deel.com/careers.
- GET <API>/<org>/job_boards/<board>/job_postings: every open job of the board in one answer, each with its full HTML
  description, pay, places, department and team. No paging, no search and no order: the page filters in the browser,
  so the engine filters here, and a description costs no extra call.
- GET .../job_postings/<id>: one job. A closed job answers 404 "Job posting is not published", one that never existed
  404 "Job posting not found".

postedAt is the time a posting entered its published state, which is what the job page's JobPosting markup calls
datePosted (10 of 10 pages, 2026-10-30). createdAt runs days or months earlier, while the posting is a draft. Only the
list carries the published state, so a link check reads the list too.

Pay is an amount and a currency with no period, and hourly, monthly and yearly amounts are all in use (17 to 18 USD,
5,500 to 6,500 PLN, 160,000 to 210,000 USD), so no period is stated. Locations are what each employer typed: a city, a
country, "Remoto (Latam)", usually with a country beside it, up to 69 on one job.

Boards have no index: search engines, the Wayback Machine and the companies' names find them.
"""
import re
from urllib.parse import urlparse

from ..http import BoardError, send
from ..inputs import JOB_TYPE_ALIASES
from ..rows import job_places
from .base import Platform
from .cornerstone import text_of

API = 'https://api-prod.letsdeel.com/guest/ats/organizations'
SETTINGS = API + '/{slug}/career_page_settings'
POSTINGS = API + '/{org}/job_boards/{board}/job_postings'
JOB_LINK = 'https://jobs.deel.com/{slug}/job-details/{job}/overview'
APPLY_LINK = 'https://jobs.deel.com/{slug}/job-details/{job}/application'
HOST = 'jobs.deel.com'
MAIN_SITE = ('deel.com', 'www.deel.com')
OLD_PATH = 'job-boards'
SLUG = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
COPY = re.compile(r'\s*\(copy\)\s*$', re.I)
WORK_TYPE = {'REMOTE': 'remote', 'HYBRID': 'hybrid', 'ON_SITE': 'onsite'}


def place(entry: dict) -> dict | None:
    """An employer's location -> one place. The country is added to the text unless the text already names it."""
    name = ' '.join((entry.get('name') or '').split())
    country = entry.get('country') or {}
    full = country.get('name')
    if not name:
        return {'name': full, 'country': full} if full else None
    named = country.get('code') in job_places({'places': [{'name': name}]})[1]
    return {'name': name if named or not full else f'{name}, {full}', 'country': full}


def names(links: list | None, key: str) -> str | None:
    """[{'department': {'name': 'Sales'}}] -> 'Sales'."""
    return ', '.join(n for n in ((link.get(key) or {}).get('name') for link in links or []) if n) or None


def job_type(links: list | None) -> str | None:
    for link in links or []:
        kind = JOB_TYPE_ALIASES.get(' '.join(((link.get('employmentType') or {}).get('name') or '').lower().split()))
        if kind:
            return kind
    return None


def pay_text(low: float | None, high: float | None, currency: str | None) -> str | None:
    """160000, 210000, 'USD' -> 'USD 160,000 - 210,000'. 22.35 keeps its cents; a job can give only one amount."""
    amounts = [f'{value:,.2f}'.removesuffix('.00') for value in (low, high) if value is not None]
    return f'{currency} {" - ".join(amounts)}'.strip() if amounts else None


def published(record: dict) -> str | None:
    state = ((record.get('jobPostingPublications') or [{}])[0].get('currentState') or {})
    return state.get('createdAt') or record.get('createdAt')


def answer(response) -> dict | list:
    if response.status_code != 200:
        raise BoardError(f'HTTP {response.status_code}')
    try:
        return response.json()
    except ValueError as exc:
        raise BoardError('the answer was not JSON') from exc


class Deel(Platform):
    key = 'deel'
    name = 'Deel'
    site_word = 'career page'
    actor = 'deel-jobs-search'
    board_example = 'https://jobs.deel.com/klarna'
    job_example = 'https://jobs.deel.com/klarna/job-details/87bd8299-48ef-4c79-9005-0347ecf8b054/overview'
    concurrency = 32
    never = ('expiresAt', 'companyWebsite', 'reference', 'salaryPeriod', 'offersEquity', 'city', 'region')

    def read_link(self, text):
        link = text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}'
        parsed = urlparse(link)
        host = (parsed.hostname or '').lower() if ' ' not in text else ''
        if host in MAIN_SITE and parsed.path.rstrip('/') in ('', '/careers'):
            return 'deel', None
        if host != HOST:
            return None
        parts = [p for p in parsed.path.split('/') if p]
        parts = parts[1:] if parts[:1] == [OLD_PATH] else parts
        if not parts:
            return None, None
        job = parts[2].lower() if len(parts) > 2 and parts[1] == 'job-details' and UUID.match(parts[2]) else None
        return parts[0], job

    def guess_board(self, name):
        return re.sub(r'[^A-Za-z0-9._-]+', '-', name).strip('-.') or None

    async def open(self, http, board):
        if not SLUG.match(board.key):
            return None
        for slug in dict.fromkeys((board.key, board.key.lower())):
            response = await send(http, 'GET', SETTINGS.format(slug=slug))
            if response.status_code == 404:
                continue
            settings = answer(response)
            org, found = settings.get('organizationId'), (settings.get('jobBoard') or {}).get('id')
            if not org or not found:
                return None
            name = COPY.sub('', settings.get('preferredOrganizationName') or '').strip()
            return {'slug': settings.get('urlSlug') or slug, 'org': org, 'board': found, 'name': name or None}
        return None

    def job(self, session: dict, record: dict) -> dict:
        data = record.get('job') or {}
        places = [p for p in (place(link.get('location') or {}) for link in data.get('jobLocations') or []) if p]
        pay = (data.get('currentCompensation') or {}) if record.get('isCompensationVisible') else {}
        low, high, currency = pay.get('minAmount'), pay.get('maxAmount'), pay.get('currencyIsoCode')
        html = record.get('richtextDescription') or None
        return {
            'id': record.get('id'),
            'company': session['name'],
            'title': ' '.join((record.get('title') or '').split()),
            'location': places[0]['name'] if places else None,
            'otherLocations': [p['name'] for p in places[1:]],
            'places': places,
            'workType': WORK_TYPE.get(data.get('workArrangementEnum')),
            'employmentType': job_type(data.get('jobEmploymentTypes')),
            'department': names(data.get('jobDepartments'), 'department'),
            'team': names(data.get('jobTeams'), 'team'),
            'salary': pay_text(low, high, currency),
            'salaryMin': low,
            'salaryMax': high,
            'salaryCurrency': currency,
            'postedAt': published(record),
            'jobUrl': JOB_LINK.format(slug=session['slug'], job=record.get('id')),
            'applyUrl': APPLY_LINK.format(slug=session['slug'], job=record.get('id')),
            'description': text_of(html),
            'descriptionHtml': html,
        }

    async def _postings(self, http, session: dict) -> list[dict]:
        records = answer(await send(http, 'GET', POSTINGS.format(org=session['org'], board=session['board'])))
        if not isinstance(records, list):
            raise BoardError('the job list was not a list')
        return records

    async def pages(self, http, session, term, days):
        yield [self.job(session, record) for record in await self._postings(http, session)]

    async def lookup(self, http, session, job_id):
        if 'listed' not in session:
            session['listed'] = {record.get('id'): record for record in await self._postings(http, session)}
        record = session['listed'].get(job_id)
        if record is None:
            url = f'{POSTINGS.format(org=session["org"], board=session["board"])}/{job_id}'
            response = await send(http, 'GET', url)
            if response.status_code == 404:
                return ('closed' if 'not published' in response.text else 'not found'), None
            record = answer(response)
        return 'open', self.job(session, record)
