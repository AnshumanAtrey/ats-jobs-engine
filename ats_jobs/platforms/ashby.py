"""
Ashby job boards (jobs.ashbyhq.com), read live through Ashby's two public sources (ported from the ashby-jobs-search
actor, 2026-11-13):

- The board page, https://jobs.ashbyhq.com/<board>: `window.__appData` holds the company (name, website, demo flag)
  and a short list of every open job (title, location, work type, job type), about 20 KB a board, with no dates. It
  is open(); a board whose short list has no job that can match (rows.Filters.maybe, through Platform.prefilter) ends
  there.
- The public job posting API, https://api.ashbyhq.com/posting-api/job-board/<board>?includeCompensation=true, the one
  Ashby documents for companies' own career pages: every listed job with its posting date, addresses, pay and
  description, about 13 KB a job, so it is read only for boards whose short list can match. It is pages(): one page.

A board name nobody uses answers 200 with `organization: null`; the API answers 404. A demo board (isDemoOrg) is no
company's: it is skipped as missing. Board names are read in any case, spaces included ("Blackpoint Cyber").
"""
import json
import re
from urllib.parse import quote, unquote, urlparse

from ..http import BoardError, BoardMissing, send
from .base import Platform

BOARD_URL = 'https://jobs.ashbyhq.com/{}'
POSTINGS_URL = 'https://api.ashbyhq.com/posting-api/job-board/{}'
HOST = 'jobs.ashbyhq.com'
APP_DATA = 'window.__appData = '
UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
SLUG = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._~ &-]{0,99}$')
WORK_TYPE = {'Remote': 'remote', 'Hybrid': 'hybrid', 'OnSite': 'onsite'}
JOB_TYPE = {'FullTime': 'full-time', 'PartTime': 'part-time', 'Contract': 'contract', 'Intern': 'intern',
            'Temporary': 'temporary'}
PERIOD = {'1 YEAR': 'year', '1 MONTH': 'month', '1 WEEK': 'week', '1 DAY': 'day', '1 HOUR': 'hour'}


def clean(text) -> str:
    return ' '.join(text.split()) if isinstance(text, str) else ''


def parse_board(html: str) -> dict | None:
    """Board page HTML -> {'org': {...}, 'postings': [...]}, or None when no company uses the name."""
    start = html.find(APP_DATA)
    if start < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(html, start + len(APP_DATA))
    except ValueError:
        return None
    org = data.get('organization') if isinstance(data, dict) else None
    if not isinstance(org, dict):
        return None
    board = data.get('jobBoard') or {}
    website = clean(org.get('publicWebsite')) or None
    if website and '://' not in website:
        website = f'https://{website}'          # Ashby keeps what the company typed: joinhomebase.com
    return {
        'org': {'name': clean(org.get('name')) or None, 'slug': org.get('hostedJobsPageSlug'), 'website': website,
                'demo': bool(org.get('isDemoOrg'))},
        'postings': [p for p in board.get('jobPostings') or [] if isinstance(p, dict)],
    }


def work_type(record: dict) -> str | None:
    kind = WORK_TYPE.get(record.get('workplaceType') or '')
    if kind:
        return kind
    return 'remote' if record.get('isRemote') is True else None


def short_job(posting: dict) -> dict:
    """A board page's short list entry in the engine's job shape, for rows.Filters.maybe."""
    return {'title': clean(posting.get('title')), 'location': clean(posting.get('locationName')) or None,
            'workType': work_type(posting), 'employmentType': JOB_TYPE.get(posting.get('employmentType') or '')}


def _address(entry: dict | None) -> dict:
    return ((entry or {}).get('address') or {}).get('postalAddress') or {}


def places(record: dict) -> list[dict]:
    """The primary location, then the secondary ones, each with the parts of its postal address."""
    found = []
    for entry in [record, *(record.get('secondaryLocations') or [])]:
        if not isinstance(entry, dict):
            continue
        address = _address(entry)
        name = clean(entry.get('location') or entry.get('locationName'))
        city, region, country = (clean(address.get(k)) or None for k in ('addressLocality', 'addressRegion',
                                                                             'addressCountry'))
        if name or city or region or country:
            found.append({'name': name or ', '.join(p for p in (city, region, country) if p), 'city': city,
                          'region': region, 'country': country})
    return found


