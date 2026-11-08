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
- GET https://cutshort.io/backend-api/webpage/jobs/<listing>?page=N: a listing such as python-jobs, remote-python-jobs
  or python-jobs-in-pune (sitemap_listings.xml names 21,036), 50 jobs a page in the sitemap's order, 8 pages at most.
  A search by skill reads it to know which jobs to open, since an address does not name a job's skills. startup-jobs
  holds every job (liveJobCount 44,963, its page 1 the sitemap's top 50): read past Cloudflare's copy of hours ago (a
  query that changes every 10 minutes; 2 to 15 s from Cutshort's server), it adds the jobs posted since the sitemap was
  built. The sitemap answered the
  same bytes at 00:35 and 02:27 UTC, its newest lastmod 23:30 UTC, so it may be built once a day.
robots.txt disallows /profile/, /apply/, /view/j/ and ?free_text= search pages; none of them is read. The site's own
search (/finddemojobsignals/q?free_text=) is one of them, ranks by relevance and sends about 1 MB a job.

Dates: postedAt is the JobPosting markup's datePosted: when the job was posted, or reopened after it closed (then its
ObjectId is older). Cutshort's own job lists show and sort by it (148 of 148 python jobs to the minute), and so does the
sitemap: 7 reopened jobs sat at their reopening date, one created in April 2025 among the jobs of 2 Nov 2026. So newest
first holds exactly. The job's own page shows "Posted <date>" from hiringIntentShownOn (lastActiveAt): the last time the
company showed it is still hiring, which moves forward on active jobs: later than datePosted on 59% of the 2,000 newest
jobs, by over a week on 35%.

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
import math
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from ..http import STATS, BoardError
from ..inputs import EVERY, _items, parse_bool, parse_choices, parse_terms
from ..rows import Filters, Place, Term, index, parse_time, words
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
LISTINGS_SITEMAP = SITE + '/sitemap_listings.xml'
LISTING_API = SITE + '/backend-api/webpage/jobs/{slug}'
HOSTS = ('cutshort.io', 'www.cutshort.io')
EVERYONE = Board('*', 'Cutshort')     # every job of every company (the sitemap); also the board of a job link
NOBODY = {'company': None, 'companySlug': None, 'companyWebsite': None}   # a gone job whose company is unknown
LOC = re.compile(r'<loc>\s*https?://(?:www\.)?cutshort\.io/job/([^<\s]+?)\s*</loc>')
COMPANY_LOC = re.compile(r'<loc>\s*https?://(?:www\.)?cutshort\.io/company/([^<\s]+?)\s*</loc>')
LISTING_LOC = re.compile(r'<loc>\s*https?://(?:www\.)?cutshort\.io/jobs/([^<\s?#]+?)\s*</loc>')
SHORT_ID = re.compile(r'^[A-Za-z0-9]{8}$')
OBJECT_ID = re.compile(r'^[0-9a-f]{24}$')
ALIAS = re.compile(r'^\S+-[A-Za-z0-9]{8}$')         # a company page: egnyte-SClW7oZH
PACE = 0.125                  # seconds between two call starts: 8 a second, half the rate that passed (docstring)
RETRY_S = (2, 5)              # waits before a 5xx or a broken connection is tried again
REFUSAL_WAIT_CAP = 330        # the longest Retry-After waited out (Cloudflare's 1015 lasts about 5 minutes)
PAGE = 50                     # jobs read and handed to the engine at a time
LISTING_PAGES = 8             # a listing serves its newest 400 jobs, 50 a page; page 9 answers 400 jobs_not_found
EVERY_JOB = 'startup-jobs'    # the listing that holds every job: 44,963 of 44,963, its page 1 the sitemap's top 50
FRESH_S = 600                 # the live list's query changes this often (newer_than)
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
    """The job page's answer -> one job in the shape of rows.py, with Cutshort's own fields (Cutshort.extra)."""
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
        'skills': [s for s in page.get('allSkills') or [] if isinstance(s, str)],
        'experienceMin': low,
        'experienceMax': high,
        'remotePolicy': summary.get('remotePolicy') or None,
        'companyType': company.get('type') if isinstance(company.get('type'), str) else None,
        'companySize': company.get('size') if isinstance(company.get('size'), str) else None,
        'companyStage': company.get('stage') if isinstance(company.get('stage'), str) else None,
        'companyFounded': number(company.get('founded')),
        'agency': (' '.join(str(poster.get('name') or '').split()) or None) if agency else None,
        'lastActiveAt': page.get('hiringIntentShownOn') or None,
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
    extra = ('skills', 'experienceMin', 'experienceMax', 'salaryMin', 'salaryMax', 'salaryCurrency', 'salaryPeriod',
             'remotePolicy', 'companyType', 'companySize', 'companyStage', 'companyFounded', 'agency', 'offersEquity',
             'lastActiveAt')   # on every row: one call reads each job whole, so a plain row has them at no cost

    def __init__(self):
        self.cfg = None
        self._turn = asyncio.Lock()
        self._last = 0.0
        self._waited = False          # a refusal was waited out once this run
        self._resumed = 0.0           # when that wait ended: a refusal to a call sent before it is the same refusal
        self._stopped: str | None = None
        self._jobs: list[str] | None = None          # the sitemap's job addresses, newest first
        self._aliases: list[str] | None = None       # the company pages, for names typed in companies
        self._listings: set[str] | None = None       # every listing page's name, for a search by skill
        self.read = 0                 # job pages read this run
        self.closed = 0               # jobs on the sitemap that closed before their page was read
        self.unreadable = 0           # job pages that did not answer, or answered in a shape not seen before
        self.passed = 0               # jobs read that passed Cutshort's own filters
        self.oldest: str | None = None
        self._said: dict[str, int] = {}   # topic -> its note's place in cfg.notes (say)
        self._settings: dict = {}         # Cutshort's own settings of the run (read_input), for OUTPUT

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
        listings: list[str] = []                    # pasted listing pages, read as they are
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
                if found['skills']:
                    listings.append(parts[1].lower())
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
        self._settings.update({
            'skills': list(dict.fromkeys(skills)),
            'experienceYears': self.read_years(raw.get('experienceYears'), notes),
            'minSalaryLakhs': self.read_lakhs(raw.get('minSalaryLakhs'), notes),
            'companyStage': list(dict.fromkeys(stages)) if len(set(stages)) < len(STAGES) else [],
            'companyType': list(dict.fromkeys(types)) if len(set(types)) < len(TYPES) else [],
            'excludeAgencies': parse_bool(raw.get('excludeAgencies'), False),
        })
        if listings:
            self._settings['listings'] = list(dict.fromkeys(listings))
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

    def say(self, topic: str, note: str) -> None:
        """A note kept current while the run reads: one per topic, replaced in place, so it is right whenever the
        engine stops reading (it can stop between two pages)."""
        if self.cfg is None:
            return
        at = self._said.get(topic)
        if at is None:
            self._said[topic] = len(self.cfg.notes)
            self.cfg.notes.append(note)
        else:
            self.cfg.notes[at] = note

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
        """Every live job's address, newest first: the sitemap, with the jobs posted since it was built on top. The
        live list's first page is asked for at the same time as the sitemap."""
        if self._jobs is None:
            text, first = await asyncio.gather(self.text(http, JOBS_SITEMAP), self.live_page(http, 1),
                                               return_exceptions=True)
            if isinstance(text, BaseException):
                raise text
            listed = list(dict.fromkeys(LOC.findall(text)))
            if not listed:
                raise BoardError('the jobs sitemap listed no job')
            self._jobs = await self.newer_than(http, listed, first) + listed
        return self._jobs

    async def live_page(self, http, page: int) -> list[dict]:
        """A page of startup-jobs, every job newest first, past Cloudflare's copy of hours ago: the query changes every
        10 minutes, so runs inside the same 10 minutes share one copy and Cutshort's server answers once."""
        params = {'fresh': str(int(time.time() // FRESH_S))} | ({'page': page} if page > 1 else {})
        response = await self.call(http, LISTING_API.format(slug=EVERY_JOB), params=params)
        if response.status_code != 200:
            raise BoardError(f'HTTP {response.status_code}')
        jobs = ((response.json().get('data') or {}).get('pageData') or {}).get('jobs') or []
        return [j for j in jobs if isinstance(j, dict)]

    async def newer_than(self, http, listed: list[str], first) -> list[str]:
        """The jobs of the live list down to the first one the sitemap has (first: its page 1, or why it failed). A
        list that cannot be read adds nothing: the sitemap alone is searched, and a note says so."""
        known = {short_id(a) for a in listed[:2000]}
        newer: list[str] = []
        try:
            for page in range(1, LISTING_PAGES + 1):
                jobs = first if page == 1 else await self.live_page(http, page)
                if isinstance(jobs, BaseException):
                    raise jobs
                for job in jobs:
                    address = str(job.get('publicUrl') or '').rsplit('/job/', 1)[-1]
                    if short_id(address) in known:
                        return newer
                    if short_id(address):
                        newer.append(address)
                if len(jobs) < PAGE:
                    return newer
        except Stopped:
            raise
        except (BoardError, ValueError, AttributeError) as exc:
            self.tell(f"Cutshort's live list of new jobs could not be read ({exc}), so jobs posted since its sitemap was "
                      f'built may be missing.')
        return newer

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

    async def listing_slugs(self, http) -> set[str]:
        if self._listings is None:
            self._listings = set(LISTING_LOC.findall(await self.text(http, LISTINGS_SITEMAP)))
        return self._listings

    def listing_for(self, skill: str, slugs: set[str]) -> str | None:
        """A skill typed -> Cutshort's listing of it, the narrowest that the search allows: remote-python-jobs when only
        remote jobs are asked for, python-jobs-in-pune for one town Cutshort lists, else python-jobs. A listing's name is
        the skill's words joined by hyphens or run together (reactjs, nodejs), or followed by its short form
        (machine-learning-ml); a pasted listing link is used as it is."""
        cfg = self.cfg
        mine = [s for s in (self._settings.get('listings') or []) if listing_parts(s)['skills'] == [skill]]
        if mine:
            return mine[0] if mine[0] in slugs else None
        ws = words(re.sub(r'[+#]', ' ', skill))
        if not ws:
            return None
        forms = list(dict.fromkeys(['-'.join(ws), ''.join(ws)]))
        forms += sorted(s.removesuffix('-jobs') for s in slugs if any(
            s.startswith(f + '-') and s.endswith('-jobs') and re.fullmatch(r'[a-z0-9]{1,4}', s[len(f) + 1:-5])
            for f in forms[:1]))
        towns = []
        if len(cfg.locations) == 1:
            typed = ' '.join(words(cfg.locations[0]))
            towns = sorted({s.split('-jobs-in-', 1)[1] for s in slugs if '-jobs-in-' in s and typed and
                            f' {typed} ' in f" {s.split('-jobs-in-', 1)[1].replace('-', ' ')} "})
        tries = []
        for form in forms:
            if cfg.work_types == ['remote']:
                tries.append(f'remote-{form}-jobs')
            tries += [f'{form}-jobs-in-{town}' for town in towns[:1]]
            tries.append(f'{form}-jobs')
        return next((s for s in tries if s in slugs), None)

    async def listing_ids(self, http, slug: str) -> tuple[list[str], bool]:
        """A listing's jobs, newest first, as 8-character ids, and whether that is all of them (fewer than 8 pages).
        Page 1 comes first; when it is full, pages 2 to 8 are asked for together, since a page Cloudflare does not hold
        comes from Cutshort's server in 6 to 13 s (golang-jobs took 90 s read one page after another)."""
        def read(response) -> list[dict] | None:
            if response.status_code == 400:
                return None                                       # jobs_not_found: past its last page
            if response.status_code != 200:
                raise BoardError(f'HTTP {response.status_code} from the listing {slug}')
            try:
                return ((response.json().get('data') or {}).get('pageData') or {}).get('jobs') or []
            except (ValueError, AttributeError) as exc:
                raise BoardError(f'the listing {slug} was not JSON') from exc

        async def page(n: int):
            return await self.call(http, LISTING_API.format(slug=slug), params={'page': n} if n > 1 else None)
        first = read(await page(1))
        pages = [first] if first is not None else []
        if first is not None and len(first) >= PAGE:
            pages += [read(r) for r in await asyncio.gather(*(page(n) for n in range(2, LISTING_PAGES + 1)))]
        ids: list[str] = []
        for jobs in pages:
            if jobs is None:
                return ids, True
            ids += [i for i in (short_id(str(j.get('publicUrl') or '').rsplit('/job/', 1)[-1]) for j in jobs
                                if isinstance(j, dict)) if i]
            if len(jobs) < PAGE:
                return ids, True
        return ids, len(pages) < LISTING_PAGES

    async def skill_addresses(self, http, addresses: list[str]) -> list[str] | None:
        """A search by skills with no title words: the jobs worth opening, from Cutshort's listing of each skill. A job
        is opened when a listing names it, when its title has a skill's words, or when some listing does not cover its
        place in the sitemap: newer than the listing's newest job (Cloudflare can serve a listing hours old) or older
        than its last page. None when a skill has no listing: then every job is opened."""
        cfg = self.cfg
        skills = self._settings.get('skills') or [] if cfg is not None else []
        if not skills or cfg.search_terms or cfg.search_descriptions:
            return None
        slugs = await self.listing_slugs(http)
        chosen = [self.listing_for(skill, slugs) for skill in skills]
        if None in chosen:
            return None
        where = {short_id(a): i for i, a in enumerate(addresses)}
        members: set[int] = set()
        covered: list[tuple[int, int, bool]] = []
        for slug in dict.fromkeys(chosen):
            ids, complete = await self.listing_ids(http, slug)
            spots = [where[i] for i in ids if i in where]
            members.update(spots)
            covered.append((min(spots), max(spots), complete) if spots else (len(addresses), -1, complete))
        self.tell(f'Read the Cutshort listing{"s" if len(set(chosen)) > 1 else ""} {", ".join(dict.fromkeys(chosen))} '
                  f'to find the jobs with {" or ".join(skills)}.')
        titled = [t for t in (Term(re.sub(r'[+#]', ' ', s)) for s in skills) if t.tokens]

        def wanted(i: int, address: str) -> bool:
            if i in members or any(i < top or (i > bottom and not complete) for top, bottom, complete in covered):
                return True
            body = address.rsplit('-', 1)[0].replace('-', ' ')
            return any(t.in_title(index(body)) for t in titled)
        return [a for i, a in enumerate(addresses) if wanted(i, a)]

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

    def settings(self) -> dict:
        return dict(self._settings)

    def fits(self, job: dict) -> bool:
        """Cutshort's own filters: skills or roles, experience, pay, funding stage, company type, agencies."""
        settings = self._settings
        extra = job
        wanted = [Term(s) for s in settings.get('skills') or []]
        wanted = [t for t in wanted if t.tokens]
        if wanted and not any(t.in_title(index(job.get('title'))) or any(t.in_title(index(s)) for s in extra.get('skills') or [])
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
        """Job pages in the order given (newest first), a page at a time; yields every open job read, and the engine
        asks fits() for Cutshort's own filters. It stops once maxJobs jobs that pass every filter are held (nothing older
        can make the cut), past the period asked for, at READ_BUDGET pages, or when Cutshort refuses the address."""
        cfg = self.cfg
        filters = Filters(cfg) if cfg is not None else None
        now = datetime.now(timezone.utc)
        window = now - timedelta(days=days) if days else None
        held: set = set()
        budget = READ_BUDGET if cfg is None or cfg.max_jobs is not None else None
        wanted = cfg.max_jobs if cfg is not None else None
        start = 0
        while start < len(addresses):
            if budget is not None and start >= budget:
                self.incomplete = True
                self.tell(f'This run read the newest {start:,} of the {len(addresses):,} Cutshort jobs that could '
                          f'match (back to {self.oldest_day()}), the most one run reads. Add a job title or another '
                          f'filter to search older ones.')
                return
            # the next page is what the matches still wanted take at the rate seen so far, with a quarter more: 100
            # jobs read 100 to 110 job pages, not 150; with nothing known yet, or every match wanted, PAGE
            size = PAGE
            if wanted:
                rate = len(held) / start if start and held else 1.0
                size = min(PAGE, max(10, math.ceil((wanted - len(held)) / rate * 1.25)))
            end = min(start + size, len(addresses))
            try:
                jobs = await self.jobs_of(http, addresses[start:end])
            except Stopped:
                self.incomplete = True
                return
            start = end
            if jobs:
                self.oldest = jobs[-1].get('postedAt') or self.oldest
            fit = [j for j in jobs if self.fits(j)]   # the engine asks fits() too; counted here for the note and the stop
            self.passed += len(fit)
            if self.own_filters():
                self.say('filters', f"Cutshort's own filters ({', '.join(self.own_filters())}) kept {self.passed:,} of "
                                    f'the {self.read:,} jobs read, newest first back to {self.oldest_day()}.')
            if self.unreadable:
                self.say('unreadable', f'{self.unreadable:,} Cutshort job pages could not be read, so they were left '
                                       f'out.')
            if jobs:                                   # every job read, so the engine counts what was read
                yield jobs
            if filters is not None and cfg.max_jobs:
                for job in fit:
                    if filters.passes(job, now):
                        held.add(((job.get('company') or '').casefold(), *self.copy_key(job)))
                if len(held) >= cfg.max_jobs:
                    return
            last = parse_time(jobs[-1].get('postedAt')) if jobs else None
            if window and last and last < window:
                return

    def oldest_day(self) -> str:
        return (self.oldest or '')[:10] or 'the start'

    def own_filters(self) -> list[str]:
        """The names of Cutshort's own filters this run uses, as the form names them."""
        settings = self._settings
        names = {'skills': 'skills', 'experienceYears': 'experience', 'minSalaryLakhs': 'salary',
                 'companyStage': 'funding stage', 'companyType': 'company type', 'excludeAgencies': 'no agency jobs'}
        return [name for key, name in names.items() if settings.get(key) not in (None, [], False)]

    async def pages(self, http, session, term, days):
        if session.get('everyone'):
            everything = await self.sitemap(http)
            try:
                by_skill = await self.skill_addresses(http, everything)
            except Stopped:
                raise
            except BoardError as exc:              # a listing that cannot be read: every job is opened instead
                self.tell(f'A Cutshort listing could not be read ({exc}), so every job was read for the skills.')
                by_skill = None
            addresses = self.candidates(everything if by_skill is None else by_skill)
        else:   # a company: its jobs newest first by the time in their ObjectIds
            addresses = [a for _, a in sorted(session['jobs'], key=lambda j: j[0], reverse=True)]
        async for jobs in self.scan(http, addresses, days):
            yield jobs

    async def lookup(self, http, session, job_id):
        status, data = await self.job_answer(http, job_id)
        if status != 'open':
            return status, dict(NOBODY)
        job = job_from(data)
        return ('open', job) if job else ('not found', dict(NOBODY))
