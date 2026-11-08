"""
foundit (www.foundit.in, formerly Monster India) and its eight sister sites, read through the JSON API their own search
pages call. No login, no key, no browser. Measured from this laptop on 2026-11-08:

- GET https://www.foundit.in/middleware/jobsearch: one search of one site. The site is the X-Source-Site-Context header
  (SITES below), and this one host answers for all nine: 450,058 open jobs on 2026-11-08 (India 219,732, Singapore
  107,828, Philippines 39,492, Gulf 30,909, Malaysia 21,817, Indonesia 11,602, Vietnam 6,923, Thailand 6,194, Hong Kong
  5,561). Parameters as the search page sends them: query (titles, skills and descriptions), locations (repeated for
  several places, which are OR'ed; the site reads its own spellings, so Bangalore finds the jobs written Bengaluru),
  jobFreshness (days since the job was last listed), experienceRanges (low~high years: the jobs whose range overlaps),
  jobTypes (Work From Home, Contract Job), employmentTypes (Full time, Part time), sort=2 (newest listed first), start
  and limit (100 at most). start stops at 9,500 (a later start answers that page again), so one search reaches its
  9,600 newest jobs. Without a Referer on a foundit page it answers 400 "content negotiation failed", and Akamai answers
  403 to a user agent that carries a link ("+https://...").
- A place that the site's own place list does not know (Bombay, Atlantis) is answered with the site's remote jobs, and
  Gurgaon is answered with all of Delhi NCR. So a place goes to a site only when its list (data/foundit_places.tsv, from
  scripts/refresh_foundit.py) or pycountry places it there, a job's place gets the list's other spellings of it
  (Bengaluru / Bangalore), and every job is still checked here. A state is not on the lists and not in the job list's
  location text, so with a state typed a job is opened (details) for its state before it is checked.
- Remote: the API's own remote filter (the place Remote, the job type Work From Home, or remote in a query, which it reads
  as that filter: "python remote" answers the 102 jobs of python with the place Remote) covers only jobs posted on
  foundit. Syndicated jobs say remote only in their title (35 of the newest 1,000 foundit.in jobs, none with the place
  or the type). So a remote search is checked here, from the type, the location and the title, on the full list; the
  place Remote is sent only for Worldwide or a typed Remote place, which can match only a location.
- GET https://www.foundit.in/middleware/jobdetail/<jobId> (any site's job, by its id): the description as HTML, every
  place with city, state and country, and qualifications. A job that never existed or was removed answers 200 with
  jobDetailStatus 404; a closed one has activeJob false (status 2).

Two dates. createdAt, when foundit first listed the job, is the date its job page gives Google (JobPosting datePosted,
2 of 2 pages, one of them a job renewed the same day: datePosted 31-10-2026, shown as posted 5 hours ago on
2026-11-07). freshness, when the job was last listed, is what the site shows as "Posted 5 hours ago" and sorts by: an
employer's renewal lists an old job again (12 of the 2,000 newest foundit.in jobs). postedAt is the earlier of the two,
because on about 1% of syndicated jobs createdAt runs up to 2 hours after freshness, and listedAt is freshness, which
the engine pages on.

About 98% of the newest jobs of every site are syndicated: copied from other job sites, mostly LinkedIn, with the
apply link going there (jobSource SCRAPPING, 1,986 of the newest 2,000 on foundit.in); the rest are posted on foundit by
the employer (ORGANIC). Pay is published on 40% of Singapore's newest jobs and 1% or less elsewhere, and an employer can
hide it (hideSalary): the API still sends a hidden amount, and the row leaves it out, as the site does.

robots.txt of www.foundit.in disallows /middleware/, the path of both calls above, which foundit's own search and job
pages make in every visitor's browser (memo23/foundit-jobs-scraper's README names the same two calls); the place list comes from
/raven/api/public/, which robots.txt leaves open. The HTML job pages answer 403 to anything but a browser's TLS
fingerprint; the actor never reads them.

Limits from one address: Akamai refuses an address that makes about 150 calls in a short window (140 calls in 2.3 s,
152 in 31 s), with 403 on every call for 30 s to 4 minutes, longer while calls go on; 2 calls a second held for 3
minutes passed (360 of 360, with or without its cookies). So calls wait their turn (RATE, BURST), and a refusal makes
every call wait (REFUSAL_WAITS) before the next try.
"""
import asyncio
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from importlib import resources
from urllib.parse import parse_qs, urlparse

