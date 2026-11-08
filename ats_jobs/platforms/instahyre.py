"""
Instahyre (www.instahyre.com), India's curated tech hiring site, read through the public JSON its own pages use. No login
and no key. Measured from this laptop on 2026-11-08:

- GET /api/v1/job_search?<filters>&offset=N: the site's search, 35 jobs a call (20 with isLandingPage; limit is
  ignored), offset + 35 at most 10,000 (HTTP 400 "Result window is too large"). Filters: skills (repeated, never with a
  comma; it matches more than titles), jobLocations (Instahyre's place names, Work From Home, regions), years (jobs whose
  experience range holds it), company_size (1 small, 3 medium, 2 large), job_type (1 full-time, 2 internship),
  companies (exact names), industry_types, job_functions, job_categories. No date and no order parameter (order_by,
  sort, ordering and id__gte are ignored): jobs come by Instahyre's own score, and the jobs it has not scored yet, the
  newest among them, come last, by id. A job there has its title, places, skills and employer (headcount band,
  founding year, tagline), and no date or experience. Limits per address: 20 calls a minute (429, Retry-After up to
  60 s) and about 250 an hour (429, Retry-After up to 40 minutes: a crawl at 19 calls a minute met it after 13 minutes).
- GET /api/v1/employer_public_jobs/<id>: one job: is_active, title, places, skills, experience range, category and
  functions, the HTML description, the recruiter's company and an internship's stipend. 404 for an id with no job. A
  budget of its own: 187 calls at 1.55 a second passed; a burst of 100 got a 12 s wait.
- GET /job-<id>/ (a 301 to /job-<id>-<slug>/): the job page, behind Cloudflare's browser check: httpx with a browser's
  headers gets 403 "Just a moment", curl_cffi's Chrome gets the page (115 of 115 at 1.9 a second, 134 KB each). Its
  JobPosting markup holds datePosted, the only posting date Instahyre publishes anywhere.
- GET /api/v1/employer_misc/employer_profile/anon_employer/<employer id>: the company page: industry, website, LinkedIn,
  tech stack, benefits, Glassdoor ratings when Instahyre shows them, and its slug (instahyre.com/jobs-at-<slug>/).
- GET /api/v1/candidate_misc/profile/candidate_locations/location_data/: every place Instahyre knows with its state and
  the other names people use (Bengaluru, Gurugram), kept in data/instahyre_places.tsv by scripts/refresh_instahyre.py.

Ids are given in the order jobs are posted: on 420 job pages, from id 16,893 (2018) to 450,470, datePosted never went
back as the id grew, and the 100 newest ids were all open jobs. So the newest jobs are the highest ids, and two pages
posted the same day date every job between them.

Each run takes the cheaper of two ways to the newest matches (pages()):
- walk: the newest ids one by one through each job's own answer, newest first, every filter checked here. One call per
  job looked at, so it fits a search most new jobs match: the empty form, a common skill.
- search: Instahyre's search with every filter it has, read to its end, then put in id order. One call per 35 jobs at
  20 calls a minute, so it fits a small search: a rare skill, a city with a skill, remote, a company.
A walk that finds matches too slowly to beat the search hands over to it. Dates come last, for the jobs that are
delivered or counted, from as few job pages as a bisection needs. The run's own clock stops both ways in time to
deliver what was found.
"""
import asyncio
import html as htmllib
import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from importlib import resources
from urllib.parse import urlparse

import httpx

from ..http import STATS, BoardError
from ..rows import Filters, country_code, dedupe_key
from .base import Board, Platform
from .cornerstone import text_of

HOST = 'https://www.instahyre.com'
SEARCH = HOST + '/api/v1/job_search'
DETAIL = HOST + '/api/v1/employer_public_jobs/{id}'
PROFILE = HOST + '/api/v1/employer_misc/employer_profile/anon_employer/{id}'
PAGE = HOST + '/job-{id}/'
COMPANY_PAGE = HOST + '/jobs-at-{slug}/'
PAGE_SIZE = 35                 # the search's page without isLandingPage
WINDOW = 10_000                # offset + PAGE_SIZE may not pass it
SEARCH_GAP = 3.1               # 20 search calls a minute per address
DETAIL_GAP = 0.65              # job answers and company pages: 92 a minute (2 a second met 429s; 1.55 a second held)
PAGE_GAP = 0.35                # job pages
LONGEST_WAIT = 75              # a 429 asking for longer is the hourly block: that kind of call is over for the run
SMALL_SEARCH_S = 31            # a search read whole in this time (10 more pages) is read whole, with no walk
DRY_PAGES = 8                  # pages of a word search read before judging whether its matches run dry
LONG_SEARCH_S = 150            # a word search still matching after DRY_PAGES pages is read on when the rest takes this
WALK_BATCH = 40                # ids asked at once in a walk
WALK_SAMPLE = 120              # ids a walk reads before it judges its pace against the search's
WALK_CAP = 4_000               # ids one walk may read: about 35 minutes
RESERVE_S = 150                # seconds kept before the run's time limit for dates, details and delivery
TOP_STEP = 64                  # the first step above the newest id known, when looking for the newest id
IDS_PER_DAY = 150              # job ids handed out a day: 3,571 from 2026-10-14 to 2026-11-07 (job pages)
WALK_START_S = 20              # finding the newest open job before a walk: about 30 answers
PENDING_STEP = 16              # the step down through the newest ids, jobs not yet published, to the first open one
PENDING_TRIES = 60             # steps tried before the walk starts at the very top anyway
NEWEST_KNOWN = 450_470         # an id known to exist on 2026-11-08: where the newest-id probe starts without a search
SITE = Board('*', 'Instahyre')
SIZE_CODES = {'small': 1, 'medium': 3, 'large': 2}            # company_size of the search
JOB_TYPE_CODES = {'full-time': 1, 'intern': 2}               # job_type of the search; Instahyre has no others
CODE_JOB_TYPE = {1: 'full-time', 2: 'intern'}
HEADCOUNT = {1: '0-10', 10: '10-50', 50: '50-200', 200: '200-500', 500: '500-1000', 1000: '1000+'}
SIZE_OF = {1: 'small', 10: 'small', 50: 'medium', 200: 'large', 500: 'large', 1000: 'large'}
REMOTE_PLACE = 'Work From Home'
WORLD = ('india', 'anywhere in india')          # places that do not narrow Instahyre's search
JOB_LINK = re.compile(r'^/job-(\d+)(?:-[^/]*)?/?$')
COMPANY_LINK = re.compile(r'^/jobs-at-([a-z0-9][a-z0-9-]*)/?$')
LOGO_EMPLOYER = re.compile(r'/profile/base/employer/(\d+)/')
LD_JSON = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S)
PAGE_EXPERIENCE = re.compile(r'class="experience">(?:\s*<img[^>]*>)?\s*([^<]*?)\s*<')
PAGE_FUNCTION = re.compile(r'Function:\s*</span>(.*?)</p>', re.S)
PAGE_SPAN = re.compile(r'<span>\s*([^<]*?)\s*</span>')
EMPLOYER_ID = re.compile(r"employerId\s*=\s*'?(\d+)")
PAGE_PLACES = re.compile(r'class="location-span">(?:\s*<img[^>]*>)?\s*([^<]*?)\s*<')
PAGE_SKILLS = re.compile(r'id="job-skills-description"(.*?)</ul>', re.S)
PAGE_SKILL = re.compile(r'<li>([^<]+)</li>')
INTERNSHIP_BLOCK = 'job.internship.duration'
YEARS_TEXT = re.compile(r'(\d+)\s*(?:-\s*(\d+))?\s*\+?\s*years?', re.I)
SIZE_WORDS = {'small': 'small', 'startup': 'small', 'startups': 'small', 'medium': 'medium', 'mid': 'medium',
              'midsize': 'medium', 'mid-size': 'medium', 'large': 'large', 'big': 'large', 'enterprise': 'large'}
