"""
Workable (apply.workable.com) jobs: each company's career page through its own JSON, and a keyword across every
company through Workable's public jobs feed. No key. Measured from this laptop on 2026-11-02:

- GET https://apply.workable.com/api/v1/widget/accounts/<account>: one company's career page in one answer, the
  documented jobs widget (?details=true adds descriptions, 6.6 times the size). A job in several places comes once per
  place. published_on is the date the job page gives Google (datePosted, 10 of 10 pages rendered; created_at differed
  on 8 of them). No search, no order, no work type beyond telecommuting. 404 "Not Found" is no such account; the old
  https://www.workable.com/api/accounts/<account> redirects here.
- GET https://apply.workable.com/api/v2/accounts/<account>/jobs/<shortcode>: one job, the call its page makes: the
  description, requirements and benefits, pay (salary_from, salary_to, currency, frequency) when shown, workplace
  (on_site, hybrid, remote), type (full, part, contract, temporary, other), departments, published, state. 404 "Job
  not found" for a job that closed and for a code that never existed alike (12 old links): both read as closed.
- GET https://www.workable.com/boards/workable.xml: Workable's public feed of every job its customers list on its job
  board (help.workable.com article 4420464031767: hourly, no filters), 861 MB, the oldest listing first (129,639 of
  the first 129,721 steps), each job once per place, with title, company, website, city, state, country code, remote,
  job type, description and the apply.workable.com/j/<shortcode> link. Its date is when the job board listed the job in
  that place: the datePosted of that listing's jobs.workable.com page; the career page shows the published date, which
  is earlier for jobs listed before the board began (2021-07-28) and for places added to an old job. It names no
  hybrid or on-site. Cloudflare compresses it on the way (brotli 131 MB, gzip 128 MB); its first byte came after 19 to
  23 s and the answer ends at 60 to 66 s whatever has arrived: 1 of 8 downloads from here finished (brotli, 51 s), so a
  feed is read only once it has all arrived, and a short one gives nothing. About 9 downloads from one address in 3
  minutes made Cloudflare refuse it for an hour (429, Retry-After 3590).
- GET https://jobs.workable.com/api/v1/jobs/<id>: one job of Workable's job board (the links people paste from
  jobs.workable.com): created, workplace, pay; 410 is a job gone, 404 one that never was. Its search
  (/api/v1/jobs?query=...) is not used: after 400 to 600 searches it refused each address of this laptop for a day
  ("contact us ... for our jobs XML feed", Retry-After 86,400), and a keyword with a place needed 356 searches.

Each run takes the route that fits it. A word, a place, a work type or a job type needs every company: the feed, read
once (dates have a time); when it cannot be read, the career pages of the biggest companies for FALLBACK_SECONDS, and
the message says so. A run for the newest jobs only (maybe within a period) reads the CAREER_PAGES biggest career pages
and stops once maxJobs jobs posted today are held (dates are days). Named companies always read their career pages.

Accounts are lower case (apply.workable.com/BIFFA answers 404, its page redirects to /oops); shortcodes are ten hex
digits in any case. Old links: <account>.workable.com/... and /<account>/jobs/<number> redirect to /<account>/j/<code>;
apply.workable.com/j/<code> redirects to the account's job, or to /oops once the job is gone. robots.txt of
apply.workable.com disallows nothing.

Limits from one address: apply.workable.com answered 3 calls a second for 2 minutes and refused about half of 4 a
second after 70 s (HTTP 429, Retry-After 0, gone within seconds), so its calls wait their turn (PACE); its own limiter
also answers 429 with a Retry-After near 86,400 that is not the real wait.
"""
import asyncio
import dataclasses
import html as htmllib
import importlib.util
import logging
import re
import tempfile
import time
import xml.etree.ElementTree as ET
import zlib
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import unquote, urlparse

import httpx

from ..http import STATS, BoardError, send
from ..inputs import JOB_TYPE_ALIASES
from ..rows import _COUNTRIES, Filters, folded_text, index, terms
from .base import Board, Platform
from .cornerstone import text_of

