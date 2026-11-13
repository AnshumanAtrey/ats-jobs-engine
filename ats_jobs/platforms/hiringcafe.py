"""
HiringCafe (hiringcafe.com; hiring.cafe redirects there), a search engine of jobs read straight from employers' own
career sites and applicant tracking systems: 6,161,588 open jobs at 185,865 companies on 2026-11-10 (its own count).
Read through the JSON its own search page loads: no login, no key, no browser. Measured from this laptop (a home
address in India) on 2026-11-10:

- GET https://hiringcafe.com/_next/data/<build>/classic.json?searchState=<json>&page=<n>: the search page's own data,
  the site's search as JSON. A page is 40 employers (a group per employer's board: Siemens' three companies on one
  Workday site are one), newest first with sortBy date, each with its newest matching jobs, at most 4 of them; a group
  never comes back on a later page (the site's cards show the same 4 and a "View all jobs" link). About 130 jobs and
  1 MB a page (100 KB gzipped), 1.5 to 3 s. Pages 0 to 249 answer and page 250 is empty, so one search reaches 10,000
  employers, about 33,000 jobs (the newest 12 hours of the whole site). <build> is the deployment's id, in every page's
  __NEXT_DATA__ (/talent-network, 64 KB, is the smallest page found); after a deployment the old one answers 404.
- The same address with company=<base64 of source___board___name___website>, as "View all jobs" writes it: one
  employer board's every job, 40 a page, newest first, inside the same searchState (OpenAI's Ashby board: 778 jobs, 328
  with engineer in the title). So an employer shown with 4 waits in a queue and is read once the newest maxJobs are
  known and its 4th job is still newer than them (every match, and the end of the search, read them all): a "full stack
  engineer" search of 48 hours found 108 jobs this way where the plain search missed 5 titles a broader search showed.
  Opening them all on the first page cost a 2,000-job run 16 calls before its first page and met the rate limit.
- searchState: jobTitleQuery is the job title alone, in Elasticsearch's query syntax: "python" AND "developer" matched
  598 titles, 98% holding both words, where python developer unquoted matched 26,496 holding either; plurals are not
  folded ("nurses" matched 12 titles, "nurse" thousands) and Front-End is two words, so title_query() spells each word
  every way the engine's own match reads it. searchQuery is the search box (titles, descriptions, company text: 126,712
  for python developer). locations: the site's own place objects (GET /api/searchLocation?query=: cities, states,
  countries and continents with coordinates; it knows Bengaluru and Gurugram, not Bangalore, Gurgaon, NYC or TX); a
  country or continent can be built here, with its name (a code alone: "Failed to load jobs"). dateFetchedPastNDays is a
  bucket number (2 the last 24 hours, 4 three days, 14 a week, 61 a month, 121 three months, -1 every date; an unknown
  number reads as every date, and three months when it is missing) or a range {"from": "2026-11-08", "to":
  "2026-11-08"} of whole UTC days of the posting date (16 data analyst jobs that day, every one dated that day).
  defaultToUserLocation false, or the server limits the search to the caller's country by Cloudflare's reading of the
  address (India here). Also workplaceTypes, commitmentTypes, seniorityLevel, roleYoeRange (the least years asked,
  0 to 20), companySizeRanges, companyNames (the company and the companies HiringCafe relates to it: Stripe brought
  Paystack and Lumanity), benefitsAndPerks (visa_sponsorship), published pay (maxCompensationLowEnd: the top of the
  range reaches it, 1,054 product manager jobs at $150,000; minCompensationLowEnd: ranges that start there, 512; with
  frequency Any and calcFrequency Yearly, pay listed by the hour or month counts by its yearly sum) and sortBy (date:
  newest first). A country object is matched by its code (short_name), not its name.
- GET /api/job-description?requisitionId=<id>: one job's description as HTML (2.8 KB, 1.4 KB gzipped).
- GET /_next/data/<build>/job/<id>.json: a redirect to the job's own address, /job/<title-company-place>-<id>, whose
  data is the job with its description, views and saves; {"notFound": true} with 404 when there is no such job. No job
  read in search answers was marked expired (475 of 475), so a job HiringCafe removed most likely reads as not found.

Cloudflare in front challenges a caller (403, or 429 past its rate, with cf-mitigated: challenge):
- by TLS: Python's own TLS gets the challenge on every page and data route (search-totals, searchLocation and the MCP
  server answer it); curl_cffi's Chrome passes, and only with Chrome's headers (a bare request with Chrome's TLS got 403).
- by address: 8 of 8 Tor exits got it even with Chrome's TLS and headers, while /api/mcp, /api/search-totals,
  /api/searchLocation and /api/getCountry answered them. abotapi/hiring-cafe-scraper's listing (2026-11-10) says
  datacenter addresses are challenged and keeps Apify's residential proxy on, so a run starts direct and moves at the
  first challenge to the proxy the user set, else (on Apify) to Apify Proxy's RESIDENTIAL group, a fresh address for
  each challenge. About 700 bytes a job on the wire: $0.0056 per 1,000 jobs at Apify's $8 per GB.
  Residential addresses are challenged too, by country. On 2026-11-13 Cloudflare challenged 13 of 13 fresh addresses
  in GB and 13 of 13 in IN; of any country, 56 of about 62, and two runs failed after 13 in a row (one a user's daily
  task); in the US, 23 of about 31 over 6 runs, at most 7 in a row. So the automatic proxy is RESIDENTIAL in the US
  (PROXY_COUNTRY), and a run gives up only after ROTATIONS challenged addresses in a row. HiringCafe's MCP servers for
  AI assistants (/api/mcp, /api/chatgpt/mcp) answer any address, even Python's own TLS, but give at most 25 jobs a
  search, with no paging and no employer apply link.
- by rate: one address got 40 calls in a row (13 a second, after 5 quiet minutes) and then a challenge on every call,
  the search too, for 5 to 6 minutes. At one call every 2 s a 429 came after about 80 calls, and after each wait (1 to 2
  minutes) again after about 25; at one every 5 s, on an address busy minutes before, after about 70; 100 calls 10 s
  apart met none. So direct calls wait GAP_S, SLOW_GAP_S once a 429 was waited out (WAITS), and through a proxy the run
  takes a fresh address every CALLS_PER_ADDRESS calls.

A job: HiringCafe's AI reading of the posting (v5_processed_job_data: places, remote or office, job types, seniority,
the least years of experience asked, pay in every period, tools, requirements, visa sponsorship), the company as it
enriched it (headcount, industries, founding year, funding, headquarters) and the employer's own apply link with the
applicant tracking system it came from. Pay is given only when the employer published it (is_compensation_transparent:
66 of 475 jobs read, the 66 that had amounts). estimated_publish_date is the posting time; whole days come at midnight
(Zoho Recruit, Workday, Phenom) or noon (TalentBrew) UTC, so those are dated by the day. HiringCafe folds copies of one
job itself (dedup_alternates); one employer's two boards can still list the same job twice (T. Rowe Price), which the
engine keeps once. Its job types are the engine's five and Volunteer (Platform.job_types; Seasonal reads as temporary).

A place typed with the job title ("nurse in California") is read by the cities and states HiringCafe's newest jobs name
(data/hiringcafe_places.tsv, rebuilt by scripts/refresh_hiringcafe.py; Platform.place_names).
"""
import asyncio
import base64
import json
import math
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from importlib import resources
from urllib.parse import parse_qs, unquote, urlparse

from .. import places as regions
from ..http import STATS, BoardError
from ..inputs import IS_URL, _clean, _items, _links_whole
from ..rows import WORD, Filters, classify_place, fold, plain, state_names, words
from .base import Board, Platform
from .cornerstone import text_of
from .workable import country_name, pay_text

