"""
SmartRecruiters career sites (careers.smartrecruiters.com/<company>, jobs.smartrecruiters.com/<company>/<id>), read
through SmartRecruiters' public Posting API. No key. Measured 2026-11-02:

- GET https://api.smartrecruiters.com/v1/companies/<company>/postings?limit=100&offset=<n>: a company's open postings,
  newest first by releasedDate, 100 a page at most, also when searched. Filters: q, releasedAfter, country (a lower-case
  code: de finds 746 Bosch jobs, DE none), region, city, language, locationType. q reads titles and descriptions for
  any of its words, stemmed ("software engineer" is software or engineer, engineers is engineer); a quoted phrase finds
  the phrase; "&&" between three words finds nothing, so it is never sent. A company is read in any case, and one that
  does not exist answers 200 with no posting, like one that is not hiring; one that locked the API answers 401
  (CHECK24). The list has no pay and no description.
- GET .../postings/<id> (id, uuid or jobAdId): the job ad's four HTML sections, active, postingUrl, applyUrl, and
  compensation {min, max, currency, period} on 34 of 400 sampled jobs. A closed posting answers 200 with active false
  (ServiceNow's 2021 ids); one that never existed 404; text that is no id 400; another company's id 400.
- GET https://jobs.smartrecruiters.com/sr-jobs/company-name-lookup?q=<company>: the identifier and name of a company,
  "" when there is none: how a typed company is told apart from one with no open job. It knows only the companies in
  SmartRecruiters' job search ("" for AbbVie, 1,989 open jobs), so a company's postings are asked first.

postedAt is releasedDate: the job page's JobPosting markup gives the same instant as datePosted on 11 of 11 pages
(2024-12 to 2026-11). The page's validThrough is not in the API, so rows carry no expiry. A job page answers with any
slug or none; links built here use SmartRecruiters' slug rule (399 of 400 postingUrls). 128 parallel calls from one
laptop met no limit (192 a second, no 429). api.smartrecruiters.com's robots.txt allows /v1/companies/ to LinkedInBot
only; the Posting API is the one SmartRecruiters documents for career sites.

The job search on jobs.smartrecruiters.com (sr-jobs/search) spans companies but answers only the newest 100 of a query
(offset and limit are ignored), leaves out the 1,387 hiring sites that opted out (AbbVie, City of New York) and 446 of
the 4,909 newest postings of 80 companies it does hold (some almost all), with fewer fields: it cannot vouch that no
board holds anything newer, so every board is read, and it only helps to find companies (scripts/refresh_smartrecruiters.py).
"""
import html as htmllib
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from ..http import BoardError, BoardMissing, send
from ..inputs import JOB_TYPE_ALIASES
from ..rows import Term, country_code, index, words
from .base import Platform
from .cornerstone import text_of

try:
    import pycountry
except ImportError:                    # the actor installs it; without it a country keeps its code
    pycountry = None

API = 'https://api.smartrecruiters.com/v1/companies/{company}/postings'
NAME_LOOKUP = 'https://jobs.smartrecruiters.com/sr-jobs/company-name-lookup'
JOB_LINK = 'https://jobs.smartrecruiters.com/{company}/{job}'
MAIN_SITE = {'www.smartrecruiters.com', 'smartrecruiters.com'}     # old company and job links, and the vendor's site
HOSTS = {'careers.smartrecruiters.com', 'jobs.smartrecruiters.com', 'api.smartrecruiters.com', *MAIN_SITE}
APP_PATHS = {'oneclick-ui', 'external-referrals'}           # .../company/<company>/(publication|job)/<id>
COMPANY_FIRST = {'ni', 'my-applications'}                   # /ni/<company>/<uuid>-<slug>, /my-applications/<company>/...
NO_COMPANY = {'sr-jobs', 'cdn-cgi', 'static', '_incapsula_resource', 'robots.txt', 'favicon.ico', 'sitemap.xml'}
COMPANY = re.compile(r'^[A-Za-z0-9-]{1,100}$')
POSTING = re.compile(r'^(\d{6,})(?:-|$)')
UUID = re.compile(r'^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:-|$)', re.I)
ENTITY = re.compile(r'&(?:#\d+|#x[0-9a-f]+|[a-z]+);', re.I)
PAGE_SIZE = 100
# 400 is text that is no company or posting, 404 a posting that never existed, 401 a company that locked its Posting
# API (CHECK24, 1 of about 16,000 companies on 2026-11-02): none of them is SmartRecruiters failing to answer. No company
# answered 403; it is what a blocked address gets, so it stays an error and cannot pass for a company that is not there
NOT_READABLE = (400, 401, 404)
# A board's first page is short, because most boards only confirm that they hold nothing newer than the jobs already
# kept. With no word every board has jobs and its newest one decides (an empty form read 12,102 boards in 112 s with
# 1, 129 s with 10); a word search is slower per call and many boards hold a few matches (202 s with 10, 262 s with 1).
FIRST_PAGE, FIRST_PAGE_SEARCHED = 1, 10
SECTIONS = ('companyDescription', 'jobDescription', 'qualifications', 'additionalInformation')
JOB_TYPE = {'permanent': 'full-time', 'full-time': 'full-time', 'part-time': 'part-time', 'contract': 'contract',
            'intern': 'intern', 'temporary': 'temporary'}