FRESHER = ('fresher', 'freshers', 'fresh', 'entry', 'entry level', 'graduate', 'graduates', 'no experience')
RATINGS = {'overall': 'overall', 'work_life_balance': 'workLifeBalance', 'compensation_benefits': 'salaryAndBenefits',
           'culture_values': 'culture', 'career_opportunities': 'careerGrowth', 'diversity_inclusions': 'diversity',
           'recommendation': 'recommendToFriend'}


class Refused(BoardError):
    """Instahyre refused this kind of call for longer than a run can wait (the hourly limit)."""


def _data(name: str) -> str:
    return resources.files('ats_jobs').joinpath(f'data/{name}').read_text(encoding='utf-8')


def load_places() -> tuple[dict, dict, dict]:
    """data/instahyre_places.tsv -> (place name in lower case -> (name, state, country); other name -> name;
    state in lower case -> its places). Lines: name, state, country, other names separated by commas."""
    named, other, states = {}, {}, {}
    for line in _data('instahyre_places.tsv').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        name, state, country, aliases = (line.split('\t') + ['', '', ''])[:4]
        name, state, country = name.strip(), state.strip() or None, country.strip() or None
        named.setdefault(name.lower(), (name, state, country))
        for alias in aliases.split(','):
            if alias.strip():
                other.setdefault(alias.strip().lower(), (name, alias.strip()))
        if state:
            states.setdefault(state.lower(), []).append(name)
    for alias in [a for a in other if a in named]:      # a place's own name wins over another place's alias
        del other[alias]
    return named, other, states   # other: other name in lower case -> (the place's name, the other name as written)


def years(value) -> int | None:
    try:
        return int(value) if value is not None and str(value).strip() != '' else None
    except (TypeError, ValueError):
        return None


def stipend(info: dict | None) -> dict:
    """An internship's stipend -> the shared pay fields. Instahyre states it a month, in rupees."""
    info = info or {}
    amount = info.get('stipend')
    if info.get('hide_stipend') or not info.get('is_paid') or not isinstance(amount, (int, float)) or amount <= 0:
        return {}
    return {'salary': f'INR {amount:,.0f} a month', 'salaryMin': amount, 'salaryMax': amount, 'salaryCurrency': 'INR',
            'salaryPeriod': 'month'}


def read_page(html: str, job_id: int | None = None) -> dict:
    """A job page -> {date, title, company, logo, website, descriptionHtml, employmentType, experience (low, high),
    function [category, function, ...], locations, skills}. The JobPosting markup gives the date, title, company,
    description and type (a page can repeat the job's block once per city; the block naming the job is used); the
    page's own labelled parts give the experience ("2-4 Years"), function, places as shown and skill tags."""
    out: dict = {}
    postings = []
    for block in LD_JSON.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get('@type') == 'JobPosting':
            postings.append(data)
    named = [d for d in postings if str((d.get('identifier') or {}).get('value') or '').startswith(f'job-{job_id}-')]
    posting = (named or postings or [None])[0]
    if posting:
        company = posting.get('hiringOrganization') or {}
        out.update({'date': (posting.get('datePosted') or '')[:10] or None,
                    'title': ' '.join((posting.get('title') or '').split()) or None,
                    'company': (company.get('name') or '').strip() or None, 'logo': company.get('logo') or None,
                    'descriptionHtml': posting.get('description') or None,
                    'employmentType': posting.get('employmentType'), 'website': company.get('sameAs') or None})
    found = PAGE_EXPERIENCE.search(html)
    span = YEARS_TEXT.search(found.group(1)) if found else None
    if span:
        low = int(span.group(1))
        out['experience'] = (low, int(span.group(2)) if span.group(2) else low)
    found = PAGE_FUNCTION.search(html)
    if found:
        out['function'] = [htmllib.unescape(s) for s in PAGE_SPAN.findall(found.group(1)) if s and s != '&rarr;']
    found = PAGE_PLACES.search(html)
    if found:
        out['locations'] = [c.strip() for c in htmllib.unescape(found.group(1)).split(',') if c.strip()]
    found = PAGE_SKILLS.search(html)
    if found:
        out['skills'] = [htmllib.unescape(s).strip() for s in PAGE_SKILL.findall(found.group(1)) if s.strip()]
    # an internship's page carries the internship block (start date, duration, stipend, drawn in the browser) and no
    # experience; its JobPosting markup still says FULL_TIME (3 of 3 internships, 0 of 3 jobs, 2026-11-08)
    out['internship'] = INTERNSHIP_BLOCK in html
    return out


def seconds_left() -> float | None:
    """Seconds until the platform stops this run (ACTOR_TIMEOUT_AT), or None when it sets no limit (a local run)."""
    value = os.environ.get('ACTOR_TIMEOUT_AT')
    if not value:
        return None
    try:
        at = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).timestamp() - time.time()


def out_of_time() -> bool:
    left = seconds_left()
    return left is not None and left < RESERVE_S


class Pace:
    """One kind of call, spaced gap seconds apart for the whole run; a short 429 holds every call of the kind."""

    def __init__(self, gap: float):
        self.gap, self.next, self.lock = gap, 0.0, asyncio.Lock()
        self.refused: str | None = None
        self.challenged = 0

    async def turn(self) -> None:
        async with self.lock:
            wait = self.next - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self.next = time.monotonic() + self.gap

    def hold(self, seconds: float) -> None:
        self.next = max(self.next, time.monotonic() + seconds)