ORIGIN = 'https://hiringcafe.com'
HOSTS = frozenset({'hiringcafe.com', 'www.hiringcafe.com', 'hiring.cafe', 'www.hiring.cafe'})
SEARCH = ORIGIN + '/_next/data/{build}/classic.json'
JOB = ORIGIN + '/_next/data/{build}/job/{id}.json'
DESCRIPTION = ORIGIN + '/api/job-description'
PLACES = ORIGIN + '/api/searchLocation'
BUILD_PAGE = ORIGIN + '/talent-network'   # the smallest page found whose __NEXT_DATA__ names the build (64 KB)
EVERYONE = Board('hiringcafe', 'HiringCafe', ORIGIN)      # every job on HiringCafe; also the board of every job link
LAST_PAGE = 249                           # page 250 answers empty: 10,000 employers a search
PER_GROUP = 4                             # an employer's jobs in one search
GAP_S = 2.0                               # seconds between calls from one address (40 in a row at once drew a 429)
SLOW_GAP_S = 10.0                         # after a 429 without a proxy: at 2 s blocks then came every 25 calls or so,
                                          # and 100 calls 10 s apart met none (2026-11-10)
PROXY_GAP_S = 0.5                         # through a proxy, where the address changes every CALLS_PER_ADDRESS calls
CALLS_PER_ADDRESS = 30
PROXY_COUNTRY = 'US'                      # Apify Proxy's residential addresses Cloudflare challenges least (2026-11-13)
ROTATIONS = 60                            # fresh proxy addresses challenged in a row before a run gives up: about 15 KB
                                          # ($0.0001) and a second each
WAITS = (60, 120, 180)                    # without a proxy, seconds a 429 is waited out (the block lasted 5 to 6 minutes)
TRIES = 6                                 # tries of one call on network errors, and on server trouble, each its own count
EXPAND_PAGES = 25                         # pages of one employer's own list a search reads: 1,000 jobs
MAX_PLACES = 40                           # place objects one search sends
BARE_PLACE_JOBS = 40                      # jobs of the place census naming a city or state for it to read alone at
                                          # an entry's end: 0.3% of 14,134, as Welcome to the Jungle's 300 of 84,831
YEARS_CAP = 20                            # the most years HiringCafe's experience filter takes (20 is 20 or more)
SPLIT_MIN = 7                             # a typed word this long is also looked for split in two (frontend, Front-End)
MAX_QUERY = 3000                          # characters of a title query; a longer one leaves out the split readings
BUILD_ID = re.compile(r'"buildId":"([^"]+)"')
REQ_ID = re.compile(r'^[a-z0-9]{16}$')
JOB_PATH = re.compile(r'^/(?:job|viewjob|req)/(?:[^/]*-)?([a-z0-9]{16})/?$')
WORK_TYPE = {'Remote': 'remote', 'Hybrid': 'hybrid', 'Onsite': 'onsite', 'Field': 'onsite'}
SITE_WORK_TYPES = {'remote': ['Remote'], 'hybrid': ['Hybrid'], 'onsite': ['Onsite', 'Field']}
JOB_TYPE = {'Full Time': 'full-time', 'Part Time': 'part-time', 'Contract': 'contract', 'Internship': 'intern',
            'Temporary': 'temporary', 'Seasonal': 'temporary', 'Volunteer': 'volunteer'}
SITE_JOB_TYPES = {'full-time': ['Full Time'], 'part-time': ['Part Time'], 'contract': ['Contract'],
                  'intern': ['Internship'], 'temporary': ['Temporary', 'Seasonal'], 'volunteer': ['Volunteer']}
SPECIFIC = ('intern', 'contract', 'temporary', 'part-time', 'volunteer', 'full-time')   # one type for a job of several
PERIODS = {'Yearly': 'year', 'Monthly': 'month', 'Bi-Weekly': 'two weeks', 'Weekly': 'week', 'Daily': 'day',
           'Hourly': 'hour'}
PAY_KEYS = {'Yearly': 'yearly', 'Monthly': 'monthly', 'Bi-Weekly': 'bi-weekly', 'Weekly': 'weekly', 'Daily': 'daily',
            'Hourly': 'hourly'}
SENIORITY = {'no-experience': 'No Prior Experience Required', 'entry': 'Entry Level', 'mid': 'Mid Level',
             'senior': 'Senior Level'}
SENIORITY_WORDS = {'no experience': 'no-experience', 'no prior experience': 'no-experience',
                   'no prior experience required': 'no-experience', 'none': 'no-experience', 'fresher': 'no-experience',
                   'entry': 'entry', 'entry level': 'entry', 'junior': 'entry', 'graduate': 'entry',
                   'mid': 'mid', 'mid level': 'mid', 'middle': 'mid', 'intermediate': 'mid',
                   'senior': 'senior', 'senior level': 'senior', 'lead': 'senior', 'staff': 'senior'}
COMPANY_SIZES = ((1, 10), (11, 50), (51, 200), (201, 500), (501, 1000), (1001, 2000), (2001, 5000), (5001, 10000),
                 (10001, None))
# The continents of HiringCafe's place list, by the countries of UN M49 (places.py) each one holds. The search matches the
# place's own name, which for Oceania is Australia ('Australia / Oceania' is its label: a place named so found nothing).
CONTINENTS = {
    ('Europe', 'Europe'): regions.UN['EUROPE'],
    ('Asia', 'Asia'): regions.UN['ASIA'],
    ('Africa', 'Africa'): regions.UN['AFRICA'],
    ('North America', 'North America'): regions.UN['NORTHERN_AMERICA'] | regions.UN['CENTRAL_AMERICA'] | regions.UN['CARIBBEAN'],
    ('South America', 'South America'): regions.UN['SOUTH_AMERICA'],
    ('Australia', 'Australia / Oceania'): regions.UN['OCEANIA'],
}


def parse_link(text: str):
    """urlparse, or None for text it cannot read (a stray bracket reads as a broken IPv6 address)."""
    try:
        return urlparse(text if IS_URL.match(text) else f'https://{text}')
    except ValueError:
        return None


def closed(text: str) -> str:
    """JSON cut off at its end -> the same with the string and brackets it left open closed again:
    '{"a": ["b' -> '{"a": ["b"]}'."""
    stack, quoted, escaped = [], False, False
    for ch in text:
        if quoted:
            escaped, quoted = (not escaped and ch == '\\'), quoted and (escaped or ch != '"')
        elif ch == '"':
            quoted = True
        elif ch in '[{':
            stack.append(']' if ch == '[' else '}')
        elif ch in ']}' and stack:
            stack.pop()
    return text + ('"' if quoted else '') + ''.join(reversed(stack))


class Refused(BoardError):
    """Cloudflare challenged every address the run could use, so HiringCafe was not read (further)."""


def employer_key(hit: dict) -> str | None:
    """The company= of one employer's own list, as the site's "View all jobs" writes it: base64 of
    source___board___name___website."""
    v = hit.get('v5_processed_job_data') or {}
    if not hit.get('source') or not hit.get('board_token'):
        return None
    text = f"{hit['source']}___{hit['board_token']}___{v.get('company_name') or 'Unknown Company'}___{v.get('company_website') or 'N/A'}"
    return base64.b64encode(text.encode('utf-8')).decode('ascii')


def challenged(response) -> bool:
    """Cloudflare's answer instead of the page: its challenge, or a refusal in its name."""
    if response.status_code not in (403, 429, 503):
        return False
    return (response.headers.get('cf-mitigated') or '').lower() == 'challenge' or response.status_code in (403, 429)


def clean(text) -> str:
    return ' '.join(str(text or '').split())


def iso(value) -> str | None:
    """'2026-11-10T09:23:41.194Z' -> the same, checked; anything else -> None."""
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return value


def day_only(value: str | None) -> bool:
    """A posting time at exactly midnight or noon UTC is a day: the sources that give only one are dated so."""
    return bool(value) and value[10:23] in ('T00:00:00.000', 'T12:00:00.000')


def website(domain) -> str | None:
    text = clean(domain).lower()
    if not text or text in ('n/a', 'none'):
        return None
    return text if text.startswith(('http://', 'https://')) else f'https://{text}'