PERIOD = {'YEARLY': 'year', 'MONTHLY': 'month', 'WEEKLY': 'week', 'DAILY': 'day', 'HOURLY': 'hour'}
PER = {'year': 'a year', 'month': 'a month', 'week': 'a week', 'day': 'a day', 'hour': 'an hour'}


def posting_id(part: str) -> str | None:
    """'744000159116379-pmi-project-lead-em' -> '744000159116379'; '<uuid>-process-workers' -> the uuid."""
    found = POSTING.match(part) or UUID.match(part)
    return found.group(1).lower() if found else None


def slug(title: str) -> str:
    """SmartRecruiters' own link slug: accents dropped, any other run of characters a dash ('Anlæg Øst' -> 'anl-g-st')."""
    plain = ''.join(c for c in unicodedata.normalize('NFKD', title) if not unicodedata.combining(c)).lower()
    return re.sub(r'[^a-z0-9]+', '-', plain)


def clean(text: str | None) -> str:
    text = ' '.join((text or '').split())
    return htmllib.unescape(text) if ENTITY.search(text) else text


def country_name(code: str | None, full: str | None) -> str | None:
    """'cn', 'Shanghai, Shanghai, China' -> 'China': SmartRecruiters' word when it names that country, else ISO's."""
    code = (code or '').strip().upper()
    if not code:
        return None
    last = (full or '').rsplit(',', 1)[-1].strip()
    if last and country_code(last, allow_codes=False) == code:
        return last
    found = pycountry.countries.get(alpha_2=code) if pycountry else None
    return (getattr(found, 'common_name', None) or found.name) if found else code


def place(location: dict) -> dict | None:
    """A posting's address -> one place: 'Singapore, , Singapore' reads 'Singapore', 'Shanghai, Shanghai, China'
    reads 'Shanghai, China'."""
    city, region = clean(location.get('city')) or None, clean(location.get('region')) or None
    country = country_name(location.get('country'), location.get('fullLocation'))
    parts = []
    for part in (city, region, country):
        if part and (not parts or parts[-1].casefold() != part.casefold()):
            parts.append(part)
    return {'name': ', '.join(parts), 'city': city, 'region': region, 'country': country} if parts else None


def job_type(kind: dict | None) -> str | None:
    kind = kind or {}
    return JOB_TYPE.get(kind.get('id') or '') or JOB_TYPE_ALIASES.get(' '.join((kind.get('label') or '').lower().split()))


def pay(compensation: dict | None) -> dict:
    """{'min': 60000, 'max': 70000, 'currency': 'USD', 'period': 'YEARLY'} -> the salary fields. 0 to 0 is no pay."""
    comp = compensation or {}
    comp = comp.get('salary') if isinstance(comp.get('salary'), dict) else comp
    low, high = (v if isinstance(v, (int, float)) and v > 0 else None for v in (comp.get('min'), comp.get('max')))
    if low is None and high is None:
        return {}
    currency, period = comp.get('currency') or None, PERIOD.get(str(comp.get('period') or '').upper())
    amounts = ' - '.join(dict.fromkeys(f'{v:,.2f}'.removesuffix('.00') for v in (low, high) if v is not None))
    text = ' '.join(p for p in (currency, amounts, PER.get(period)) if p)
    return {'salary': text, 'salaryMin': low, 'salaryMax': high, 'salaryCurrency': currency, 'salaryPeriod': period}