from .. import places as regions
from ..http import BoardError, send
from ..rows import classify_place, country_code, words
from .base import Board, Platform
from .cornerstone import text_of
from .workable import pay_text

ORIGIN = 'https://www.foundit.in'
SEARCH = ORIGIN + '/middleware/jobsearch'
DETAIL = ORIGIN + '/middleware/jobdetail/{id}'
MASTER = ORIGIN + '/raven/api/public/search/v1/master-data'
AGENT = 'Mozilla/5.0 (compatible; foundit-jobs-search/1.0)'      # Akamai refuses one that carries a link
PAGE = 100
LAST_START = 9_500            # a later start answers the page at 9,500 again
WINDOW = LAST_START + PAGE    # the newest jobs one search reaches
SCAN_PAGES = 5                # pages per site read for a place no site knows, checked here by its name
RATE = 2.0                    # calls a second, held
BURST = 10                    # calls that may go at once before RATE holds
REFUSAL_WAITS = (30, 60, 120)  # seconds every call waits after Akamai refuses the address, try by try
MAX_YEARS = 50                # the most years the site's experience filter offers
NOBODY = {'company': None, 'companySlug': None, 'companyWebsite': None}   # a gone job: its company is not known


@dataclass(frozen=True)
class Site:
    host: str                 # the site's address and the board key
    name: str                 # the country site as people call it
    code: str                 # X-Source-Country; also the site's mark in data/foundit_places.tsv
    context: str              # X-Source-Site-Context
    countries: frozenset      # where its jobs are
    old_hosts: tuple = ()     # its Monster address, which redirects here


SITES = (
    Site('foundit.in', 'India', 'IN', 'rexmonster', frozenset({'IN'}), ('monsterindia.com',)),
    Site('founditgulf.com', 'Gulf', 'SA', 'monstergulf', regions.MIDDLE_EAST, ('monstergulf.com',)),
    Site('foundit.sg', 'Singapore', 'SG', 'monstersingapore', frozenset({'SG'}), ('monster.com.sg',)),
    Site('foundit.com.ph', 'Philippines', 'PH', 'monsterphilippines', frozenset({'PH'}), ('monster.com.ph',)),
    Site('foundit.my', 'Malaysia', 'MY', 'monstermalaysia', frozenset({'MY'}), ('monster.com.my',)),
    Site('foundit.id', 'Indonesia', 'ID', 'monsterindonesia', frozenset({'ID'}), ('monster.co.id',)),
    Site('foundit.hk', 'Hong Kong', 'HK', 'monsterhongkong', frozenset({'HK', 'TW', 'CN', 'MO'}), ('monster.com.hk',)),
    Site('monster.co.th', 'Thailand', 'TH', 'monsterthailand', frozenset({'TH'})),
    Site('monster.com.vn', 'Vietnam', 'VN', 'monstervietnam', frozenset({'VN'})),
)
BY_HOST = {host: site for site in SITES for host in (site.host, *site.old_hosts)}
BY_CODE = {site.code: site for site in SITES}
BY_CONTEXT = {site.context: site for site in SITES}
JOB_ID = re.compile(r'-(\d{5,12})(?:\.html?)?$')
AGO = re.compile(r'(\d+|an?|few)\s+(minute|hour|day|week|month|year)s?\s+ago', re.I)


def headers(site: Site) -> dict:
    """What foundit's own search page sends: the site's context, and a Referer on that site."""
    return {'User-Agent': AGENT, 'Accept': 'application/json, text/plain, */*', 'Referer': f'https://www.{site.host}/',
            'X-Source-Country': site.code, 'X-Language-Code': 'EN', 'X-Source-Site-Context': site.context}


def fold(text: str | None) -> str:
    """'Bengaluru / Bangalore' -> 'bengaluru bangalore': lower case, no accents, words only."""
    return ' '.join(words(text))


