"""
Lever job sites (jobs.lever.co/<site>, jobs.eu.lever.co/<site>), read through Lever's public Postings API
(github.com/lever/postings-api). No key. Measured on 2026-11-02:

- GET https://jobs.lever.co/v0/postings/<site>?mode=json&skip=<n>&limit=<n>: the site's published postings with the
  full description, lists, pay range, places, team, department, commitment and workplace type. The job site's own
  host serves the documented API through Cloudflare, gzip compressed: every site read in one pass was 1,045 MB of JSON
  and 84 MB on the wire. The documented host, api.lever.co, answers the same bytes uncompressed, so it is the fallback
  when Cloudflare refuses. EU sites live only on jobs.eu.lever.co and api.eu.lever.co, global ones only on jobs.lever.co
  and api.lever.co: neither redirects to the other.
- No word search, no date filter and no sort: a list is alphabetical by title, and the only filters are a company's own
  location, commitment, team and department values. skip and limit page it, and pages are stable (4,612 of 4,612 jobs
  on boxlunch in pages of 500).
- GET .../v0/postings/<site>/<id>: one posting. A site that does not exist, a posting that closed and a posting that
  never existed all answer 404 {"ok":false,"error":"Document not found"}, so a link on a live site that no longer
  answers is closed.
- A site name keeps its case (AIFund answers, aifund does not, and contactout and ContactOut are two sites). Posting
  ids are lower case.

postedAt is createdAt: the job page's JobPosting markup gives that day (UTC) as datePosted on 14 of 14 pages, postings
created in 2009 and still open included, while the XML feed's post_date is a later publication time. Pay comes with its
interval (per-year-salary, per-hour-wage, ...), so the period is stated, never guessed. workplaceType is onsite, remote,
hybrid or unspecified (the documentation says on-site). commitment is each company's own words (Full-time, CDI,
Contract: Part-Time, Full Time/Part Time), read as a job type only where the words say one. country is the primary
location's ISO code. No expiry date, requisition number, equity flag or company website: the job site's logo link
points back to Lever on 76 of 100 sites and to the company on 10.

No 429 and no Retry-After: 1,280 calls at 128 at once on api.lever.co, and full passes through Cloudflare at 12, 32 and
64 at once with no refusal. Cloudflare does filter clients: it answers Python's urllib agent with 403 while the
documented host answers it, and that is the refusal the fallback is for. A pass is bound by one round trip per site, not
by bytes: 32 at once read every site in 72 s from a laptop and 64 was no faster, the light ?mode=html list (titles only)
took 108 s, and the engine read all 2,326 sites in 134 to 228 s as Lever's own speed changed (an average call of 1.2 s
at 13:40 UTC, 2.8 s at 14:00). About 1 call in 200 waits past 30 s on a slow list and passes on its retry.
robots.txt: api.lever.co allows everything with Crawl-delay 1; jobs.lever.co allows everything (search=yes, ai-train=no).

Sites have no index: the Wayback Machine, Common Crawl, freehire and public lists on GitHub find them.
"""
import asyncio
import html as htmllib
import re
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import quote, unquote, urlparse

from ..http import BoardError, BoardMissing, send
from ..rows import words
from .base import Board, Platform
from .cornerstone import text_of
from .deel import pay_text

HOSTS = {'jobs.lever.co': 'global', 'api.lever.co': 'global', 'jobs.eu.lever.co': 'eu', 'api.eu.lever.co': 'eu'}
POSTINGS = {   # the job site's own host first (Cloudflare, gzip), then Lever's documented API host
    'global': ('https://jobs.lever.co/v0/postings/', 'https://api.lever.co/v0/postings/'),
    'eu': ('https://jobs.eu.lever.co/v0/postings/', 'https://api.eu.lever.co/v0/postings/'),
}
JOB_SITE = {'global': 'https://jobs.lever.co/', 'eu': 'https://jobs.eu.lever.co/'}
EU = '/eu'                      # the end of an EU site's board key: engelvoelkers/eu
SLUG = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)
TAG = re.compile(r'<[^>]+>')
LIST_BREAK = re.compile(r'</li>|<br\s*/?>', re.I)
EMPLOYER = re.compile(r'<employer>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</employer>', re.S)
PAGE_SIZE = 500                 # jobs per call: a 4,600-job site is 10 calls, and no answer holds more than about 12 MB
SWITCH_AFTER = 5                # Cloudflare refusals before a run reads only the documented host
DIRECT_AT_ONCE = 12             # calls to the documented host at once: it slowed past that (502 and timeouts at 32)
WORK_TYPE = {'remote': 'remote', 'hybrid': 'hybrid', 'onsite': 'onsite', 'on-site': 'onsite'}
PERIOD = {   # Lever's interval -> (salaryPeriod, the words in the salary text)
    'per-year-salary': ('year', 'per year'), 'per-month-salary': ('month', 'per month'),
    'per-week-salary': ('week', 'per week'), 'per-day-wage': ('day', 'per day'), 'per-hour-wage': ('hour', 'per hour'),
    'bi-week-salary': (None, 'every two weeks'), 'semi-month-salary': (None, 'twice a month'),
    'bi-month-salary': (None, 'every two months'), 'one-time': (None, 'one time'),
}
# commitment words, read in this order: an arrangement says more than the hours ("Contract: Full-Time" is a contract)
INTERN = ('intern', 'interns', 'internship', 'internships', 'co op', 'coop', 'apprentice', 'apprenticeship', 'trainee',
          'traineeship', 'stage', 'stagiaire', 'praktikum', 'estagio', 'estágio', 'werkstudent', 'working student',
          'pasantia', 'pasantía', 'becario')
