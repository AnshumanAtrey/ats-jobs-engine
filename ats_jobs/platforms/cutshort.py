"""
Cutshort (cutshort.io), India's hiring site for tech and startup jobs: every live job, read the way its own pages read
them. No login and no key. Measured from this laptop on 2026-11-08.

- GET https://cutshort.io/sitemap_jobs.xml: every live job page, the newest posted first (44,963 jobs; 8.6 MB, 1.5 MB
  gzipped, in 1.4 to 3 s; never cached). A job's address is its title, its places and its company, then an 8-character
  id (/job/Senior-Python-Developer-Pune-Acme-AbCd1234), equal to the job's own publicUrl on 60 of 60 read, so a search
  for words reads only the jobs whose address holds them, and a search for places only the jobs that name the place.
- GET https://cutshort.io/backend-api/webpage/job/<id>: the answer the job's page is built from: title, places, remote
  policy, job type, experience, pay as shown, skills, the company (type, size, funding stage, founded, website), the
  description, and the page's JobPosting markup (datePosted, pay as numbers with its period, the first place's
  country). About 25 KB (5 KB gzipped); Cloudflare keeps a copy for 4 hours. It takes the 8-character id or the job's
  24-character ObjectId. A job that is gone answers 400 with job_deactivated or job_deleted (closed) or job_not_found.
- GET https://cutshort.io/backend-api/webpage/company/<alias>?page=N: a company's page and its open jobs.
- GET https://cutshort.io/sitemap_companies.xml: every company page (16,202), read only to find a company typed by name.
robots.txt disallows /profile/, /apply/, /view/j/ and ?free_text= search pages; none of them is read. The site's own
search (/finddemojobsignals/q?free_text=) is one of them, ranks by relevance and sends about 1 MB a job.

Dates: postedAt is when the job was first posted: the JobPosting markup's datePosted, also the time in its ObjectId and
the order of the sitemap, so newest first holds exactly. The page shows "Posted <date>" from hiringIntentShownOn
(lastPostedAt): when the job went live after review, minutes later for most, or when the company re-posted it. In the
newest 400 jobs of three listings it was more than a day later for 1.8% to 4.5% (python, remote and sales jobs), most of
them over 30 days; 3 of the 60 newest jobs went live 1 to 2 days after they were created.

Remote: Cutshort marks a job remote only, remote friendly ("Remote, Coimbatore": remote or that office) or neither, and
its own list of remote jobs holds only the first kind (50 of 50): workType remote, hybrid and onsite. A remote-only job
names no place or country (the markup says "NA"); it is read as remote in India, where Cutshort hires, unless its pay is
in another currency. The markup gives the first place's country; Cutshort writes Bangalore as "Bengaluru (Bangalore)".

Job types: Cutshort has full time (its employer form says "Full time / Contractual") and internship.

Pace: Cloudflare refuses an address for about 5 minutes (HTTP 429, error code 1015, a Retry-After) after a burst: 2, 4,
8 and 16 calls a second for 30 to 120 s passed (1,560 calls in 7 minutes), about 45 a second did not. Calls start PACE
apart; the first refusal is waited out once, with no call going out, and a second one stops the run's calls.
"""
import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from ..http import STATS, BoardError
from ..inputs import EVERY, _items, parse_bool, parse_choices, parse_terms
from ..rows import Filters, Place, Term, country_code, fold, index, parse_time, words
from .base import Board, Platform
from .cornerstone import text_of

try:
    from pycountry import countries as _COUNTRIES
except ImportError:           # the actor installs pycountry with the engine
    _COUNTRIES = None

