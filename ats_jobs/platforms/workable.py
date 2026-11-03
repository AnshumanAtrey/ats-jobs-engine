"""
Workable (apply.workable.com) jobs: each company's career page through its own JSON, and a search across every
company through Workable's public jobs feed. No key. Measured from this laptop on 2026-11-02:

- GET https://apply.workable.com/api/v1/widget/accounts/<account>: one company's career page in one answer, the
  documented jobs widget (?details=true adds descriptions, 6.6 times the size). A job in several places comes once per
  place. published_on is the date the job page gives Google (datePosted, 10 of 10 pages rendered; created_at differed
  on 8 of them; the page computes it in the reader's time zone, so a render from Los Angeles showed a day earlier).
  No search, no order, no work type beyond telecommuting. 404 "Not Found" is no such account; the old
  https://www.workable.com/api/accounts/<account> redirects here.
- GET https://apply.workable.com/api/v2/accounts/<account>/jobs/<shortcode>: one job, the call its page makes: the
  description, requirements and benefits, pay (salary_from, salary_to, currency, frequency) when shown, workplace
  (on_site, hybrid, remote), type (full, part, contract, temporary, other), departments, published, state. 404 "Job
  not found" for a job that closed and for a code that never existed alike (12 old links): both read as closed.
  (POST /api/v3/accounts/<account>/jobs, the career page's own search, pages 10 jobs at a time: not needed.)
- GET https://www.workable.com/boards/workable.xml: Workable's public feed of every job its customers list on its job
  board (help.workable.com article 4420464031767: hourly, no filters), about 860 MB (859,438,148 bytes at 17:17 UTC),
  the oldest listing first (129,639 of the first 129,721 steps), with title, company, website, city, state, country
  code, remote, job type, experience, description and the apply.workable.com/j/<shortcode> link, and no pay. A job
  comes once per place, and its entries are not always together: in the first three quarters, 3,436 of 82,471 jobs
  had entries apart, their first and last a median 30 days apart, since a place added later is listed when it is
  added. So a job's entries become one job, with every place and the first entry's date. On 6,000 jobs also read on
  their career page, that date fell on the job page's own day (published_on, its datePosted) for 95% (98.5% of those
  published since September); 3.5% were listed over a week after they were published (a median 339 days), and of
  the jobs inside a 7- or 30-day window 0.6% were published before it. With details a row takes the job page's day
  from the job's own answer. It names no hybrid or on-site.
  Cloudflare compresses it on the way (gzip about 128 MB, brotli about 131 MB) and ignores Range (200, the whole
  body). Its first byte comes after 17 to 24 s and the answer ends about 63 s after the request whatever has
  arrived: of 11 downloads from here 10 were cut (the most that arrived reached the listings of 2026-10-14, about
  three quarters) and one brotli download ended cleanly at 130.8 MB in 51 s; the reviews' 4 downloads on 2026-11-02
  and 03 were all cut (one at 63.8 s with 63.3 MB of gzip, 1.55 MB/s; the furthest reached 2026-10-22). This line
  gives 3.8 to 4.1 MB/s (Cloudflare's speed test, OVH): the plain feed came at that speed, gzip and brotli at about
  15 MB/s of XML, below it, so a datacenter line may be cut too and has to be measured. A cut feed still holds every
  open job listed before the cut (the routes below). About 9 downloads from one address in 3 minutes made Cloudflare
  refuse it for an hour (429, Retry-After 3590).
- GET https://jobs.workable.com/api/v1/jobs/<id>: one job of Workable's job board (the links people paste from
  jobs.workable.com): created, workplace, and no pay (none in 5 answers read, and its app shows none); 410 is a job
  gone (8 of 10 links from older crawls), 404 one that never was. Its search
  (/api/v1/jobs?query=...) is not used: it ranks by relevance with no date order (filters: day_range, workplace,
  employment_type, experience), 20 jobs a page, and a burst of 32 searches at once (47 a second) got this laptop's
  IPv6 address banned by Cloudflare for 24 hours on every workable.com host, career pages included (error code 1015,
  Retry-After 86,400, still counting down 5 hours later), while its IPv4 address kept answering.

Each run takes the route that fits it. A word, a place, a work type or a job type needs every company: the feed, read
once (dates have a time). When it is cut, the part that came holds every open job listed before the cut, and for
FALLBACK_SECONDS the career pages of the companies with the most matching jobs in that part add the jobs listed since
(on 1,020 accounts, 8 of 10 searches found more of the true newest 100 this way than from the biggest companies' pages,
647 against 585 in all); a page replaces its company's feed jobs only when it holds one of them, since a generic name
(Elevate, 309 of 918 teacher jobs in the feed's first three quarters) can point to another account. When no byte came,
the biggest companies' pages; the message says which. A run for the newest jobs only (maybe within a period) reads the
CAREER_PAGES biggest career pages (dates are days), and reads the feed instead when Workable refuses them before any
answered: www.workable.com kept answering while apply.workable.com refused. Named companies always read their career
pages. A hybrid or on-site search asks each candidate job's own answer, since the lists mark only remote jobs.

The feed writes a state as a code for 99% of US, Canadian, Australian and Indian jobs (TX 3,368 and Texas 20 of 35,511
US jobs; ON, NSW, KA; measured 2026-11-03 on its first three quarters), where career pages and job pages write the name,
so "Texas" found no feed job: region_name() reads each code as its name (pycountry), without accents, which the
matching does not fold.

Accounts are lower case (apply.workable.com/BIFFA answers 404, its page redirects to /oops); shortcodes are ten hex
digits in any case. Old links: <account>.workable.com/... and /<account>/jobs/<number> redirect to /<account>/j/<code>;
apply.workable.com/j/<code> redirects to the account's job, or to /oops once the job is gone. robots.txt of
apply.workable.com disallows nothing.

Limits from one address: apply.workable.com answered 3 calls a second for 2 minutes and refused about half of 4 a
second after 70 s (HTTP 429, Retry-After 0, gone within seconds). Held longer it counts: about 1,800 calls at 1.5 to
2.2 a second over 20 minutes brought refusals on every call (error code 1015, Retry-After 0) that cleared 90 s after
the calls stopped, and 100 calls at 1.3 a second 8 minutes later got the address banned from apply.workable.com for
24 hours (Retry-After 86,400, counting down; the feed and the job board kept answering it). So calls wait their turn
(PACE, 2 a second), and the first refusal (429, 403 or a challenge page) stops every later call of the run at once: the
shared retries of http.send went on into a refusal for about 20 s at up to 6 calls a second (review mock, 2026-11-03).
A 5xx or a broken connection is tried again in its turn (RETRY_S). A run makes BUDGET calls at most, about 8 minutes:
CAREER_PAGES or FALLBACK_SECONDS of career pages, one call or two per job with details (2,000 jobs with details made
3,050 calls before the budget), three per apply.workable.com/j/ link, and a company's career page once per run.
"""
import asyncio
import dataclasses
import html as htmllib
import logging
import re
import tempfile
import time
import unicodedata
import xml.etree.ElementTree as ET
import zlib
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import unquote, urlparse