APPLY = 'https://apply.workable.com'
WIDGET = APPLY + '/api/v1/widget/accounts/{account}'
JOB = APPLY + '/api/v2/accounts/{account}/jobs/{code}'
SHORT_LINK = APPLY + '/j/{code}'
APPLY_LINK = APPLY + '/j/{code}/apply'
OLD_JOB = APPLY + '/{account}/jobs/{number}'
FEED_URL = 'https://www.workable.com/boards/workable.xml'
BOARD_HOST = 'jobs.workable.com'
BOARD_JOB = f'https://{BOARD_HOST}/api/v1/jobs/{{id}}'
EVERYONE = Board('*', 'Workable')     # every company at once (the feed); also the board of a link naming no account
HOSTS = ('apply.workable.com', 'www.workable.com', 'workable.com')
OWN_SITES = {'www', 'apply', 'jobs', 'resources', 'help', 'partners', 'partnerhelp', 'get', 'jobseekers', 'events',
             'blog', 'api', 'status', 'developers', 'app', 'mail', 'email'}      # Workable's own sites, not accounts
NOT_ACCOUNTS = {'j', 'api', 'oops', 'cdn-cgi', 'static', 'assets', 'embed', 'boards'}
ACCOUNT = re.compile(r'^[a-z0-9][a-z0-9-]{0,99}$')
CODE = re.compile(r'^[0-9a-f]{10}$', re.I)
NUMBER = re.compile(r'^\d{1,9}$')
BOARD_ID = re.compile(r'^(?:[1-9A-HJ-NP-Za-km-z]{20,24}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$')
CODE_IN_TEXT = re.compile(r'/j/([0-9a-f]{10})(?![0-9a-z])', re.I)
PAGE = 500                    # matching feed jobs handed to the engine at a time
FEED_TIMEOUT = httpx.Timeout(120.0, connect=15.0)    # its first byte came after 19 to 23 s
# Cloudflare compresses the feed on the way: brotli (131 MB of 861 MB) finished in time, gzip did not. It is decoded
# here with the brotli package, which the engine depends on; gzip only without it
FEED_ENCODING = 'br' if importlib.util.find_spec('brotli') else 'gzip'
BROTLI_ERRORS = (__import__('brotli').error,) if FEED_ENCODING == 'br' else ()
FALLBACK_SECONDS = 120        # career pages read, biggest first, when the feed cannot be read
WORKPLACE_SECONDS = 90        # a hybrid or on-site search checks feed jobs one by one for this long at most
CAREER_PAGES = 300            # the biggest career pages a run for the newest jobs reads: 120 s at most
MONTHS = {m: i for i, m in enumerate(('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'), 1)}
FEED_DATE = re.compile(r'(\d{1,2}) ([A-Z][a-z]{2}) (\d{4}) (\d{2}):(\d{2}):(\d{2})')
TAG = re.compile(r'<[^>]*>')
NOBODY = {'company': None, 'companySlug': None, 'companyWebsite': None}   # a gone job whose company is unknown
REFUSALS = 3                  # failed calls in a row after which apply.workable.com is not asked again this run
PACE = 0.4                    # seconds between two calls to apply.workable.com: 2.5 a second, under the 3 measured
LOG = logging.getLogger('apify')
WORKPLACE = {'remote': 'remote', 'hybrid': 'hybrid', 'on_site': 'onsite'}
TYPE = {'full': 'full-time', 'part': 'part-time', 'contract': 'contract', 'temporary': 'temporary'}
PERIOD = {'year': 'year', 'month': 'month', 'week': 'week', 'day': 'day', 'hour': 'hour'}


def clean(text) -> str:
    return ' '.join(str(text or '').split())


def place(entry: dict) -> dict | None:
    """{'city': 'High Wycombe', 'region': 'England', 'country': 'United Kingdom'} -> one place."""
    city, region, country = (clean(entry.get(k)) or None for k in ('city', 'region', 'country'))
    name = ', '.join(dict.fromkeys(p for p in (city, region, country) if p))
    return {'name': name, 'city': city, 'region': region, 'country': country} if name else None


def job_type(text: str | None, experience: str | None = None) -> str | None:
    """'Full-time', 'Part-time', 'Contract', 'Temporary' -> the engine's job types; 'Other' -> None. Workable has no
    internship type: an internship is the experience level 'Internship' (957 feed entries), whatever the type says."""
    if clean(experience).lower() == 'internship':
        return 'intern'
    return JOB_TYPE_ALIASES.get(clean(text).lower())


def pay_text(low, high, currency: str | None, period: str | None) -> str | None:
    """28000, 32000, 'GBP', 'year' -> 'GBP 28,000 - 32,000 per year'. One amount when both are equal or one is missing."""
    amounts = list(dict.fromkeys(f'{v:,.2f}'.removesuffix('.00') for v in (low, high) if isinstance(v, (int, float)) and v > 0))
    if not amounts:
        return None
    return ' '.join(p for p in (currency, ' - '.join(amounts), f'per {period}' if period else None) if p)