SITE = 'https://cutshort.io'
JOBS_SITEMAP = SITE + '/sitemap_jobs.xml'
COMPANIES_SITEMAP = SITE + '/sitemap_companies.xml'
JOB_API = SITE + '/backend-api/webpage/job/{id}'
COMPANY_API = SITE + '/backend-api/webpage/company/{alias}'
JOB_LINK = SITE + '/job/{slug}'
HOSTS = ('cutshort.io', 'www.cutshort.io')
EVERYONE = Board('*', 'Cutshort')     # every job of every company (the sitemap); also the board of a job link
NOBODY = {'company': None, 'companySlug': None, 'companyWebsite': None}   # a gone job whose company is unknown
LOC = re.compile(r'<loc>\s*https?://(?:www\.)?cutshort\.io/job/([^<\s]+?)\s*</loc>')
COMPANY_LOC = re.compile(r'<loc>\s*https?://(?:www\.)?cutshort\.io/company/([^<\s]+?)\s*</loc>')
SHORT_ID = re.compile(r'^[A-Za-z0-9]{8}$')
OBJECT_ID = re.compile(r'^[0-9a-f]{24}$')
ALIAS = re.compile(r'^\S+-[A-Za-z0-9]{8}$')         # a company page: egnyte-SClW7oZH
PACE = 0.125                  # seconds between two call starts: 8 a second, half the rate that passed (docstring)
RETRY_S = (2, 5)              # waits before a 5xx or a broken connection is tried again
REFUSAL_WAIT_CAP = 330        # the longest Retry-After waited out (Cloudflare's 1015 lasts about 5 minutes)
PAGE = 50                     # jobs read and handed to the engine at a time
READ_BUDGET = 5000            # job pages read at most when a number of jobs is asked for: about 11 minutes at PACE
WORK_TYPES = {'remote_only': 'remote', 'remote_okay': 'hybrid', 'remote_not_okay': 'onsite'}
PERIODS = {'YEAR': 'year', 'MONTH': 'month', 'WEEK': 'week', 'DAY': 'day', 'HOUR': 'hour'}
HIDDEN_PAY = {'best in industry', 'not disclosed'}
STAGES = ('bootstrapped', 'funded', 'profitable')
STAGE_ALIASES = {'bootstrapped': 'bootstrapped', 'self funded': 'bootstrapped', 'self-funded': 'bootstrapped',
                 'funded': 'funded', 'raised funding': 'funded', 'vc funded': 'funded', 'venture funded': 'funded',
                 'funded startup': 'funded', 'profitable': 'profitable'}
STAGE_OF = {'bootstrapped': 'bootstrapped', 'raised funding': 'funded', 'profitable': 'profitable'}
TYPES = ('product', 'services')
TYPE_ALIASES = {'product': 'product', 'products': 'product', 'product based': 'product', 'product company': 'product',
                'service': 'services', 'services': 'services', 'service based': 'services', 'it services': 'services',
                'consulting': 'services', 'agency': 'services'}
# Cutshort writes a few cities by one name; people type either (it already writes "Bengaluru (Bangalore)")
CITY_NAMES = (('gurgaon', 'Gurugram'), ('gurugram', 'Gurgaon'), ('bombay', 'Mumbai'), ('madras', 'Chennai'),
              ('calcutta', 'Kolkata'), ('cochin', 'Kochi'), ('trivandrum', 'Thiruvananthapuram'),
              ('mysore', 'Mysuru'), ('baroda', 'Vadodara'), ('poona', 'Pune'), ('new delhi', 'Delhi'))
# listings that are about the company, not a skill: their slug -> the setting they stand for
COMPANY_LISTINGS = {'product-based-company': ('companyType', 'product'), 'funded-startup': ('companyStage', 'funded'),
                    'startup': None, 'startups': None}


def short_id(slug: str | None) -> str | None:
    """'Senior-Python-Developer-Pune-Acme-AbCd1234' -> 'AbCd1234'."""
    tail = (slug or '').rstrip('/').rsplit('-', 1)[-1]
    return tail if SHORT_ID.match(tail) else None


def created_at(object_id: str | None) -> str | None:
    """A MongoDB ObjectId starts with its creation time: '6a69d90f...' -> '2026-07-29T10:42:23+00:00'."""
    if not object_id or not OBJECT_ID.match(object_id):
        return None
    return datetime.fromtimestamp(int(object_id[:8], 16), timezone.utc).isoformat()


def country_name(code: str | None) -> str | None:
    """'IN' -> 'India'; a code pycountry does not know stays as it is."""
    if not code:
        return None
    found = None
    try:
        found = _COUNTRIES.get(alpha_2=code.upper()) if _COUNTRIES and len(code) == 2 else None
    except (KeyError, LookupError):
        found = None
    return (getattr(found, 'common_name', None) or found.name) if found else code


def job_posting(data: dict) -> dict:
    return next((ld for ld in (data.get('seoData') or {}).get('jsonlds') or []
                 if isinstance(ld, dict) and ld.get('@type') == 'JobPosting'), {})