CONTRACT = ('contract', 'contractor', 'contractors', 'contracted', 'freelance', 'freelancer', 'consultant', 'b2b', '1099',
            'c2c', 'corp to corp')
TEMPORARY = ('temporary', 'temp', 'seasonal', 'fixed term', 'short term', 'cdd', 'befristet', 'temporal', 'temporario',
             'temporário', 'interim')
PART_TIME = ('part time', 'parttime', 'teilzeit', 'tiempo parcial', 'mi temps', 'meio periodo', 'meio período')
FULL_TIME = ('full time', 'fulltime', 'full times', 'vollzeit', 'tiempo completo', 'tempo integral', 'temps plein')
PERMANENT = ('permanent', 'regular', 'cdi', 'efetivo', 'clt', 'indefinido', 'festanstellung', 'unbefristet', 'exempt',
             'salaried')


def board_key(site: str, region: str) -> str:
    return f'{site}{EU}' if region == 'eu' else site


def site_of(key: str) -> tuple[str, str | None]:
    """'engelvoelkers/eu' -> ('engelvoelkers', 'eu'); 'palantir' -> ('palantir', None): the region is not known."""
    return (key[:-len(EU)], 'eu') if key.endswith(EU) else (key, None)


def clean(text) -> str:
    if not isinstance(text, str):
        return ''
    return ' '.join((htmllib.unescape(text) if '&' in text else text).split())


def job_type(commitment) -> str | None:
    """Each company's own words -> one job type, or None when they name none or name both full and part time."""
    return _job_type(commitment) if isinstance(commitment, str) else None


@lru_cache(maxsize=8192)
def _job_type(commitment: str) -> str | None:
    said = f' {" ".join(words(commitment))} '

    def has(phrases):
        return any(f' {p} ' in said for p in phrases)
    for kind, phrases in (('intern', INTERN), ('contract', CONTRACT), ('temporary', TEMPORARY)):
        if has(phrases):
            return kind
    part, full = has(PART_TIME), has(FULL_TIME)
    if part != full:
        return 'part-time' if part else 'full-time'
    return 'full-time' if not part and has(PERMANENT) else None


def _countries():
    try:
        import pycountry
        return pycountry.countries
    except ImportError:          # the tests and the actor install it; without it the code stays
        return None


_COUNTRIES = _countries()


def country_name(code) -> str | None:
    """'US' -> 'United States'; a code pycountry does not know stays a code."""
    return _country_name(code.strip().upper()) if isinstance(code, str) and code.strip() else None


@lru_cache(maxsize=1024)
def _country_name(code: str) -> str:
    found = _COUNTRIES.get(alpha_2=code) if _COUNTRIES is not None and len(code) == 2 else None
    return getattr(found, 'common_name', None) or getattr(found, 'name', None) or code


def places(record: dict) -> list[dict]:
    """The primary location first, then the others. Lever's country belongs to the primary location only."""
    categories = record.get('categories') if isinstance(record.get('categories'), dict) else {}
    names = []
    for name in [categories.get('location'), *(categories.get('allLocations') or [])]:
        name = clean(name)
        if name and name not in names:
            names.append(name)
    country = country_name(record.get('country'))
    if not names:
        return [{'name': country, 'country': country}] if country else []
    return [{'name': name, 'country': country if i == 0 else None} for i, name in enumerate(names)]


def number(value) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).replace(',', '').strip())
    except (TypeError, ValueError):
        return None


def pay(record: dict) -> dict:
    """salaryRange -> salary text and numbers. No range, or no amount in it, is no pay."""
    found = record.get('salaryRange')
    if not isinstance(found, dict):
        return {}
    low, high = number(found.get('min')), number(found.get('max'))
    if low is None and high is None:
        return {}
    currency = (found.get('currency') or '').strip().upper() or None
    period, said = PERIOD.get(found.get('interval') or '', (None, ''))
    return {'salary': f'{pay_text(low, high, currency or "")} {said}'.strip(), 'salaryMin': low, 'salaryMax': high,
            'salaryCurrency': currency, 'salaryPeriod': period}