def spellings(name: str) -> set[str]:
    """A name of the list -> every way it finds a job: 'Hyderabad / Secunderabad, Telangana' -> hyderabad, secunderabad,
    secunderabad telangana and the whole name."""
    out = {fold(name)}
    for alias in name.split('/'):
        out.add(fold(alias))
        out.add(fold(alias.split(',')[0]))
    return {s for s in out if s}


@lru_cache(maxsize=1)
def place_list() -> tuple[dict, dict, str | None]:
    """data/foundit_places.tsv -> (spelling -> [(name, country code, site codes)], (site code, country code) -> the
    country's name on that site's list, the date of the list)."""
    found: dict[str, list[tuple[str, str, frozenset]]] = {}
    country_names: dict[tuple[str, str], str] = {}
    updated = None
    text = resources.files('ats_jobs').joinpath('data/foundit_places.tsv').read_text(encoding='utf-8')
    for line in text.splitlines():
        if line.startswith('#'):
            if 'updated' in line and updated is None:
                updated = line.split('updated', 1)[1].strip().split(':')[0].strip()
            continue
        parts = line.split('\t')
        if len(parts) < 4:
            continue
        name, code, kind, listed = parts[0], parts[1], parts[2], frozenset(parts[3].split(','))
        if kind == 'COUNTRY':
            for site in listed:
                country_names.setdefault((site, code), name)
        for spelling in spellings(name):
            found.setdefault(spelling, []).append((name, code, listed))
    return found, country_names, updated


def country_label(code: str) -> str | None:
    """'IN' -> 'India', the name the engine's place matching reads."""
    try:
        import pycountry
        found = pycountry.countries.get(alpha_2=code)
        return getattr(found, 'common_name', None) or getattr(found, 'name', None)
    except (ImportError, LookupError, KeyError):
        return None


@lru_cache(maxsize=1)
def subdivisions() -> dict[str, str]:
    """Every state and province of the sites' countries, its name folded -> its country: pycountry writes Karnātaka and
    Mahārāshtra, people type Karnataka and Maharashtra."""
    try:
        import pycountry
    except ImportError:
        return {}
    covered = {code for site in SITES for code in site.countries}
    return {fold(s.name): s.country_code for s in pycountry.subdivisions if s.country_code in covered and fold(s.name)}


def subdivision(text: str) -> str | None:
    """'Karnataka' -> 'IN', 'Selangor' -> 'MY': the country of a state or province, by its name."""
    return subdivisions().get(fold(text))