def number(value) -> float | int | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def pay(page: dict, posting: dict) -> dict:
    """The pay the job's page shows: its text, and from the markup its numbers, currency and period. A hidden pay
    ("Best in industry", markup "Not Disclosed") is none; "Upto ..." (showOnlyMaxSalary) has no lowest amount."""
    shown = ' '.join(str(page.get('salaryRange') or '').split())
    if shown.lower() in HIDDEN_PAY or not re.search(r'\d', shown):
        return {'salary': None, 'salaryMin': None, 'salaryMax': None, 'salaryCurrency': None, 'salaryPeriod': None}
    base = posting.get('baseSalary') if isinstance(posting.get('baseSalary'), dict) else {}
    value = base.get('value') if isinstance(base.get('value'), dict) else {}
    low, high = number(value.get('minValue')), number(value.get('maxValue'))
    if page.get('showOnlyMaxSalary'):
        low = None
    numbers = low is not None or high is not None
    return {'salary': shown, 'salaryMin': low, 'salaryMax': high,
            'salaryCurrency': (base.get('currency') or None) if numbers else None,
            'salaryPeriod': PERIODS.get(str(value.get('unitText') or '').upper()) if numbers else None}


def places_of(page: dict, posting: dict, currency: str | None) -> list[dict]:
    """'Remote, Coimbatore' -> [Coimbatore]; 'Remote only' -> [Remote only, in India unless paid in another currency].
    The markup's address gives the first place's country."""
    kind = page.get('remoteType')
    if kind == 'remote_only':
        return [{'name': 'Remote only', 'country': 'India' if currency in (None, 'INR') else None}]
    names = [' '.join(p.split()) for p in str(page.get('locations') or '').split(', ')]
    names = [n for n in names if n and n.lower() not in ('remote', 'remote only')]
    found = posting.get('jobLocation')
    first = found[0] if isinstance(found, list) and found else found if isinstance(found, dict) else {}
    address = first.get('address') if isinstance(first.get('address'), dict) else {}
    country = country_name(address.get('addressCountry'))
    return [{'name': n, 'city': n, 'country': country if i == 0 else None} for i, n in enumerate(names)]


def job_from(data: dict) -> dict | None:
    """The job page's answer -> one job in the shape of rows.py, Cutshort's own fields under extra."""
    page = data.get('pageData') if isinstance(data, dict) else None
    if not isinstance(page, dict) or not page.get('publicUrl'):
        return None
    posting = job_posting(data)
    url = page['publicUrl']
    slug = url.rstrip('/').rsplit('/job/', 1)[-1]
    company = page.get('companyDetails') if isinstance(page.get('companyDetails'), dict) else {}
    poster = page.get('companyId') if isinstance(page.get('companyId'), dict) else {}
    summary = page.get('jobFactSummary') if isinstance(page.get('jobFactSummary'), dict) else {}
    paid = pay(page, posting)
    spots = places_of(page, posting, paid['salaryCurrency'])
    posted = posting.get('datePosted') or created_at(page.get('_id'))
    roles = page.get('roleTypes') or []
    experience = page.get('expRange') if isinstance(page.get('expRange'), dict) else {}
    low = number(experience.get('minVanity', experience.get('min')))
    high = number(experience.get('maxVanity', experience.get('max')))
    links = company.get('links') if isinstance(company.get('links'), dict) else {}
    html = page.get('sanitizedComment') or None
    agency = page.get('hiringForClient') is True
    equity = True if re.search(r'\besops?\b', str(paid['salary'] or ''), re.I) else None   # "(ESOP available)"
    return {
        'id': short_id(slug) or page.get('_id'),
        'title': ' '.join(str(page.get('headline') or '').split()),
        'company': ' '.join(str(company.get('name') or summary.get('companyName') or '').split()) or None,
        'companySlug': company.get('alias') or None,
        'companyWebsite': links.get('website') or None,
        'location': spots[0]['name'] if spots else None,
        'otherLocations': [s['name'] for s in spots[1:]],
        'places': spots,
        'workType': WORK_TYPES.get(page.get('remoteType')),
        'employmentType': 'intern' if roles == ['internship'] else 'full-time' if 'full_time' in roles else None,
        **paid,
        'offersEquity': equity,
        'postedAt': posted,
        'jobUrl': url,
        'applyUrl': url,
        'description': text_of(html),
        'descriptionHtml': html,
        'extra': {
            'skills': [s for s in page.get('allSkills') or [] if isinstance(s, str)],
            'experienceMin': low,
            'experienceMax': high,
            'salaryMin': paid['salaryMin'],
            'salaryMax': paid['salaryMax'],
            'salaryCurrency': paid['salaryCurrency'],
            'salaryPeriod': paid['salaryPeriod'],
            'remotePolicy': summary.get('remotePolicy') or None,
            'companyType': company.get('type') if isinstance(company.get('type'), str) else None,
            'companySize': company.get('size') if isinstance(company.get('size'), str) else None,
            'companyStage': company.get('stage') if isinstance(company.get('stage'), str) else None,
            'companyFounded': number(company.get('founded')),
            'agency': (' '.join(str(poster.get('name') or '').split()) or None) if agency else None,
            'offersEquity': equity,
            'lastPostedAt': page.get('hiringIntentShownOn') or None,
        },
    }