def number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def years(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


@lru_cache(maxsize=1)
def place_names() -> tuple[frozenset[str], frozenset[str]]:
    """The cities and states HiringCafe's jobs name (data/hiringcafe_places.tsv), in places.key form with and without
    accents: (every one, read after in, at or near; those BARE_PLACE_JOBS jobs or more name, read alone at the end of a
    search entry too, since a small town's name can be a word of a job title)."""
    every, major = set(), set()
    text = resources.files('ats_jobs').joinpath('data/hiringcafe_places.tsv').read_text(encoding='utf-8')
    for line in text.splitlines():
        parts = line.split('\t')
        if line.startswith('#') or len(parts) < 4 or parts[1] not in ('city', 'state') or not parts[3].isdigit():
            continue
        keys = {regions.key(parts[0]), regions.key(plain(parts[0]))} - {''}
        every |= keys
        if int(parts[3]) >= BARE_PLACE_JOBS:
            major |= keys
    return frozenset(every), frozenset(major)


def place_entry(text: str) -> dict | None:
    """'Bengaluru, Karnataka, IN' -> {'name': 'Bengaluru, Karnataka, India', 'city': ..., 'region': ..., 'country':
    'India'}; 'Karnataka, IN' -> the state; 'IN' -> the country."""
    parts = [p.strip() for p in (text or '').split(',') if p.strip()]
    if not parts:
        return None
    code = parts[-1] if len(parts[-1]) == 2 and parts[-1].isalpha() and parts[-1].isupper() else None
    country = country_name(code) if code else None
    head = parts[:-1] if code else parts
    name = ', '.join(head + ([country] if country else []))
    entry = {'name': name, 'country': country or code}
    if len(head) >= 2:
        entry.update({'city': head[0], 'region': head[1]})
    elif len(head) == 1 and code:
        entry['region'] = head[0]
    return entry


class Pace:
    """Calls in their turn: gap seconds apart; a hold makes every call wait."""

    def __init__(self):
        self.next = 0.0
        self.lock = asyncio.Lock()

    async def turn(self, gap: float) -> None:
        async with self.lock:
            wait = self.next - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self.next = time.monotonic() + gap

    def hold(self, seconds: float) -> None:
        self.next = max(self.next, time.monotonic() + seconds)


class Connection:
    """The run's calls to hiringcafe.com, through curl_cffi's Chrome (TLS, HTTP/2 and headers): Cloudflare challenges
    anything else. From the first call through the proxy the user set, else on Apify through Apify Proxy's RESIDENTIAL
    group (Cloudflare challenges Apify's own addresses), else direct (a laptop). At a challenge a direct run moves to a
    proxy when it has one, and a run on a proxy takes a fresh address with a fresh session, ROTATIONS times at most; it
    also takes one every CALLS_PER_ADDRESS calls, before Cloudflare's rate is reached. With no proxy a 429 is waited out
    (WAITS) and the calls go on SLOW_GAP_S apart, and a 403 ends the reading, with a note."""

    def __init__(self, platform):
        self.platform = platform
        self.session = None
        self.proxy_input = None       # the run's proxyConfiguration, when it names a proxy
        self.proxy = None             # its ProxyConfiguration, or Apify's RESIDENTIAL group
        self.proxy_ready = False      # the proxy was set up (or found missing) once
        self.proxy_url: str | None = None      # the address in use; None: direct
        self.address_calls = 0
        self.rotations = 0            # fresh addresses taken after a challenge
        self.waited = 0               # 429s waited out without a proxy
        self.pace = Pace()
        self.gap = GAP_S              # seconds between direct calls, SLOW_GAP_S once HiringCafe asked to slow down
        self.sleep = asyncio.sleep    # the wait between tries (instant in the tests)
        self.build_id: str | None = None
        self.counts = {'calls': 0, 'challenges': 0, 'proxyAddresses': 0}
        self.retired: list = []       # the sessions of earlier addresses, closed at the end: a call may still be on one

    def client(self):
        if self.session is None:
            self.session = self.new_session()
        return self.session

    @staticmethod
    def new_session():
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError as exc:
            raise BoardError('curl_cffi is not installed, and HiringCafe answers only a browser\'s TLS') from exc
        return AsyncSession(impersonate='chrome', timeout=30)

    @staticmethod
    def at_home() -> bool:
        from apify import Actor
        return Actor.is_at_home()

    async def close(self) -> None:
        for session in [*self.retired, self.session]:
            if session is not None:
                try:
                    await session.close()
                except Exception:  # noqa: BLE001
                    pass
        self.retired, self.session = [], None

    async def setup_proxy(self, automatic: bool) -> bool:
        """The proxy to move to: the user's, or Apify's RESIDENTIAL group in PROXY_COUNTRY when automatic and the run is
        on Apify."""
        if not self.proxy_ready:
            self.proxy_ready = True
            from apify import Actor
            try:
                if self.proxy_input:
                    self.proxy = await Actor.create_proxy_configuration(actor_proxy_input=self.proxy_input)
                elif automatic and self.at_home():
                    self.proxy = await Actor.create_proxy_configuration(groups=['RESIDENTIAL'],
                                                                        country_code=PROXY_COUNTRY)
            except Exception as exc:  # noqa: BLE001  a proxy the platform cannot use (no access, a typo)
                why = str(exc).splitlines()[0][:160] if str(exc) else type(exc).__name__
                self.platform.note(f'The proxy could not be used ({why}), so the run kept its own address.', 'proxy')
                self.proxy = None
        return self.proxy is not None

    async def rotate(self) -> None:
        """A fresh proxy address with a fresh session, so Cloudflare's cookies stay with the address that got them: when
        one session went through every address, 13 fresh residential addresses in a row were challenged and a 2,000-job
        run stopped at 1,985 (run XfSvA5FkrUJfHSdSD, 2026-11-10)."""
        if self.session is not None:
            self.retired.append(self.session)
            self.session = None
        self.proxy_url = await self.proxy.new_url(session_id=f'hc_{secrets.token_hex(5)}')
        self.address_calls = 0
        self.counts['proxyAddresses'] += 1

    async def get(self, url: str, params=None, *, expect: tuple = (200,)):
        """One GET in its turn -> the response (any status but Cloudflare's). Refused when no address gets through.
        Network errors, server trouble and challenged addresses are each counted on their own: one shared count let a
        network error after 12 challenges end a run as if every try was spent (6Z5qYHeiSdQ9XtDso, 2026-11-13)."""
        # the user's proxy from the first call; on Apify, Apify Proxy's RESIDENTIAL group from the first call too, since
        # Cloudflare challenged Apify's own address on the first call of 10 runs of 10 (2026-11-10)
        if self.proxy_url is None and not self.proxy_ready and (self.proxy_input or self.at_home()) \
                and await self.setup_proxy(automatic=True):
            await self.rotate()
        errors = troubles = 0
        for _ in range(2 * (TRIES + ROTATIONS) + len(WAITS) + 2):   # a bound: calls at once share the address count
            if self.proxy_url and self.address_calls >= CALLS_PER_ADDRESS:
                await self.rotate()
            await self.pace.turn(PROXY_GAP_S if self.proxy_url else self.gap)
            started = time.monotonic()
            try:
                response = await self.client().get(url, params=params, proxy=self.proxy_url)
            except Exception as exc:  # noqa: BLE001  curl_cffi raises its own errors on network trouble
                STATS['retries'][type(exc).__name__] += 1
                errors += 1
                if errors > TRIES:
                    raise BoardError(f'HiringCafe did not answer: {type(exc).__name__}') from exc
                if self.proxy_url:
                    await self.rotate()
                await self.sleep(errors)
                continue
            STATS['calls'] += 1
            STATS['seconds'] += time.monotonic() - started
            self.counts['calls'] += 1
            self.address_calls += 1
            if response.status_code in (500, 502, 504) and troubles < TRIES:
                STATS['retries'][str(response.status_code)] += 1
                await self.sleep(1 + 2 * troubles)
                troubles += 1
                continue
            if not challenged(response) or response.status_code in expect:
                if not challenged(response):
                    self.rotations = 0    # ROTATIONS counts challenged addresses in a row, not in the whole run
                return response
            self.counts['challenges'] += 1
            STATS['retries'][f'{response.status_code} challenge'] += 1
            if self.proxy_url is None and await self.setup_proxy(automatic=True):
                await self.rotate()
                self.platform.note('Cloudflare challenged this run\'s own address, so it moved to a residential proxy '
                                   'address (Apify Proxy), a fresh one at each challenge.', 'moved')
                continue
            if self.proxy_url is not None:
                if self.rotations >= ROTATIONS:
                    raise Refused(f'Cloudflare challenged {self.rotations + 1} proxy addresses in a row. Run it again in '
                                  f'a few minutes')
                self.rotations += 1
                await self.rotate()
                continue
            if response.status_code == 429 and self.waited < len(WAITS):
                wait = WAITS[self.waited]
                self.waited += 1
                self.gap = SLOW_GAP_S
                self.pace.hold(wait)
                self.platform.note('HiringCafe asked this address to slow down (HTTP 429), so the run waited.', 'slow')
                continue
            raise Refused(f'Cloudflare challenged this address (HTTP {response.status_code}): run it on Apify, '
                          f'or set a proxy in Connection (proxyConfiguration)')
        raise BoardError('HiringCafe did not answer after several tries')

    async def build(self, fresh: bool = False) -> str:
        """The id of HiringCafe's deployment, which the data routes are under."""
        if self.build_id and not fresh:
            return self.build_id
        response = await self.get(BUILD_PAGE)
        found = BUILD_ID.search(response.text or '')
        if response.status_code != 200 or not found:
            raise BoardError(f'HiringCafe\'s page did not name its build (HTTP {response.status_code})')
        self.build_id = found.group(1)
        return self.build_id

    async def data(self, route: str, params=None, *, missing_ok: bool = False) -> dict | None:
        """A Next.js data route -> its pageProps. After a deployment the old build answers 404: read the new one once.
        missing_ok: a 404 that says notFound is an answer (no such job), None."""
        for fresh in (False, True):
            build = await self.build(fresh)
            response = await self.get(route.format(build=build, id='{id}') if '{build}' in route else route,
                                      params, expect=(200, 404))
            if response.status_code == 404:
                try:
                    if response.json().get('notFound') and missing_ok:
                        return None
                except ValueError:
                    pass
                continue
            if response.status_code != 200:
                raise BoardError(f'HTTP {response.status_code}')
            try:
                return response.json().get('pageProps') or {}
            except ValueError as exc:
                raise BoardError('HiringCafe\'s answer was not JSON') from exc
        raise BoardError('HiringCafe answered 404 for its own build')


class HiringCafe(Platform):
    key = 'hiringcafe'
    name = 'HiringCafe'
    site_word = 'company search'
    actor = 'hiringcafe-jobs-search'
    board_example = 'https://hiringcafe.com/?searchState=%7B%22searchQuery%22%3A%22data%20analyst%22%7D'
    job_example = 'https://hiringcafe.com/job/data-analyst-jwch-institute-united-states-23llqnpvt9uksz4i'
    concurrency = 4               # job details at once; Connection paces every call
    server_search = True          # HiringCafe searches the words in titles (or everywhere, searchDescriptions)
    server_filters = True         # places, period, work and job types, experience and its own filters go to it
    newest_first = True           # sortBy date, also with words; a page's employers in the order of their newest job
    never = ('team', 'offersEquity', 'expiresAt', 'reference')   # reference: the ATS's job id is atsJobId on every row
    job_types = ('volunteer',)    # HiringCafe's Volunteer commitment, a job type of its own on the site
    job_type_aliases = {'volunteer': 'volunteer', 'volunteers': 'volunteer', 'volunteering': 'volunteer'}
    filter_words = {
        'seniority': ('seniority', 'pick more seniority levels or clear Seniority'),
        'minSalary': ('minimum pay', 'type a lower Minimum pay or leave it empty'),
        'companySize': ('company size', 'pick more company sizes or clear Company size'),
        'visaSponsorship': ('visa sponsorship', 'untick Visa sponsorship'),
    }
    # HiringCafe's own fields on every row
    extra = ('experienceMin', 'seniority', 'roleType', 'employmentTypes', 'skills',
             'requirementsSummary', 'salaryMin', 'salaryMax', 'salaryCurrency', 'salaryPeriod', 'visaSponsorship',
             'companySize', 'companyIndustries', 'companyFounded', 'companyType', 'companyFunding',
             'companyHeadquarters', 'ats', 'atsJobId')

    def __init__(self):
        self.cfg = None
        self.conn = Connection(self)
        self.options: dict = {}       # HiringCafe's own settings of the run (read_input), in OUTPUT's settings
        self.pasted: dict = {}        # the filters of a pasted HiringCafe search link the form has no field for
        self.base: dict | None = None  # the search state every page asks with, but the words and the period
        self.nowhere = False          # every typed place is missing from HiringCafe's place list: nothing to search
        self.told: set[str] = set()
        self.seen: set[str] = set()   # job ids already handed over this run (an employer's list repeats its first 4)
        self.wanted_dates: list[str] = []      # posting times of jobs that pass the filters: wanted_floor
        self.filters = None
        self.left_out: dict[str, dict] = {}    # a named company -> the related companies' jobs left out, by company
        self.related_note: dict[str, int] = {}
        self.counts = {'employersOpened': 0, 'employerPages': 0}
        self.expand_failed: str | None = None  # why an employer's list could not be read; no more are opened then

    # ---------- notes, boards, links ----------

    def place_names(self):
        """HiringCafe's cities and states for the engine's reading of a search entry ("nurse in California")."""
        return place_names()

    def note(self, text: str, key: str | None = None) -> None:
        """A note for the run's message and OUTPUT, once."""
        if self.cfg is not None and (key or text) not in self.told:
            self.told.add(key or text)
            self.cfg.notes.append(text)

    def boards(self):
        return [], None               # no list of companies: HiringCafe searches every company itself

    def search_all(self, boards, cfg):
        self.cfg = cfg
        return [EVERYONE]

    def guess_board(self, name):
        return ' '.join(name.split()) or None

    def read_link(self, text):
        """A HiringCafe link -> (board, job id): a job page names its job, a company page (/org/<domain>) its company,
        a search page none (the engine then reads its words and places, search_link)."""
        token = text.strip().rstrip('.,;:!?')
        if REQ_ID.match(token) and any(ch.isdigit() for ch in token) and any(ch.isalpha() for ch in token):
            return EVERYONE.key, token
        parsed = parse_link(token)
        host = (parsed.hostname or '').lower() if parsed is not None and ' ' not in token.split('?')[0] else ''
        if host not in HOSTS:
            return None
        path = unquote(parsed.path)
        job = JOB_PATH.match(path.rstrip('/') + ('/' if path.endswith('/') else ''))
        if job:
            return EVERYONE.key, job.group(1)
        query = parse_qs(parsed.query)
        if query.get('openJob') and REQ_ID.match(query['openJob'][0].strip()):
            return EVERYONE.key, query['openJob'][0].strip()
        org = re.match(r'^/org/([^/]+)/?$', path)
        if org:
            return org.group(1), None
        company = self.pasted_company(query)
        if company:
            return company, None
        return None, None

    @staticmethod
    def pasted_company(query: dict) -> str | None:
        """The company of a search link's company= (base64 of source___board___name___website): its name."""
        value = (query.get('company') or [''])[0].strip()
        if not value:
            return None
        try:
            parts = base64.b64decode(value + '=' * (-len(value) % 4)).decode('utf-8').split('___')
        except (ValueError, UnicodeDecodeError):
            return None
        return clean(parts[2]) if len(parts) >= 3 and clean(parts[2]) and parts[2] != 'Unknown Company' else None

    @staticmethod
    def link_state(text: str) -> dict:
        """A pasted search link's searchState, as JSON or base64 JSON; {} when it has none."""
        parsed = parse_link(text)
        raw = (parse_qs(parsed.query).get('searchState') or [''])[0].strip() if parsed is not None else ''
        if not raw:
            return {}
        # as typed; closed again where the engine's cleaning of a pasted entry stripped a decoded link's last quotes and
        # brackets; base64
        for decode in (lambda s: s, closed, lambda s: base64.b64decode(s + '=' * (-len(s) % 4)).decode('utf-8')):
            try:
                state = json.loads(decode(raw))
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(state, dict):
                return state
        return {}

    def search_link(self, text):
        """A HiringCafe search page -> (words, places) of its searchState: the title query, else the search box, and
        the places by name. Its other filters are read by read_input."""
        state = self.link_state(text)
        asked = []
        for key in ('jobTitleQuery', 'searchQuery'):
            value = clean(state.get(key))
            if value:
                asked = [w for w in re.split(r'\s+(?:OR|\|)\s+', value.replace('"', '').replace('(', '').replace(')', ''))
                         if w.strip()]
                asked = [re.sub(r'\s+AND\s+', ' ', w).strip() for w in asked]
                break
        where = [clean(p.get('formatted_address')) for p in state.get('locations') or []
                 if isinstance(p, dict) and clean(p.get('formatted_address')) and p.get('id') != 'user_country']
        return asked, where

    def searches(self, token: str) -> bool:
        text = _clean(token)
        parsed = parse_link(text)
        return parsed is not None and (parsed.hostname or '').lower() in HOSTS and bool(self.link_state(text))

    # ---------- inputs ----------

    def read_input(self, raw, cfg):
        """HiringCafe's own fields (seniority, minSalary, salaryCurrency, companySize, visaSponsorship,
        proxyConfiguration) and the filters of a pasted search link (work and job types, period, seniority, years)."""
        self.cfg = cfg
        notes = cfg.notes
        linked: dict = {}
        for token in _links_whole(raw.get('searchTerms')):
            if self.searches(token):
                linked = {**linked, **self.link_state(_clean(token))}
        options: dict = {}
        if not cfg.work_types and linked.get('workplaceTypes'):
            kinds = list(dict.fromkeys(WORK_TYPE[w] for w in linked['workplaceTypes'] if w in WORK_TYPE))
            cfg.work_types = kinds if len(kinds) < 3 else []
        if not cfg.job_types and linked.get('commitmentTypes'):
            cfg.job_types = list(dict.fromkeys(JOB_TYPE[t] for t in linked['commitmentTypes']
                                               if JOB_TYPE.get(t) in SITE_JOB_TYPES))
        if cfg.posted_within_days is None and raw.get('postedWithin') in (None, '') and linked:
            bucket = linked.get('dateFetchedPastNDays')
            days = {2: 1, 4: 3, 14: 7, 21: 14, 29: 21, 61: 30, 91: 61, 121: 91, 151: 122, 181: 152, 211: 183,
                    750: 365, 1080: 730, 1440: 1095}.get(bucket if isinstance(bucket, int) else 121 if bucket is None else -1)
            if isinstance(bucket, dict) and isinstance(bucket.get('from'), str):      # a range: from its first day
                try:
                    start = datetime.fromisoformat(bucket['from'][:10]).replace(tzinfo=timezone.utc)
                    days = max((datetime.now(timezone.utc) - start).days + 1, 1)
                except ValueError:
                    days = None
            cfg.posted_within_days = float(days) if days else None
        if cfg.experience is None and isinstance(linked.get('roleYoeRange'), list) and len(linked['roleYoeRange']) == 2:
            low, high = linked['roleYoeRange']
            if isinstance(low, (int, float)) and isinstance(high, (int, float)) and (low, high) != (0, YEARS_CAP):
                cfg.experience = (float(low), float('inf') if high >= YEARS_CAP else float(high))
        seniority = self.choices(raw.get('seniority'), 'seniority', SENIORITY, SENIORITY_WORDS, notes)
        if not seniority and linked.get('seniorityLevel'):
            by_label = {label: key for key, label in SENIORITY.items()}
            seniority = [by_label[s] for s in linked['seniorityLevel'] if s in by_label]
        if seniority and len(seniority) < len(SENIORITY):
            options['seniority'] = seniority
        sizes = self.sizes(raw.get('companySize'), notes)
        if sizes:
            options['companySize'] = sizes
        pay = raw.get('minSalary')
        if pay not in (None, '', 0):
            typed = re.sub(r'[\s,$€£₹]|usd|eur|gbp|inr|a year|per year|/yr|/year', '', str(pay).lower())
            try:
                amount = float(typed.removesuffix('k')) * (1000 if typed.endswith('k') else 1)
            except ValueError:
                amount = None
            if amount is None or amount < 0:
                notes.append(f'Could not read minSalary "{pay}" as a yearly amount, so every pay is kept.')
            elif amount > 0:
                options['minSalary'] = amount
                currency = clean(raw.get('salaryCurrency')).upper() or 'USD'
                if not re.fullmatch(r'[A-Z]{3}', currency):
                    notes.append(f'salaryCurrency "{raw.get("salaryCurrency")}" is not a currency code such as USD, '
                                 f'so USD was used.')
                    currency = 'USD'
                options['salaryCurrency'] = currency
        if raw.get('visaSponsorship') is True or str(raw.get('visaSponsorship')).strip().lower() in ('true', 'yes', '1'):
            options['visaSponsorship'] = True
        proxy = raw.get('proxyConfiguration')
        if isinstance(proxy, dict) and (proxy.get('useApifyProxy') or proxy.get('proxyUrls')):
            self.conn.proxy_input = proxy     # kept out of options: proxy addresses can hold a password
        self.options = options

    @staticmethod
    def choices(value, key: str, allowed: dict, aliases: dict, notes: list[str]) -> list[str]:
        chosen, unknown = [], []
        for token in _items(value):
            word = ' '.join(token.lower().replace('_', ' ').replace('-', ' ').split())
            found = word.replace(' ', '-') if word.replace(' ', '-') in allowed else aliases.get(word)
            if found is None:
                unknown.append(token)
            elif found not in chosen:
                chosen.append(found)
        if unknown:
            notes.append(f'{key} has no option {", ".join(unknown)}, so it was left out. The options are '
                         f'{", ".join(allowed)}.')
        return chosen

    @staticmethod
    def sizes(value, notes: list[str]) -> list[tuple[int, int | None]]:
        """Company sizes typed as HiringCafe's bands ('51-200', '10001+') or any range ('1-500', '1000+') -> the
        bands that overlap them."""
        found = []
        for token in _items(value):
            text = token.lower().replace('employees', '').replace(',', '').replace(' ', '')
            m = re.fullmatch(r'(\d+)(?:-(\d+)|(\+))?', text)
            if not m:
                notes.append(f'Could not read companySize "{token}" as a number of employees such as 51-200 or '
                             f'10001+, so it was left out.')
                continue
            low = int(m.group(1))
            high = None if m.group(3) else int(m.group(2)) if m.group(2) else low
            for band in COMPANY_SIZES:
                if band[0] <= (high if high is not None else math.inf) and (band[1] is None or band[1] >= low):
                    if band not in found:
                        found.append(band)
        return found if len(found) < len(COMPANY_SIZES) else []

    def settings(self):
        out = dict(self.options)
        if 'companySize' in out:
            out['companySize'] = [f'{low}+' if high is None else f'{low}-{high}' for low, high in out['companySize']]
        return out

    # ---------- the search ----------

    def note_cap(self, total: int) -> None:
        self.incomplete = self.count_is_floor = True
        self.note(f'HiringCafe shows the newest 10,000 companies of one search (about 33,000 jobs) and this one matched '
                  f'{total:,} jobs: add a place, a period or more words to reach the rest.', 'cap')

    async def open(self, http, board):
        """The search state every page of this board asks with (places resolved once a run)."""
        if self.base is None:
            self.base = await self.state_base()
        if board.key == EVERYONE.key:
            return {'company': None}
        return {'company': board.key}

    async def state_base(self) -> dict:
        cfg = self.cfg
        state: dict = {'sortBy': 'date', 'defaultToUserLocation': False}
        if cfg is not None:
            if cfg.locations:
                state['locations'] = await self.locations(cfg.locations)
                if not state['locations']:
                    del state['locations']
            kinds = [k for t in cfg.work_types for k in SITE_WORK_TYPES.get(t, [])]
            if kinds:
                state['workplaceTypes'] = kinds
            types = [k for t in cfg.job_types for k in SITE_JOB_TYPES.get(t, [])]
            if types:
                state['commitmentTypes'] = types
            if cfg.experience is not None:
                high = cfg.experience[1]
                state['roleYoeRange'] = [0, YEARS_CAP if math.isinf(high) else min(math.ceil(high), YEARS_CAP)]
                state['excludeIfRoleYoeIsNotSpecified'] = True
        if self.options.get('seniority'):
            state['seniorityLevel'] = [SENIORITY[s] for s in self.options['seniority']]
        if self.options.get('companySize'):
            state['companySizeRanges'] = [list(band) for band in self.options['companySize']]
        if self.options.get('visaSponsorship'):
            state['benefitsAndPerks'] = ['visa_sponsorship']
        if self.options.get('minSalary'):
            state.update({'currency': {'label': self.options['salaryCurrency'], 'value': self.options['salaryCurrency']},
                          # pay listed by the hour or month too, by its yearly sum (calcFrequency): Yearly here
                          # keeps only pay listed by the year (635 registered nurse jobs of a week reaching $100,000
                          # against 5,762, most of them paid by the hour, 2026-11-10)
                          'frequency': {'label': 'Any', 'value': None}, 'calcFrequency': 'Yearly',
                          'restrictJobsToTransparentSalaries': True,
                          # the top of the published range reaches it (minCompensationLowEnd keeps ranges that start there)
                          'maxCompensationLowEnd': self.options['minSalary']})
        return state

    @staticmethod
    def title_query(term: str, split_min: int = SPLIT_MIN) -> str:
        """A search term -> HiringCafe's title query for every title the engine's own match accepts (rows.Term): each
        word in any order, in its singular and plural ('nurses' finds Nurse) and as typed with its accents, two
        neighbouring words also joined ('front end' finds Frontend) and a word also split in two ('frontend' finds
        Front-End). The engine checks every title again, so a reading that finds more costs reads, never a wrong row."""
        toks = words(term)
        if not toks:
            return ''
        typed = WORD.findall(term.casefold())
        accented = dict(zip(toks, typed)) if len(typed) == len(toks) else {}

        def spellings(word: str) -> list[str]:
            base = fold(word)
            out = [word, accented.get(word, word), base] + ([base + 's'] if base.isalpha() and len(base) > 2 else [])
            return list(dict.fromkeys(out))

        readings = [toks]
        readings += [toks[:i] + [toks[i] + toks[i + 1]] + toks[i + 2:] for i in range(len(toks) - 1)]
        readings += [toks[:i] + [t[:j], t[j:]] + toks[i + 1:] for i, t in enumerate(toks) if len(t) >= split_min
                     and t.isalpha() for j in range(1, len(t))]

        def clause(reading: list[str]) -> str:
            parts = []
            for word in reading:
                forms = [f'"{w}"' for w in spellings(word)]
                parts.append(forms[0] if len(forms) == 1 else f'({" OR ".join(forms)})')
            return ' AND '.join(parts)
        clauses = list(dict.fromkeys(clause(r) for r in readings))
        query = clauses[0] if len(clauses) == 1 else ' OR '.join(f'({c})' for c in clauses)
        if len(query) > MAX_QUERY and split_min < 99:
            return HiringCafe.title_query(term, split_min=99)      # many long words: no split readings
        return query

    def state(self, session: dict, term: str | None, days: int | None) -> dict:
        state = dict(self.base or {'sortBy': 'date', 'defaultToUserLocation': False})
        if term:
            query = self.title_query(term)
            if self.cfg is not None and self.cfg.search_descriptions:
                state['searchQuery'] = query
            elif query:
                state['jobTitleQuery'] = query
        if session.get('company'):
            typed = session['company']
            # a company page's address (hiringcafe.com/org/stripe.com) names its website: the name before the dot
            state['companyNames'] = [typed.split('.')[0] if '.' in typed and ' ' not in typed else typed]
        if days:
            start = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
            state['dateFetchedPastNDays'] = {'from': start}
        else:
            state['dateFetchedPastNDays'] = -1
        return state

    async def pages(self, http, session, term, days):
        """The search's pages, newest first. A page holds 40 employers, each with at most 4 of its matches; an employer
        shown with 4 may have more, so it waits in a queue for its own list (company=, every job of its board, 40 a
        page). Once the newest maxJobs are known (wanted_floor), the queued employers whose 4th job is still newer are
        opened, newest first, and the floor rises with every job they add; when the search ends, every one still in
        the queue is. Every match opens them all. A refusal once anything was read ends the reading with what was read
        (incomplete, with a note); an employer's list that cannot be read never costs the page."""
        if self.nowhere:
            return
        state = self.state(session, term, days)
        searched = json.dumps(state, separators=(',', ':'), ensure_ascii=False)
        company = session.get('company')
        queue: list[list[dict]] = []            # employers shown with 4 jobs, not opened yet
        yielded = False
        for n in range(LAST_PAGE + 1):
            params = {'searchState': searched, **({'page': n} if n else {})}
            try:
                props = await self.conn.data(SEARCH, params)
            except BoardError as exc:
                if not yielded:
                    raise                      # nothing was read: the run fails and says why
                pages_read = f'{n:,} page{"" if n == 1 else "s"}'
                self.stopped(f'HiringCafe stopped answering after {pages_read} of the search ({exc}), so the run saved '
                             f'the jobs read by then.', 'refused')
                return
            if props.get('ssrError'):
                raise BoardError(f'HiringCafe\'s search failed: {str(props["ssrError"])[:120]}')
            hits = [h for h in props.get('ssrHits') or [] if isinstance(h, dict) and h.get('requisition_id')]
            ended = not hits or bool(props.get('ssrIsLastPage'))
            groups = self.groups(hits)
            for members in groups:
                self.seen.update(j['id'] for j in members)
                self.count_wanted(members)
            queue += [m for m in groups if len(m) >= PER_GROUP]
            page = [job for members in groups for job in members]
            page += await self.open_employers(searched, queue, n, everything=ended or n == LAST_PAGE)
            if company:
                page = self.of_company(page, company)
            self.progress = None              # the engine's own line again, once the page is handed over
            yielded = True
            yield page
            if ended:
                return
            if n == LAST_PAGE:
                self.note_cap(int(props.get('ssrTotalCount') or 0))

    def stopped(self, text: str, key: str) -> None:
        """The search was not read to its end, so the matches counted are a floor ("at least")."""
        self.incomplete = self.count_is_floor = True
        self.note(text, key)

    async def open_employers(self, searched: str, queue: list[list[dict]], n: int, everything: bool) -> list[dict]:
        """The queued employers whose hidden jobs can still be wanted, newest 4th job first, each opened once; the rest
        stay queued. everything: the search ended, so whatever can still be wanted is opened now."""
        out: list[dict] = []
        queue.sort(key=lambda m: min((j['postedAt'] for j in m if j.get('postedAt')), default=''), reverse=True)
        while queue and not self.expand_failed:
            floor = self.wanted_floor()
            every = self.cfg is not None and self.cfg.max_jobs is None
            if floor is None and not every and not everything:
                break                         # the newest maxJobs are not known yet: read on first
            members = queue[0]
            if not every and floor is not None and not self.hides_wanted(members):
                break                         # this one's 4th job is older than the floor, and so is every one after it
            queue.pop(0)
            self.progress = (f'Reading page {n + 1:,} of HiringCafe\'s search: an employer\'s own list for the jobs past '
                             f'the 4 it shows ({self.counts["employersOpened"] + 1:,} opened so far).')
            try:
                out += await self.employer(searched, members)
            except BoardError as exc:
                self.expand_failed = str(exc)
                self.stopped(f'An employer\'s own list could not be read ({exc}), so employers shown with 4 jobs gave '
                             f'only those 4 from then on.', 'expand')
        return out

    def groups(self, hits: list[dict]) -> list[list[dict]]:
        """A page's hits -> its employers' jobs, in HiringCafe's order, each job listed at its employer's newest time:
        the page order is by that time, so the engine stops paging on it (listedAt)."""
        found: dict[str, list[dict]] = {}
        for hit in hits:
            found.setdefault(hit.get('collapse_key') or hit.get('requisition_id'), []).append(hit)
        out = []
        for members in found.values():
            made = [self.job(h) for h in members]
            stamps = [j['postedAt'] for j in made if j.get('postedAt')]
            newest = max(stamps) if stamps else None
            for job in made:
                job['listedAt'] = newest
            for job, hit in zip(made, members):
                job['_key'] = employer_key(hit)
            out.append(made)
        return out

    def page_jobs(self, hits: list[dict]) -> list[dict]:
        return [job for members in self.groups(hits) for job in members]

    def count_wanted(self, jobs: list[dict]) -> None:
        """Keeps the dates of the jobs that pass the run's filters, for wanted_floor."""
        if self.cfg is None or self.cfg.max_jobs is None:
            return
        if self.filters is None:
            self.filters = Filters(self.cfg)
        now = datetime.now(timezone.utc)
        for job in jobs:
            if job.get('postedAt') and self.filters.passes(job, now, True) and self.fits(job) is True:
                self.wanted_dates.append(job['postedAt'])

    def wanted_floor(self) -> str | None:
        """The posting time of the maxJobs-th newest wanted job seen so far; None while fewer were seen."""
        want = self.cfg.max_jobs if self.cfg is not None else None
        if not want or len(self.wanted_dates) < want:
            return None
        self.wanted_dates = sorted(self.wanted_dates, reverse=True)[:want]
        return self.wanted_dates[-1]

    def hides_wanted(self, members: list[dict]) -> bool:
        """An employer shown with 4 jobs whose oldest shown is still newer than the floor may hide wanted jobs."""
        if self.cfg is not None and self.cfg.max_jobs is None:
            return True
        floor = self.wanted_floor()
        oldest = min((j['postedAt'] for j in members if j.get('postedAt')), default=None)
        return floor is None or (oldest is not None and oldest >= floor)

    async def employer(self, searched: str, members: list[dict]) -> list[dict]:
        """Every match of one employer's board past the 4 its group showed, newest first: its own list with the same
        search (company=), EXPAND_PAGES pages at most, until its jobs are older than the floor."""
        key, listed = members[0].get('_key'), members[0].get('listedAt')
        if not key:
            return []
        out: list[dict] = []
        for n in range(EXPAND_PAGES):
            params = {'searchState': searched, 'company': key, **({'page': n} if n else {})}
            props = await self.conn.data(SEARCH, params)
            hits = [h for h in props.get('ssrHits') or [] if isinstance(h, dict) and h.get('requisition_id')]
            self.counts['employerPages'] += 1
            fresh = []
            for hit in hits:
                job = self.job(hit)
                if job['id'] in self.seen:
                    continue
                self.seen.add(job['id'])
                job['listedAt'] = max(listed or '', job.get('postedAt') or '') or None
                fresh.append(job)
            self.count_wanted(fresh)
            out += fresh
            floor = self.wanted_floor() if self.cfg is not None and self.cfg.max_jobs is not None else None
            oldest = min((j['postedAt'] for j in fresh if j.get('postedAt')), default=None)
            if not fresh or props.get('ssrIsLastPage') or (floor and oldest and oldest < floor):
                break
        else:
            self.note(f'An employer\'s own list was read to {EXPAND_PAGES * 40:,} of its jobs, the most one search reads '
                      f'of one employer: name the company in companies, or narrow the search, for the rest.', 'employer')
        self.counts['employersOpened'] += 1
        return out

    def of_company(self, jobs: list[dict], typed: str) -> list[dict]:
        """A named company's search: HiringCafe also lists its related companies (Stripe brought Paystack), so a job
        is kept when its company or website holds the typed name; the others are counted in a note."""
        wanted = [w for w in words(typed.split('.')[0] if '.' in typed and ' ' not in typed else typed) if w]
        squeezed = ''.join(wanted)
        kept, others = [], {}
        for job in jobs:
            name_words = set(words(job.get('company')))
            site = (job.get('companySlug') or '').lower()
            if (wanted and all(w in name_words for w in wanted)) or (squeezed and squeezed in re.sub(r'[^a-z0-9]', '', site.split('.')[0])):
                kept.append(job)
            else:
                others[job.get('company') or 'unknown'] = others.get(job.get('company') or 'unknown', 0) + 1
        if others:
            self.left_out[typed] = {**self.left_out.get(typed, {}), **{k: self.left_out.get(typed, {}).get(k, 0) + v
                                                                        for k, v in others.items()}}
            listed = ', '.join(sorted(self.left_out[typed], key=lambda k: -self.left_out[typed][k])[:3])
            total = sum(self.left_out[typed].values())
            text = (f'HiringCafe also listed {total:,} jobs of companies it relates to {typed} ({listed}); only '
                    f'{typed}\'s own were kept. Type those names in companies to get them too.')
            if self.cfg is not None:
                key = f'related {typed}'
                if key in self.told:
                    self.cfg.notes[self.related_note[key]] = text
                else:
                    self.related_note[key] = len(self.cfg.notes)
                    self.note(text, key)
        return kept

    # ---------- places ----------

    async def locations(self, typed: list[str]) -> list[dict]:
        """Typed places -> HiringCafe's place objects. A country or region goes as its countries or whole continents,
        a city or state by HiringCafe's own place list (other names too: Bangalore is Bengaluru there). A place the list
        does not hold is left out with a note, and when no typed place is left nothing is searched: reading the newest
        pages for its name instead found nothing for Koramangala in 63 s (run jgGIPnx9sJkyfyO57, 2026-11-10)."""
        found: list[dict] = []
        unknown: list[str] = []
        for text in typed:
            toks = regions.tokens(text)
            if regions.world_query(toks) or regions.entry_kind(toks) == 'remote':
                return []         # jobs open everywhere can be posted for any country: none goes to HiringCafe
            kind, codes = classify_place(text)
            if kind in ('country', 'region'):
                found += self.country_objects(codes)
                continue
            got = await self.lookup_place(text)
            if got:
                found.append(got)
            else:
                unknown.append(text)
        if unknown and not found:
            self.nowhere = self.searched_nothing = True
            self.note(f'{", ".join(unknown)} {"is" if len(unknown) == 1 else "are"} not on HiringCafe\'s list of '
                      f'places, so nothing was searched: type the city, state or country '
                      f'{"it is" if len(unknown) == 1 else "they are"} in.', 'unknown')
            return []
        if unknown:
            self.note(f'{", ".join(unknown)} {"is" if len(unknown) == 1 else "are"} not on HiringCafe\'s list of places, '
                      f'so it was searched only among the other places.', 'unknown')
        if len(found) > MAX_PLACES:
            self.note(f'The places typed hold {len(found)} countries, more than one search takes, so every place was '
                      f'checked here instead.', 'many')
            return []
        return found

    @staticmethod
    def country_objects(codes: frozenset[str]) -> list[dict]:
        """Countries -> place objects: a continent whose countries are all asked goes whole, the rest one by one."""
        out, left = [], set(codes)
        for (name, label), members in CONTINENTS.items():
            if members <= codes:
                out.append({'types': ['continent'], 'formatted_address': label,
                            'address_components': [{'long_name': name, 'short_name': name, 'types': ['continent']}]})
                left -= members
        for code in sorted(left):
            label = country_name(code)
            out.append({'types': ['country'], 'formatted_address': label,
                        'address_components': [{'long_name': label, 'short_name': code, 'types': ['country']}]})
        return out

    async def lookup_place(self, text: str) -> dict | None:
        """A city or state by HiringCafe's own list: the best candidate whose name starts with the typed name, by
        every other name the place goes by."""
        typed = ' '.join(words(text))
        # the place's other names (Bangalore: Bengaluru), and a state typed as its code by its name (TX: Texas)
        names = [text] + [n.title() for n in regions.OTHER_NAMES.get(typed, ()) + state_names(text)]
        for name in names:
            response = await self.conn.get(PLACES, {'query': name})
            try:
                candidates = response.json() if response.status_code == 200 else None
            except ValueError:
                candidates = None
            if not isinstance(candidates, list):
                raise BoardError(f'HiringCafe\'s place list did not answer for {name} (HTTP {response.status_code})')
            wanted = ' '.join(words(name.split(',')[0]))
            for c in candidates:
                detail = c.get('placeDetail') if isinstance(c, dict) else None
                label = ' '.join(words((detail or {}).get('formatted_address', '').split(',')[0]))
                if detail and label == wanted:
                    return {k: detail[k] for k in ('id', 'types', 'address_components', 'geometry', 'formatted_address')
                            if k in detail}
        return None

    # ---------- jobs ----------

    def job(self, hit: dict) -> dict:
        """One HiringCafe hit (a search's or a job page's) -> the engine's job shape."""
        v = hit.get('v5_processed_job_data') or {}
        info = hit.get('job_information') or {}
        firm = hit.get('enriched_company_data') or {}
        org = hit.get('attributed_org') or {}
        rid = hit.get('requisition_id')
        company = clean(v.get('company_name')) or clean(firm.get('name')) or clean(org.get('name')) or None
        domain = clean(v.get('company_website')) or clean(firm.get('homepage_uri')) or clean(org.get('website')) or None
        posted = iso(v.get('estimated_publish_date'))
        entries, seen = [], set()
        for text in (v.get('workplace_cities') or []) + (v.get('workplace_states') or []) + (v.get('workplace_countries') or []):
            entry = place_entry(text) if isinstance(text, str) else None
            if entry and not any(entry['name'] in s for s in seen):
                entries.append(entry)
                seen.add(entry['name'])
        for continent in v.get('boundless_workplace_continents') or []:
            if isinstance(continent, str) and continent:
                entries.append({'name': continent})
        if v.get('is_workplace_worldwide_ok'):
            entries.append({'name': 'Worldwide'})
        cities = [e['name'] for e in entries if e.get('city')]
        location = clean(v.get('formatted_workplace_location')) or (entries[0]['name'] if entries else None)
        others = [c for c in cities if c != location]
        others += [clean(p) for p in hit.get('dedup_other_locations') or [] if clean(p) and clean(p) not in others]
        if len(cities) == 1 and location and ' or ' not in location:
            others = [o for o in others if o != cities[0]]
        types = [JOB_TYPE[t] for t in v.get('commitment') or [] if t in JOB_TYPE]
        asked = set(self.cfg.job_types) if self.cfg is not None else set()
        kind = next((t for t in types if t in asked), None) or next((t for t in SPECIFIC if t in types), None)
        frequency = v.get('listed_compensation_frequency')
        shown = bool(v.get('is_compensation_transparent'))
        low = number(v.get(f'{PAY_KEYS.get(frequency, "yearly")}_min_compensation')) if shown else None
        high = number(v.get(f'{PAY_KEYS.get(frequency, "yearly")}_max_compensation')) if shown else None
        currency = clean(v.get('listed_compensation_currency')) or None
        period = PERIODS.get(frequency)
        least = years(v.get('min_industry_and_role_yoe'))
        year_low = number(v.get('yearly_min_compensation')) if shown else None      # every pay as a year's, as the
        year_high = number(v.get('yearly_max_compensation')) if shown else None     # pay filter compares it
        description = info.get('description') or None
        return {
            'id': rid,
            'company': company,
            'companySlug': (domain or '').lower() or None,
            'companyWebsite': website(domain),
            'title': clean(info.get('title') or info.get('job_title_raw')),
            'location': location,
            'otherLocations': list(dict.fromkeys(others)),
            'places': entries,
            'workType': WORK_TYPE.get(v.get('workplace_type')),
            'employmentType': kind,
            'department': clean(v.get('job_category')) or None,
            'salary': pay_text(low, high, currency, period) if (low or high) else None,
            'postedAt': posted,
            'dateOnly': day_only(posted),
            'jobUrl': f'{ORIGIN}/job/{rid}',
            'applyUrl': hit.get('apply_url') or None,
            'reference': clean(hit.get('original_source_id')) or None,
            'description': text_of(description) if description else None,
            'descriptionHtml': description,
            'experienceMin': least,           # the least years asked; HiringCafe states no ceiling (none: no limit)
            'seniority': v.get('seniority_level') or None,
            'roleType': v.get('role_type') or None,
            'employmentTypes': types,
            'skills': [clean(t) for t in v.get('technical_tools') or [] if clean(t)],
            'requirementsSummary': clean(v.get('requirements_summary')) or None,
            'salaryMin': low,
            'salaryMax': high,
            'salaryCurrency': currency if (low or high) else None,
            'salaryPeriod': period if (low or high) else None,
            'payPerYear': (year_low, year_high),
            'visaSponsorship': v.get('visa_sponsorship') if isinstance(v.get('visa_sponsorship'), bool) else None,
            'companySize': firm.get('nb_employees') if isinstance(firm.get('nb_employees'), int) else None,
            'companyIndustries': [clean(i) for i in firm.get('industries') or [] if clean(i)],
            'companyFounded': firm.get('year_founded') if isinstance(firm.get('year_founded'), int) else None,
            'companyType': clean(firm.get('organization_type')) or None,
            'companyFunding': clean(firm.get('latest_funding_type')) or None,
            'companyHeadquarters': country_name(firm['hq_country']) if isinstance(firm.get('hq_country'), str)
            and firm['hq_country'] else None,
            'ats': clean(hit.get('source')) or None,
            'atsJobId': clean(hit.get('original_source_id')) or None,
            'expired': bool(hit.get('is_expired')),
        }

    def fits(self, job: dict) -> bool | str:
        """HiringCafe's own filters, checked on what each job states (the search applied them too)."""
        options = self.options
        if options.get('seniority') and job.get('seniority') not in {SENIORITY[s] for s in options['seniority']}:
            return 'seniority'
        if options.get('companySize'):
            size = job.get('companySize')
            if not isinstance(size, int) or not any(low <= size <= (high or math.inf)
                                                    for low, high in options['companySize']):
                return 'companySize'
        if options.get('visaSponsorship') and job.get('visaSponsorship') is not True:
            return 'visaSponsorship'
        if options.get('minSalary'):
            low, high = job.get('payPerYear') or (None, None)
            top = high or low
            if job.get('salaryCurrency') != options.get('salaryCurrency') or not top or top < options['minSalary']:
                return 'minSalary'
        return True

    # ---------- details and links ----------

    async def details(self, http, session, job):
        if job.get('description'):
            return job
        response = await self.conn.get(DESCRIPTION, {'requisitionId': job['id']}, expect=(200, 404))
        if response.status_code != 200:
            return job                # gone since the search: saved without details
        try:
            html = ((response.json() or {}).get('job') or {}).get('job_information', {}).get('description') or None
        except (ValueError, AttributeError):
            return job
        return {**job, 'description': text_of(html), 'descriptionHtml': html} if html else job

    async def lookup(self, http, session, job_id):
        props = await self.conn.data(JOB.replace('{id}', job_id), missing_ok=True)
        moved = (props or {}).get('__N_REDIRECT')
        if isinstance(moved, str) and moved.startswith('/job/'):
            # an id alone is answered with a redirect to the job's page address, /job/<title-company-place>-<id>
            props = await self.conn.data(JOB.replace('{id}', moved.removeprefix('/job/').split('?')[0]), missing_ok=True)
        if props is None:
            return 'not found', {'company': None, 'companySlug': None, 'companyWebsite': None}
        hit = props.get('job') if isinstance(props.get('job'), dict) else None
        if hit is None:
            return 'not found', {'company': None, 'companySlug': None, 'companyWebsite': None}
        job = self.job(hit)
        if hit.get('is_expired'):
            return 'closed', {'company': job['company'], 'companySlug': job['companySlug'],
                              'companyWebsite': job['companyWebsite']}
        job['listedAt'] = job.get('postedAt')
        return 'open', job