def pay(low, high, currency, frequency) -> dict:
    """The salary fields of a row, filled only when Workable shows an amount."""
    period = PERIOD.get(frequency)
    shown = pay_text(low, high, currency or None, period)

    def amount(value):
        return value if shown and isinstance(value, (int, float)) and value > 0 else None
    return {'salary': shown, 'salaryMin': amount(low), 'salaryMax': amount(high),
            'salaryCurrency': (currency or None) if shown else None, 'salaryPeriod': period if shown else None}


@lru_cache(maxsize=512)
def country_name(code: str) -> str:
    """'GB' -> 'United Kingdom', 'KR' -> 'South Korea'; a code pycountry does not know stays as it is."""
    try:
        found = _COUNTRIES.get(alpha_2=code.upper()) if _COUNTRIES and len(code) == 2 else None
    except (KeyError, LookupError):
        found = None
    return (getattr(found, 'common_name', None) or found.name) if found else code


def feed_date(text: str) -> str | None:
    """'Wed, 28 Jul 2021 10:24:17 UTC' -> '2021-07-28T10:24:17+00:00'."""
    found = FEED_DATE.search(text or '')
    if not found or found.group(2) not in MONTHS:
        return None
    day, month, year, hh, mm, ss = found.groups()
    return f'{year}-{MONTHS[month]:02d}-{int(day):02d}T{hh}:{mm}:{ss}+00:00'


def words_of(html: str | None) -> str | None:
    """HTML -> its words, for matching: half the work of text_of, which the rows that are delivered get."""
    if not html:
        return None
    text = TAG.sub(' ', html)
    return htmllib.unescape(text) if '&' in text else text


def decoder(encoding: str):
    """The content encoding of a download -> a function from its bytes, in order, to plain bytes."""
    if encoding == 'br':
        import brotli
        return brotli.Decompressor().process
    if encoding in ('gzip', 'x-gzip'):
        return zlib.decompressobj(16 + zlib.MAX_WBITS).decompress
    if encoding in ('', 'identity'):
        return lambda chunk: chunk
    raise httpx.DecodingError(f'unknown content encoding {encoding}')


def answer(response) -> dict | list:
    if response.status_code != 200:
        raise BoardError(f'HTTP {response.status_code}')
    try:
        return response.json()
    except ValueError as exc:
        raise BoardError('the answer was not JSON') from exc


def matches(term, job: dict) -> bool:
    """A word in a career page job's title, or in its description when the page gave one (as a search would)."""
    if term.in_title(index(job.get('title'))):
        return True
    description = job.get('description')
    if not description:
        return False
    folded = description.casefold()
    if not all(token in folded for token in term.tokens):    # most descriptions fail this cheap test first
        return False
    text, words = folded_text(description)
    return term.in_text(text, words)