def job_ad(record: dict) -> str | None:
    """The four sections of the job ad as one HTML text, each under its own heading."""
    sections = ((record.get('jobAd') or {}).get('sections')) or {}
    parts = []
    for key in SECTIONS:
        section = sections.get(key) or {}
        text = (section.get('text') or '').strip()
        if text:
            title = htmllib.escape(clean(section.get('title')))
            parts.append(f'<h3>{title}</h3>{text}' if title else text)
    return ''.join(parts) or None


def answer(response) -> dict:
    if response.status_code != 200:
        raise BoardError(f'HTTP {response.status_code}')
    try:
        data = response.json()
    except ValueError as exc:
        raise BoardError('the answer was not JSON') from exc
    if not isinstance(data, dict):
        raise BoardError('the answer was not a JSON object')
    return data


class SmartRecruiters(Platform):
    key = 'smartrecruiters'
    name = 'SmartRecruiters'
    site_word = 'career site'
    actor = 'smartrecruiters-jobs-search'
    board_example = 'https://careers.smartrecruiters.com/BoschGroup'
    job_example = 'https://jobs.smartrecruiters.com/BoschGroup/744000159116379-pmi-project-lead-em'
    concurrency = 64             # 128 from one laptop met no limit; half of it for Apify's shared addresses
    server_search = True
    newest_first = True
    never = ('otherLocations', 'team', 'expiresAt', 'offersEquity', 'companyWebsite')
    _known: set[str] | None = None

    def read_link(self, text):
        link = text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}'
        parsed = urlparse(link)
        host = (parsed.hostname or '').lower() if ' ' not in text.strip() else ''
        if host not in HOSTS:
            return None
        parts = [p for p in parsed.path.split('/') if p]
        query = parse_qs(parsed.query)
        named = (query.get('dcr_ci') or query.get('company') or [None])[0]
        if host == 'api.smartrecruiters.com':
            if len(parts) >= 3 and parts[:2] == ['v1', 'companies']:
                return parts[2], posting_id(parts[4]) if len(parts) > 4 and parts[3] == 'postings' else None
            return None
        if not parts or parts[0].lower() in NO_COMPANY:
            return named, None
        first = parts[0].lower()
        if first in APP_PATHS:
            if len(parts) >= 3 and parts[1] == 'company':
                return parts[2], posting_id(parts[4]) if len(parts) > 4 and parts[3] in ('publication', 'job') else None
            return named, None
        if first in COMPANY_FIRST:
            if len(parts) < 2:
                return named, None
            return parts[1], posting_id(parts[2]) if first == 'ni' and len(parts) > 2 else None
        job = posting_id(parts[1]) if len(parts) > 1 else None
        if host in MAIN_SITE and len(parts) > 1 and not job:
            return None                  # www.smartrecruiters.com/resources/...: SmartRecruiters' own pages
        return parts[0], job

    def guess_board(self, name):
        squeezed = re.sub(r'[^A-Za-z0-9-]+', '', name)
        return squeezed if COMPANY.match(squeezed) else None

    def known(self) -> set[str]:
        if self._known is None:
            self._known = {board.key.lower() for board in self.boards()[0]}
        return self._known

    async def open(self, http, board):
        if not COMPANY.match(board.key):
            return None
        session = {'company': board.key}
        if board.key.lower() in self.known():
            return session
        # not on our list. A company with open jobs shows them; the company lookup tells one with none from one that
        # does not exist, but it knows only the companies in SmartRecruiters' job search (not AbbVie, 1,989 jobs)
        try:
            newest = (await self._list(http, session, {'limit': 1})).get('content') or []
        except BoardMissing:
            return None
        if newest:
            return {'company': (newest[0].get('company') or {}).get('identifier') or board.key}
        response = await send(http, 'GET', NAME_LOOKUP, params={'q': board.key})
        if response.status_code != 200:
            raise BoardError(f'HTTP {response.status_code}')
        try:
            found = response.json()
        except ValueError as exc:
            raise BoardError('the company lookup was not JSON') from exc
        return {'company': found['identifier']} if isinstance(found, dict) and found.get('identifier') else None

    def job(self, session: dict, record: dict) -> dict:
        location = record.get('location') or {}
        spot = place(location)
        company = record.get('company') or {}
        job_id = str(record.get('id') or '')
        title = clean(record.get('name'))
        link = JOB_LINK.format(company=company.get('identifier') or session['company'],
                               job=f'{job_id}-{slug(title)}' if re.search(r'[a-z0-9]', slug(title)) else job_id)
        return {
            'id': job_id,
            'company': clean(company.get('name')) or None,
            'title': title,
            'location': spot['name'] if spot else None,
            'places': [spot] if spot else [],
            'workType': 'remote' if location.get('remote') else 'hybrid' if location.get('hybrid') else 'onsite',
            'employmentType': job_type(record.get('typeOfEmployment')),
            'department': clean((record.get('department') or {}).get('label')) or None,
            'postedAt': record.get('releasedDate'),
            'dateOnly': False,
            'jobUrl': link,
            'applyUrl': f'{link}?oga=true',
            'reference': clean(record.get('refNumber')) or None,
        }

    def enrich(self, job: dict, record: dict) -> dict:
        html = job_ad(record)
        return {**job, **pay(record.get('compensation')),
                'jobUrl': record.get('postingUrl') or job.get('jobUrl'),
                'applyUrl': record.get('applyUrl') or job.get('applyUrl'),
                'description': text_of(html), 'descriptionHtml': html}

    async def _list(self, http, session: dict, params: dict) -> dict:
        response = await send(http, 'GET', API.format(company=session['company']), params=params)
        if response.status_code in NOT_READABLE:
            raise BoardMissing(session['company'])
        return answer(response)

    async def _pages(self, http, session: dict, params: dict):
        offset, size = 0, FIRST_PAGE_SEARCHED if 'q' in params else FIRST_PAGE
        while True:
            data = await self._list(http, session, {**params, 'limit': size, 'offset': offset})
            records = [r for r in data.get('content') or [] if isinstance(r, dict) and r.get('id')]
            if records:
                yield [self.job(session, r) for r in records]
            offset += len(data.get('content') or [])
            if not records or offset >= (data.get('totalFound') or 0):
                return
            size = PAGE_SIZE

    async def pages(self, http, session, term, days):
        params = {}
        if days:
            after = datetime.now(timezone.utc) - timedelta(days=days)
            params['releasedAfter'] = after.strftime('%Y-%m-%dT%H:%M:%S.000Z')
        said = words(term) if term else []
        if not said:
            async for page in self._pages(http, session, params):
                yield page
            return
        stream = self._pages(http, session, {**params, 'q': ' '.join(said)})
        if len(said) == 1 or not self.search_descriptions:
            async for page in stream:              # any word: the engine keeps the titles that hold every word
                yield page
            return
        # descriptions searched: a job counts when its title holds every word or its text holds the phrase. The
        # phrase search is read as far as the any-word pages go; a job that is neither loses its id, so the engine
        # passes over it and still sees where the page ends.
        wanted, phrases = Term(term), self._pages(http, session, {**params, 'q': f'"{" ".join(said)}"'})
        phrase_ids, phrase_end, phrases_done = set(), None, False
        async for page in stream:
            end = page[-1]['postedAt'] or ''
            while not phrases_done and (phrase_end is None or phrase_end >= end):
                try:
                    more = await anext(phrases)
                except StopAsyncIteration:
                    phrases_done = True
                    break
                phrase_ids.update(j['id'] for j in more)
                phrase_end = more[-1]['postedAt'] or ''
            yield [j if j['id'] in phrase_ids or wanted.in_title(index(j['title'])) else {**j, 'id': None} for j in page]

    async def _detail(self, http, session: dict, job_id: str) -> tuple[int, dict]:
        response = await send(http, 'GET', f'{API.format(company=session["company"])}/{job_id}')
        if response.status_code != 200:
            return response.status_code, {}
        return 200, answer(response)

    async def details(self, http, session, job):
        status, record = await self._detail(http, session, job['id'])
        if status in NOT_READABLE:
            return job
        if status != 200:
            raise BoardError(f'HTTP {status}')
        return self.enrich(job, record)

    async def lookup(self, http, session, job_id):
        status, record = await self._detail(http, session, job_id)
        if status in NOT_READABLE:
            return 'not found', None
        if status != 200:
            raise BoardError(f'HTTP {status}')
        if record.get('active') is not True:
            return 'closed', None
        return 'open', self.enrich(self.job(session, record), record)