def description_html(record: dict) -> str | None:
    """What the job page shows: the description, each list under its heading, the pay note and the closing text."""
    parts = [record.get('description') or '']
    for item in record.get('lists') or []:
        if isinstance(item, dict) and (item.get('content') or item.get('text')):
            heading = htmllib.escape(clean(item.get('text')), quote=False)
            parts.append(f'<h3>{heading}</h3><ul>{item.get("content") or ""}</ul>' if heading
                         else f'<ul>{item.get("content") or ""}</ul>')
    parts += [record.get('salaryDescription') or '', record.get('additional') or '']
    return ''.join(p for p in parts if isinstance(p, str)) or None


def plain(record: dict, key: str) -> str | None:
    """Lever's plain text of a field, or its HTML read as text where Lever left the plain one empty (5% of
    descriptions and 4% of closing texts in a sample of 1,372 jobs)."""
    text = record.get(f'{key}Plain')
    if isinstance(text, str) and text.strip():
        return text
    html = record.get(key)
    return text_of(html) if isinstance(html, str) and html.strip() else None


def description_text(record: dict) -> str | None:
    """The same as plain text, from Lever's own plain text where it has one: no HTML parser for 80,000 jobs. Word
    matching needs no tidy lines, so tidy() runs only on the jobs delivered with their description."""
    parts = [plain(record, 'description')]
    for item in record.get('lists') or []:
        if isinstance(item, dict):
            content = item.get('content') or ''
            parts += [item.get('text'), TAG.sub(' ', LIST_BREAK.sub('\n', content)) if '<' in content else content]
    parts += [plain(record, 'salaryDescription'), plain(record, 'additional')]
    text = '\n'.join(p for p in parts if isinstance(p, str) and p)
    return (htmllib.unescape(text) if '&' in text else text) or None


def tidy(text: str | None) -> str | None:
    """One line per block, spaces squeezed, empty lines gone."""
    lines = (' '.join(line.split()) for line in (text or '').splitlines())
    return '\n'.join(line for line in lines if line) or None