class Workable(Platform):
    key = 'workable'
    name = 'Workable'
    site_word = 'career page'
    actor = 'workable-jobs-search'
    board_example = 'https://apply.workable.com/huggingface/'
    job_example = 'https://apply.workable.com/huggingface/j/DB4D7C0EC8/'
    concurrency = 4               # career pages at once; PACE keeps their rate
    server_search = True          # pages() gets the word: the feed and a career page are matched here
    never = ('team', 'offersEquity', 'expiresAt')

    def __init__(self):
        self._turn = asyncio.Lock()
        self._last = 0.0
        self._refused = 0
        self._told = False
        self._known: dict[str, Board] | None = None
        self._names: dict[str, str] | None = None
        self.cfg = None

    # ---------- links and names ----------

    def read_link(self, text):
        link = text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}'
        parsed = urlparse(link)
        host = (parsed.hostname or '').lower() if ' ' not in text.strip() else ''
        if not host.endswith('workable.com'):
            return None
        raw = [unquote(p) for p in parsed.path.split('/') if p]
        parts = [p.lower() for p in raw]
        found = CODE_IN_TEXT.search(unquote(parsed.query))       # ?lng=en/j/<code>, as some job boards mangle links
        if host == BOARD_HOST:                                   # Workable's job board: its own job ids
            if len(parts) > 1 and parts[0] == 'view' and BOARD_ID.match(raw[1]):
                return EVERYONE.key, raw[1]
            return (None, None) if parts[:1] in ([], ['company'], ['search']) else None
        if host in HOSTS:
            if parts[:3] in (['api', 'v1', 'widget'], ['api', 'v3', 'accounts'], ['api', 'v2', 'accounts'],
                             ['api', 'v1', 'accounts']) and len(parts) > 3:
                parts = parts[4:] if parts[2] == 'widget' else parts[3:]
                parts = [parts[0], 'j', parts[2]] if len(parts) > 2 and parts[1] == 'jobs' else parts[:1]
            elif parts[:2] == ['api', 'accounts'] and len(parts) > 2:          # the old www.workable.com API
                parts = parts[2:3]
            elif host != 'apply.workable.com' and parts[:1] != ['j']:
                return (None, None) if parts[:2] == ['boards', 'workable.xml'] else None
        elif host.endswith('.workable.com'):
            account = host.removesuffix('.workable.com')
            if account in OWN_SITES or '.' in account:
                return None
            parts = [account, *parts]
        else:
            return None
        if not parts:
            return None, None
        if parts[0] == 'j':
            code = parts[1] if len(parts) > 1 and CODE.match(parts[1]) else None
            return (EVERYONE.key, code.upper()) if code else (None, None)
        account = parts[0]
        if account in NOT_ACCOUNTS or not ACCOUNT.match(account):
            return None, None
        job = None
        if len(parts) > 2 and parts[1] == 'j' and CODE.match(parts[2]):
            job = parts[2].upper()
        elif len(parts) > 3 and parts[1:3] == ['jobs', 'view'] and CODE.match(parts[3].removesuffix('.md')):
            job = parts[3].removesuffix('.md').upper()
        elif len(parts) > 2 and parts[1] in ('jobs', 'j') and NUMBER.match(parts[2]):
            job = parts[2]                         # an old job number: its page redirects to the shortcode
        elif found:
            job = found.group(1).upper()
        return account, job

    def aliases(self, board: Board) -> list[str]:
        """A company's website finds it too: biffa.co.uk -> biffa."""
        if not board.website:
            return []
        host = urlparse(board.website if '://' in board.website else f'https://{board.website}').hostname or ''
        return [host.removeprefix('www.')] if host else []

    def guess_board(self, name):
        slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
        return slug if ACCOUNT.match(slug) else None

    def known(self) -> dict[str, Board]:
        if self._known is None:
            self._known = {b.key: b for b in self.boards()[0]}
        return self._known

    def names(self) -> dict[str, str]:
        """Company name, folded -> its account, for the names only one account on our list carries."""
        if self._names is None:
            seen: dict[str, list[str]] = {}
            for board in self.known().values():
                seen.setdefault(board.name.casefold(), []).append(board.key)
            self._names = {name: keys[0] for name, keys in seen.items() if len(keys) == 1}
        return self._names

    def search_all(self, boards, cfg):
        """A word, a place, a work type or a job type: the feed, every company at once. Only the newest jobs (maybe
        within a period): the biggest career pages, whose dates are days, so the run stops once today fills the list
        and never reads for longer than about CAREER_PAGES * PACE seconds."""
        self.cfg = cfg
        if cfg.search_terms or cfg.locations or cfg.work_types or cfg.job_types:
            return [EVERYONE]
        return boards[:CAREER_PAGES]

    # ---------- calls to apply.workable.com, one at a time ----------

    async def call(self, http, method: str, url: str, **kwargs):
        """One call to apply.workable.com in its turn. After REFUSALS failed calls in a row (each already tried four
        times over 11 s) the rest fail at once: Workable has stopped answering this address."""
        if self._refused >= REFUSALS:
            raise BoardError('Workable stopped answering this address')
        async with self._turn:
            wait = self._last + PACE - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
        try:
            response = await send(http, method, url, **kwargs)
        except BoardError:
            self._refused += 1
            if self._refused >= REFUSALS and not self._told and self.cfg is not None:
                self._told = True
                self.cfg.notes.append(f'Workable stopped answering this address after {REFUSALS} refused calls in a '
                                      'row, so the career pages, details and links after that were not read.')
            raise
        self._refused = 0
        return response

    async def career_page(self, http, account: str, descriptions: bool = False) -> dict | None:
        """The widget answer, or None when there is no such account."""
        if not ACCOUNT.match(account):
            return None
        url = WIDGET.format(account=account) + ('?details=true' if descriptions else '')
        response = await self.call(http, 'GET', url)
        if response.status_code == 404:
            return None
        data = answer(response)
        if not isinstance(data, dict) or not isinstance(data.get('jobs'), list):
            raise BoardError('the career page answer had no job list')
        return data

    # ---------- one company's career page ----------

    async def open(self, http, board):
        if board.key == EVERYONE.key:
            return {'everyone': True}
        wanted = bool(self.cfg and self.cfg.search_descriptions and self.cfg.search_terms)
        data = await self.career_page(http, board.key, descriptions=wanted)
        if data is None:
            return None
        known = self.known().get(board.key)
        return {'account': board.key, 'name': clean(data.get('name')) or board.name,
                'website': known.website if known else None, 'jobs': data['jobs']}

    def from_widget(self, session: dict, entries: list[dict]) -> list[dict]:
        """Widget entries -> jobs, one per shortcode with every place of it."""
        jobs: dict[str, dict] = {}
        for entry in entries:
            code = clean(entry.get('shortcode')).upper()
            if not CODE.match(code):
                continue
            own = {'city': entry.get('city'), 'region': entry.get('state'), 'country': entry.get('country')}
            places = [p for p in (place(loc) for loc in entry.get('locations') or [own]) if p]
            if code in jobs:
                held = jobs[code]
                held['places'] += [p for p in places if p['name'] not in {q['name'] for q in held['places']}]
                continue
            html = entry.get('description') or None
            jobs[code] = {
                'id': code,
                'company': session['name'],
                'companySlug': session['account'],
                'companyWebsite': session.get('website'),
                'title': clean(entry.get('title')),
                'places': places,
                'workType': 'remote' if entry.get('telecommuting') is True else None,
                'employmentType': job_type(entry.get('employment_type'), entry.get('experience')),
                'department': clean(entry.get('department')) or None,
                'reference': clean(entry.get('code')) or None,
                'postedAt': clean(entry.get('published_on')) or None,
                'dateOnly': True,
                'jobUrl': SHORT_LINK.format(code=code),
                'applyUrl': APPLY_LINK.format(code=code),
                'description': text_of(html),
                'descriptionHtml': html,
            }
        for job in jobs.values():
            job['location'] = job['places'][0]['name'] if job['places'] else None
            job['otherLocations'] = [p['name'] for p in job['places'][1:]]
        return list(jobs.values())

    async def pages(self, http, session, term, days):
        if session.get('everyone'):
            if session.get('read'):         # every word was matched on the first call
                return
            session['read'] = True
            jobs = await self.every_company(http, days)
            for i in range(0, len(jobs), PAGE):
                yield jobs[i:i + PAGE]
            return
        jobs = self.from_widget(session, session['jobs'])
        if term:        # a career page has no search: the word is matched here, as the job board would have
            wanted = terms([term])
            jobs = [j for j in jobs if wanted and matches(wanted[0], j)]
        if jobs:
            yield jobs

    # ---------- every company: Workable's jobs feed ----------

    def wanted(self, days: int | None):
        """The settings -> a test a job of the feed must pass to be kept: a word of the search in its title (or its
        description, when descriptions are searched) and a date inside the period. The engine checks the rest."""
        cfg = self.cfg
        words = terms(cfg.search_terms) if cfg else []
        texts = bool(cfg and cfg.search_descriptions)
        oldest = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat() if days else None

        def keep(job: dict) -> bool:
            if oldest and (job.get('postedAt') or '') < oldest:
                return False
            if not words:
                return True
            looked = job if texts else {**job, 'description': None}
            return any(matches(word, looked) for word in words)
        return keep

    def from_feed(self, fields: dict) -> dict | None:
        """One <job> of the feed, as {tag: text} -> a job; None without a shortcode or a title."""
        code, title = fields.get('referencenumber', '').upper(), clean(fields.get('title'))
        if not CODE.match(code) or not title:
            return None
        city, region, country = (clean(fields.get(k)) or None for k in ('city', 'state', 'country'))
        spot = place({'city': city, 'region': region, 'country': country_name(country) if country else None})
        company = clean(fields.get('company')) or None
        html = fields.get('description') or None
        texts = bool(self.cfg and (self.cfg.search_descriptions or self.cfg.include_details))
        return {
            'id': code,
            'company': company,
            'companySlug': self.names().get(company.casefold()) if company else None,
            'companyWebsite': clean(fields.get('website')) or None,
            'title': title,
            'location': spot['name'] if spot else None,
            'otherLocations': [],
            'places': [dict(spot, country=country)] if spot else [],
            'workType': 'remote' if fields.get('remote', '').strip().lower() == 'true' else None,
            'employmentType': job_type(fields.get('jobtype'), fields.get('experience')),
            'postedAt': feed_date(fields.get('date')),
            'dateOnly': False,
            'jobUrl': clean(fields.get('url')) or SHORT_LINK.format(code=code),
            'applyUrl': APPLY_LINK.format(code=code),
            'descriptionHtml': html if texts else None,
            'description': words_of(html) if texts else None,
        }

    def reader(self):
        """A parser fed the feed's bytes as they arrive: feed(chunk) and close() give the jobs completed so far. The
        feed lists a job once per place, one entry after the other: those entries become one job with every place.
        close() raises ET.ParseError when the feed stopped before its end."""
        parser = ET.XMLPullParser(events=('start', 'end'))
        root: list = []
        held: list[dict] = []           # the last job, until an entry of another job shows it has all its places

        def merge(job: dict) -> list[dict]:
            if held and held[0]['id'] == job['id']:
                first = held[0]
                if job['places'] and job['places'][0]['name'] not in {p['name'] for p in first['places']}:
                    first['places'].append(job['places'][0])
                    first['otherLocations'].append(job['places'][0]['name'])
                return []
            done = held[:]
            held[:] = [job]
            return done

        def read() -> list[dict]:
            jobs = []
            for event, elem in parser.read_events():
                if event == 'start':
                    if not root:
                        root.append(elem)
                elif elem.tag == 'job':
                    job = self.from_feed({child.tag: (child.text or '').strip() for child in elem})
                    if job:
                        jobs += merge(job)
            if root:
                root[0].clear()         # what was read is kept or dropped: memory stays flat
            return jobs

        def feed(chunk: bytes) -> list[dict]:
            parser.feed(chunk)
            return read()

        def close() -> list[dict]:
            parser.close()
            jobs = read() + held[:]
            held.clear()
            return jobs
        return feed, close

    async def download(self, http):
        """The feed as it travels, in a temporary file, and its encoding. It is read after it has all arrived: Workable
        ends the answer about 60 s after the request, so nothing may slow the download down. A failure before the
        first byte is tried once more; a cut download is not (the same line cut it), nor a 429 (Workable refuses an
        address for an hour)."""
        for attempt in range(2):
            spool, started = tempfile.TemporaryFile(), False
            try:
                async with http.stream('GET', FEED_URL, timeout=FEED_TIMEOUT,
                                       headers={'Accept-Encoding': FEED_ENCODING}) as response:
                    STATS['calls'] += 1
                    if response.status_code == 429:
                        raise BoardError('HTTP 429: Workable allows a few downloads of its feed an hour from one address')
                    if response.status_code != 200:
                        raise httpx.HTTPStatusError(f'HTTP {response.status_code}', request=response.request,
                                                    response=response)
                    encoding = response.headers.get('content-encoding', 'identity').strip().lower()
                    async for chunk in response.aiter_raw(1 << 18):
                        started = True
                        spool.write(chunk)
                spool.seek(0)
                return spool, encoding
            except (httpx.HTTPError, OSError) as exc:
                spool.close()
                STATS['retries'][type(exc).__name__] += 1
                if started or attempt:
                    raise BoardError(('the download was cut before its end' if started else f'{type(exc).__name__}: {exc}')[:200]) from exc
                await asyncio.sleep(3)
            except BaseException:
                spool.close()
                raise
        raise BoardError('no answer')

    async def feed_jobs(self, http, keep) -> list[dict]:
        """Every job of the feed that keep() wants. A feed that stops before its end gives nothing: it lists the
        oldest jobs first, so part of a feed would answer with old jobs only."""
        spool, encoding = await self.download(http)
        with spool:
            decode, (feed, close), jobs = decoder(encoding), self.reader(), []
            try:
                while chunk := spool.read(1 << 20):
                    jobs += [job for job in feed(decode(chunk)) if keep(job)]
                    await asyncio.sleep(0)          # the engine's progress line runs between chunks
                return jobs + [job for job in close() if keep(job)]
            except (ET.ParseError, zlib.error) + BROTLI_ERRORS as exc:
                raise BoardError('the download was cut before its end') from exc

    async def biggest_career_pages(self, http, keep, reason: str) -> list[dict]:
        """When the feed cannot be read: the career pages of the biggest companies on our list, for FALLBACK_SECONDS."""
        started, read, jobs = time.monotonic(), 0, []
        for board in self.known().values():         # the file lists the biggest company first
            if time.monotonic() - started > FALLBACK_SECONDS:
                break
            try:
                session = await self.open(http, board)
            except BoardError:
                continue
            if session:
                read += 1
                jobs += [job for job in self.from_widget(session, session['jobs']) if keep(job)]
        note = (f"Workable's jobs feed could not be read ({reason}), so these jobs come from the career pages of the "
                f'{read:,} biggest companies on our list of {len(self.known()):,}. Run again later for every company.')
        LOG.warning(note)
        if self.cfg is not None:
            self.cfg.notes.append(note)
        return jobs

    async def every_company(self, http, days: int | None) -> list[dict]:
        keep = self.wanted(days)
        try:
            jobs = await self.feed_jobs(http, keep)
        except BoardError as exc:
            return await self.biggest_career_pages(http, keep, str(exc))
        return await self.workplaces(http, jobs)

    async def workplaces(self, http, jobs: list[dict]) -> list[dict]:
        """The feed marks remote jobs and nothing else. For a hybrid or on-site search, the jobs that pass every other
        filter and are not marked remote get their work type from their own answer, newest first, until maxJobs of
        them match or WORKPLACE_SECONDS have passed."""
        cfg = self.cfg
        wanted = set(cfg.work_types) if cfg else set()
        if not wanted - {'remote'}:
            return jobs
        others = Filters(dataclasses.replace(cfg, work_types=[]))
        now = datetime.now(timezone.utc)
        candidates = sorted((j for j in jobs if j.get('workType') != 'remote' and others.passes(j, now)),
                            key=lambda j: j.get('postedAt') or '', reverse=True)
        kept = [j for j in jobs if j.get('workType') == 'remote'] if 'remote' in wanted else []
        started, checked, found = time.monotonic(), 0, 0
        for job in candidates:
            if time.monotonic() - started > WORKPLACE_SECONDS or (cfg.max_jobs and found >= cfg.max_jobs):
                break
            checked += 1
            try:
                account = job.get('companySlug') or await self.account_of(http, job['id'])
                data = await self.job_answer(http, account, job['id']) if account else None
            except BoardError:
                break                   # Workable stopped answering: what was checked stands
            kind = WORKPLACE.get((data or {}).get('workplace'))
            if kind in wanted:
                found += 1
                kept.append({**job, 'workType': kind})
        if checked < len(candidates):
            cfg.notes.append(f"Workable's feed marks only remote jobs, so the {checked:,} newest of {len(candidates):,} "
                             f'matching jobs not marked remote were checked one by one for {" or ".join(sorted(wanted - {"remote"}))}.')
        return kept

    def from_board(self, job_id: str, data: dict) -> dict:
        """A job of Workable's job board: created is the datePosted of its page; it names no account or shortcode."""
        company = data.get('company') or {}
        name = clean(company.get('title')) or None
        texts = [clean(t) for t in data.get('locations') or [] if clean(t) and clean(t) != 'TELECOMMUTE']
        first = data.get('location') or {}
        places = [{'name': t} for t in dict.fromkeys(texts)]
        if places and first.get('countryName'):
            places[0].update(city=clean(first.get('city')) or None, region=clean(first.get('subregion')) or None,
                             country=clean(first.get('countryName')))
        sections = [x for x in (data.get(k) for k in ('description', 'requirementsSection', 'benefitsSection')) if x]
        html = ''.join(sections) or None
        url = clean(data.get('url')) or f'https://{BOARD_HOST}/view/{job_id}'
        remote = 'remote' if 'TELECOMMUTE' in (data.get('locations') or []) else None
        return {
            'id': job_id, 'company': name, 'companySlug': self.names().get(name.casefold()) if name else None,
            'companyWebsite': clean(company.get('website')) or None, 'title': clean(data.get('title')),
            'places': places, 'location': places[0]['name'] if places else None,
            'otherLocations': [p['name'] for p in places[1:]],
            'workType': WORKPLACE.get(data.get('workplace')) or remote,
            'employmentType': job_type(data.get('employmentType')), 'department': clean(data.get('department')) or None,
            **pay(data.get('salaryFrom'), data.get('salaryTo'), data.get('salaryCurrencyIsoCode'), data.get('salaryFrequency')),
            'postedAt': clean(data.get('created')) or None, 'dateOnly': False, 'jobUrl': url, 'applyUrl': url,
            'description': text_of(html), 'descriptionHtml': html,
        }

    # ---------- one job ----------

    async def job_answer(self, http, account: str, code: str) -> dict | None:
        """The job's own JSON, or None when Workable says there is no such job."""
        response = await self.call(http, 'GET', JOB.format(account=account, code=code))
        if response.status_code == 404:
            return None
        data = answer(response)
        if not isinstance(data, dict):
            raise BoardError('the job answer was not an object')
        return data

    def enrich(self, job: dict, data: dict) -> dict:
        sections = [s for s in (data.get(k) for k in ('description', 'requirements', 'benefits')) if s]
        html = ''.join(sections) or job.get('descriptionHtml')
        places = [p for p in (place(loc) for loc in data.get('locations') or []) if p] or job.get('places') or []
        departments = ', '.join(clean(d) for d in data.get('department') or [] if clean(d)) or None
        return {
            **job,
            'title': clean(data.get('title')) or job.get('title'),
            'places': places,
            'location': places[0]['name'] if places else job.get('location'),
            'otherLocations': [p['name'] for p in places[1:]],
            'workType': WORKPLACE.get(data.get('workplace')) or ('remote' if data.get('remote') is True else job.get('workType')),
            'employmentType': job.get('employmentType') if job.get('employmentType') == 'intern'
            else TYPE.get(data.get('type')) or job.get('employmentType'),
            'department': departments or job.get('department'),
            'reference': clean(data.get('code')) or job.get('reference'),
            **pay(data.get('salary_from'), data.get('salary_to'), data.get('salary_currency_iso_code'), data.get('salary_frequency')),
            'postedAt': job.get('postedAt') or clean(data.get('published'))[:10] or None,
            'description': text_of(html),
            'descriptionHtml': html,
        }

    async def account_of(self, http, code: str) -> str | None:
        """apply.workable.com/j/<code> redirects to /<account>/j/<code>, or to /oops when the job is gone."""
        response = await self.call(http, 'GET', SHORT_LINK.format(code=code), follow_redirects=False)
        found = re.match(r'^(?:https://apply\.workable\.com)?/([a-z0-9-]+)/j/', response.headers.get('location') or '')
        if response.status_code in (301, 302, 307, 308):
            return found.group(1) if found and found.group(1) not in NOT_ACCOUNTS else None
        if response.status_code == 404:
            return None
        raise BoardError(f'HTTP {response.status_code}')

    async def code_of(self, http, account: str, number: str) -> str | None:
        """An old job number -> its shortcode, from the redirect of /<account>/jobs/<number>."""
        response = await self.call(http, 'GET', OLD_JOB.format(account=account, number=number), follow_redirects=False)
        found = re.search(r'/j/([0-9A-Fa-f]{10})', response.headers.get('location') or '')
        if response.status_code in (301, 302, 307, 308, 404):
            return found.group(1).upper() if found else None
        raise BoardError(f'HTTP {response.status_code}')

    async def details(self, http, session, job):
        if job.get('descriptionHtml'):          # a feed job carries its text: delivered rows get it whole
            job = {**job, 'description': text_of(job['descriptionHtml'])}
        account = job.get('companySlug') or (session or {}).get('account') or await self.account_of(http, job['id'])
        data = await self.job_answer(http, account, job['id']) if account else None
        return self.enrich(job, data) if data else job

    async def lookup(self, http, session, job_id):
        if BOARD_ID.match(job_id) and not CODE.match(job_id):     # a jobs.workable.com link: 410 is gone, 404 never was
            response = await send(http, 'GET', BOARD_JOB.format(id=job_id))
            if response.status_code in (404, 410):
                return ('closed' if response.status_code == 410 else 'not found'), dict(NOBODY)
            data = answer(response)
            if not isinstance(data, dict) or data.get('state') not in (None, 'published'):
                return 'closed', dict(NOBODY)
            return 'open', self.from_board(job_id, data)
        account = session.get('account')
        if NUMBER.match(job_id):
            job_id = await self.code_of(http, account, job_id) if account else None
            if not job_id:
                return 'closed', None
        if not account:                     # a link with no account: its redirect names it
            account = await self.account_of(http, job_id)
            session = await self.open(http, self.known().get(account) or Board(account, account)) if account else None
            if session is None:
                return 'closed', dict(NOBODY)
        listed = {job['id']: job for job in self.from_widget(session, session['jobs'])}
        data = await self.job_answer(http, account, job_id)
        if job_id not in listed and (data is None or data.get('state') not in (None, 'published')):
            return 'closed', {'company': session['name'], 'companySlug': account, 'companyWebsite': session.get('website')}
        job = listed.get(job_id) or {
            'id': job_id, 'company': session['name'], 'companySlug': account, 'companyWebsite': session.get('website'),
            'title': '', 'places': [], 'dateOnly': True, 'jobUrl': SHORT_LINK.format(code=job_id),
            'applyUrl': APPLY_LINK.format(code=job_id)}
        return 'open', self.enrich(job, data) if data else job