def iso(ms) -> str | None:
    """Milliseconds since 1970 -> '2026-11-07T19:25:40Z'."""
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def ago(text: str | None, now: datetime | None = None) -> tuple[str | None, bool]:
    """'5 hours ago' -> (that time, False); '8 days ago' -> (that day, True: only the day is known). Months and years
    are too rough to give: (None, False)."""
    m = AGO.search(text or '')
    if not m:
        return (None, False) if 'just now' not in (text or '').lower() else (
            (now or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ'), False)
    count = {'a': 1, 'an': 1, 'few': 1}.get(m.group(1).lower()) or int(m.group(1))
    unit, now = m.group(2).lower(), now or datetime.now(timezone.utc)
    if unit in ('minute', 'hour'):
        return (now - timedelta(**{f'{unit}s': count})).strftime('%Y-%m-%dT%H:%M:%SZ'), False
    if unit in ('day', 'week'):
        return (now - timedelta(days=count * (7 if unit == 'week' else 1))).date().isoformat(), True
    return None, False


def years(value) -> int | float | None:
    """{'years': 6} -> 6, as foundit gives it (whole years)."""
    found = (value or {}).get('years') if isinstance(value, dict) else value
    return found if isinstance(found, (int, float)) and not isinstance(found, bool) and found >= 0 else None


def names(values) -> list[str]:
    """['Finance', None, ' Banking '] -> ['Finance', 'Banking']; foundit's Other and Others say nothing and are left out."""
    seen = []
    for value in values or []:
        text = ' '.join(str(value or '').split())
        if text and text.lower() not in ('other', 'others') and text not in seen:
            seen.append(text)
    return seen


def pay(record: dict) -> dict:
    """The salary fields, when the employer shows pay. Amounts are a year's (absoluteValue; absoluteMonthlyValue is a
    twelfth of it), so the period is year."""
    if record.get('hideSalary') or record.get('jobSalaryConfidential'):
        return {}
    low, high = ((record.get(k) or {}) for k in ('minimumSalary', 'maximumSalary'))
    amounts = [v if isinstance(v, (int, float)) and v > 0 else None for v in (low.get('absoluteValue'), high.get('absoluteValue'))]
    if not any(amounts):
        return {}
    currency = high.get('currency') or low.get('currency') or record.get('currencyCode') or None
    return {'salary': pay_text(amounts[0], amounts[1], currency, 'year'), 'salaryMin': amounts[0], 'salaryMax': amounts[1],
            'salaryCurrency': currency, 'salaryPeriod': 'year'}


def job_type(record: dict) -> str | None:
    kinds = {k.lower() for k in record.get('jobTypes') or [] if isinstance(k, str)}
    employment = {k.lower() for k in record.get('employmentTypes') or [] if isinstance(k, str)}
    if 'contract job' in kinds:
        return 'contract'
    if 'part time' in employment:
        return 'part-time'
    return 'full-time' if 'full time' in employment else None


def company_slug(record: dict) -> str | None:
    """'/search/niit-technologies-1248503-jobs-career' -> 'niit-technologies-1248503', the company's foundit page."""
    m = re.match(r'^/search/(.+)-jobs-career/?$', record.get('seoCompanyUrl') or '')
    return m.group(1) if m else None


class Pace:
    """Calls to foundit in their turn: BURST at once, then RATE a second; after a refusal every call waits."""

    def __init__(self):
        self.tokens, self.at, self.until = float(BURST), time.monotonic(), 0.0
        self.lock = asyncio.Lock()

    async def turn(self) -> None:
        async with self.lock:
            now = time.monotonic()
            if self.until > now:
                await asyncio.sleep(self.until - now)
                now = time.monotonic()
            self.tokens = min(BURST, self.tokens + (now - self.at) * RATE)
            self.at = now
            if self.tokens < 1:
                await asyncio.sleep((1 - self.tokens) / RATE)
                self.tokens, self.at = 1.0, time.monotonic()
            self.tokens -= 1

    def hold(self, seconds: float) -> None:
        self.until = max(self.until, time.monotonic() + seconds)


class Foundit(Platform):
    key = 'foundit'
    name = 'foundit'
    site_word = 'country site'
    actor = 'foundit-jobs-search'
    board_example = 'https://www.foundit.in'
    job_example = 'https://www.foundit.in/job/polarion-alm-developer-career-soft-solutions-pvt-ltd-bengaluru-bangalore-india-69755276'
    concurrency = 9               # every site at once; Pace keeps the rate
    server_search = True          # pages() gets the word: foundit searches titles, skills and descriptions
    newest_first = True           # sort=2: newest listed first, and listedAt says when
    never = ('team', 'offersEquity', 'companyWebsite', 'reference')

    def __init__(self):
        self.cfg = None
        self.plan: dict[str, list[str] | None] = {}   # site -> places sent to it (None: no place, the whole site)
        self.scan = False             # no typed place is known anywhere: the newest jobs are checked by the place's name
        self.states: list[str] = []   # typed states: a job is opened for its state
        self.types: list[tuple[str, str]] = []        # job type parameters sent to every site
        self.pace = Pace()
        self.told: set[str] = set()
        self.windows: dict[str, int] = {}        # searches that reached foundit's 9,600-job window -> their totals
        self.window_note: int | None = None      # where their one note sits in cfg.notes

    # ---------- the sites ----------

    def boards(self):
        return [Board(s.host, f'foundit {s.name}', f'https://www.{s.host}') for s in SITES], place_list()[2]

    def note(self, text: str, key: str | None = None) -> None:
        """A note for the run's message and OUTPUT, once."""
        if self.cfg is not None and (key or text) not in self.told:
            self.told.add(key or text)
            self.cfg.notes.append(text)

    def search_all(self, boards, cfg):
        """The sites a search reads, and what each is asked: a typed place goes to the sites that hold it."""
        self.cfg = cfg
        self.settle(cfg)
        self.plan = self.route(cfg)
        return [b for b in boards if b.key in self.plan]

    def route(self, cfg) -> dict[str, list[str] | None]:
        """Typed places -> site -> the places sent to it (None: the whole site). A place goes to the sites whose list
        holds it, a country or region to the sites in it, a state to its country's site."""
        if not cfg.locations:
            return {s.host: None for s in SITES}
        spelled, country_names, _ = place_list()
        plan: dict[str, list[str] | None] = {}

        def add(site: Site, place: str | None) -> None:
            if place is None or plan.get(site.host, []) is None:
                plan[site.host] = None
            elif place not in plan.setdefault(site.host, []):
                plan[site.host].append(place)

        unknown = []
        for text in cfg.locations:
            toks = regions.tokens(text)
            if regions.world_query(toks) or regions.entry_kind(toks) == 'remote':
                for site in SITES:            # only remote jobs can be open everywhere
                    add(site, 'Remote')
                continue
            kind, codes = classify_place(text)
            if kind in ('country', 'region'):
                covering = [s for s in SITES if s.countries & codes]
                for site in covering:     # the whole site when the place covers it, else the country's name on its list
                    whole = kind == 'region' or site.countries <= codes
                    add(site, None if whole else next((country_names[(site.code, c)] for c in sorted(codes & site.countries)
                                                       if (site.code, c) in country_names), None))
                listed = [] if covering else [(s, country_names[(s.code, c)]) for s in SITES for c in sorted(codes)
                                              if (s.code, c) in country_names]
                for site, name in listed:     # a country no site covers, on a site's list: its jobs there
                    add(site, name)
                if not covering and not listed:
                    unknown.append(text)
                continue
            known = spelled.get(fold(text))
            if known:
                for _, code, listed in known:
                    home = [BY_CODE[c] for c in listed if c in BY_CODE and code in BY_CODE[c].countries]
                    for site in home or [BY_CODE[c] for c in listed if c in BY_CODE]:
                        add(site, text)
                continue
            state = subdivision(text)
            home = [s for s in SITES if state in s.countries] if state else []
            if home:
                self.states.append(text)
                for site in home:
                    add(site, text)
                continue
            unknown.append(text)
        if unknown and not plan:
            self.scan = True
            plan = {s.host: None for s in SITES}
            self.note(f'{", ".join(unknown)} {"is" if len(unknown) == 1 else "are"} not on foundit\'s place lists, so the '
                      f'{SCAN_PAGES * PAGE:,} newest jobs of each site were checked for '
                      f'{"it" if len(unknown) == 1 else "them"} by name. foundit writes Mumbai, Bengaluru and Gurugram.')
        elif unknown:
            self.note(f'{", ".join(unknown)} {"is" if len(unknown) == 1 else "are"} not on foundit\'s place lists, so no site '
                      f'was searched for {"it" if len(unknown) == 1 else "them"}.')
        return plan

    def settle(self, cfg) -> None:
        """What foundit cannot say is dropped with a note, and the job types it can say are sent to the site. Gulf, the
        name of a foundit site, is read as the GCC (the engine leaves Gulf out of its regions: Gulf Breeze, FL)."""
        gulf = [p for p in cfg.locations if fold(p) in ('gulf', 'the gulf')]
        if gulf:
            cfg.locations = list(dict.fromkeys('GCC' if p in gulf else p for p in cfg.locations))
            self.note('Read "Gulf" in location as the GCC: the UAE, Saudi Arabia, Qatar, Kuwait, Oman and Bahrain.')
        if 'onsite' in cfg.work_types:      # remote comes from foundit's work-from-home type and from Remote in a
            cfg.work_types = [t for t in cfg.work_types if t != 'onsite']     # location or title, hybrid from a title
            self.note('foundit does not say which jobs are on site, so the onsite work type was left out.')
        for kind, word in (('intern', 'internship'), ('temporary', 'temporary')):
            if kind in cfg.job_types:
                cfg.job_types = [t for t in cfg.job_types if t != kind]
                self.note(f'foundit has no {word} job type, so it was left out: type {kind} in Job titles to find '
                          f'(searchTerms) instead.')
        if cfg.job_types == ['contract']:
            self.types = [('jobTypes', 'Contract Job')]
        elif cfg.job_types in (['part-time'], ['full-time']):
            self.types = [('employmentTypes', 'Part time' if cfg.job_types == ['part-time'] else 'Full time')]

    # ---------- calls ----------

    async def get(self, http, url: str, site: Site, params=None):
        """One call in its turn; after a refusal (Akamai's 403) every call waits, then this one is tried again."""
        for attempt in range(len(REFUSAL_WAITS) + 1):
            await self.pace.turn()
            response = await send(http, 'GET', url, params=params, headers=headers(site))
            if response.status_code != 403:
                return response
            if attempt == len(REFUSAL_WAITS):
                break
            self.pace.hold(REFUSAL_WAITS[attempt])
            self.note('foundit refused this address for a while (HTTP 403, too many calls), so the run waited for it.',
                      key='refused')
        raise BoardError(f'foundit refused this address (HTTP 403) for {sum(REFUSAL_WAITS)} s')

    async def open(self, http, board):
        site = BY_HOST.get(board.key)
        if site is None:
            return None
        return {'site': site, 'places': self.plan.get(board.key)}      # a site named by a pasted link: all of it

    def params(self, session: dict, term: str | None, days: int | None) -> list[tuple[str, object]]:
        out: list[tuple[str, object]] = [('sort', 2), ('limit', PAGE)]
        if term:
            out.append(('query', term))
        out += [('locations', place) for place in session.get('places') or []]
        if days:
            out.append(('jobFreshness', days))
        experience = getattr(self.cfg, 'experience', None)
        if experience is not None:
            low = min(int(experience[0]), MAX_YEARS)
            high = MAX_YEARS if math.isinf(experience[1]) else min(math.ceil(experience[1]), MAX_YEARS)
            out.append(('experienceRanges', f'{low}~{high}'))
        return out + self.types

    async def pages(self, http, session, term, days):
        site, start = session['site'], 0
        base = self.params(session, term, days)
        while True:
            response = await self.get(http, SEARCH, site, base + [('start', start)])
            if response.status_code != 200:
                raise BoardError(f'HTTP {response.status_code}')
            try:
                data = response.json()
            except ValueError as exc:
                raise BoardError('the job list was not JSON') from exc
            found = data.get('jobSearchResponse') or {}
            if data.get('jobSearchStatus') not in (200, None) or not isinstance(found.get('data'), list):
                raise BoardError(f'the job list answered {data.get("jobSearchStatus")} {data.get("jobSearchStatusText") or ""}'.strip())
            records = [r for r in found['data'] if isinstance(r, dict) and not r.get('type') and r.get('jobId')]
            total = ((found.get('meta') or {}).get('paging') or {}).get('total') or 0
            yield [self.job(site, r) for r in records]
            start += PAGE
            if not records or start >= total or (self.scan and start >= SCAN_PAGES * PAGE):
                return
            if start > LAST_START:
                self.incomplete = True
                self.window(f'"{term}" on {site.host}' if term else site.host, total)
                return

    def window(self, what: str, total: int) -> None:
        """One note for every search that reached the window: the run's message says each and its total."""
        self.windows[what] = total
        listed = ', '.join(f'{name} had {count:,}' for name, count in self.windows.items())
        text = (f'foundit shows the {WINDOW:,} newest jobs of one search on one site: {listed}, so older ones were not '
                f'reached. Narrow the search by place, period or experience to reach them.')
        if self.cfg is None:
            return
        if self.window_note is None:
            self.window_note = len(self.cfg.notes)
            self.cfg.notes.append(text)
        else:
            self.cfg.notes[self.window_note] = text

    # ---------- jobs ----------

    def places(self, text: str | None, site: Site) -> list[dict]:
        """A job's location text -> its places: the text, with the country it names, else the country of a city on the
        place lists (Kuala Lumpur is Malaysia, even on foundit.sg), else the country of a site that has one; then the
        list's name of each place in it when that is another spelling (Bengaluru, India -> Bengaluru / Bangalore)."""
        home = next(iter(site.countries)) if len(site.countries) == 1 else None
        if not text:
            return [{'name': country_label(home), 'country': country_label(home)}] if home else []
        spelled = place_list()[0]
        parts = list(dict.fromkeys(p.strip() for p in text.split(',') if p.strip()))
        named = {code for code in (country_code(p, allow_codes=False) for p in parts) if code}
        out, found = [{'name': text}], []
        for part in parts:
            matches = spelled.get(fold(part), [])
            # a name in several countries: the one the text names, else the site's own, else every one
            matches = ([m for m in matches if m[1] in named] or [m for m in matches if m[1] in site.countries]
                       or matches)
            for name, code, _ in matches:
                found.append(code)
                other = code not in named and len(named) > 0      # Ho Chi Minh in "Singapore, Ho Chi Minh"
                if (fold(name) != fold(part) or other) and all(p['name'] != name for p in out):
                    out.append({'name': name, 'country': country_label(code)})
        country = next(iter(named), None) if len(named) == 1 else None
        country = country or (None if named else (found[0] if found else home))
        if country:
            out[0]['country'] = country_label(country)
        return out

    def job(self, site: Site, r: dict) -> dict:
        home = BY_CONTEXT.get(r.get('siteContext')) or site
        created, listed = r.get('createdAt'), r.get('freshness')
        stamps = [v for v in (created, listed) if isinstance(v, (int, float)) and v > 0]
        low, high = years(r.get('minimumExperience')), years(r.get('maximumExperience'))
        if not low and not high:
            low = high = None             # 0 to 0 years is what foundit stores when the employer states none
        where = ' '.join((r.get('locations') or '').split()) or None
        url = f'https://www.{home.host}{r.get("seoJdUrl") or r.get("jdUrl") or ""}'
        apply = r.get('applyUrl') or r.get('redirectUrl') or url
        kinds = [k for k in r.get('jobTypes') or [] if isinstance(k, str)]
        company = None if r.get('hideCompanyName') else ' '.join((r.get('companyName') or '').split()) or None
        employer = (r.get('employerTypes') or [None])[0]
        return {
            'id': str(r.get('jobId')),
            'company': company or 'Not disclosed',
            'companySlug': company_slug(r) if company else None,
            'title': ' '.join((r.get('title') or '').split()),
            'location': where,
            'places': self.places(where, home),
            'workType': 'remote' if 'Work From Home' in kinds else None,
            'employmentType': job_type(r),
            'department': ', '.join(names(r.get('functions'))) or None,
            **pay(r),
            'postedAt': iso(min(stamps)) if stamps else None,
            'listedAt': iso(listed),
            'expiresAt': iso(r.get('closedAt')),
            'jobUrl': url,
            'applyUrl': apply,
            'experienceMin': low,
            'experienceMax': high,
            'extra': {
                'site': home.host,
                'skills': names(re.split(r'\s*,\s*', r.get('skills') or '')) if isinstance(r.get('skills'), str) else [],
                'industry': ', '.join(names(r.get('industries'))) or None,
                'employerType': employer.lower() if isinstance(employer, str) else None,
                'jobSource': 'foundit' if r.get('jobSource') == 'ORGANIC' else 'syndicated' if r.get('jobSource') else None,
                'applicants': r.get('totalApplicants') if isinstance(r.get('totalApplicants'), int) else None,
                'refreshedAt': iso(listed),
            },
        }

    def vague_place(self, job):
        """With a state typed, a job whose location text does not name it is opened for its state (details)."""
        if not self.states:
            return False
        said = ' ' + ' '.join(fold(p.get('name')) for p in job.get('places') or []) + ' '
        return not any(f' {fold(state)} ' in said for state in self.states)

    # ---------- details and links ----------

    async def detail(self, http, site: Site, job_id: str) -> dict | None:
        """One job's full record, None when foundit has no such job."""
        response = await self.get(http, DETAIL.format(id=job_id), site)
        if response.status_code != 200:
            raise BoardError(f'HTTP {response.status_code}')
        try:
            data = response.json()
        except ValueError as exc:
            raise BoardError('the job details were not JSON') from exc
        status = data.get('jobDetailStatus')
        if status == 404:
            return None
        if status != 200 or not isinstance(data.get('jobDetailResponse'), dict):
            raise BoardError(f'the job details answered {status}')
        return {**data['jobDetailResponse'], 'activeJob': data.get('activeJob')}

    def enrich(self, job: dict, info: dict) -> dict:
        """A job and its full record: the description, every place with its state, qualifications."""
        found = []
        for entry in info.get('locations') or []:
            if not isinstance(entry, dict):
                continue
            parts = list(dict.fromkeys(bit.strip() for p in (entry.get('city'), entry.get('state'), entry.get('country'))
                                       if isinstance(p, str) for bit in p.split(',') if bit.strip()))  # Bilaspur, Chhattisgarh
            if parts:
                found.append({'name': ', '.join(parts), 'city': entry.get('city'), 'region': entry.get('state'),
                              'country': entry.get('country')})
        html = info.get('description') or None
        extra = dict(job.get('extra') or {})
        if self.cfg is None or self.cfg.include_details:   # a state search opens jobs for their state, not for details
            extra['qualifications'] = [q.get('text') for q in info.get('qualifications') or []
                                       if isinstance(q, dict) and q.get('text')] or None
        out = {**job, 'description': text_of(html), 'descriptionHtml': html, 'extra': extra}
        if found:
            site = BY_HOST.get(extra.get('site')) or SITES[0]
            spelled = [p for p in self.places(job.get('location'), site)[1:]]      # the list's other spellings
            out.update({'location': found[0]['name'], 'otherLocations': [p['name'] for p in found[1:]],
                        'places': found + spelled})
        return out

    async def details(self, http, session, job):
        site = BY_HOST.get((job.get('extra') or {}).get('site')) or (session or {}).get('site') or SITES[0]
        info = await self.detail(http, site, job['id'])
        return self.enrich(job, info) if info else job

    def read_link(self, text):
        """A foundit link -> (site, job id). A job page gives its id, a search page (/srp/results?query=...) is read for
        its words by the engine, any other page of a site names that site."""
        link = text.strip().rstrip('.,;:!?')
        link = link if re.match(r'^[a-z][a-z0-9+.-]*://', link, re.I) else f'https://{link}'
        parsed = urlparse(link)
        host = (parsed.hostname or '').lower() if ' ' not in text.strip() else ''
        host = re.sub(r'^(?:www|m)\.', '', host)
        site = BY_HOST.get(host)
        if site is None:
            return None
        path = parsed.path.rstrip('/')
        if path.startswith('/srp/'):
            return None, None
        found = JOB_ID.search(path) if '/job' in path else None
        if found:
            return site.host, found.group(1)
        given = parse_qs(parsed.query).get('id') or parse_qs(parsed.query).get('jobId')
        if given and given[0].isdigit():
            return site.host, given[0]
        return site.host, None

    async def lookup(self, http, session, job_id):
        info = await self.detail(http, session['site'], job_id)
        if info is None:
            return 'not found', NOBODY
        if info.get('activeJob') is False or info.get('status') not in (1, None):
            return 'closed', NOBODY
        return 'open', self.from_detail(session['site'], info)

    def from_detail(self, site: Site, info: dict) -> dict:
        """A job from its full record alone, for a checked link. The record gives its first listing only as text (8 days
        ago), so that is read to the day."""
        text = ', '.join(dict.fromkeys(p.strip() for e in info.get('locations') or [] if isinstance(e, dict)
                                       for p in (e.get('city'), e.get('country')) if isinstance(p, str) and p.strip()))
        skills = ', '.join(s.get('text') for s in info.get('skills') or [] if isinstance(s, dict) and s.get('text'))
        job = self.job(site, {**info, 'createdAt': None, 'locations': text or None, 'skills': skills})
        posted, day_only = ago(info.get('createdAt'))
        if posted and (not job['listedAt'] or posted <= job['listedAt']):
            job['postedAt'], job['dateOnly'] = posted, day_only
        return self.enrich(job, info)