def _seconds(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 30.0


def _number(value):
    try:
        return round(float(value), 1) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _list(value) -> list[str]:
    raw = value if isinstance(value, list) else [value] if isinstance(value, (str, int, float)) else []
    return [part.strip() for item in raw if isinstance(item, (str, int, float))
            for part in re.split(r'[,;\n]+', str(item)) if part.strip()]


class Instahyre(Platform):
    key = 'instahyre'
    name = 'Instahyre'
    site_word = 'employer'         # 'company' would read "companys" in the engine's running message
    actor = 'instahyre-jobs-search'
    board_example = 'https://www.instahyre.com/jobs-at-zomato/'
    job_example = 'https://www.instahyre.com/job-447415-senior-data-engineer-at-the-walt-disney-company-bangalore/'
    concurrency = 4               # companies named at once; the calls themselves wait their turn (Pace)
    never = ('team', 'expiresAt', 'offersEquity', 'reference')

    def __init__(self):
        self.cfg = None
        self.wanted = lambda job: True
        self.with_details = False                   # includeDetails, kept for link checks (no search_all there)
        answers = Pace(DETAIL_GAP)                  # job answers and company pages share one budget (run 04: 23 429s apart)
        self.paces = {'search': Pace(SEARCH_GAP), 'detail': answers, 'page': Pace(PAGE_GAP), 'profile': answers}
        self.dates: dict[int, str] = {}             # job id -> datePosted, read or inferred
        self.answers: dict[int, dict | None] = {}   # job id -> its own answer (None: no such job)
        self.profiles: dict[str, dict | None] = {}  # employer id -> its company page
        self._places = None
        self._companies: dict[str, dict] | None = None
        self._pages = None                          # the curl_cffi session for job pages
        self.read_pages: dict[int, dict | None] = {}    # job id -> what its page says (read_page), None: no page
        self.page_trouble: str | None = None        # why job pages stopped being read
        self.counts = {'searchPages': 0, 'jobAnswers': 0, 'jobPages': 0, 'companyPages': 0}
        self.refusals: dict[str, int] = {}          # 429s by kind of call ('search', 'detail', 'profile', 'page')

    # ---------- data ----------

    def places(self) -> tuple[dict, dict, dict]:
        if self._places is None:
            self._places = load_places()
        return self._places

    def companies(self) -> dict[str, dict]:
        """data/instahyre.tsv beyond the shared three columns: employer id -> headcount band code, founding year,
        tagline. Lines: employer id, name, website, headcount code, founding year, tagline."""
        if self._companies is None:
            self._companies = {}
            for line in _data(f'{self.key}.tsv').splitlines():
                if line.startswith('#') or not line.strip():
                    continue
                parts = (line.split('\t') + [''] * 6)[:6]
                self._companies[parts[0].strip()] = {'name': parts[1].strip(), 'employees': years(parts[3]),
                                                     'founded': years(parts[4]), 'tagline': parts[5].strip() or None}
        return self._companies

    def boards(self) -> tuple[list[Board], str | None]:
        """The companies of data/instahyre.tsv, keyed by name: Instahyre's search filters companies by name, and a typed
        name then reads as itself (the file's first column is the employer id, for the headcount of a job's company).
        A name two employers share (5 on 2026-11-08) is the one with more jobs, listed first."""
        boards, updated = super().boards()
        seen, out = set(), []
        for board in boards:
            if board.name.lower() not in seen:
                seen.add(board.name.lower())
                out.append(Board(board.name, board.name, board.website))
        return out, updated

    def all_jobs(self) -> int:
        """Open jobs on Instahyre when the data file was built (its header), for judging how dense a filter is."""
        found = re.search(r'([\d,]+) open jobs', _data(f'{self.key}.tsv').split('\n', 1)[0])
        return int(found.group(1).replace(',', '')) if found else 13_000

    # ---------- links, names and the platform's own fields ----------

    def read_link(self, text):
        link = text.strip().rstrip('.,;:!?')
        link = link if re.match(r'^[a-z][a-z0-9+.-]*://', link, re.I) else f'https://{link}'
        parsed = urlparse(link)
        host = (parsed.hostname or '').lower() if ' ' not in text.strip() else ''
        if host not in ('instahyre.com', 'www.instahyre.com'):
            return None
        job = JOB_LINK.match(parsed.path)
        if job:
            return SITE.key, job.group(1)
        found = COMPANY_LINK.match(parsed.path.lower())
        if found:
            return found.group(1), None
        return None, None            # the home page, or a search link: its skills and location are read as such

    def aliases(self, board: Board) -> list[str]:
        """A name that starts with The is found without it: Walt Disney finds The Walt Disney Company."""
        return [board.name[4:]] if board.name.lower().startswith('the ') else []

    def guess_board(self, name):
        return name if re.search(r'[A-Za-z0-9]', name) else None

    def read_inputs(self, raw, notes):
        """experienceYears: jobs open to someone with that many years (Instahyre's own filter; fresher reads as 0).
        companySize: small, medium or large, by Instahyre's headcount bands."""
        self.with_details = raw.get('includeDetails') in (True, 'true', 'True', 'yes', 1, '1')
        extra = {}
        value = raw.get('experienceYears')
        text = str(value).strip().lower() if value is not None else ''
        if text and text not in ('any', 'all', 'none'):
            text = re.sub(r'\s*(?:years?|yrs?)\.?$', '', text).strip()
            if text in FRESHER:
                text = '0'
            try:
                number = float(text)
            except ValueError:
                notes.append(f'Could not read experienceYears "{value}" as a number of years, so jobs for every '
                             f'experience are kept.')
            else:
                if number < 0:
                    notes.append(f'experienceYears {value} is below 0, so jobs for every experience are kept.')
                else:
                    if not isinstance(value, int) or isinstance(value, bool):
                        notes.append(f'Read experienceYears "{value}" as {int(number)} years.')
                    extra['experience_years'] = int(number)
        sizes, unknown = [], []
        for token in _list(raw.get('companySize')):
            size = SIZE_WORDS.get(token.lower())
            if token.lower() in ('any', 'all', 'every'):
                sizes = list(SIZE_CODES)
                break
            if size is None:
                unknown.append(token)
            elif size not in sizes:
                sizes.append(size)
                if token.lower() != size:
                    notes.append(f'Read "{token}" in companySize as {size}.')
        if unknown:
            notes.append(f'companySize has no option {", ".join(unknown)}, so it was left out. The options are small '
                         f'(up to 50 employees), medium (50 to 200) and large (over 200).')
        if sizes and len(sizes) < len(SIZE_CODES):
            extra['company_sizes'] = sizes
        return extra

    # ---------- what a search reads ----------

    def search_all(self, boards, cfg):
        """Every company at once: Instahyre's search and its newest ids are one place, the site."""
        self.cfg = cfg
        self.wanted = self.matcher(cfg)
        if cfg.work_types == ['hybrid']:
            cfg.notes.append('Instahyre marks no job hybrid: a job is in a city (onsite) or Work From Home (remote).')
        if cfg.job_types and any(t not in JOB_TYPE_CODES for t in cfg.job_types):
            cfg.notes.append('Instahyre lists full-time jobs and internships only.')
        return [SITE]

    def matcher(self, cfg):
        """Every filter of the run but the posting date, which needs the job's page (the engine checks it once the
        date is known), plus experience and company size where the job states them."""
        filters = Filters(cfg)
        filters.within_days = None
        exp = (cfg.extra or {}).get('experience_years')
        sizes = set((cfg.extra or {}).get('company_sizes') or [])
        now = datetime.now(timezone.utc)

        def wanted(job: dict) -> bool:
            if not filters.passes(job, now):
                return False
            low, high = job.get('_exp') or (None, None)
            if exp is not None and low is not None and high is not None and not low <= exp <= high:
                return False
            return not (sizes and job.get('_size') is not None and job['_size'] not in sizes)
        return wanted

    async def open(self, http, board):
        if board.key == SITE.key:
            return {'company': None}
        names = {name.lower() for name in (c['name'] for c in self.companies().values())}
        if board.key.lower() in names:
            return {'company': board.name}
        if COMPANY_LINK.match(f'/jobs-at-{board.key}/'):          # a company page's name, from a pasted link
            name = await self.company_by_slug(http, board.key)
            if name:
                return {'company': name}
        return {'company': board.name}

    # ---------- calls, each kind at its own pace ----------

    async def call(self, http, kind: str, url: str, params=None) -> httpx.Response:
        pace = self.paces[kind]
        for attempt in range(4):
            if pace.refused:
                raise Refused(pace.refused)
            await pace.turn()
            if pace.refused:                         # refused while this call waited its turn
                raise Refused(pace.refused)
            started = time.monotonic()
            try:
                response = await http.request('GET', url, params=params)
            except httpx.HTTPError as exc:
                STATS['retries'][type(exc).__name__] += 1
                if attempt == 3:
                    raise BoardError(f'{type(exc).__name__}: {exc}'[:200]) from exc
                await asyncio.sleep(2 + 3 * attempt)
                continue
            STATS['calls'] += 1
            STATS['seconds'] += time.monotonic() - started
            if response.status_code == 429:
                STATS['retries']['429'] += 1
                self.refusals[kind] = self.refusals.get(kind, 0) + 1
                wait = _seconds(response.headers.get('retry-after'))
                if wait > LONGEST_WAIT:
                    pace.refused = (f'Instahyre limits how often one address may call its {kind}, and asked this run '
                                    f'to wait {max(round(wait / 60), 2)} minutes')
                    raise Refused(pace.refused)
                pace.hold(wait + 1)
                continue
            if response.status_code in (500, 502, 503, 504) and attempt < 3:
                STATS['retries'][str(response.status_code)] += 1
                await asyncio.sleep(2 + 3 * attempt)
                continue
            return response
        raise BoardError(f'Instahyre kept refusing its {kind} calls')

    async def search_page(self, http, query: list, offset: int) -> dict:
        response = await self.call(http, 'search', SEARCH, params=query + [('offset', offset)])
        self.counts['searchPages'] += 1
        if response.status_code != 200:
            raise BoardError(f'the search answered HTTP {response.status_code}: {response.text[:120]}')
        try:
            return response.json()
        except ValueError as exc:
            raise BoardError('the search did not answer JSON') from exc

    async def answer(self, http, job_id: int) -> dict | None:
        """A job's own answer, once a run; None when there is no such job."""
        if job_id not in self.answers:
            response = await self.call(http, 'detail', DETAIL.format(id=job_id))
            self.counts['jobAnswers'] += 1
            if response.status_code == 404:
                self.answers[job_id] = None
            elif response.status_code != 200:
                raise BoardError(f'a job answered HTTP {response.status_code}')
            else:
                try:
                    self.answers[job_id] = response.json()
                except ValueError as exc:
                    raise BoardError('a job did not answer JSON') from exc
        return self.answers[job_id]

    async def profile(self, http, employer: str | None) -> dict | None:
        """A company's page, once a run; None when it cannot be read (the job keeps what it has)."""
        if not employer:
            return None
        if employer not in self.profiles:
            self.profiles[employer] = None
            try:
                response = await self.call(http, 'profile', PROFILE.format(id=employer))
                self.counts['companyPages'] += 1
                if response.status_code == 200:
                    self.profiles[employer] = response.json()
            except (BoardError, ValueError):
                pass
        return self.profiles[employer]

    # ---------- job shape ----------

    def place_entries(self, cities: list[str]) -> list[dict]:
        """Instahyre's place names -> places with their state and country, plus each place's other names as places
        of their own, so Bengaluru finds Bangalore and Karnataka finds both."""
        named, other, _ = self.places()
        entries, also = [], []
        for city in cities:
            if city.lower() in (REMOTE_PLACE.lower(), 'work from home / remote'):
                entries.append({'name': REMOTE_PLACE, 'country': 'India'})
                continue
            name, state, country = named.get(city.lower()) or (city, None, None)
            if country is None and country_code(city, allow_codes=False):
                country = city
            entries.append({'name': city, 'city': city if state or country == 'India' else None, 'region': state,
                            'country': country})
            also += [{'name': written} for canonical, written in other.values() if canonical == name]
        return entries + also

    def job(self, record: dict, answer: dict | None = None, kind: str | None = None) -> dict:
        """A search record and/or a job's own answer -> the job shape. The answer adds experience, category,
        functions, the description, internship and the recruiter's company. kind: the job type the search was
        narrowed to, for a record with no answer."""
        employer = record.get('employer') or {}
        info = answer or {}
        job_id = int(record.get('id') or info.get('id'))
        cities = info.get('locations') or [c.strip() for c in (record.get('locations') or '').split(',') if c.strip()]
        remote = any(c.lower() == REMOTE_PLACE.lower() for c in cities)
        title = ' '.join((info.get('title') or record.get('title') or info.get('candidate_title')
                          or record.get('candidate_title') or '').split())
        company = (info.get('hiring_company_name') or employer.get('company_name') or '').strip() or None
        logo = info.get('employer_profile_url') or employer.get('profile_image_src') or None
        found = LOGO_EMPLOYER.search(logo or '')
        employer_id = str(employer.get('id') or (found.group(1) if found else '')) or None
        known = self.companies().get(employer_id or '', {})
        employees = employer.get('employee_count') or known.get('employees')
        functions = [f for names in (info.get('job_function_dict') or {}).values() for f in names or []] or \
            (info.get('agency_function_names') or None)
        agency = (info.get('recruiter_company_name') or '').strip()
        url = (HOST + info['opportunity_url']) if info.get('opportunity_url') else \
            (record.get('public_url') or PAGE.format(id=job_id))
        html = info.get('description') or None
        internship = info.get('is_internship') if answer else None
        exp = (years(info.get('workex_min')), years(info.get('workex_max'))) if answer else (None, None)
        job = {
            'id': str(job_id),
            'rank': job_id,
            'company': company,
            'title': title,
            'location': cities[0] if cities else None,
            'otherLocations': cities[1:],
            'places': self.place_entries(cities),
            'workType': 'remote' if remote else ('onsite' if cities else None),
            'employmentType': ('intern' if internship else 'full-time') if internship is not None else kind,
            'department': info.get('job_category') or None,
            'postedAt': self.dates.get(job_id),
            'dateOnly': True,
            'jobUrl': url,
            'applyUrl': url,
            'companySlug': None,
            'companyWebsite': None,
            'skills': list(info.get('keywords') or record.get('keywords') or []),
            'description': text_of(html),
            'descriptionHtml': html,
            '_exp': exp,
            '_size': SIZE_OF.get(employees),
            '_employer': employer_id,
            '_answered': answer is not None,
            **stipend(info.get('internship') if internship else None),
        }
        job['extra'] = {
            'experienceMin': exp[0],
            'experienceMax': exp[1],
            'skills': job['skills'],
            'jobFunctions': functions,
            'companyEmployees': HEADCOUNT.get(employees),
            'companySize': SIZE_OF.get(employees),
            'companyFounded': employer.get('company_founded') or known.get('founded'),
            'companyTagline': (employer.get('company_tagline') or '').strip() or known.get('tagline'),
            'companyLogo': logo,
            'relocationOk': info.get('accept_outstation', record.get('accept_outstation')),
            'agencyName': agency if agency and company and agency.casefold() != company.casefold() else None,
            'internshipMonths': (info.get('internship') or {}).get('duration') if internship else None,
            'internshipStart': (info.get('internship') or {}).get('start_date') if internship else None,
        }
        return job

    def copy_key(self, job):
        """One title in the same cities with the same skill tags is one job, posted twice: a company that posts one
        title for other cities or other skills (Accenture does, often) has different openings. The search's records
        carry the skills and no experience, so the skills decide."""
        cities = tuple(sorted({' '.join(c.casefold().split()) for c in [job.get('location') or '',
                                                                      *(job.get('otherLocations') or [])] if c}))
        return (dedupe_key(job)[0], cities,
                tuple(sorted({s.casefold() for s in job.get('skills') or [] if isinstance(s, str)})))

    # ---------- the search ----------

    def query(self, company: str | None) -> list:
        """The run's filters in Instahyre's own words."""
        cfg = self.cfg
        query = [('company_size', 0), ('job_type', 0)]
        sizes = (cfg.extra or {}).get('company_sizes') or []
        if len(sizes) == 1:
            query[0] = ('company_size', SIZE_CODES[sizes[0]])
        kinds = [JOB_TYPE_CODES[t] for t in cfg.job_types if t in JOB_TYPE_CODES]
        if len(kinds) == 1:
            query[1] = ('job_type', kinds[0])
        query += [('skills', term) for term in cfg.search_terms]
        query += [('jobLocations', place) for place in self.search_places(cfg) or []]
        exp = (cfg.extra or {}).get('experience_years')
        if exp is not None:
            query.append(('years', exp))
        if company:
            query.append(('companies', company))
        return query

    def search_places(self, cfg) -> list[str] | None:
        """The places of the run in Instahyre's names, or None when one of them has none (then the search is not
        narrowed by place and places are matched here). A remote search asks for Work From Home too."""
        named, other, states = self.places()
        found: list[str] = []
        for typed in cfg.locations:
            key = ' '.join(typed.lower().split())
            if key in WORLD:
                return None
            if key in ('delhi ncr', 'ncr', 'delhi / ncr', 'delhi-ncr', 'delhi/ncr'):
                found.append('Delhi / NCR')
            elif key in named:
                found.append(named[key][0])
            elif key in other:
                found.append(other[key][0])
            elif key in states:
                found += states[key]
            else:
                return None
        if found and cfg.work_types == ['remote']:
            found.append(REMOTE_PLACE)
        elif not cfg.locations and cfg.work_types == ['remote']:
            found = [REMOTE_PLACE]
        return list(dict.fromkeys(found))

    async def read_search(self, http, query: list, first: dict, dry: bool = False, most: int | None = None,
                          offset: int = PAGE_SIZE) -> tuple[list, str]:
        """Records of a search page by page, past the 10,000 window by company size when it is bigger -> (records, how
        the read ended: 'whole', 'dry', 'most', 'time' or 'refused'). dry: a word was searched, so two pages in a row
        with no match end the read, since Instahyre ranks a word's matches first (on 2026-11-08, 99 of the 100 python
        developer jobs were on the first 6 of its 184 pages). most: pages to read at most."""
        total = (first.get('meta') or {}).get('total_count') or 0
        if total + PAGE_SIZE > WINDOW and dict(query).get('company_size') == 0:
            records, how = [], 'whole'
            for code in (1, 3, 2):
                part = [(k, code if k == 'company_size' else v) for k, v in query]
                try:
                    head = await self.search_page(http, part, 0)
                except Refused:
                    return records, 'refused'
                got, how = await self.read_search(http, part, head, dry, most)
                records += got
                if how not in ('whole', 'dry'):
                    break
            return records, how
        records, pages = list(first.get('objects') or []), 1         # offset: where the next page starts
        empty = 0 if not records or any(self.wanted(self.job(r)) for r in records) else 1
        while offset < total and offset + PAGE_SIZE <= WINDOW:
            if dry and empty >= 2:
                return records, 'dry'
            if most is not None and pages >= most:
                return records, 'most'
            if out_of_time():
                return records, 'time'
            self.progress = f'Read {len(records):,} of {total:,} jobs of Instahyre\'s search.'
            try:
                page = await self.search_page(http, query, offset)
            except Refused:
                return records, 'refused'
            got = page.get('objects') or []
            records += got
            pages += 1
            if not got:
                break
            empty = 0 if any(self.wanted(self.job(r)) for r in got) else empty + 1
            offset += PAGE_SIZE
        return records, 'whole'

    async def intern_ids(self, http, query: list) -> set[int]:
        """The internships of a search that is not narrowed by job type: a second, small search."""
        part = [(k, 2 if k == 'job_type' else v) for k, v in query]
        try:
            first = await self.search_page(http, part, 0)
            records, _ = await self.read_search(http, part, first)
        except BoardError:
            return set()
        return {int(r['id']) for r in records if r.get('id')}

    async def from_search(self, http, query: list, records: list, how: str, total: int, need: int | None,
                          cutoff: str | None, found: dict) -> list[dict]:
        """A search's records in id order. The newest need matches get their own answers (or pages, when Instahyre
        refuses those) and dates; one that its answer rules out (closed since, other experience) makes room for the
        next. With a posting period every match inside it gets its date, so the count is right. found: jobs a walk
        already answered."""
        cfg = self.cfg
        if how in ('time', 'refused', 'most'):
            self.incomplete = True
            why = {'time': 'the run was about to reach its time limit', 'most': 'the rest would take too long',
                   'refused': self.paces['search'].refused or 'Instahyre refused the search'}[how]
            cfg.notes.append(f'Read {len(records):,} of {total:,} jobs of Instahyre\'s search: {why}.')
        code = dict(query).get('job_type')
        interns = await self.intern_ids(http, query) if cfg.job_types and code == 0 else set()
        jobs, keys = [], set()
        for record in sorted(records, key=lambda r: int(r.get('id') or 0), reverse=True):
            job_id = int(record.get('id') or 0)
            if not job_id:
                continue
            kind = CODE_JOB_TYPE.get(code) or (('intern' if job_id in interns else 'full-time') if cfg.job_types else None)
            job = found.get(job_id) or self.job(record, self.answers.get(job_id), kind)
            key = (job['company'] or '').casefold(), *self.copy_key(job)
            if key in keys or not self.wanted(job):
                continue
            keys.add(key)
            jobs.append(job)
        if cutoff:
            jobs = jobs[:await self.cutoff_index([j['rank'] for j in jobs], cutoff)]
        records_by_id = {int(r['id']): r for r in records if r.get('id')}
        kept, queue, failed, from_pages = [], list(jobs), 0, 0
        while queue and (need is None or len(kept) < need):
            batch = queue[:len(queue) if need is None else need - len(kept)]
            queue = queue[len(batch):]
            failed += await self.answer_all(http, [j['rank'] for j in batch if not j['_answered']])
            for job in batch:
                info = self.answers.get(job['rank'])
                record = records_by_id.get(job['rank'], {'id': job['rank']})
                if not job['_answered'] and job['rank'] not in self.answers:       # its answer was refused: its page
                    read = await self.page(job['rank'])
                    if read and read.get('date'):
                        info, from_pages = self.from_page(record, read), from_pages + 1
                if not job['_answered'] and info is not None:
                    job = self.job(record, info)
                if info is not None and not info.get('is_active', True):
                    continue                         # closed since Instahyre's search last saw it
                if job['_answered'] and not self.wanted(job):
                    continue                         # its own answer rules it out (experience)
                kept.append(job)
            if out_of_time():
                break
        rest = queue
        if from_pages:
            cfg.notes.append(f'Instahyre refused the job answers, so {from_pages:,} jobs were read from their pages, '
                             f'which name no recruiter company or stipend.')
        if failed > from_pages:
            cfg.notes.append(f'{failed - from_pages:,} jobs could not be read one by one, so they come without '
                             f'experience.')
        await self.fill_dates([j['rank'] for j in kept + (rest if cutoff else [])])
        await self.company_facts(http, kept)
        for job in kept + rest:
            job['postedAt'] = self.dates.get(job['rank'])
        return kept + rest

    async def answer_all(self, http, ids: list[int]) -> int:
        """Own answers for these jobs -> how many could not be read."""
        results = await asyncio.gather(*(self.answer(http, i) for i in ids), return_exceptions=True)
        return sum(1 for r in results if isinstance(r, Exception))

    # ---------- the walk ----------

    async def exists(self, http, job_id: int) -> bool:
        """Whether a job has this id, published or not: its answer, or its page once Instahyre refuses the answers."""
        try:
            return await self.answer(http, job_id) is not None
        except Refused:
            got = await self.browser_get(PAGE.format(id=job_id))
            return got is not None and got[0] != 404

    async def open_job(self, http, job_id: int) -> dict | None:
        """An open job's answer; its page standing in once Instahyre refuses the answers. None: no open job there."""
        try:
            info = await self.answer(http, job_id)
            return info if info and info.get('is_active') else None
        except Refused:
            read = await self.page(job_id)
            return self.page_answer(job_id, read) if read and read.get('date') else None

    async def newest_id(self, http, hint: int) -> int:
        """The highest job id: up from one known to exist in growing steps to an id with no job, then halving the gap.
        Four ids past the top are tried too, in case a job just below it was deleted."""
        low, step = hint, TOP_STEP
        while await self.exists(http, low + step):
            low, step = low + step, step * 2
        high = low + step
        while high - low > 1:
            middle = (low + high) // 2
            if await self.exists(http, middle):
                low = middle
            else:
                high = middle
        for ahead in range(high + 1, high + 5):
            if await self.exists(http, ahead):
                return await self.newest_id(http, ahead)
        return low

    async def first_open(self, http, top: int) -> int:
        """Where a walk starts. The newest ids are jobs Instahyre has not published yet: on 2026-11-08 the 193 ids
        below the top were all unpublished, in one block, and the 165 below them all open jobs. So ids PENDING_STEP
        apart are tried down from the top until one is an open job, and the walk starts PENDING_STEP - 1 above it: 14
        answers instead of 193, and on that map no open job passed over (an open job inside the block, between two ids
        tried, would be)."""
        for job_id in range(top, max(top - PENDING_STEP * PENDING_TRIES, 0), -PENDING_STEP):
            try:
                if await self.open_job(http, job_id):
                    return min(top, job_id + PENDING_STEP - 1)
            except BoardError:
                return top
        return top

    async def walk(self, http, top: int, need: int | None, cutoff: str | None, search_s: float | None,
                   found: dict) -> tuple[str, int]:
        """Newest ids down from top, every filter checked here, into found (id -> job) -> (why it stopped, the next id
        it would read): 'enough', 'cutoff' (older than the posting period), 'bottom', 'slow' (the search is faster),
        'time', 'cap' or 'refused'."""
        current, read, keys = top, 0, set()
        while current > 0:
            if read >= WALK_CAP:
                return 'cap', current
            if out_of_time():
                return 'time', current
            rate = len(found) / read if read else 1.0          # matches per id so far; the first batch assumes all
            wanted = need - len(found) if need is not None else WALK_BATCH
            size = WALK_BATCH if cutoff and need is None else min(WALK_BATCH, max(5, int(wanted / max(rate, 0.2)) + 3))
            ids = list(range(current, max(current - size, 0), -1))
            results = await asyncio.gather(*(self.open_job(http, i) for i in ids), return_exceptions=True)
            if self.paces['detail'].refused and self.page_trouble:          # answers and pages both refused
                return 'refused', current
            read += len(ids)
            fresh = [self.job({'id': i}, r) for i, r in zip(ids, results) if isinstance(r, dict)]
            if cutoff:
                await self.fill_dates([j['rank'] for j in fresh])
            for job in fresh:
                job['postedAt'] = self.dates.get(job['rank'])
                if cutoff and job['postedAt'] and job['postedAt'] < cutoff:
                    return 'cutoff', job['rank']
                key = (job['company'] or '').casefold(), *self.copy_key(job)
                if key not in keys and self.wanted(job):
                    keys.add(key)
                    found[job['rank']] = job
            current = ids[-1] - 1
            self.progress = f'Read the newest {read:,} jobs on Instahyre one by one, {len(found):,} matching so far.'
            if need is not None and len(found) >= need:
                return 'enough', current
            if search_s is not None and need is not None and read >= WALK_SAMPLE:
                rate = len(found) / read
                left = (need - len(found)) / rate * DETAIL_GAP if rate else math.inf
                if left > 1.25 * search_s:
                    return 'slow', current
        return 'bottom', current

    # ---------- dates ----------

    async def page_date(self, job_id: int) -> str | None:
        """datePosted from a job's page. None when the page cannot be read or holds none (a closed job's page)."""
        if job_id in self.dates:
            return self.dates[job_id]
        return (await self.page(job_id) or {}).get('date')

    async def page(self, job_id: int) -> dict | None:
        """A job's page, read once a run (read_page). None when it cannot be read, or when the job is not published (its
        address then redirects to the search)."""
        if job_id in self.read_pages:
            return self.read_pages[job_id]
        got = await self.browser_get(PAGE.format(id=job_id))
        if got is None:
            return None
        status, url, text = got
        if status != 200 or not JOB_LINK.match(urlparse(url).path):
            self.read_pages[job_id] = None
            return None
        read = read_page(text, job_id)
        self.read_pages[job_id] = read
        if read.get('date'):
            self.dates[job_id] = read['date']
        return read

    async def browser_get(self, url: str) -> tuple[int, str, str] | None:
        """One of Instahyre's pages through curl_cffi's Chrome (they need a browser's TLS), at the page pace -> (status,
        final address, text). None when pages cannot be read; page_trouble says why once that stops them for the run."""
        if self.page_trouble:
            return None
        if self._pages is None:
            try:
                from curl_cffi.requests import AsyncSession
            except ImportError:
                self.page_trouble = 'curl_cffi is not installed, and Instahyre shows its pages only to browsers'
                return None
            self._pages = AsyncSession(impersonate='chrome', timeout=30)
        pace = self.paces['page']
        for attempt in range(3):
            await pace.turn()
            started = time.monotonic()
            try:
                response = await self._pages.get(url)
            except Exception as exc:  # noqa: BLE001  curl_cffi raises its own errors
                STATS['retries'][type(exc).__name__] += 1
                await asyncio.sleep(2 + 2 * attempt)
                continue
            STATS['calls'] += 1
            STATS['seconds'] += time.monotonic() - started
            self.counts['jobPages'] += 1
            if response.status_code == 429:
                STATS['retries']['429'] += 1
                self.refusals['page'] = self.refusals.get('page', 0) + 1
                wait = _seconds(response.headers.get('retry-after'))
                if wait > LONGEST_WAIT:
                    self.page_trouble = (f'Instahyre asked this run to wait {max(round(wait / 60), 2)} minutes for '
                                         f'its job pages, which hold the date')
                    return None
                pace.hold(wait + 1)
                continue
            if response.status_code in (403, 503) and 'just a moment' in response.text[:4000].lower():
                pace.challenged += 1
                if pace.challenged >= 3:
                    self.page_trouble = 'Cloudflare showed its browser check instead of the job pages, which hold the date'
                    return None
                await asyncio.sleep(3)
                continue
            return response.status_code, str(response.url), response.text
        return None

    async def company_by_slug(self, http, slug: str) -> str | None:
        """The company of a pasted company page (instahyre.com/jobs-at-<slug>/), by the employer id the page names."""
        got = await self.browser_get(COMPANY_PAGE.format(slug=slug))
        found = EMPLOYER_ID.search(got[2]) if got and got[0] == 200 else None
        if not found:
            return None
        known = self.companies().get(found.group(1))
        return known['name'] if known else ((await self.profile(http, found.group(1))) or {}).get('company_name')

    def page_answer(self, job_id: int, read: dict) -> dict:
        """A job's page alone -> an answer of the job's own shape, for a walk when Instahyre refuses the answers."""
        record = {'id': job_id, 'title': read.get('title'), 'locations': ','.join(read.get('locations') or []),
                  'keywords': read.get('skills') or [],
                  'employer': {'company_name': read.get('company'), 'profile_image_src': read.get('logo')}}
        return self.from_page(record, read)

    def from_page(self, record: dict, read: dict) -> dict:
        """A search record and a job's page -> an answer of the job's own shape, for when Instahyre refuses those: the
        page has the experience, category, functions and description, and no recruiter or stipend."""
        category, *functions = read.get('function') or [None]
        return {'id': record.get('id'), 'is_active': True, 'title': record.get('title'),
                'hiring_company_name': (record.get('employer') or {}).get('company_name'),
                'employer_profile_url': (record.get('employer') or {}).get('profile_image_src'),
                'locations': [c.strip() for c in (record.get('locations') or '').split(',') if c.strip()],
                'keywords': record.get('keywords'), 'accept_outstation': record.get('accept_outstation'),
                'workex_min': read.get('experience', (None, None))[0], 'workex_max': read.get('experience', (None, None))[1],
                'job_category': category, 'job_function_dict': {category: functions} if category and functions else {},
                'description': read.get('descriptionHtml'), 'is_internship': bool(read.get('internship')),
                'opportunity_url': None}

    async def fill_dates(self, ids: list[int]) -> None:
        """Dates for these ids from as few pages as needed: two ids posted the same day date every id between them,
        since ids are given in the order jobs are posted. Every date known this run is an anchor."""
        wanted = {i for i in ids if i not in self.dates}
        if not wanted:
            return
        low, high = min(wanted), max(wanted)
        line = sorted(wanted | {i for i in self.dates if low <= i <= high}, reverse=True)
        segments = [(0, len(line) - 1)]
        await asyncio.gather(*(self.page_date(line[i]) for i in {0, len(line) - 1}))
        while segments and not self.page_trouble:
            ask, nxt = set(), []
            for i, j in segments:
                di, dj = self.dates.get(line[i]), self.dates.get(line[j])
                if di is not None and di == dj:
                    for k in range(i + 1, j):
                        self.dates.setdefault(line[k], di)
                    continue
                if j - i <= 1:
                    continue
                middle = (i + j) // 2
                ask.add(line[middle])
                nxt += [(i, middle), (middle, j)]
            await asyncio.gather(*(self.page_date(i) for i in ask))
            segments = nxt

    async def cutoff_index(self, ids_desc: list[int], cutoff: str) -> int:
        """How many of these ids (newest first) were posted on or after the cutoff day: a binary search over pages."""
        low, high = 0, len(ids_desc)               # ids_desc[:low] are in, ids_desc[high:] are out
        while low < high:
            middle = (low + high) // 2
            day = await self.page_date(ids_desc[middle])
            if day is None:
                return high if self.page_trouble else low
            if day >= cutoff:
                low = middle + 1
            else:
                high = middle
        return low

    # ---------- pages: one place, the site ----------

    async def pages(self, http, session, term, days):
        """The run's jobs, newest first, by the cheaper way (module docstring): a company's search read whole; with
        words, the search until its matches run dry, or a walk when they do not and the rest would take long; without,
        a walk unless the search is small. A slow walk hands over to the search."""
        cfg = self.cfg
        company = session.get('company')
        need = cfg.max_jobs
        cutoff = None
        if cfg.posted_within_days is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=cfg.posted_within_days)).date().isoformat()
        query = self.query(company)
        self.progress = 'Asking Instahyre\'s search.'
        try:
            first = await self.search_page(http, query, 0)
        except Refused:
            if company or (need is None and cutoff is None):
                raise
            first = None
            cfg.notes.append(f'{self.paces["search"].refused}, so its newest jobs were read one by one instead.')
        total = ((first or {}).get('meta') or {}).get('total_count') or 0
        pages_left = max(math.ceil(min(total, WINDOW) / PAGE_SIZE) - 1, 0)
        if total + PAGE_SIZE > WINDOW:
            pages_left = math.ceil(total / PAGE_SIZE) + 2
        found: dict[int, dict] = {}
        records, how_read, way = None, None, 'search'
        walkable = not company and (need is not None or cutoff is not None)
        if first is not None and cfg.search_terms and walkable and pages_left > DRY_PAGES:
            records, how_read = await self.read_search(http, query, first, dry=True, most=DRY_PAGES)
            if how_read == 'most':                       # still finding matches after DRY_PAGES pages
                rest = pages_left - DRY_PAGES
                if rest * SEARCH_GAP <= LONG_SEARCH_S:
                    more, how_read = await self.read_search(http, query, {'meta': first['meta'], 'objects': []},
                                                            dry=True, offset=PAGE_SIZE * DRY_PAGES)
                    records += more
                else:
                    records, how_read, way = None, None, 'walk'
        elif first is None:
            way = 'walk' if walkable else 'search'
        elif walkable and not cfg.search_terms and pages_left * SEARCH_GAP > SMALL_SEARCH_S:
            # a walk reads every new id and keeps the matches: Instahyre's count for these filters over all its jobs is
            # how many ids it reads per match (freshers, 512 of 13,453: about 790 ids for 30 jobs, against 15 pages)
            density = min(1.0, total / max(self.all_jobs(), total, 1))
            ids = need / density if need is not None else (cfg.posted_within_days or 0) * IDS_PER_DAY
            walk_s = min(ids, WALK_CAP) * DETAIL_GAP + WALK_START_S
            way = 'walk' if walk_s < pages_left * SEARCH_GAP else 'search'
        stop, next_id, top = None, None, None
        if way == 'walk':
            hint = max([int(r['id']) for r in (first or {}).get('objects') or [] if r.get('id')] + [NEWEST_KNOWN])
            self.progress = 'Finding Instahyre\'s newest job.'
            top = await self.first_open(http, await self.newest_id(http, hint))
            stop, next_id = await self.walk(http, top, need, cutoff, pages_left * SEARCH_GAP if first else None, found)
            # a refused walk hands over only to a search that can finish (the empty form's is 385 pages behind a limit
            # of about 250 an hour): otherwise the jobs found so far are delivered, marked partial
            if first is not None and (stop == 'slow' or (stop == 'refused' and pages_left * SEARCH_GAP <= LONG_SEARCH_S)):
                way = 'search'
            elif stop in ('time', 'cap', 'refused'):
                self.incomplete = True
        if way == 'search':
            if records is None:
                records, how_read = await self.read_search(http, query, first, dry=bool(cfg.search_terms))
            jobs = await self.from_search(http, query, records, how_read, total, need, cutoff, found)
            if how_read == 'dry':
                cfg.notes.append(f'Read Instahyre\'s search for these words until its pages stopped matching: '
                                 f'{len(records):,} of the {total:,} jobs it lists for them.')
        else:
            jobs = sorted(found.values(), key=lambda j: j['rank'], reverse=True)
            jobs = jobs[:need] if need is not None else jobs
            await self.fill_dates([j['rank'] for j in jobs])
            await self.company_facts(http, jobs)
            for job in jobs:
                job['postedAt'] = self.dates.get(job['rank'])
            why = {'enough': 'the newest matches were found', 'cutoff': 'the jobs past it are older than the period',
                   'bottom': 'it reached the oldest job', 'time': 'the run was about to reach its time limit',
                   'cap': f'it read {WALK_CAP:,} jobs, the most one run reads this way',
                   'refused': self.paces['detail'].refused or 'Instahyre refused the calls'}[stop]
            cfg.notes.append(f'Read Instahyre\'s jobs one by one, newest first, from job {top} down to job {next_id + 1}: '
                             f'{why}.' + (f' Instahyre\'s own search counts {total:,} jobs for these filters.'
                                          if total else ''))
        if self.page_trouble:
            undated = sum(1 for j in jobs if not j.get('postedAt'))
            if undated:
                cfg.notes.append(f'{undated:,} jobs have no posting date: {self.page_trouble}.')
        if self.refusals:
            kinds = {'search': 'searches', 'detail': 'job answers', 'profile': 'company pages', 'page': 'job pages'}
            cfg.notes.append('Instahyre asked this run to slow down ' + ', '.join(
                f'{n:,} time{"" if n == 1 else "s"} for its {kinds.get(k, k)}' for k, n in sorted(self.refusals.items()))
                + ', and it waited.')
        self.progress = None
        for i in range(0, len(jobs), 200):
            yield jobs[i:i + 200]

    async def company_facts(self, http, jobs: list[dict]) -> None:
        """Headcount and founding year for jobs whose company is not on our list (a company new to Instahyre), from
        its company page; a company size filter is checked again once the headcount is known."""
        missing = {j['_employer'] for j in jobs if j['_employer'] and j['extra'].get('companyEmployees') is None}
        for employer in sorted(missing)[:60]:
            if out_of_time():
                break
            info = await self.profile(http, employer)
            if not info:
                continue
            for job in jobs:
                if job['_employer'] == employer:
                    employees = info.get('employee_count')
                    job['_size'] = SIZE_OF.get(employees)
                    job['extra'].update({'companyEmployees': HEADCOUNT.get(employees),
                                         'companySize': SIZE_OF.get(employees),
                                         'companyFounded': info.get('company_founded'),
                                         'companyTagline': (info.get('company_tagline') or '').strip() or None})
        sizes = set((self.cfg.extra or {}).get('company_sizes') or []) if self.cfg else set()
        if sizes:
            jobs[:] = [j for j in jobs if j['_size'] is None or j['_size'] in sizes]

    # ---------- details and links ----------

    async def details(self, http, session, job):
        """With details on: the description came with the job's own answer; this adds its company page: industry,
        website, LinkedIn, tech stack, benefits and Glassdoor ratings when Instahyre shows them."""
        if not job.get('_answered') and job.get('id'):
            info = await self.answer(http, int(job['id']))
            if info:
                job = {**self.job({'id': job['id']}, info), 'postedAt': job.get('postedAt')}
        info = await self.profile(http, job.get('_employer')) or {}
        social = info.get('social_accounts') or {}
        rated = info.get('glassdoor_data') or {}
        ratings = {name: _number(rated.get(key)) for key, name in RATINGS.items() if _number(rated.get(key)) is not None}
        job['companySlug'] = info.get('slug') or None
        job['companyWebsite'] = social.get('website') or (self.read_pages.get(job['rank']) or {}).get('website')
        job['extra'] = {**job.get('extra', {}),
                        'companyIndustry': info.get('industries') or None,
                        'companyLinkedin': social.get('linkedin') or None,
                        'companyTechStack': info.get('tech_stack') or None,
                        'companyBenefits': info.get('benefits') or None,
                        'companyRating': ratings.get('overall'),
                        'companyRatings': ratings or None,
                        'companyPage': COMPANY_PAGE.format(slug=info['slug']) if info.get('slug') else None}
        return job

    async def lookup(self, http, session, job_id):
        info = await self.answer(http, int(job_id))
        if info is None:
            return 'not found', {'company': None, 'companySlug': None, 'companyWebsite': None}
        if not info.get('is_active'):
            return 'closed', {'company': info.get('hiring_company_name'), 'companySlug': None, 'companyWebsite': None}
        await self.page_date(int(job_id))
        job = self.job({'id': job_id}, info)
        await self.company_facts(http, [job])
        return 'open', (await self.details(http, session, job)) if self.with_details else job