def alias_names(alias: str) -> set[str]:
    """A company page's address -> the company's name as words, with and without the number Cutshort adds:
    'hello-mentor-private-limited-65-MaMzzYis' -> {'hello mentor private limited 65', 'hello mentor private limited'};
    'jobs-at-am:pm-network-31-iYNcGQRZ' -> {'am pm network 31', 'am pm network'}."""
    body = re.sub(r'^jobs-at-', '', alias.rsplit('-', 1)[0])
    names = {' '.join(words(body))}
    names.add(' '.join(words(re.sub(r'-\d+$', '', body))))
    return {n for n in names if n}


def listing_parts(slug: str) -> dict:
    """A Cutshort listing page -> what it searches: 'remote-python-jobs-in-pune' -> skill python, remote, place Pune.
    A city part holds every name of the city ('bangalore-bengaluru', 'delhi-ncr-gurgaon-noida'): each is one place."""
    slug = slug.lower().strip('/')
    found: dict = {'skills': [], 'places': [], 'remote': False, 'setting': None}
    if '-jobs-in-' in slug:
        slug, city = slug.split('-jobs-in-', 1)
        found['places'] = [w.capitalize() for w in city.split('-') if w]
    else:
        slug = slug.removesuffix('-jobs')
    if slug.startswith('remote-'):
        found['remote'], slug = True, slug.removeprefix('remote-')
    if slug in COMPANY_LISTINGS:
        found['setting'] = COMPANY_LISTINGS[slug]
    elif slug and slug != 'remote':
        found['skills'] = [' '.join(slug.split('-'))]
    return found


class Stopped(BoardError):
    """Cutshort's Cloudflare refused this address twice: no call goes out for the rest of the run."""