def posted(value) -> str | None:
    """createdAt in milliseconds -> '2026-10-23T15:00:40Z'."""
    try:
        return datetime.fromtimestamp(float(value) / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def answer(response):
    if response.status_code != 200:
        raise BoardError(f'HTTP {response.status_code}')
    try:
        return response.json()
    except ValueError as exc:
        raise BoardError('the answer was not JSON') from exc


def from_lever(response) -> bool:
    """Lever's own answer: a 404 is Lever's "Document not found", a page of HTML is Cloudflare's."""
    kind = response.headers.get('content-type', '')
    return response.status_code in (200, 404) and 'text/html' not in kind


class Lever(Platform):
    key = 'lever'
    name = 'Lever'
    site_word = 'job site'
    actor = 'lever-jobs-search'
    board_example = 'https://jobs.lever.co/palantir'
    job_example = 'https://jobs.lever.co/palantir/5168e8fd-fec1-4fea-b7a1-81bdaea65850'
    concurrency = 32             # the fastest on a laptop: 64 at once was no faster and no refusal came at 64 or 128
    never = ('expiresAt', 'reference', 'offersEquity', 'city', 'region', 'companyWebsite')

    def __init__(self):
        self._known: set[str] | None = None
        self.direct = False      # Cloudflare refused this run: read only the documented host
        self.refusals = 0
        self._direct_slots: asyncio.Semaphore | None = None

    def known(self) -> set[str]:
        if self._known is None:
            self._known = {board.key for board in self.boards()[0]}
        return self._known

    def aliases(self, board: Board) -> list[str]:
        site, region = site_of(board.key)
        return [site] if region else []

    def read_link(self, text):
        link = text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}'
        parsed = urlparse(link)
        host = (parsed.hostname or '').lower() if ' ' not in text.strip() else ''
        region = HOSTS.get(host.removeprefix('www.'))
        if region is None:
            return None
        parts = [unquote(p).strip() for p in parsed.path.split('/') if p.strip()]
        api = parts[:2] == ['v0', 'postings']
        if host.startswith('api.') and not api:
            return None, None
        parts = parts[2:] if api else parts
        if not parts or not SLUG.match(parts[0]):
            return None, None
        job = UUID.match(parts[1]) if len(parts) > 1 else None
        return board_key(parts[0], region), job.group(0).lower() if job else None

    def guess_board(self, name):
        site = re.sub(r'[^A-Za-z0-9._-]+', '', name)
        return site if SLUG.match(site) else None

    async def get(self, http, region: str, path: str, params: dict | None = None):
        """Lever's answer for one path: through the job site's host, or the documented host when Cloudflare refuses."""
        primary, direct = POSTINGS[region]
        if not self.direct:
            try:
                response = await send(http, 'GET', primary + path, params=params)
                refused = not from_lever(response)
            except BoardError as exc:    # no answer through Cloudflare after the retries
                response, refused = None, str(exc) == 'HTTP 429'
            if response is not None and not refused:
                return response
            if refused:
                self.refusals += 1
                self.direct = self.direct or self.refusals >= SWITCH_AFTER
        if self._direct_slots is None:
            self._direct_slots = asyncio.Semaphore(DIRECT_AT_ONCE)
        async with self._direct_slots:
            return await send(http, 'GET', direct + path, params=params)

    def session(self, site: str, region: str, name: str | None = None, live: bool | None = None) -> dict:
        found = {'site': site, 'region': region, 'name': name, 'link': JOB_SITE[region] + site}
        if live is not None:
            found['live'] = live
        return found

    async def employer(self, http, region: str, site: str) -> str | None:
        """The company's name as Lever's job feed writes it. A site with no posting has none (its feed answers 502)."""
        try:
            response = await self.get(http, region, quote(site), {'mode': 'xml', 'limit': 1})
        except BoardError:
            return None
        found = EMPLOYER.search(response.text) if response.status_code == 200 else None
        return clean(found.group(1)) or None if found else None

    async def open(self, http, board):
        site, region = site_of(board.key)
        if not SLUG.match(site):
            return None
        if board.key in self.known():            # checked by the refresh: the list call itself tells if it is gone
            return self.session(site, region or 'global')
        sites = [site] if site == site.lower() else [site, site.lower()]
        for name in sites:
            for where in ([region] if region else ['global', 'eu']):
                response = await self.get(http, where, quote(name), {'mode': 'json', 'limit': 1})
                if response.status_code == 404:
                    continue
                hiring = answer(response)
                company = await self.employer(http, where, name) if hiring else None
                return self.session(name, where, company, live=True)
        return None

    def job(self, session: dict, record: dict) -> dict:
        categories = record.get('categories') if isinstance(record.get('categories'), dict) else {}
        found = places(record)
        job_id = str(record.get('id'))
        job = {
            'id': job_id,
            'title': clean(record.get('text')),
            'location': found[0]['name'] if found else None,
            'otherLocations': [p['name'] for p in found[1:]],
            'places': found,
            'workType': WORK_TYPE.get(str(record.get('workplaceType') or '').strip().lower()),
            'employmentType': job_type(categories.get('commitment')),
            'department': clean(categories.get('department')) or None,
            'team': clean(categories.get('team')) or None,
            'postedAt': posted(record.get('createdAt')),
            'jobUrl': record.get('hostedUrl') or f'{session["link"]}/{job_id}',
            'applyUrl': record.get('applyUrl') or f'{session["link"]}/{job_id}/apply',
            'description': description_text(record),
            'descriptionHtml': description_html(record),
            **pay(record),
        }
        if session.get('name'):
            job['company'] = session['name']
        return job

    async def pages(self, http, session, term, days):
        skip = 0
        while True:
            params = {'mode': 'json', 'skip': skip, 'limit': PAGE_SIZE}
            response = await self.get(http, session['region'], quote(session['site']), params)
            if response.status_code == 404:
                raise BoardMissing(session['link'])
            records = answer(response)
            if not isinstance(records, list):
                raise BoardError('the job list was not a list')
            yield [self.job(session, r) for r in records if isinstance(r, dict) and r.get('id')]
            if len(records) < PAGE_SIZE:
                return
            skip += PAGE_SIZE

    async def details(self, http, session, job):
        """The list carries the whole description: a job chosen for delivery only gets its lines tidied."""
        return {**job, 'description': tidy(job.get('description'))}

    async def lookup(self, http, session, job_id):
        site = quote(session['site'])
        response = await self.get(http, session['region'], f'{site}/{quote(job_id)}')
        if response.status_code == 404:
            if 'live' not in session:
                listed = await self.get(http, session['region'], site, {'mode': 'json', 'limit': 1})
                if listed.status_code not in (200, 404):
                    raise BoardError(f'HTTP {listed.status_code}')
                session['live'] = listed.status_code == 200
            return ('closed' if session['live'] else 'not found'), None
        record = answer(response)
        if not isinstance(record, dict) or not record.get('id'):
            raise BoardError('the job was not a job')
        job = self.job(session, record)
        return 'open', {**job, 'description': tidy(job['description'])}