import httpx

from .. import places
from ..http import STATS, BoardError
from ..inputs import JOB_TYPE_ALIASES
from ..rows import _COUNTRIES, Filters, folded_text, index, terms
from .base import Board, Platform
from .cornerstone import text_of

try:
    from pycountry import subdivisions as _SUBDIVISIONS
except ImportError:           # the actor installs pycountry with the engine; without it a state code stays a code
    _SUBDIVISIONS = None

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
NOT_ACCOUNTS = {'j', 'api', 'oops', 'cdn-cgi', 'static', 'assets', 'embed', 'boards', 'null'}   # null: a crawl artifact
ACCOUNT = re.compile(r'^[a-z0-9][a-z0-9-]{0,99}$')
CODE = re.compile(r'^[0-9a-f]{10}$', re.I)
NUMBER = re.compile(r'^\d{1,9}$')
BOARD_ID = re.compile(r'^(?:[1-9A-HJ-NP-Za-km-z]{20,24}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$')
CODE_IN_TEXT = re.compile(r'/j/([0-9a-f]{10})(?![0-9a-z])', re.I)
REGION_CODE = re.compile(r'^[A-Z0-9]{1,3}$')     # how the feed writes a state: TX, ON, NSW, KA
PAGE = 500                    # matching feed jobs handed to the engine at a time
FEED_TIMEOUT = httpx.Timeout(120.0, connect=15.0)    # its first byte came after 19 to 23 s
# Cloudflare compresses the feed on the way. gzip (128 MB) came as fast as brotli (131 MB), about 15 MB/s of XML for
# both on 2026-11-02, and needs no package
FEED_ENCODING = 'gzip'
FALLBACK_SECONDS = 120        # career pages read, biggest first, when the feed cannot be read
WORKPLACE_SECONDS = 90        # a hybrid or on-site search checks feed jobs one by one for this long at most
CAREER_PAGES = 300            # the biggest career pages a run for the newest jobs reads: 150 s at most
MONTHS = {m: i for i, m in enumerate('Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split(), 1)}
FEED_DATE = re.compile(r'(\d{1,2}) ([A-Z][a-z]{2}) (\d{4}) (\d{2}):(\d{2}):(\d{2})')
TAG = re.compile(r'<[^>]*>')
NOBODY = {'company': None, 'companySlug': None, 'companyWebsite': None}   # a gone job whose company is unknown
REFUSALS = 3                  # calls in a row that got no answer after which apply.workable.com is not asked again;
#                               one refusal (429, 403 or a challenge) counts as all three at once
PACE = 0.5                    # seconds between two calls to apply.workable.com: 2 a second (see the docstring)
RETRY_S = (1, 3, 7)           # waits before a 5xx or a broken connection is tried again, each try in its turn
BUDGET = 1000                 # calls to apply.workable.com in one run, about 8 minutes at PACE (see the docstring)
LOG = logging.getLogger('apify')
WORKPLACE = {'remote': 'remote', 'hybrid': 'hybrid', 'on_site': 'onsite'}
TYPE = {'full': 'full-time', 'part': 'part-time', 'contract': 'contract', 'temporary': 'temporary'}
PERIOD = {'year': 'year', 'month': 'month', 'week': 'week', 'day': 'day', 'hour': 'hour'}


def clean(text) -> str:
    return ' '.join(str(text or '').split())


def plain(text: str) -> str:
    """'Karnātaka' -> 'Karnataka', 'São Paulo' -> 'Sao Paulo': the matching does not fold accents, and people type
    place names without them."""
    return ''.join(ch for ch in unicodedata.normalize('NFKD', text) if not unicodedata.combining(ch))


def place(entry: dict) -> dict | None:
    """{'city': 'High Wycombe', 'region': 'England', 'country': 'United Kingdom'} -> one place. A part written twice
    (Bogotá, Bogota) is named once, and stray commas at a part's end go."""
    city, region, country = (clean(entry.get(k)).strip(' ,;') or None for k in ('city', 'region', 'country'))
    parts: dict[str, str] = {}
    for part in (city, region, country):
        if part:
            parts.setdefault(plain(part).casefold(), part)
    name = ', '.join(parts.values())
    return {'name': name, 'city': city, 'region': region, 'country': country} if name else None


@lru_cache(maxsize=4096)
def region_name(country: str, region: str) -> str:
    """A state the feed writes as a code -> its name, as career pages and job pages write it: ('US', 'TX') -> 'Texas',
    ('IN', 'KA') -> 'Karnataka'. The feed writes codes for 99% of US, Canadian, Australian and Indian jobs (2026-11-03),
    so without this "Texas" found none of them. A name, or a code pycountry does not know, stays as it is."""
    if not (_SUBDIVISIONS and REGION_CODE.match(region) and len(country) == 2):
        return region
    try:
        found = _SUBDIVISIONS.get(code=f'{country.upper()}-{region}')
    except (KeyError, LookupError):
        found = None
    return plain(found.name) if found else region


def job_type(text: str | None, experience: str | None = None) -> str | None:
    """'Full-time', 'Part-time', 'Contract', 'Temporary' -> the engine's job types; 'Other' -> None. Workable has no
    internship type: an internship is the experience level 'Internship' (957 feed entries), whatever the type says."""
    if clean(experience).lower() == 'internship':
        return 'intern'
    return JOB_TYPE_ALIASES.get(clean(text).lower())


def pay_text(low, high, currency: str | None, period: str | None) -> str | None:
    """28000, 32000, 'GBP', 'year' -> 'GBP 28,000 - 32,000 per year'. One amount when both are equal or one is
    missing."""
    shown = [v for v in (low, high) if isinstance(v, (int, float)) and v > 0]
    amounts = list(dict.fromkeys(f'{v:,.2f}'.removesuffix('.00') for v in shown))
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
    """The content encoding of a download -> a function from its bytes, in order, to plain bytes. BoardError for an
    encoding that was not asked for, so the run falls back instead of failing."""
    if encoding in ('gzip', 'x-gzip'):
        return zlib.decompressobj(16 + zlib.MAX_WBITS).decompress
    if encoding in ('', 'identity'):
        return lambda chunk: chunk
    raise BoardError(f'the feed came in an encoding that was not asked for ({encoding[:40]})')


class Stopped(BoardError):
    """apply.workable.com is not asked again this run: it refused this address, or the run made BUDGET calls."""


def refusal(response, url: str) -> str | None:
    """Why an answer is Workable's Cloudflare refusing this address, or None. A refusal is 429 (its rate limit, error
    code 1015), 403, or a challenge page (a cf-mitigated header, or HTML where the JSON API was asked)."""
    status, headers = response.status_code, response.headers
    if status in (429, 403):
        return f'HTTP {status}'
    if headers.get('cf-mitigated'):
        return f'a Cloudflare challenge, HTTP {status}'
    if status == 200 and '/api/' in url and 'text/html' in (headers.get('content-type') or ''):
        return 'a Cloudflare challenge page'
    return None


def waiting(response) -> str:
    """The wait a refusal asks for (Retry-After, in seconds), in words: ', for about 24 hours'; '' for none or short."""
    try:
        seconds = int(str(response.headers.get('retry-after') or '0').strip())
    except ValueError:
        return ''
    if seconds >= 5400:
        return f', for about {round(seconds / 3600)} hours'
    return f', for about {round(seconds / 60)} minutes' if seconds >= 90 else ''


async def fetch(http, method: str, url: str, **kwargs):
    """One request, counted in the run's traffic; call() decides what is tried again."""
    started = time.monotonic()
    response = await http.request(method, url, **kwargs)
    STATS['calls'] += 1
    STATS['seconds'] += time.monotonic() - started
    return response


def answer(response) -> dict | list:
    if response.status_code != 200:
        raise BoardError(f'HTTP {response.status_code}')
    try:
        return response.json()
    except ValueError as exc:
        raise BoardError('the answer was not JSON') from exc


def in_description(term, description: str | None) -> bool:
    """A word of the search in a description's text, as the engine matches it."""
    if not description:
        return False
    folded = description.casefold()
    if not all(token in folded for token in term.tokens):    # most descriptions fail this cheap test first
        return False
    text, words = folded_text(description)
    return term.in_text(text, words)


def matches(term, job: dict) -> bool:
    """A word in a career page job's title, or in its description when the page gave one (as a search would)."""
    return term.in_title(index(job.get('title'))) or in_description(term, job.get('description'))


class Workable(Platform):
    key = 'workable'
    name = 'Workable'
    site_word = 'career page'
    actor = 'workable-job-search'   # the Store slug: workable-jobs-search is api-co's actor (2026-11-02)
    board_example = 'https://apply.workable.com/huggingface/'
    job_example = 'https://apply.workable.com/huggingface/j/DB4D7C0EC8/'
    concurrency = 8               # calls waiting at once (15% of career page answers take 5 s); PACE keeps the rate
    server_search = True          # pages() gets the word: the feed and a career page are matched here
    never = ('team', 'offersEquity', 'expiresAt')

    def __init__(self):
        self._turn = asyncio.Lock()
        self._last = 0.0
        self._refused = 0             # calls in a row that got no answer; REFUSALS stops the run's calls
        self._refusal: str | None = None    # why Workable refused this address; it stays for the run
        self._calls = 0               # calls made to apply.workable.com this run, retries included
        self._read = 0                # career pages that answered this run
        self._told = False
        self._pages: dict[tuple, dict | None] = {}     # career page answers of this run, by (account, descriptions)
        self._known: dict[str, Board] | None = None
        self._names: dict[str, str] | None = None
        self._newest = False          # the run reads the biggest career pages for the newest jobs (search_all)
        self._fell_back = False       # those pages were refused, so the feed was read instead
        self.cfg = None

    # ---------- links and names ----------

    def read_link(self, text):
        text = text.rstrip('.,;:!?')               # a link copied from a sentence: .../j/DB4D7C0EC8. is that job
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
        coded = [i for i in range(1, len(parts) - 1) if parts[i] == 'j' and CODE.match(parts[i + 1])]
        if coded:                                  # /<account>/j/<code>, and /<account>/jobs/j/<code> from job boards
            job = parts[coded[0] + 1].upper()
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
        within a period): the CAREER_PAGES biggest career pages, about CAREER_PAGES * PACE seconds; if Workable refuses
        them before any answered, open() reads the feed instead."""
        self.cfg = cfg
        if any(places.world_query(places.tokens(p)) for p in cfg.locations):
            cfg.notes.append('Workable gives every job a country, so no job is marked worldwide: type Remote in '
                             'location to get remote jobs.')
        if cfg.search_terms or cfg.locations or cfg.work_types or cfg.job_types:
            return [EVERYONE]
        self._newest = not cfg.companies
        return boards[:CAREER_PAGES]

    # ---------- calls to apply.workable.com, one at a time ----------

    def quiet(self) -> bool:
        """Workable refused this address, or REFUSALS calls in a row got no answer."""
        return self._refusal is not None or self._refused >= REFUSALS

    def tell(self, note: str) -> None:
        """The one note of a run that stopped asking apply.workable.com."""
        if not self._told and self.cfg is not None:
            self._told = True
            self.cfg.notes.append(note)

    def ready(self) -> None:
        """Stopped when apply.workable.com is not to be asked again this run."""
        if self.quiet():
            raise Stopped('Workable stopped answering this address')
        if self._calls >= BUDGET:
            self.tell(f'This run made {BUDGET:,} calls to apply.workable.com, the most one run makes so that Workable '
                      'keeps answering, so the career pages, details and links after that were not read.')
            raise Stopped(f'the run made its {BUDGET:,} calls to apply.workable.com')

    async def call(self, http, method: str, url: str, **kwargs):
        """One call to apply.workable.com in its turn, PACE after the one before; a 5xx or a broken connection is
        tried again after RETRY_S, each try in its turn. The first refusal stops every later call of the run at once:
        Workable's Cloudflare bans an address for a day when calls go on after refusals (see the docstring). So do
        REFUSALS calls in a row that got no answer, and BUDGET calls in all."""
        last = 'no answer'
        for attempt in range(len(RETRY_S) + 1):
            async with self._turn:
                self.ready()
                wait = self._last + PACE - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self.ready()                # a refusal can have come in while this call waited
                self._last = time.monotonic()
                self._calls += 1
            try:
                response = await fetch(http, method, url, **kwargs)
            except httpx.HTTPError as exc:
                STATS['retries'][type(exc).__name__] += 1
                last = f'{type(exc).__name__}: {exc}'[:200]
            else:
                why = refusal(response, url)
                if why:                     # an answer to a call made before it does not lift it
                    self._refusal = why
                    self.tell(self.refused_note(why, waiting(response)))
                    raise Stopped(f'Workable refused this address ({why})')
                if response.status_code < 500:
                    self._refused = 0
                    return response
                STATS['retries'][str(response.status_code)] += 1
                last = f'HTTP {response.status_code}'
            if attempt < len(RETRY_S):
                await asyncio.sleep(RETRY_S[attempt])
        self._refused += 1
        if self._refused >= REFUSALS:
            self.tell(f'Workable did not answer {REFUSALS} calls in a row ({last}), so the run stopped asking '
                      'apply.workable.com: the career pages, details and links after that were not read.')
        raise BoardError(last)

    def refused_note(self, why: str, wait: str) -> str:
        calls = f'{self._calls:,} call' + ('' if self._calls == 1 else 's')
        note = (f'Workable refused this address ({why}) after {calls} to apply.workable.com{wait}, so the run stopped '
                'asking it: the career pages, details and links after that were not read.')
        if self._newest and self._read:     # with none read, open() reads the feed instead and says so
            return note + (' Type a job title, a place, remote or a job type to search Workable\'s jobs feed, which '
                           'comes from www.workable.com, or run again later.')
        return note + ' A refusal can last up to a day.'

    async def career_page(self, http, account: str, descriptions: bool = False) -> dict | None:
        """The widget answer, or None when there is no such account. Asked once a run: links to several jobs of one
        company read its page once."""
        if not ACCOUNT.match(account):
            return None
        if (account, descriptions) in self._pages:
            return self._pages[(account, descriptions)]
        url = WIDGET.format(account=account) + ('?details=true' if descriptions else '')
        response = await self.call(http, 'GET', url)
        data = None
        if response.status_code != 404:
            data = answer(response)
            if not isinstance(data, dict) or not isinstance(data.get('jobs'), list):
                raise BoardError('the career page answer had no job list')
            self._read += 1
        self._pages[(account, descriptions)] = data
        return data

    # ---------- one company's career page ----------

    async def open(self, http, board):
        if board.key == EVERYONE.key:
            return {'everyone': True}
        wanted = bool(self.cfg and self.cfg.search_descriptions and self.cfg.search_terms)
        try:
            data = await self.career_page(http, board.key, descriptions=wanted)
        except Stopped:
            # a run for the newest jobs that Workable refused before any career page answered reads the feed once:
            # the feed comes from www.workable.com, which kept answering while apply.workable.com refused
            if not self._newest or self._read or self._fell_back or not self.quiet():
                raise
            self._fell_back = True
            if self.cfg is not None:
                self.cfg.notes.append("No career page had answered, so these are the newest jobs of Workable's jobs "
                                      'feed, from every company.')
            return {'everyone': True}
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
            jobs = await self.every_company(http)
            for i in range(0, len(jobs), PAGE):
                yield jobs[i:i + PAGE]
            return
        jobs = self.from_widget(session, session['jobs'])
        if term:        # a career page has no search: the word is matched here, as the job board would have
            wanted = terms([term])
            jobs = [j for j in jobs if wanted and matches(wanted[0], j)]
        jobs = await self.workplaces(http, jobs, self.wanted(matched=term is not None))
        if jobs:
            yield jobs

    # ---------- every company: Workable's jobs feed ----------

    def wanted(self, matched: bool = False):
        """The settings -> a test of one job: every filter but the work type, which Workable's lists state only for
        remote jobs (workplaces() asks the rest). matched: the words were already found here, in the title or in a
        description the job no longer carries."""
        if self.cfg is None:            # no search: nothing to filter
            return lambda job: True
        others = Filters(dataclasses.replace(self.cfg, work_types=[]))
        now = datetime.now(timezone.utc)
        return lambda job: others.passes(job, now, server_match=matched)

    def from_feed(self, fields: dict) -> dict | None:
        """One <job> of the feed, as {tag: text} -> a job with that entry's one place; None without a shortcode or a
        title. No description: details() reads the job's own answer, which has all of it."""
        code, title = fields.get('referencenumber', '').upper(), clean(fields.get('title'))
        if not CODE.match(code) or not title:
            return None
        city, region, country = (clean(fields.get(k)) or None for k in ('city', 'state', 'country'))
        region = region_name(country, region) if region and country else region
        spot = place({'city': city, 'region': region, 'country': country_name(country) if country else None})
        company = clean(fields.get('company')) or None
        return {
            'id': code,
            'company': company,
            'companySlug': self.names().get(company.casefold()) if company else None,
            'companyWebsite': clean(fields.get('website')) or None,
            'title': title,
            'places': [spot] if spot else [],
            'workType': 'remote' if fields.get('remote', '').strip().lower() == 'true' else None,
            'employmentType': job_type(fields.get('jobtype'), fields.get('experience')),
            'postedAt': feed_date(fields.get('date')),
            'dateOnly': False,
            'jobUrl': clean(fields.get('url')) or SHORT_LINK.format(code=code),
            'applyUrl': APPLY_LINK.format(code=code),
        }

    def reader(self):
        """A parser fed the feed's bytes in order: feed(chunk) for each, then close() gives every wanted job once, and
        last() the date of the last entry read. close(whole=False) takes a feed that was cut as far as it came. The
        feed lists a job once per place, and not always one entry after the other (3,436 of 82,471 jobs on 2026-11-02,
        a median of 30 days between their first and last entry): all of a job's entries become one job with every
        place and the first entry's date. An entry whose title, job type or (when searched) description cannot match is
        dropped as it passes, so no description is held. close() raises ET.ParseError when the feed stopped early."""
        parser = ET.XMLPullParser(events=('start', 'end'))
        root: list = []
        jobs: dict[str, dict] = {}
        last: list[str | None] = [None]
        cfg = self.cfg
        words, excludes = (terms(cfg.search_terms), terms(cfg.exclude_terms)) if cfg else ([], [])
        kinds, texts = (set(cfg.job_types), cfg.search_descriptions) if cfg else (set(), False)

        def wanted(job: dict, html: str | None) -> bool:
            title = index(job['title'])
            if any(t.in_title(title) for t in excludes) or (kinds and job['employmentType'] not in kinds):
                return False
            if not words or any(t.in_title(title) for t in words):
                return True
            text = words_of(html) if texts else None
            return bool(text) and any(in_description(t, text) for t in words)

        def add(held: dict, job: dict) -> None:
            names = {p['name'] for p in held['places']}
            held['places'] += [p for p in job['places'] if p['name'] not in names]
            if job['postedAt'] and (not held['postedAt'] or job['postedAt'] < held['postedAt']):
                held['postedAt'] = job['postedAt']
            held['workType'] = held['workType'] or job['workType']

        def read() -> None:
            for event, elem in parser.read_events():
                if event == 'start':
                    if not root:
                        root.append(elem)
                elif elem.tag == 'job':
                    fields = {child.tag: (child.text or '').strip() for child in elem}
                    job = self.from_feed(fields)
                    last[0] = job['postedAt'] if job and job['postedAt'] else last[0]
                    if job and job['id'] in jobs:
                        add(jobs[job['id']], job)
                    elif job and wanted(job, fields.get('description')):
                        jobs[job['id']] = job
            if root:
                root[0].clear()         # what was read is kept or dropped: memory stays flat

        def feed(chunk: bytes) -> None:
            parser.feed(chunk)
            read()

        def close(whole: bool = True) -> list[dict]:
            if whole:
                parser.close()
                read()
            for job in jobs.values():
                job['location'] = job['places'][0]['name'] if job['places'] else None
                job['otherLocations'] = [p['name'] for p in job['places'][1:]]
            return list(jobs.values())
        return feed, close, lambda: last[0]

    async def download(self, http):
        """The feed as it travels, in a temporary file, its encoding, and whether it all arrived. It is read once the
        answer has ended: Workable ends it about 63 s after the request, so nothing may slow the download down. A
        failure before the first byte is tried once more; a cut download is not (the same line cut it), nor a 429
        (Workable refuses an address for an hour)."""
        for attempt in range(2):
            spool, started = tempfile.TemporaryFile(), False
            try:
                async with http.stream('GET', FEED_URL, timeout=FEED_TIMEOUT,
                                       headers={'Accept-Encoding': FEED_ENCODING}) as response:
                    STATS['calls'] += 1
                    if response.status_code == 429:
                        raise BoardError('HTTP 429: Workable allows a few downloads of its feed an hour from one '
                                         'address')
                    if response.status_code != 200:
                        raise httpx.HTTPStatusError(f'HTTP {response.status_code}', request=response.request,
                                                    response=response)
                    encoding = response.headers.get('content-encoding', 'identity').strip().lower()
                    async for chunk in response.aiter_raw(1 << 18):
                        started = True
                        spool.write(chunk)
                spool.seek(0)
                return spool, encoding, True
            except (httpx.HTTPError, OSError) as exc:
                STATS['retries'][type(exc).__name__] += 1
                if started:                     # cut: the part that arrived holds every job listed before the cut
                    spool.seek(0)
                    return spool, encoding, False
                spool.close()
                if attempt:
                    raise BoardError(f'{type(exc).__name__}: {exc}'[:200]) from exc
                await asyncio.sleep(3)
            except BaseException:
                spool.close()
                raise
        raise BoardError('no answer')

    async def feed_jobs(self, http, keep) -> tuple[list[dict], str | None]:
        """Every job of the feed that keep() wants, and, when the download was cut, the date of the last listing that
        arrived (None when all of it did). The feed lists the oldest first, so a cut feed holds every open job listed
        before that date and none listed after it."""
        spool, encoding, whole = await self.download(http)
        with spool:
            decode, (feed, close, last) = decoder(encoding), self.reader()
            try:
                while chunk := spool.read(1 << 20):
                    feed(decode(chunk))
                    await asyncio.sleep(0)          # the engine's progress line runs between chunks
                jobs = close(whole)
            except (ET.ParseError, zlib.error):
                jobs, whole = close(False), False
        return [job for job in jobs if keep(job)], None if whole else (last() or '')[:10] or 'its first day'

    async def career_pages(self, http, partial: list[dict], cut: str | None, reason: str) -> list[dict]:
        """When the feed did not all arrive: the jobs of the part that did (all open, listed before the cut), and for
        FALLBACK_SECONDS the career pages of the companies with the most matching jobs in that part, then of the
        biggest. A career page's jobs replace that company's feed entries when the page holds at least one of them,
        which proves it is the company's own account: the page has the published date and only jobs still open. A page
        that holds none of them (a company whose name points to another account) leaves the feed's jobs in. BoardError
        when nothing at all could be read."""
        keep, known = self.wanted(), self.known()
        counts = Counter(job['companySlug'] for job in partial if job.get('companySlug') in known)
        order = [key for key, _ in counts.most_common()] + [key for key in known if key not in counts]
        listed_in_feed: dict[str, set[str]] = {}
        for job in partial:
            listed_in_feed.setdefault(job.get('companySlug'), set()).add(job['id'])
        started, read, same, found = time.monotonic(), set(), set(), {}
        for key in order:
            if time.monotonic() - started > FALLBACK_SECONDS:
                break
            try:
                session = await self.open(http, known[key])
            except Stopped:
                break                       # refused or out of calls: the rest would fail at once
            except BoardError:
                continue
            if session:
                read.add(key)
                page = self.from_widget(session, session['jobs'])
                if listed_in_feed.get(key, set()) & {job['id'] for job in page}:
                    same.add(key)
                found.update((job['id'], job) for job in page if keep(job))
        if not read and not partial:
            raise BoardError(f'its jobs feed could not be read ({reason}) and no career page answered')
        jobs = list(found.values()) + [job for job in partial if job['id'] not in found
                                       and job.get('companySlug') not in same]
        pages = f'{len(read):,} compan{"y" if len(read) == 1 else "ies"}'
        self.incomplete = True              # OUTPUT says partial: the newest jobs of most companies are missing
        if not cut:
            note = (f"Workable's jobs feed could not be read ({reason}), so these jobs come from the career pages of "
                    f"the {pages} biggest on our list of {len(known):,}. Other companies' jobs are not in this run.")
        elif read:
            which = (f'{pages} with the most matching jobs' if counts
                     else f'{len(read):,} biggest ' + pages.split(' ')[1])
            note = (f"Workable's jobs feed was cut after its listings of {cut}, so the jobs listed since then come "
                    f'only from the career pages of the {which} (of {len(known):,} on our list). The newest jobs of '
                    'other companies are not in this run.')
        else:
            note = (f"Workable's jobs feed was cut after its listings of {cut} and no career page answered, so these "
                    'are the jobs listed before then. Newer jobs are not in this run.')
        LOG.warning(note)
        if self.cfg is not None:
            self.cfg.notes.append(note)
        return jobs

    async def every_company(self, http) -> list[dict]:
        keep = self.wanted(matched=True)
        try:
            jobs, cut = await self.feed_jobs(http, keep)
        except BoardError as exc:
            jobs, cut, reason = [], None, str(exc)
        else:
            if cut is None:
                return await self.workplaces(http, jobs, keep)
            reason = 'the download was cut before its end'
        jobs = await self.career_pages(http, jobs, cut, reason)
        return await self.workplaces(http, jobs, lambda job: True)     # each was kept by its own test already

    async def workplaces(self, http, jobs: list[dict], keep) -> list[dict]:
        """Workable's lists (the feed and the career pages) mark remote jobs and nothing else. For a hybrid or on-site
        search, the jobs that keep() wants and are not marked remote get their work type from their own answer, newest
        first, until maxJobs of them match or WORKPLACE_SECONDS have passed."""
        cfg = self.cfg
        wanted = set(cfg.work_types) if cfg else set()
        if not wanted - {'remote'}:
            return jobs
        candidates = sorted((j for j in jobs if j.get('workType') != 'remote' and keep(j)),
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
            asked = ' or '.join(sorted(wanted - {'remote'}))
            note = (f"Workable's lists mark only remote jobs, so the {checked:,} newest of {len(candidates):,} "
                    f'matching jobs not marked remote were checked one by one for {asked}.')
            if not any(n.startswith("Workable's lists mark only remote jobs") for n in cfg.notes):
                cfg.notes.append(note)          # once a run, though each named company is checked on its own
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
            'workType': WORKPLACE.get(data.get('workplace'))
            or ('remote' if data.get('remote') is True else job.get('workType')),
            'employmentType': job.get('employmentType') if job.get('employmentType') == 'intern'
            else TYPE.get(data.get('type')) or job.get('employmentType'),
            'department': departments or job.get('department'),
            'reference': clean(data.get('code')) or job.get('reference'),
            **pay(data.get('salary_from'), data.get('salary_to'), data.get('salary_currency_iso_code'),
                  data.get('salary_frequency')),
            # the job page's own day (its datePosted): a feed job's listing time is a later day on 5% of jobs
            'postedAt': clean(data.get('published'))[:10] or job.get('postedAt'),
            'dateOnly': True if clean(data.get('published'))[:10] else job.get('dateOnly'),
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
        try:
            account = job.get('companySlug') or (session or {}).get('account') or await self.account_of(http, job['id'])
            data = await self.job_answer(http, account, job['id']) if account else None
        except Stopped:
            return job      # refused or out of calls: the job as listed, charged as a job; a note says why
        return self.enrich(job, data) if data else job

    async def lookup(self, http, session, job_id):
        if BOARD_ID.match(job_id) and not CODE.match(job_id):     # a jobs.workable.com link: 410 is gone, 404 never was
            response = await self.call(http, 'GET', BOARD_JOB.format(id=job_id))   # Workable's one Cloudflare
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
            return 'closed', {'company': session['name'], 'companySlug': account,
                              'companyWebsite': session.get('website')}
        job = listed.get(job_id) or {
            'id': job_id, 'company': session['name'], 'companySlug': account, 'companyWebsite': session.get('website'),
            'title': '', 'places': [], 'dateOnly': True, 'jobUrl': SHORT_LINK.format(code=job_id),
            'applyUrl': APPLY_LINK.format(code=job_id)}
        return 'open', self.enrich(job, data) if data else job