class Cutshort(Platform):
    key = 'cutshort'
    name = 'Cutshort'
    site_word = 'company page'
    actor = 'cutshort-jobs-search'
    board_example = 'https://cutshort.io/company/egnyte-SClW7oZH'
    job_example = 'https://cutshort.io/job/Sr-Backend-Engineering-Manager-Egnyte-koBBDTQ1'
    concurrency = 8               # pages of companies read at once and the size of the connection pool; PACE keeps the rate
    newest_first = True           # the sitemap lists jobs newest posted first, and a company's jobs are sorted so
    never = ('department', 'team', 'expiresAt', 'reference', 'region')

    def __init__(self):
        self.cfg = None
        self._turn = asyncio.Lock()
        self._last = 0.0
        self._waited = False          # a refusal was waited out once this run
        self._resumed = 0.0           # when that wait ended: a refusal to a call sent before it is the same refusal
        self._stopped: str | None = None
        self._jobs: list[str] | None = None          # the sitemap's job addresses, newest first
        self._aliases: list[str] | None = None       # the company pages, for names typed in companies
        self.read = 0                 # job pages read this run
        self.closed = 0               # jobs on the sitemap that closed before their page was read
        self.unreadable = 0           # job pages that did not answer, or answered in a shape not seen before
        self.oldest: str | None = None

    # ---------- what people type ----------

    def boards(self):
        """No list to ship: every search reads the sitemap, and a company is found by its link or its name."""
        return [], None

    def read_link(self, text):
        text = text.strip().rstrip('.,;:!?')
        link = text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}'
        parsed = urlparse(link)
        host = (parsed.hostname or '').lower() if ' ' not in text else ''
        if host not in HOSTS:
            return None
        parts = [unquote(p) for p in parsed.path.split('/') if p]
        if len(parts) >= 2 and parts[0] == 'job':
            job = short_id(parts[1]) or (parts[1] if OBJECT_ID.match(parts[1]) else None)
            return (EVERYONE.key, job) if job else (None, None)
        if len(parts) >= 4 and parts[:3] == ['profile', 'view', 'j'] and OBJECT_ID.match(parts[3]):
            return EVERYONE.key, parts[3]                    # the page's own apply link, by the job's ObjectId
        if len(parts) >= 2 and parts[0] == 'company' and ALIAS.match(parts[1]):
            return parts[1], None
        return None, None                                    # the home page, a listing or a search: no company

    def guess_board(self, name):
        """A company typed by name: open() finds its page on the company sitemap."""
        return name if re.search(r'[^\W_]', name) else None

    def read_input(self, raw, cfg):
        """Cutshort's own fields, the listing and search links people paste, and the names a city also goes by."""
        self.cfg = cfg
        notes, skipped = cfg.notes, cfg.skipped
        skills: list[str] = []
        stages = parse_choices(raw.get('companyStage'), 'companyStage', STAGES, STAGE_ALIASES, notes)
        types = parse_choices(raw.get('companyType'), 'companyType', TYPES, TYPE_ALIASES, notes)

        def listing(text: str) -> bool:
            """A pasted Cutshort listing (/jobs/python-jobs-in-pune) or search (?free_text=) -> its settings."""
            link = text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}'
            parsed = urlparse(link)
            if (parsed.hostname or '').lower() not in HOSTS:
                return False
            parts = [p for p in parsed.path.split('/') if p]
            query = parse_qs(parsed.query)
            if len(parts) == 2 and parts[0] == 'jobs':
                found = listing_parts(parts[1])
            elif query.get('free_text'):
                found = {'skills': [query['free_text'][0].strip()], 'places': [], 'remote': False, 'setting': None}
            else:
                return False
            skills.extend(s for s in found['skills'] if s)
            for spot in found['places']:
                if spot.lower() not in (p.lower() for p in cfg.locations):
                    cfg.locations.append(spot)
            if found['remote'] and 'remote' not in cfg.work_types:
                cfg.work_types.append('remote')
            if found['setting']:
                key, value = found['setting']
                (types if key == 'companyType' else stages).append(value)
            what = ', '.join(filter(None, [' and '.join(found['skills']) or 'every job', 'remote' if found['remote']
                                           else '', ' or '.join(found['places']) and 'in ' + ' or '.join(found['places']),
                                           found['setting'] and f'{found["setting"][0]} {found["setting"][1]}']))
            notes.append(f'Read the Cutshort link {text} as {what}.')
            return True

        for text, why in list(skipped):            # links the shared reader could not place (a listing, a search)
            if listing(text.strip()):
                skipped.remove((text, why))
        for token in _items(raw.get('skills')):
            if not listing(token.strip()):
                skills += parse_terms([token], 'skills', notes, skipped)
        cfg.extra.update({
            'skills': list(dict.fromkeys(skills)),
            'experienceYears': self.read_years(raw.get('experienceYears'), notes),
            'minSalaryLakhs': self.read_lakhs(raw.get('minSalaryLakhs'), notes),
            'companyStage': list(dict.fromkeys(stages)) if len(set(stages)) < len(STAGES) else [],
            'companyType': list(dict.fromkeys(types)) if len(set(types)) < len(TYPES) else [],
            'excludeAgencies': parse_bool(raw.get('excludeAgencies'), False),
        })
        for typed, other in CITY_NAMES:
            if any(' '.join(words(p)) == typed for p in cfg.locations) and \
                    other.lower() not in (p.lower() for p in cfg.locations):
                cfg.locations.append(other)
                notes.append(f'Cutshort writes {typed.title()} as {other} on some jobs, so {other} is searched too.')
        if 'hybrid' in cfg.work_types:
            notes.append('Cutshort has no hybrid marker: hybrid finds its remote-friendly jobs, which can be done '
                         'remotely or from the office they name.')
        asked = [t for t in cfg.job_types if t not in ('full-time', 'intern')]
        if asked:
            notes.append(f'Cutshort has two job types, full time (which its employers use for contracts too) and '
                         f'internship, so {", ".join(asked)} finds no job.')

    @staticmethod
    def read_years(value, notes) -> list[float] | None:
        """3, '3', '3 years', '2-5', 'fresher' -> [low, high] years; empty -> None."""
        if value is None or value == '' or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return [float(value), float(value)] if value >= 0 else None
        text = str(value).strip().lower()
        if text in EVERY or text in ('any', 'none'):
            return None
        if text in ('fresher', 'freshers', 'entry level', 'entry-level', 'graduate'):
            return [0.0, 0.0]
        found = [float(n) for n in re.findall(r'\d+(?:\.\d+)?', text)]
        if 1 <= len(found) <= 2:
            low, high = min(found), max(found)
            return [low, high] if not text.endswith('+') else [low, low]
        notes.append(f'Could not read experienceYears "{value}" as years, such as 3 or 2-5, so every experience level '
                     f'is kept.')
        return None

    @staticmethod
    def read_lakhs(value, notes) -> float | None:
        """12, '12', '12 lakhs', '12 LPA', '1.2 crore', '1200000' -> lakhs a year; empty -> None."""
        if value is None or value == '' or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            amount = float(value)
        else:
            text = str(value).strip().lower().replace(',', '')
            found = re.search(r'\d+(?:\.\d+)?', text)
            if not found:
                notes.append(f'Could not read minSalaryLakhs "{value}" as lakhs a year, such as 12, so pay is not '
                             f'filtered.')
                return None
            amount = float(found.group())
            if re.search(r'\b(?:cr|crore|crores)\b', text):
                amount *= 100
        if amount >= 1000:             # a full rupee amount (1200000) typed instead of lakhs
            notes.append(f'Read minSalaryLakhs {amount:,.0f} as rupees: {amount / 100000:g} lakhs a year.')
            amount /= 100000
        return amount if amount > 0 else None

    # ---------- calls ----------

    def search_all(self, boards, cfg):
        self.cfg = cfg
        return [EVERYONE]

    async def call(self, http, url: str, **kwargs) -> httpx.Response:
        """One GET in its turn, PACE after the one before. A 5xx or a broken connection is tried again. A refusal
        (429, or a 403 page from Cloudflare) is waited out once, with no call going out meanwhile; a second stops
        every call of the run."""
        last = 'no answer'
        for attempt in range(len(RETRY_S) + 1):
            async with self._turn:
                if self._stopped:
                    raise Stopped(self._stopped)
                wait = self._last + PACE - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last = time.monotonic()
            started = time.monotonic()
            try:
                response = await http.get(url, **kwargs)
            except httpx.HTTPError as exc:
                STATS['retries'][type(exc).__name__] += 1
                last = f'{type(exc).__name__}: {exc}'[:200]
            else:
                STATS['calls'] += 1
                STATS['seconds'] += time.monotonic() - started
                if response.status_code == 429 or (response.status_code == 403 and 'json' not in
                                                   response.headers.get('content-type', '')):
                    STATS['retries'][str(response.status_code)] += 1
                    await self.refused(response, started)
                    continue
                if response.status_code < 500:
                    return response
                STATS['retries'][str(response.status_code)] += 1
                last = f'HTTP {response.status_code}'
            if attempt < len(RETRY_S):
                await asyncio.sleep(RETRY_S[attempt])
        raise BoardError(last)

    async def refused(self, response, sent: float) -> None:
        """A refusal to a call sent at monotonic time sent. Calls already out when the first one came are refused by
        the same block: once it has been waited out, they are simply sent again."""
        try:
            wait = float(response.headers.get('retry-after') or 300)
        except ValueError:
            wait = 300.0
        why = f'HTTP {response.status_code}' + (' (Cloudflare error 1015, rate limited)' if '1015' in response.text
                                                else '')
        async with self._turn:
            if self._stopped:
                raise Stopped(self._stopped)
            if self._waited and sent < self._resumed:
                return
            if self._waited or wait > REFUSAL_WAIT_CAP:
                self._stopped = f'Cutshort refused this address again ({why})'
                self.tell(f'Cutshort refused this address ({why}) after {STATS["calls"]:,} calls, so the run '
                          f'stopped asking it: the jobs read so far were kept. It answers again after about '
                          f'{max(round(wait / 60), 1)} minutes.')
                raise Stopped(self._stopped)
            self._waited = True
            self.tell(f'Cutshort asked this address to wait ({why}), so the run paused {round(wait)} s, once.')
            await asyncio.sleep(wait + 2)
            self._last = self._resumed = time.monotonic()

    def tell(self, note: str) -> None:
        if self.cfg is not None and note not in self.cfg.notes:
            self.cfg.notes.append(note)

    async def text(self, http, url: str) -> str:
        response = await self.call(http, url)
        if response.status_code != 200:
            raise BoardError(f'HTTP {response.status_code} from {url}')
        return response.text

    async def job_answer(self, http, job_id: str) -> tuple[str, dict | None]:
        """One job page's answer -> ('open', data) | ('closed', None) | ('not found', None)."""
        response = await self.call(http, JOB_API.format(id=job_id))
        try:
            body = response.json()
        except ValueError as exc:
            raise BoardError(f'HTTP {response.status_code}: the job answer was not JSON') from exc
        if response.status_code == 200 and isinstance(body, dict) and body.get('success'):
            return 'open', body.get('data') or {}
        code = ((body.get('error') or {}).get('errorCode') if isinstance(body, dict) else None) or ''
        if code in ('job_deactivated', 'job_deleted', 'job_expired', 'job_closed'):
            return 'closed', None
        if code == 'job_not_found' or response.status_code == 404:
            return 'not found', None
        raise BoardError(f'HTTP {response.status_code}: {code or "unexpected answer"}')

    async def sitemap(self, http) -> list[str]:
        """Every live job's address, newest first."""
        if self._jobs is None:
            self._jobs = list(dict.fromkeys(LOC.findall(await self.text(http, JOBS_SITEMAP))))
            if not self._jobs:
                raise BoardError('the jobs sitemap listed no job')
        return self._jobs

    # ---------- boards ----------

    async def open(self, http, board):
        if board.key == EVERYONE.key:
            return {'everyone': True}
        aliases = [board.key] if ALIAS.match(board.key) else []
        for alias in aliases:
            found = await self.company(http, alias)
            if found:
                return found
        named = await self.named(http, board.key)
        sessions = [s for s in [await self.company(http, a) for a in named] if s]
        if not sessions:
            return None
        if len(sessions) > 1:
            self.tell(f'"{board.key}" names {len(sessions)} Cutshort company pages: '
                      f'{", ".join(s["alias"] for s in sessions[:5])}; all were read.')
        return {'alias': sessions[0]['alias'], 'jobs': [j for s in sessions for j in s['jobs']]}

    async def company(self, http, alias: str) -> dict | None:
        """A company page -> its open jobs as (ObjectId, address), all pages; None when there is no such page."""
        jobs, page, pages = [], 1, 1
        while page <= pages:
            response = await self.call(http, COMPANY_API.format(alias=alias), params={'page': page} if page > 1 else None)
            if response.status_code != 200:
                if page == 1 and response.status_code in (400, 404):
                    return None
                raise BoardError(f'HTTP {response.status_code} from the company page {alias}')
            try:
                listed = ((response.json().get('data') or {}).get('pageData') or {}).get('companyJobs') or {}
            except (ValueError, AttributeError) as exc:
                raise BoardError(f'the company page {alias} was not JSON') from exc
            jobs += [(j.get('_id') or '', str(j.get('publicUrl') or '').rsplit('/job/', 1)[-1])
                     for j in listed.get('jobs') or [] if isinstance(j, dict) and j.get('publicUrl')]
            pages = min(int(listed.get('totalPages') or 1), 50)
            page += 1
        return {'alias': alias, 'jobs': jobs}

    async def named(self, http, typed: str) -> list[str]:
        """A company typed by name -> the company pages that carry that name, else those whose name starts with it."""
        if self._aliases is None:
            self._aliases = list(dict.fromkeys(COMPANY_LOC.findall(await self.text(http, COMPANIES_SITEMAP))))
        wanted = ' '.join(words(typed))
        if not wanted:
            return []
        exact = [a for a in self._aliases if wanted in alias_names(a)]
        return exact or [a for a in self._aliases if any(n.startswith(wanted + ' ') for n in alias_names(a))][:10]

    # ---------- reading jobs ----------

    def candidates(self, addresses: list[str]) -> list[str]:
        """The sitemap's addresses that can hold a match: with words searched in titles only, the addresses with every
        word of a term (signs such as ++ and # are not in addresses); with places that are towns, the addresses that
        name one. The rest is checked on the job itself."""
        cfg = self.cfg
        if cfg is None:
            return addresses
        wanted = [] if cfg.search_descriptions else \
            [t for t in (Term(re.sub(r'[+#]', ' ', x)) for x in cfg.search_terms) if t.tokens]
        spots = [Place(p) for p in cfg.locations]
        towns = [w for p in spots for w in (p.words, *p.also) if w] if spots and all(
            p.kind == 'text' for p in spots) else []
        if not wanted and not towns:
            return addresses
        kept = []
        for address in addresses:
            body = address.rsplit('-', 1)[0].replace('-', ' ')
            if wanted and not any(t.in_title(index(body)) for t in wanted):
                continue
            if towns and not any(f' {w} ' in f' {" ".join(words(body))} ' for w in towns):
                continue
            kept.append(address)
        return kept

    def site_ok(self, job: dict) -> bool:
        """Cutshort's own filters: skills or roles, experience, pay, funding stage, company type, agencies."""
        settings = (self.cfg.extra if self.cfg is not None else {}) or {}
        extra = job.get('extra') or {}
        wanted = [Term(s) for s in settings.get('skills') or []]
        wanted = [t for t in wanted if t.tokens]
        if wanted and not any(t.in_title(index(job.get('title'))) or any(t.in_title(index(s)) for s in extra['skills'])
                              for t in wanted):
            return False
        years = settings.get('experienceYears')
        if years is not None:
            low, high = extra.get('experienceMin'), extra.get('experienceMax')
            if low is None:
                return False
            if not (low <= years[1] and years[0] <= (high if high is not None else low)):
                return False
        lakhs = settings.get('minSalaryLakhs')
        if lakhs is not None:
            top = extra.get('salaryMax') if extra.get('salaryMax') is not None else extra.get('salaryMin')
            per_year = {'year': 1, 'month': 12, 'week': 52, 'day': 260, 'hour': 2080}.get(extra.get('salaryPeriod'))
            if top is None or extra.get('salaryCurrency') != 'INR' or per_year is None or \
                    top * per_year < lakhs * 100000:
                return False
        stages = settings.get('companyStage')
        if stages and STAGE_OF.get(str(extra.get('companyStage') or '').lower()) not in stages:
            return False
        types = settings.get('companyType')
        if types:
            kind = str(extra.get('companyType') or '').lower()
            if not any(t.rstrip('s') in kind for t in types):
                return False
        return not (settings.get('excludeAgencies') and extra.get('agency'))

    async def jobs_of(self, http, addresses: list[str]) -> list[dict]:
        """The open jobs among these addresses, in their order; a job that closed since, or that cannot be read, is
        left out and counted."""
        async def one(address: str):
            try:
                status, data = await self.job_answer(http, address)   # the page's own address: Cloudflare's copy
            except Stopped:
                raise
            except BoardError:
                self.unreadable += 1
                return None
            if status != 'open':
                self.closed += 1
                return None
            try:
                return job_from(data)
            except (AttributeError, TypeError, ValueError):
                self.unreadable += 1
                return None
        found = await asyncio.gather(*(one(a) for a in addresses))
        self.read += len(addresses)
        return [j for j in found if j]

    async def scan(self, http, addresses: list[str], days: int | None):
        """Job pages in the order given (newest first), PAGE at a time; yields the jobs that pass Cutshort's own
        filters. It stops once maxJobs jobs that pass every filter are held (nothing older can make the cut), past
        the period asked for, at READ_BUDGET pages, or when Cutshort refuses the address."""
        cfg = self.cfg
        filters = Filters(cfg) if cfg is not None else None
        now = datetime.now(timezone.utc)
        window = now - timedelta(days=days) if days else None
        held: set = set()
        budget = READ_BUDGET if cfg is None or cfg.max_jobs is not None else None
        # a small run reads a small first page: 10 jobs need 10 job pages, not PAGE
        first = min(PAGE, max(10, cfg.max_jobs)) if cfg is not None and cfg.max_jobs else PAGE
        starts = [0, *range(first, len(addresses), PAGE)] if addresses else []
        for start, end in zip(starts, [*starts[1:], len(addresses)]):
            if budget is not None and start >= budget:
                self.incomplete = True
                self.tell(f'This run read the newest {start:,} of the {len(addresses):,} Cutshort jobs that could '
                          f'match (back to {self.oldest_day()}), the most one run reads. Add a job title or another '
                          f'filter to search older ones.')
                return
            try:
                jobs = await self.jobs_of(http, addresses[start:end])
            except Stopped:
                self.incomplete = True
                return
            if jobs:
                self.oldest = jobs[-1].get('postedAt') or self.oldest
            kept = [j for j in jobs if self.site_ok(j)]
            if kept:
                yield kept
            if filters is not None and cfg.max_jobs:
                for job in kept:
                    if filters.passes(job, now):
                        held.add(((job.get('company') or '').casefold(), *self.copy_key(job)))
                if len(held) >= cfg.max_jobs:
                    return
            last = parse_time(jobs[-1].get('postedAt')) if jobs else None
            if window and last and last < window:
                return

    def oldest_day(self) -> str:
        return (self.oldest or '')[:10] or 'the start'

    async def pages(self, http, session, term, days):
        if session.get('everyone'):
            addresses = self.candidates(await self.sitemap(http))
        else:   # a company: its jobs newest first by the time in their ObjectIds
            addresses = [a for _, a in sorted(session['jobs'], key=lambda j: j[0], reverse=True)]
        async for jobs in self.scan(http, addresses, days):
            yield jobs
        if self.unreadable:
            self.tell(f'{self.unreadable:,} Cutshort job pages could not be read, so they were left out.')

    async def lookup(self, http, session, job_id):
        status, data = await self.job_answer(http, job_id)
        if status != 'open':
            return status, dict(NOBODY)
        job = job_from(data)
        return ('open', job) if job else ('not found', dict(NOBODY))