def pay(record: dict) -> dict:
    """The pay Ashby publishes: its summary text, and the salary component's numbers when there is one."""
    comp = record.get('compensation') if isinstance(record.get('compensation'), dict) else {}
    components = list(comp.get('summaryComponents') or [])
    for tier in comp.get('compensationTiers') or []:
        components += tier.get('components') or [] if isinstance(tier, dict) else []
    salary = next((c for c in components if isinstance(c, dict) and c.get('compensationType') == 'Salary'), None) or {}
    summary = comp.get('compensationTierSummary') or comp.get('scrapeableCompensationSalarySummary')
    return {
        'salary': clean(summary) or None,
        'salaryMin': salary.get('minValue'),
        'salaryMax': salary.get('maxValue'),
        'salaryCurrency': salary.get('currencyCode'),
        'salaryPeriod': PERIOD.get(salary.get('interval') or ''),
        'offersEquity': (any(str(c.get('compensationType') or '').startswith('Equity') for c in components
                             if isinstance(c, dict)) if components else None),
    }


class Ashby(Platform):
    key = 'ashby'
    name = 'Ashby'
    site_word = 'job board'
    actor = 'ashby-jobs-search'
    board_example = 'https://jobs.ashbyhq.com/ramp'
    job_example = 'https://jobs.ashbyhq.com/ramp/34413f8d-26bf-4bbc-8ade-eb309a0e2245'
    concurrency = 32             # board pages; 24 at once read 7,566 boards with no 429 (ashby-jobs-search, 2026-09-30)

    def read_link(self, text):
        if HOST not in text.lower():
            return None
        link = text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}'
        parsed = urlparse(link)
        if (parsed.hostname or '').lower() != HOST:
            return None
        parts = [unquote(p) for p in parsed.path.split('/') if p]
        if not parts or not SLUG.match(parts[0]):
            return None, None
        return parts[0], parts[1].lower() if len(parts) >= 2 and UUID.match(parts[1]) else None

    def guess_board(self, name):
        return name if SLUG.match(name) else None   # Ashby reads board names in any case

    async def open(self, http, board):
        response = await send(http, 'GET', BOARD_URL.format(quote(board.key, safe='')))
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise BoardError(f'HTTP {response.status_code}')
        found = parse_board(response.text)
        if found is None or found['org']['demo']:
            return None
        org = found['org']
        return {'slug': org['slug'] or board.key, 'name': org['name'] or board.name, 'website': org['website'],
                'postings': found['postings']}

    def job(self, session: dict, record: dict) -> dict:
        job_id = str(record.get('id'))
        url = record.get('jobUrl') or f'{BOARD_URL.format(quote(session["slug"], safe=""))}/{job_id}'
        found = places(record)
        return {
            'id': job_id,
            'title': clean(record.get('title')),
            'company': session['name'],
            'companySlug': session['slug'],
            'companyWebsite': session['website'],
            'location': found[0]['name'] if found else None,
            'otherLocations': [p['name'] for p in found[1:]],
            'places': found,
            'workType': work_type(record),
            'employmentType': JOB_TYPE.get(record.get('employmentType') or ''),
            'department': clean(record.get('department')) or None,
            'team': clean(record.get('team')) or None,
            'postedAt': record.get('publishedAt'),
            'jobUrl': url,
            'applyUrl': record.get('applyUrl') or f'{url}/application',
            'description': record.get('descriptionPlain'),
            'descriptionHtml': record.get('descriptionHtml'),
            **pay(record),
        }

    async def postings(self, http, slug: str) -> list[dict]:
        response = await send(http, 'GET', POSTINGS_URL.format(quote(slug, safe='')),
                              params={'includeCompensation': 'true'})
        if response.status_code == 404:
            raise BoardMissing(slug)
        if response.status_code != 200:
            raise BoardError(f'HTTP {response.status_code}')
        try:
            records = response.json().get('jobs')
        except (ValueError, AttributeError) as exc:
            raise BoardError('the job list was not JSON') from exc
        return [r for r in records or [] if isinstance(r, dict) and r.get('id') and r.get('isListed') is not False]

    async def pages(self, http, session, term, days):
        check = self.prefilter
        if check is not None and not any(check.maybe(short_job(p)) for p in session['postings']):
            return                               # no job on the short list can match: the costly list is not read
        yield [self.job(session, r) for r in await self.postings(http, session['slug'])]

    async def lookup(self, http, session, job_id):
        for record in await self.postings(http, session['slug']):
            if str(record.get('id')).lower() == job_id.lower():
                return 'open', self.job(session, record)
        return 'closed', None
