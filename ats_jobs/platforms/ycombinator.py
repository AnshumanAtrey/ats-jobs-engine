"""
Y Combinator's job board: every open job at every YC company that is hiring, as Work at a Startup (workatastartup.com)
lists them and ycombinator.com shows them, read the way YC's own pages read them. No login, no key, no browser.
Measured from this laptop on 2026-11-10.

- GET https://www.ycombinator.com/companies: the company directory's page. It ships a search-only Algolia key
  (window.AlgoliaOpts: app 45BWZJ1SGC, restricted to YCCompany_production and the ycdc_public tag, valid for about a day,
  so it is read at every run) and the Inertia version the other pages need.
- POST https://45BWZJ1SGC-dsn.algolia.net/1/indexes/*/queries: the directory itself. isHiring:true lists the companies
  that are hiring, 1,500 of 6,387 on 2026-11-10, with batch, stage, team size, industries, website and one-liner. A query
  returns 1,000 companies at most, so they are asked for in groups of batches, all in one call.
- GET https://www.ycombinator.com/companies/<slug>/jobs: one company's open jobs (an Inertia page: props.jobPostings in
  its data-page attribute). Sent as a partial reload (X-Inertia, X-Inertia-Version, X-Inertia-Partial-Data: jobPostings)
  it carries only the job list, inside the HTML shell YC renders on every request: 96 KB, 12 KB gzipped. The 1,500
  pages held 6,322 jobs, 32 read at a time in 23 s with no refusal and 2.8 s of CPU; Work at a Startup counted 6,329 the
  same hour, and each of 608 jobs sampled from its role pages and its search was among them.
  A listed job has an id, which grows with posting (0 inversions against datePosted in 80 jobs across the range), the
  title, the location as text, type, role and subtype, pay and equity as text, minimum experience, school year
  (internships), visa terms, skills, the company's batch and one-liner, and createdAt: the time since posting in words
  (Rails' time_ago_in_words: "about 5 hours", "29 days", "about 1 month", "over 1 year"). 86% of the open jobs were
  posted more than 30 days ago, some 8 years ago.
- GET https://www.ycombinator.com/companies/<slug>/jobs/<code>-<title>: the job's page. Its JobPosting markup holds
  datePosted, the moment the job was created, to the second; the description as HTML; and every office as an address
  (city, region, country). Its baseSalary always says USD a YEAR, also for "£70K - £120K GBP" or "$50 - $80 / hourly",
  and applicantLocationRequirements can contradict the job's own text (GB for a job remote in Africa), so pay and remote
  places are read from YC's text. A partial reload (props job) is 115 KB, 18 KB gzipped: 200 pages took 5 s 32 at a
  time and 0.64 s of CPU. A job that is gone answers 404 (74 of 120 job links the Wayback Machine kept), and a company
  that changed its address answers 301 to the new one.
- GET https://www.workatastartup.com/jobs/<id>: the same job on Work at a Startup, 200 while it is open and 404 once it
  is not (closed and never published look the same); no date. It answers 406 unless the call accepts text/html. Read
  only for links people paste from there.
robots.txt of ycombinator.com disallows /companies?* (directory searches in the address) and /verify, neither read here;
workatastartup.com disallows nothing. Not used: Work at a Startup's own Algolia key, whose tag filter ("none") hides every
record from a visitor; its search box (/jobs/search?q=, 30 jobs by relevance, no dates); its role pages (30 jobs each);
and YC's /jobs/role pages (20 to 37 chosen jobs).

A search: every hiring company's job list is read first. Each job is checked there with the engine's own filters
(rows.Filters) and YC's (fits): a job that can match is opened, newest first, for its exact date, and the rest go to the
engine as listed, dated the newest their words in createdAt allow, so its counts of jobs read and near misses hold. A
job whose places its text does not put in a country ("SF", "New York City", "San Francisco, CA") is opened for its
address whenever a place filter is the only one it fails. Opening stops once maxJobs jobs that pass every filter are
held: the jobs that can match and were not opened count as matches when their words and dates are certain, and make the
count a floor when they are not.

Pay: YC's text, as shown, and its numbers, currency and period ("$150K - $190K", "€60K - €80K EUR", "₹2M - ₹4M INR",
"$50 - $80 / hourly", "$1K - $4K / monthly"). An amount that cannot be a year's pay (under 1,000 a year: "$1", "$120 -
$140" typed without K) is left out of the numbers and kept in the text. Remote: a job with a Remote place ("Remote (US)",
"San Francisco, CA, US / Remote (US)") is remote, else onsite unless its text says hybrid.
"""
import asyncio
import dataclasses
import html as htmllib
import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from importlib import resources
from urllib.parse import unquote, urlencode, urlparse

from .. import places as regions
from ..http import BoardError, send
from ..inputs import _items, parse_bool
from ..rows import Filters, job_places, parse_time, words
from .base import Board, Platform
from .cornerstone import text_of

SITE = 'https://www.ycombinator.com'
DIRECTORY = SITE + '/companies'
COMPANY_JOBS = SITE + '/companies/{slug}/jobs'
JOB_PAGE = SITE + '/companies/{slug}/jobs/{code}'
WAAS = 'https://www.workatastartup.com'
WAAS_JOB = WAAS + '/jobs/{id}'
ALGOLIA = 'https://{app}-dsn.algolia.net/1/indexes/*/queries'
INDEX = 'YCCompany_production'
HOSTS = ('www.ycombinator.com', 'ycombinator.com')
WAAS_HOSTS = ('www.workatastartup.com', 'workatastartup.com')
EVERYONE = Board('*', "YC's Work at a Startup board")    # every hiring company at once; also the board of a WaaS link
NOBODY = {'company': None, 'companySlug': None, 'companyWebsite': None}   # a gone job whose company is unknown
GROUP = 900                   # companies one directory query asks for (Algolia returns 1,000 at most)
LISTS_AT_ONCE = 32            # company job lists read at once: 23 s for 1,500, no refusal (2026-11-10)
PAGES_AT_ONCE = 32            # job pages opened at once
PAGE = 50                     # job pages opened, and handed to the engine, at a time
READ_BUDGET = 7000            # job pages opened at most in one run: more than the whole board (6,322 jobs)
LIST_RESERVE_S = 90           # stop reading job lists this close to the run's time limit: room to open and deliver
CODE = re.compile(r'^([A-Za-z0-9]{7})(?:-|$)')          # a job's address: ena6RKJ-solutions-engineer
SLUG = re.compile(r'^[a-z0-9][a-z0-9._-]*$', re.I)
PAGE_DATA = re.compile(r'data-page="([^"]*)"')
ALGOLIA_OPTS = re.compile(r'window\.AlgoliaOpts\s*=\s*(\{[^<\n]*?\})\s*[;<\n]')
MIN, HOUR, DAY = 60, 3600, 86400
MONTH, YEAR = 30 * DAY, 365 * DAY
TYPES = {'full-time': 'full-time', 'internship': 'intern', 'contract': 'contract'}   # and Co-founder: no engine type
PERIODS = {'hour': 'hour', 'hourly': 'hour', 'month': 'month', 'monthly': 'month', 'week': 'week', 'weekly': 'week',
           'day': 'day', 'daily': 'day', 'year': 'year', 'yearly': 'year', 'annually': 'year'}
PER_YEAR = {'year': 1, 'month': 12, 'week': 52, 'day': 260, 'hour': 2080}
SIGNS = {'$': 'USD', '€': 'EUR', '£': 'GBP', '₹': 'INR', '¥': 'JPY'}
AMOUNT = re.compile(r'(\d+(?:[.,]\d+)*)\s*([KkMm](?![a-z]))?')
YEARLY_FLOOR = 1000           # a year's pay under this is a typing slip ("$120" for $120K) or a placeholder ("$1")

# YC's role areas (Work at a Startup's role filter), and the words people type for them
ROLES = {'eng': 'engineering', 'design': 'design', 'product': 'product', 'science': 'science', 'sales': 'sales',
         'marketing': 'marketing', 'support': 'support', 'operations': 'operations', 'recruiting': 'recruiting',
         'finance': 'finance', 'legal': 'legal'}
ROLE_ALIASES = {'engineering': 'eng', 'engineer': 'eng', 'engineers': 'eng', 'eng': 'eng', 'software': 'eng',
                'software engineering': 'eng', 'software engineer': 'eng', 'swe': 'eng', 'tech': 'eng',
                'design': 'design', 'designer': 'design', 'product': 'product', 'product manager': 'product',
                'product management': 'product', 'pm': 'product', 'science': 'science', 'research': 'science',
                'sales': 'sales', 'marketing': 'marketing', 'growth': 'marketing', 'support': 'support',
                'customer success': 'support', 'customer support': 'support', 'success': 'support',
                'operations': 'operations', 'ops': 'operations', 'recruiting': 'recruiting', 'recruiting hr': 'recruiting',
                'recruiting and hr': 'recruiting', 'hr': 'recruiting', 'people': 'recruiting', 'talent': 'recruiting',
                'sales manager': 'sales', 'account executive': 'sales',
                'finance': 'finance', 'accounting': 'finance', 'legal': 'legal'}
# YC's subtypes (roleSpecificType, on every engineering, design, recruiting and science job), by the words people type
SUBTYPE_ALIASES = {'full stack': 'full stack', 'fullstack': 'full stack', 'fs': 'full stack', 'backend': 'backend',
                   'back end': 'backend', 'be': 'backend', 'frontend': 'frontend', 'front end': 'frontend',
                   'fe': 'frontend', 'machine learning': 'machine learning', 'ml': 'machine learning',
                   'ai': 'ai engineer', 'ai engineer': 'ai engineer', 'data science': 'data science',
                   'data scientist': 'data science', 'devops': 'devops', 'sre': 'devops', 'mobile': 'mobile',
                   'ios': 'ios', 'android': 'android', 'embedded': 'embedded systems', 'embedded systems': 'embedded systems',
                   'hardware': 'hardware', 'robotics': 'robotics', 'qa': 'qa engineer', 'qa engineer': 'qa engineer',
                   'engineering manager': 'engineering manager', 'eng manager': 'engineering manager',
                   'electrical': 'electrical', 'mechanical': 'mechanical', 'bioengineering': 'bioengineering',
                   'chemical': 'chemical engineering', 'chemical engineering': 'chemical engineering',
                   'web design': 'web design', 'product design': 'product design', 'ui ux': 'ui ux', 'ux': 'ui ux',
                   'ui': 'ui ux', 'brand design': 'brand graphic design', 'graphic design': 'brand graphic design',
                   'recruiter': 'recruiter', 'sourcer': 'sourcer'}
VISAS = {'will-sponsor': 'Will sponsor', 'not-required': 'US citizenship/visa not required',
         'us-only': 'US citizen/visa only'}
VISA_ALIASES = {'will sponsor': 'will-sponsor', 'sponsor': 'will-sponsor', 'sponsors': 'will-sponsor',
                'sponsorship': 'will-sponsor', 'visa sponsorship': 'will-sponsor', 'h1b': 'will-sponsor',
                'h 1b': 'will-sponsor', 'yes': 'will-sponsor', 'not required': 'not-required',
                'no visa needed': 'not-required', 'international': 'not-required', 'anyone': 'not-required',
                'us citizenship visa not required': 'not-required', 'us only': 'us-only', 'us citizen': 'us-only',
                'us citizens': 'us-only', 'citizen': 'us-only', 'us citizen visa only': 'us-only'}
STAGES = ('early', 'growth', 'public')
STAGE_ALIASES = {'early': 'early', 'early stage': 'early', 'seed': 'early', 'startup': 'early',
                 'growth': 'growth', 'growth stage': 'growth', 'late': 'growth', 'late stage': 'growth', 'scaleup': 'growth',
                 'public': 'public', 'ipo': 'public', 'listed': 'public'}
SEASONS = {'w': 'Winter', 's': 'Summer', 'f': 'Fall', 'p': 'Spring', 'x': 'Spring'}    # YC wrote Spring 2025 as X25
SEASON_CODE = {'winter': 'W', 'summer': 'S', 'fall': 'F', 'autumn': 'F', 'spring': 'P'}
# Places people type that YC's jobs write another way, and the Bay Area's cities (YC's location text names the city)
ABBREVIATIONS = {'sf': ('San Francisco',), 'nyc': ('New York',), 'new york city': ('New York',),
                 'bay area': ('San Francisco Bay Area', 'San Francisco', 'South San Francisco', 'Oakland', 'Berkeley',
                              'Alameda', 'Emeryville', 'San Jose', 'Palo Alto', 'Mountain View', 'Menlo Park',
                              'Redwood City', 'San Mateo', 'Foster City', 'Burlingame', 'Belmont', 'Sunnyvale',
                              'Santa Clara', 'Cupertino', 'Los Altos', 'Milpitas', 'Fremont', 'Hayward', 'San Leandro')}
ABBREVIATIONS['sf bay area'] = ABBREVIATIONS['san francisco bay area'] = ABBREVIATIONS['bay area']
PLACE_WORDS = ('in', 'at', 'near', 'around')
DIRECTORY_FIELDS = ['id', 'name', 'slug', 'website', 'batch', 'status', 'stage', 'team_size', 'industries', 'one_liner',
                    'all_locations', 'isHiring']


@lru_cache(maxsize=1)
def yc_places() -> dict[str, str]:
    """data/ycombinator_places.tsv (scripts/refresh_ycombinator.py): the places YC's jobs name, as words -> the name."""
    text = resources.files('ats_jobs').joinpath('data/ycombinator_places.tsv').read_text(encoding='utf-8')
    out = {}
    for line in text.splitlines():
        if line.strip() and not line.startswith('#'):
            name = line.split('\t')[0].strip()
            out.setdefault(' '.join(words(name)), name)
    return out


def typed_place(tokens: list[str]) -> str | None:
    """Words a person typed -> the YC place they name ('new york', 'nyc', 'london'), or None."""
    key = ' '.join(words(' '.join(tokens)))
    if not key:
        return None
    return (ABBREVIATIONS.get(key) or (None,))[0] or yc_places().get(key)


def split_place(term: str) -> tuple[str, str | None]:
    """'product manager in new york' -> ('product manager', 'New York'); 'designer london' -> ('designer', 'London');
    'San Francisco' -> ('', 'San Francisco'); 'engineer in residence' -> unchanged, None: Residence is no YC place."""
    ws = term.split()
    for n in range(min(4, len(ws) - 1), 0, -1):               # "<title> in <place>"
        if ws[-n - 1].lower() in PLACE_WORDS and typed_place(ws[-n:]):
            return ' '.join(ws[:-n - 1]), typed_place(ws[-n:])
    for n in range(min(4, len(ws)), 0, -1):                   # "<title> <place>", or the place alone
        if typed_place(ws[-n:]) and not (n < len(ws) and ws[-n - 1].lower() in PLACE_WORDS):
            return ' '.join(ws[:-n]), typed_place(ws[-n:])
    return term, None


# ---------- reading YC's pages ----------

def inertia_page(text: str | None) -> dict | None:
    """An Inertia page's data-page attribute -> {component, props, url, version}; None when there is none."""
    found = PAGE_DATA.search(text or '')
    if not found:
        return None
    try:
        page = json.loads(htmllib.unescape(found.group(1)))
    except ValueError:
        return None
    return page if isinstance(page, dict) else None


def job_posting(text: str | None) -> dict:
    """The page's JobPosting markup ({} when it has none)."""
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', text or '', re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get('@type') == 'JobPosting':
                return item
    return {}


def age_range(text: str | None) -> tuple[float, float] | None:
    """YC's time since posting -> (fewest, most) seconds it can mean: Rails' time_ago_in_words rounds minutes, hours, days
    and months, so "29 days" is 28.5 to 29.5 days, "about 1 month" 30 to 45 days, "over 1 year" 1.25 to 1.75 years
    (two more days each way for leap years). None when the words are not of that kind."""
    t = ' '.join((text or '').lower().replace('ago', '').split())
    if not t:
        return None
    if 'second' in t or t in ('less than a minute', 'less than 1 minute', 'half a minute', 'just now'):
        return 0.0, float(MIN)
    m = re.fullmatch(r'(about|over|almost|less than)?\s*(an?|\d+)\s+(minute|hour|day|month|year)s?', t)
    if not m:
        return None
    qualifier, count, unit = m.group(1), m.group(2), m.group(3)
    n = 1 if count in ('a', 'an') else int(count)
    if unit == 'minute':
        return max(n - 0.5, 0) * MIN, (n + 0.5) * MIN
    if unit == 'hour':
        return (44.5 * MIN, 89.5 * MIN) if n == 1 else ((n - 0.5) * HOUR, (n + 0.5) * HOUR)
    if unit == 'day':
        return (1439.5 * MIN, 2519.5 * MIN) if n == 1 else ((n - 0.5) * DAY, (n + 0.5) * DAY)
    if unit == 'month':
        if qualifier == 'about':
            return (30 * DAY, 45 * DAY) if n == 1 else (45 * DAY, 60 * DAY)
        return (n - 0.5) * MONTH, (n + 0.5) * MONTH
    if qualifier == 'over':
        return (n + 0.25) * YEAR - 2 * DAY, (n + 0.75) * YEAR + 2 * DAY
    if qualifier == 'almost':
        return (n - 0.25) * YEAR - 2 * DAY, n * YEAR + 2 * DAY
    return n * YEAR - 2 * DAY, (n + 0.25) * YEAR + 2 * DAY


def iso(moment: datetime) -> str:
    return moment.strftime('%Y-%m-%dT%H:%M:%SZ')


def amount(number: str, scale: str | None) -> float:
    value = float(number.replace(',', ''))
    return value * {'k': 1_000, 'm': 1_000_000}.get((scale or '').lower(), 1)


def parse_pay(text: str | None) -> dict:
    """YC's pay text -> salary (as shown), salaryMin, salaryMax, salaryCurrency, salaryPeriod. '$150K - $190K' -> 150000,
    190000, USD, year; '₹2M - ₹4.5M INR' -> 2000000, 4500000, INR, year; '$50 - $80 / hourly' -> 50, 80, USD, hour. A
    yearly amount under YEARLY_FLOOR is left out of the numbers (the text keeps it)."""
    shown = ' '.join(str(text or '').split()) or None
    out = {'salary': shown, 'salaryMin': None, 'salaryMax': None, 'salaryCurrency': None, 'salaryPeriod': None}
    if not shown or not re.search(r'\d', shown):
        return {**out, 'salary': None}
    per = re.search(r'/\s*([a-z]+)', shown, re.I)
    period = PERIODS.get(per.group(1).lower(), 'year') if per else 'year'
    code = re.search(r'\b([A-Z]{3})\b', shown)
    sign = next((SIGNS[ch] for ch in shown if ch in SIGNS), None)
    found = [amount(n, s) for n, s in AMOUNT.findall(shown.split('/')[0])]
    if not found:
        return out
    low, high = found[0], found[-1]
    floor = YEARLY_FLOOR / PER_YEAR[period] if period == 'year' else 0
    low, high = (v if v >= floor and v > 0 else None for v in (low, high))
    if low is None and high is None:
        return out
    numbers = {k: (int(v) if v is not None and v == int(v) else v) for k, v in (('salaryMin', low), ('salaryMax', high))}
    return {**out, **numbers, 'salaryCurrency': code.group(1) if code else sign, 'salaryPeriod': period}


def parse_equity(text: str | None) -> tuple[float | None, float | None]:
    """'0.25% - 1.00%' -> (0.25, 1.0); '0.5%' -> (0.5, 0.5); '' -> (None, None)."""
    found = [float(n) for n in re.findall(r'(\d+(?:\.\d+)?)\s*%', str(text or ''))]
    return (found[0], found[-1]) if found else (None, None)


def parse_years(text: str | None, kind: str | None) -> tuple[float | None, float | None]:
    """YC's minimum experience -> (fewest, most) years: '3+ years' -> (3, None), 'Any (new grads ok)' -> (0, None). An
    internship states none and asks for students: (0, 0). Anything else states nothing: (None, None)."""
    value = ' '.join(str(text or '').lower().split())
    found = re.match(r'(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)?', value)
    if found:
        years = float(found.group(1))
        return (int(years) if years == int(years) else years), None
    if value.startswith('any') or 'new grad' in value:
        return 0, None
    if not value and (kind or '').lower() == 'internship':
        return 0, 0
    return None, None


def batch_code(name: str | None) -> str | None:
    """'Summer 2024' -> 'S24', 'Spring 2026' -> 'P26' (as YC's job cards write it); a code stays as it is."""
    text = ' '.join(str(name or '').split())
    found = re.fullmatch(r'(winter|summer|fall|autumn|spring)\s+(\d{4})', text, re.I)
    if found:
        return f'{SEASON_CODE[found.group(1).lower()]}{found.group(2)[2:]}'
    return text.upper() if re.fullmatch(r'[WSFPX]\d{2}', text, re.I) else None


def batches_typed(text: str) -> set[str] | None:
    """A batch as people type it -> the codes it names: 'W25', 'w 25', 'Winter 2025' -> {'W25'}; 'X25' -> {'P25'} (YC
    wrote Spring 2025 as X25); '2025' or "25" -> every batch of that year. None when it is not a batch."""
    t = ' '.join(text.lower().replace("'", ' ').split())
    t = re.sub(r'^(?:yc|y combinator)\s+', '', t)
    found = re.fullmatch(r'([wsfpx])\s*(\d{2})', t)
    if found:
        return {f'{"P" if found.group(1) == "x" else found.group(1).upper()}{found.group(2)}'}
    found = re.fullmatch(r'(winter|summer|fall|autumn|spring)\s+(\d{4}|\d{2})', t)
    if found:
        return {f'{SEASON_CODE[found.group(1)]}{found.group(2)[-2:]}'}
    found = re.fullmatch(r'(?:20)?(\d{2})', t) or re.fullmatch(r'(\d{4})', t)
    if found and (len(found.group(1)) == 2 or found.group(1).startswith('20')):
        year = found.group(1)[-2:]
        return {f'{s}{year}' for s in 'WSFP'}
    return None


def country_name(code: str | None) -> str | None:
    """'US' -> 'United States', 'GB' -> 'United Kingdom'; None when it is not a two-letter code pycountry knows ('SF')."""
    if not re.fullmatch(r'[A-Z]{2}', code or ''):
        return None
    try:
        import pycountry
        found = pycountry.countries.get(alpha_2=code)
    except (ImportError, LookupError, KeyError):
        return None
    return (getattr(found, 'common_name', None) or found.name) if found else None


def is_country(code: str) -> bool:
    """'US', 'IN' -> True; 'SF', 'NY' -> False: a two-letter code pycountry knows as a country."""
    return country_name(code) is not None


def place_of(text: str) -> dict:
    """One place as YC writes it -> a place entry: 'US' -> the country; 'Bengaluru, KA, IN' -> city, region and country;
    'CA, US' -> a region of a country; anything else ('San Francisco, CA', 'Dublin, Ireland', 'SF') keeps its words,
    and the engine reads a country name in them."""
    bits = [b.strip() for b in text.split(',') if b.strip()]
    if len(bits) == 1 and is_country(bits[0]):
        return {'name': text, 'country': country_name(bits[0])}
    if len(bits) >= 3 and is_country(bits[-1]):
        return {'name': text, 'city': bits[0], 'region': bits[-2], 'country': country_name(bits[-1])}
    if len(bits) == 2 and is_country(bits[1]) and re.fullmatch(r'[A-Z]{2,3}', bits[0]):
        return {'name': text, 'region': bits[0], 'country': country_name(bits[1])}
    return {'name': text}


def placed(entry: dict) -> bool:
    """The engine finds a country or a region in this place, or it names none (Remote, Worldwide)."""
    if entry.get('country') or job_places({'places': [entry]})[1]:
        return True
    toks = regions.tokens(entry.get('name'))
    return regions.entry_kind(toks) is not None or bool(regions.regions_in(toks, lambda t: False))


def text_places(location: str | None) -> tuple[list[str], list[dict], bool]:
    """YC's location text -> (its parts, place entries, vague). Parts are split at ' / '; "Remote (US; CA)" gives one remote
    place per item. vague: some part names a place without a country or region the engine can read ("SF", "New York City",
    "San Francisco, CA"), so a place filter asks the job's page."""
    parts = [p.strip() for p in str(location or '').split(' / ') if p.strip()]
    entries, vague = [], False
    for part in parts:
        inner = re.fullmatch(r'remote\s*\((.*)\)', part, re.I | re.S)
        items = [i.strip() for i in inner.group(1).split(';') if i.strip()] if inner else []
        for item in items:
            entry = {**place_of(item), 'name': f'Remote ({item})'}
            entries.append(entry)
            vague = vague or not placed({**entry, 'name': item})
        if inner and items:
            continue
        entry = {'name': part} if re.match(r'remote\b', part, re.I) else place_of(part)
        entries.append(entry)
        vague = vague or not placed(entry)
    return parts, entries, vague


def address_places(posting: dict) -> list[dict]:
    """The JobPosting's offices -> place entries with city, region and country code."""
    found = posting.get('jobLocation')
    out = []
    for spot in found if isinstance(found, list) else [found] if isinstance(found, dict) else []:
        address = spot.get('address') if isinstance(spot, dict) else None
        if isinstance(address, str) and address.strip():          # 1 in 132 is a text ("SF Headquarters")
            out.append({'name': ' '.join(address.split())})
            continue
        address = address if isinstance(address, dict) else {}
        city, region = address.get('addressLocality') or None, address.get('addressRegion') or None
        code = address.get('addressCountry')
        code = code.strip() if isinstance(code, str) and code.strip() else None
        country = country_name(code) or code
        name = ', '.join(p for p in (city, region, code) if p)
        if name:
            out.append({'name': name, 'city': city, 'region': region, 'country': country})
    return out


def work_type(parts: list[str], title: str | None) -> str:
    """'remote' when a place is remote, 'hybrid' when the text says so, else 'onsite' (the job names only offices)."""
    if any(re.search(r'\bremote\b', p, re.I) for p in parts):
        return 'remote'
    return 'hybrid' if re.search(r'\bhybrid\b', ' '.join(parts) + ' ' + (title or ''), re.I) else 'onsite'


def stage_of(company: dict) -> str | None:
    """The directory's stage and status -> 'early', 'growth' or 'public'."""
    if str(company.get('status') or '').lower() == 'public':
        return 'public'
    stage = str(company.get('stage') or '').lower()
    return stage if stage in ('early', 'growth') else None


def squeeze(text: str | None) -> str:
    return ''.join(words(text))


class YCombinator(Platform):
    key = 'ycombinator'
    name = 'Y Combinator'
    site_word = 'company page'       # a named company's jobs page: "Found 14 matching jobs at 1 company on 1 Y Combinator company page"
    actor = 'ycombinator-jobs-search'
    board_example = 'https://www.ycombinator.com/companies/airbnb/jobs'
    job_example = 'https://www.ycombinator.com/companies/authzed/jobs/ena6RKJ-solutions-engineer-u-s-and-canada'
    concurrency = 32              # companies named in the companies field read at once; the connections are twice this
    server_filters = False        # every listed job reaches the engine, opened or as listed, so it names the filter
    never = ('team', 'expiresAt', 'reference')
    extra = ('salaryMin', 'salaryMax', 'salaryCurrency', 'salaryPeriod')     # pay as numbers on every row: from the list
    filter_words = {'role': ('role', 'clear Role'),
                    'batch': ('YC batch', 'clear YC batch'),
                    'visa': ('visa', 'clear Visa'),
                    'minSalaryUsd': ('pay', 'type a lower Minimum pay or leave it empty'),
                    'withEquity': ('equity', 'untick Only with equity'),
                    'teamSize': ('team size', 'type a wider Team size or leave it empty'),
                    'companyStage': ('company stage', 'clear Company stage')}

    def __init__(self):
        self.cfg = None
        self.started: datetime | None = None
        self._settings: dict = {'role': [], 'batch': [], 'visa': [], 'minSalaryUsd': None, 'withEquity': False,
                                'teamSize': None, 'companyStage': []}
        self._site: dict | None = None             # the directory page's Algolia options and Inertia version
        self._site_lock = asyncio.Lock()
        self.listed = 0               # jobs listed by the companies read
        self.lists_failed = 0         # companies whose job list could not be read
        self.opened = 0               # job pages opened
        self.closed = 0               # jobs that closed between their list and their page
        self.unreadable = 0           # job pages that did not answer, or answered in a shape not seen before
        self._said: dict[str, int] = {}

    # ---------- what people type ----------

    def boards(self):
        """No list to ship: every search reads YC's directory live, and a company is found by its link or its name."""
        return [], None

    def read_link(self, text):
        """A YC or Work at a Startup link -> (company, job). Job links: ycombinator.com/companies/<slug>/jobs/<code>-<title>
        and workatastartup.com/jobs/<id> (any company: '*'). Company links: ycombinator.com/companies/<slug>[/jobs] and
        workatastartup.com/companies/<slug>. Any other page of either site names no company: (None, None)."""
        text = text.strip().rstrip('.,;:!?')
        link = text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}'
        parsed = urlparse(link)
        host = (parsed.hostname or '').lower() if ' ' not in text else ''
        if host not in HOSTS + WAAS_HOSTS:
            return None
        parts = [unquote(p) for p in parsed.path.split('/') if p]
        if host in WAAS_HOSTS:
            if len(parts) >= 2 and parts[0] == 'jobs' and parts[1].isdigit():
                return EVERYONE.key, parts[1]
            if len(parts) >= 2 and parts[0] == 'companies' and SLUG.match(parts[1]):
                return parts[1].lower(), None
            return None, None
        if len(parts) >= 2 and parts[0] == 'companies' and SLUG.match(parts[1]) and parts[1] not in ('industry',
                                                                                                   'location', 'batch'):
            if len(parts) >= 4 and parts[2] == 'jobs':
                code = CODE.match(parts[3])
                return (parts[1].lower(), code.group(1)) if code else (parts[1].lower(), None)
            return parts[1].lower(), None
        return None, None

    def search_link(self, text):
        """ycombinator.com/jobs/role/<role>/<place> and /jobs/location/<place>: the place they ask for (read_input reads
        the role)."""
        parts = [unquote(p) for p in urlparse(text if '://' in text else f'https://{text}').path.split('/') if p]
        place = None
        if len(parts) >= 3 and parts[:2] == ['jobs', 'location']:
            place = parts[2]
        elif len(parts) >= 4 and parts[:2] == ['jobs', 'role']:
            place = parts[3]
        return [], ([' '.join(w.capitalize() for w in place.split('-'))] if place else [])

    def guess_board(self, name):
        """A company typed by name: open() finds it in YC's directory."""
        return name if re.search(r'[^\W_]', name) else None

    def read_input(self, raw, cfg):
        """YC's own fields: role, batch, visa, minimum pay in US dollars, equity, team size and company stage."""
        self.cfg = cfg
        notes = cfg.notes
        # a pasted YC role page (ycombinator.com/jobs/role/software-engineer[/remote]) is that role; its place was read
        # by the engine (search_link), and a page with no place was skipped as a link naming no company
        linked = []
        for token in _items(raw.get('searchTerms')):
            found = re.search(r'ycombinator\.com/jobs/role/([a-z-]+)', token, re.I)
            if found:
                linked.append(found.group(1))
                notes.append(f'Read {token.strip()} in searchTerms as the role {found.group(1).replace("-", " ")}.')
        cfg.skipped[:] = [(t, why) for t, why in cfg.skipped if not re.search(r'ycombinator\.com/jobs/role/', t, re.I)]
        roles = self.read_roles(_items(raw.get('role')) + linked, notes)
        self.read_places(cfg)
        batches: list[str] = []
        for token in _items(raw.get('batch')):
            found = batches_typed(token)
            if found is None:
                notes.append(f'Could not read "{token}" in batch as a YC batch, such as W25, Summer 2024 or 2025, so it '
                             f'was left out.')
                continue
            batches += sorted(found - set(batches))
        visas = []
        for token in _items(raw.get('visa')):
            key = VISA_ALIASES.get(' '.join(words(token)))         # will-sponsor, Will sponsor, US citizen/visa only
            if key is None:
                notes.append(f'visa has no option {token}, so it was left out. The options are will-sponsor, '
                             f'not-required and us-only.')
            elif key not in visas:
                visas.append(key)
        stages = []
        for token in _items(raw.get('companyStage')):
            key = STAGE_ALIASES.get(' '.join(words(token)))
            if key is None:
                notes.append(f'companyStage has no option {token}, so it was left out. The options are early, growth '
                             f'and public.')
            elif key not in stages:
                stages.append(key)
        self._settings.update({
            'role': roles,
            'batch': batches,
            'visa': visas if len(visas) < len(VISAS) else [],
            'minSalaryUsd': self.read_dollars(raw.get('minSalaryUsd'), notes),
            'withEquity': parse_bool(raw.get('withEquity'), False),
            'teamSize': self.read_size(raw.get('teamSize'), notes),
            'companyStage': stages if len(stages) < len(STAGES) else [],
        })
        asked = [t for t in cfg.job_types if t in ('part-time', 'temporary')]
        if asked:
            notes.append(f'YC jobs are full-time, internship, contract or co-founder, so {", ".join(asked)} finds no '
                         f'job.')

    @staticmethod
    def read_places(cfg) -> None:
        """A place where YC companies hire, typed with the job title ("product manager in new york", "designer london"),
        moves to location, as the engine does for the places it knows; and SF, NYC and the Bay Area, typed in either
        field, also find the names YC's jobs write (San Francisco, New York, the Bay Area's cities)."""
        have = {' '.join(words(p)) for p in cfg.locations}
        terms = []
        for term in cfg.search_terms:
            title, place = split_place(term)
            if place is None:
                terms.append(term)
                continue
            read = [f'the job title "{title}"'] if title else []
            cfg.notes.append(f'Read "{term}" in searchTerms as ' + ', '.join(read + [f'jobs in {place}']) + '.')
            if ' '.join(words(place)) not in have:
                have.add(' '.join(words(place)))
                cfg.locations.append(place)
            if title and title.lower() not in (t.lower() for t in terms):
                terms.append(title)
        cfg.search_terms[:] = terms
        for typed in list(cfg.locations):
            others = ABBREVIATIONS.get(' '.join(words(typed)), ())
            added = [o for o in others if ' '.join(words(o)) not in have]
            if added:
                have.update(' '.join(words(o)) for o in added)
                cfg.locations.extend(added)
                cfg.notes.append(f'YC writes {typed} as {", ".join(added[:3])}{" and its other cities" if len(added) > 3 else ""}, '
                                 f'so {"they are" if len(added) > 1 else "it is"} searched too.')

    @staticmethod
    def read_roles(tokens: list[str], notes: list[str]) -> list[str]:
        """Role areas and subtypes typed in everyday words -> 'area:eng' or 'type:full stack' entries."""
        roles = []
        for token in tokens:
            key = ' '.join(words(token.replace('-', ' ').replace('_', ' ')))
            area = ROLE_ALIASES.get(key)
            subtype = SUBTYPE_ALIASES.get(key)
            entry = f'type:{subtype}' if subtype and not area else f'area:{area}' if area else None
            if entry is None:
                notes.append(f'role has no option {token}, so it was left out. The options are the areas engineering, '
                             f'design, product, science, sales, marketing, support, operations, recruiting, finance and '
                             f'legal, or an engineering kind such as backend, frontend, full stack or machine learning.')
            elif entry not in roles:
                roles.append(entry)
        return roles

    @staticmethod
    def read_dollars(value, notes) -> float | None:
        """150000, '150k', '$150,000', '150K USD' -> 150000 US dollars a year; empty -> None."""
        if value is None or value == '' or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            dollars = float(value)
        else:
            text = str(value).strip().lower().replace(',', '').replace('$', '')
            found = re.search(r'(\d+(?:\.\d+)?)\s*(k|m)?\b', text)
            if not found:
                notes.append(f'Could not read minSalaryUsd "{value}" as US dollars a year, such as 150000 or 150k, so '
                             f'pay is not filtered.')
                return None
            dollars = amount(found.group(1), found.group(2))
        if 0 < dollars < 1000:                    # 150 typed for 150k: thousands
            notes.append(f'Read minSalaryUsd {dollars:g} as {dollars:g},000 US dollars a year.')
            dollars *= 1000
        return dollars if dollars > 0 else None

    @staticmethod
    def read_size(value, notes) -> tuple[float, float] | None:
        """Team size as people type it -> (fewest, most) people: '1-10', '11-50', '50+', '10' (exactly that many)."""
        if value is None or value == '' or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return (float(value), float(value)) if value > 0 else None
        text = ' '.join(str(value).lower().replace('people', '').replace('employees', '').replace(',', '').split())
        found = re.fullmatch(r'(\d+)\s*(?:(?:-|to|–)\s*(\d+)|(\+|or more|and above|plus))?', text)
        if not found:
            notes.append(f'Could not read teamSize "{value}" as a team size, such as 1-10, 11-50 or 50+, so team size is '
                         f'not filtered.')
            return None
        low = float(found.group(1))
        high = float('inf') if found.group(3) else float(found.group(2)) if found.group(2) else low
        return min(low, high), max(low, high)

    # ---------- YC's own filters ----------

    def settings(self) -> dict:
        out = dict(self._settings)
        size = out.get('teamSize')
        out['teamSize'] = [size[0], None if math.isinf(size[1]) else size[1]] if size else None
        out['role'] = [ROLES.get(r[5:], r[5:]) if r.startswith('area:') else r[5:] for r in out['role']]
        return out

    def fits(self, job: dict) -> bool | str:
        """YC's own filters, in the form's order. True when the job passes them all, else the input name of the first it
        fails (Platform.fits). Years of experience is the engine's (experienceYears)."""
        s = self._settings
        extra = job.get('extra') or {}
        if s['role'] and not self.role_fits(job, s['role']):
            return 'role'
        if s['batch'] and extra.get('batch') not in s['batch']:
            return 'batch'
        if s['visa'] and not any(VISAS[v] == extra.get('visa') for v in s['visa']):
            return 'visa'
        if s['minSalaryUsd'] is not None:
            top = job.get('salaryMax') if job.get('salaryMax') is not None else job.get('salaryMin')
            per = PER_YEAR.get(job.get('salaryPeriod'))
            if top is None or job.get('salaryCurrency') != 'USD' or per is None or top * per < s['minSalaryUsd']:
                return 'minSalaryUsd'
        if s['withEquity'] and not extra.get('equity'):
            return 'withEquity'
        if s['teamSize'] is not None:
            size = extra.get('teamSize')
            if not isinstance(size, (int, float)) or not s['teamSize'][0] <= size <= s['teamSize'][1]:
                return 'teamSize'
        if s['companyStage'] and extra.get('companyStage') not in s['companyStage']:
            return 'companyStage'
        return True

    @staticmethod
    def role_fits(job: dict, roles: list[str]) -> bool:
        """The job is in one of the role areas asked for (engineering, sales) or of the subtypes (backend, ios; mobile is
        iOS, Android and mobile design)."""
        area = job.get('roleArea')
        subtype = ' '.join(words((job.get('extra') or {}).get('roleType')))
        for entry in roles:
            kind, value = entry.split(':', 1)
            if kind == 'area' and value == area:
                return True
            if kind == 'type' and (value == subtype or (value == 'mobile' and subtype in ('ios', 'android',
                                                                                           'mobile design'))):
                return True
        return False

    # ---------- calls ----------

    def say(self, topic: str, note: str) -> None:
        """A note kept current while the run reads: one per topic, replaced in place."""
        if self.cfg is None:
            return
        at = self._said.get(topic)
        if at is None:
            self._said[topic] = len(self.cfg.notes)
            self.cfg.notes.append(note)
        else:
            self.cfg.notes[at] = note

    async def site(self, http) -> dict:
        """The directory page's Algolia options and Inertia version, read once a run."""
        async with self._site_lock:
            if self._site is None:
                response = await send(http, 'GET', DIRECTORY)
                if response.status_code != 200:
                    raise BoardError(f"HTTP {response.status_code} from YC's company directory")
                opts = ALGOLIA_OPTS.search(response.text)
                page = inertia_page(response.text) or {}
                try:
                    algolia = json.loads(opts.group(1)) if opts else {}
                except ValueError:
                    algolia = {}
                if not algolia.get('app') or not algolia.get('key'):
                    raise BoardError("YC's company directory gave no search key")
                self._site = {'app': algolia['app'], 'key': algolia['key'], 'version': page.get('version')}
            return self._site

    async def algolia(self, http, queries: list[dict]) -> list[dict]:
        """Several queries of YC's directory in one call -> their results."""
        site = await self.site(http)
        body = {'requests': [{'indexName': INDEX, 'params': urlencode(
            {k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in q.items()})} for q in queries]}
        response = await send(http, 'POST', ALGOLIA.format(app=site['app'].lower()), json=body,
                              headers={'X-Algolia-Application-Id': site['app'], 'X-Algolia-API-Key': site['key']})
        if response.status_code != 200:
            raise BoardError(f"HTTP {response.status_code} from YC's company directory search")
        try:
            results = response.json()['results']
        except (ValueError, KeyError, TypeError) as exc:
            raise BoardError("YC's company directory search answered in an unknown shape") from exc
        return results

    async def hiring(self, http) -> dict[str, dict]:
        """Every company the directory marks hiring: slug -> its fields. Batches are grouped under GROUP companies a query."""
        first = (await self.algolia(http, [{'query': '', 'hitsPerPage': 0, 'filters': 'isHiring:true',
                                            'facets': ['batch'], 'maxValuesPerFacet': 1000}]))[0]
        groups, group, size = [], [], 0
        for name, count in sorted((first.get('facets') or {}).get('batch', {}).items()):
            if group and size + count > GROUP:
                groups.append(group)
                group, size = [], 0
            group.append(name)
            size += count
        if group:
            groups.append(group)
        queries = [{'query': '', 'hitsPerPage': 1000, 'attributesToRetrieve': DIRECTORY_FIELDS, 'attributesToHighlight': [],
                    'filters': 'isHiring:true AND (' + ' OR '.join('batch:' + json.dumps(b) for b in g) + ')'}
                   for g in groups] or [{'query': '', 'hitsPerPage': 1000, 'filters': 'isHiring:true',
                                         'attributesToRetrieve': DIRECTORY_FIELDS, 'attributesToHighlight': []}]
        companies = {}
        for result in await self.algolia(http, queries):
            for hit in result.get('hits') or []:
                if isinstance(hit, dict) and hit.get('slug'):
                    companies[hit['slug']] = hit
        if not companies:
            raise BoardError("YC's company directory listed no company hiring")
        expected = first.get('nbHits') or 0
        if expected > len(companies):
            self.incomplete = True
            self.say('directory', f"YC's directory listed {len(companies):,} of the {expected:,} companies it marks "
                                  f'hiring, so the jobs of the others were not searched.')
        return companies

    async def named(self, http, typed: str) -> dict[str, dict]:
        """A company typed by name or address -> the directory's companies it names: the one whose address or name is
        the typed text, else those whose name starts with it."""
        result = (await self.algolia(http, [{'query': typed, 'hitsPerPage': 30, 'attributesToRetrieve': DIRECTORY_FIELDS,
                                             'attributesToHighlight': []}]))[0]
        hits = [h for h in result.get('hits') or [] if isinstance(h, dict) and h.get('slug')]
        wanted = squeeze(typed)
        exact = [h for h in hits if h['slug'].lower() == typed.lower() or squeeze(h.get('name')) == wanted
                 or squeeze(h['slug']) == wanted]
        found = exact or [h for h in hits if ' '.join(words(h.get('name'))).startswith(' '.join(words(typed)) + ' ')]
        return {h['slug']: h for h in found[:10]}

    async def get_page(self, http, url: str, props: str | None, component: str):
        """A YC page, asked for as an Inertia partial reload of props when the version is known. A changed version
        (409) is read again whole."""
        site = await self.site(http)
        headers = {'X-Inertia': 'true', 'X-Inertia-Version': site['version'], 'X-Inertia-Partial-Component': component,
                   'X-Inertia-Partial-Data': props} if props and site.get('version') else None
        response = await send(http, 'GET', url, headers=headers)
        if response.status_code == 409:
            response = await send(http, 'GET', url)
            page = inertia_page(response.text)
            if page and page.get('version'):
                site['version'] = page['version']
        return response

    async def company_jobs(self, http, slug: str) -> list[dict] | None:
        """One company's open jobs as YC lists them; None when YC has no such company."""
        response = await self.get_page(http, COMPANY_JOBS.format(slug=slug), 'jobPostings', 'WaasShowJobsPage')
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise BoardError(f'HTTP {response.status_code} from the jobs of {slug}')
        page = inertia_page(response.text)
        if page is None or not isinstance(page.get('props'), dict):
            raise BoardError(f'the jobs page of {slug} has no job list')
        return [j for j in page['props'].get('jobPostings') or [] if isinstance(j, dict) and j.get('id')]

    # ---------- jobs ----------

    def from_list(self, raw: dict, company: dict, slug: str, now: datetime | None = None) -> dict:
        """A job as its company's list gives it -> the shape of rows.py, dated the newest its createdAt words allow."""
        now = now or datetime.now(timezone.utc)
        parts, spots, vague = text_places(raw.get('location'))
        title = ' '.join(str(raw.get('title') or '').split())
        pay = parse_pay(raw.get('salaryRange'))
        low_eq, high_eq = parse_equity(raw.get('equityRange'))
        low_years, high_years = parse_years(raw.get('minExperience'), raw.get('type'))
        ages = age_range(raw.get('createdAt')) or (0.0, math.inf)      # words not read: its page dates it
        url = str(raw.get('url') or '')
        stage = stage_of(company)
        team = company.get('team_size')
        industries = [i for i in company.get('industries') or [] if isinstance(i, str) and i and i != 'Unspecified']
        return {
            'id': str(raw['id']),
            'rank': int(raw['id']) if str(raw['id']).isdigit() else None,     # jobs posted the same second: newest id first
            'title': title,
            'company': ' '.join(str(raw.get('companyName') or company.get('name') or slug).split()),
            'companySlug': slug,
            'companyWebsite': company.get('website') or None,
            'location': parts[0] if parts else None,
            'otherLocations': parts[1:],
            'places': spots,
            'vague': vague,
            'workType': work_type(parts, title),
            'employmentType': TYPES.get(str(raw.get('type') or '').lower()),
            'department': raw.get('prettyRole') or ROLES.get(raw.get('role') or '', '').capitalize() or None,
            'roleArea': raw.get('role') or None,
            **pay,
            'offersEquity': low_eq is not None,
            'postedAt': iso(now - timedelta(seconds=ages[0])),
            'ages': ages,
            'jobUrl': SITE + url if url.startswith('/') else url or None,
            'applyUrl': raw.get('applyUrl') or raw.get('ctaUrl') or None,
            'experienceMin': low_years,
            'experienceMax': high_years,
            'skills': [s for s in raw.get('skills') or [] if isinstance(s, str)],
            'extra': {
                'batch': raw.get('companyBatchName') or batch_code(company.get('batch')),
                'roleType': raw.get('roleSpecificType') or None,
                'equity': ' '.join(str(raw.get('equityRange') or '').split()) or None,
                'equityMin': low_eq,
                'equityMax': high_eq,
                'visa': raw.get('visa') or None,
                'schoolYear': raw.get('minSchoolYear') or None,
                'skills': [s for s in raw.get('skills') or [] if isinstance(s, str)],
                'companyOneLiner': ' '.join(str(raw.get('companyOneLiner') or company.get('one_liner') or '').split())
                                   or None,
                'teamSize': team if isinstance(team, int) and not isinstance(team, bool) else None,
                'companyStage': stage,
                'industry': ', '.join(industries) or None,
                'workAtAStartupUrl': WAAS_JOB.format(id=raw['id']),
            },
        }

    def with_page(self, job: dict, posting: dict) -> dict:
        """A listed job and its page's JobPosting -> the job with its exact date, its offices and its description."""
        posted = parse_time(posting.get('datePosted'))
        html = posting.get('description') if isinstance(posting.get('description'), str) else None
        offices = address_places(posting)
        full = {**job, 'places': offices + [p for p in job.get('places') or []], 'description': text_of(html),
                'descriptionHtml': html or None}
        if posted:
            full['postedAt'] = iso(posted)
        full.pop('ages', None)
        full.pop('vague', None)
        return full

    async def open_one(self, http, job: dict) -> dict | None:
        """A job that can match -> the job with its page read; None when it closed meanwhile or its page cannot be read."""
        try:
            response = await self.get_page(http, job['jobUrl'], 'job', 'WaasShowJobPage')
        except BoardError:
            self.unreadable += 1
            return None
        finally:
            self.opened += 1
        if response.status_code == 404:
            self.closed += 1
            return None
        posting = job_posting(response.text) if response.status_code == 200 else {}
        if not posting.get('datePosted'):
            self.unreadable += 1
            return None
        return self.with_page(job, posting)

    def in_period(self, job: dict) -> bool:
        """Can the job have been posted inside postedWithin? Judged by the youngest age its createdAt words allow."""
        cfg = self.cfg
        return cfg is None or cfg.posted_within_days is None or job['ages'][0] <= cfg.posted_within_days * DAY

    def can_match(self, job: dict, filters: Filters, loose: Filters | None, now: datetime) -> bool:
        """Can this listed job pass every filter once its page is read? Its words, place, work and job type, years and
        YC's own filters are all in the list; its date is known to the minute up to a day, the day up to a month, and
        less closely after (in_period). A place filter cannot be judged on a job whose place names no country: its page
        says."""
        dated = {**job, 'postedAt': iso(now)}         # the date is judged on its own, below
        missed = filters.misses(dated, now, every=True)
        if missed is None and loose is not None:      # words searched in descriptions too: the page holds them
            missed = loose.misses(dated, now, every=True)
        if missed is None:
            return False
        failed = set(missed)
        fit = self.fits(job)
        if fit is not True:
            failed.add(fit)
        if not self.in_period(job):
            failed.add('postedWithin')
        return not failed or (failed == {'location'} and job.get('vague'))

    def as_listed(self, job: dict, now: datetime) -> dict:
        """A job that cannot match, for the engine to count (read, near misses): dated the newest its words allow, or a
        day older than that when it is outside the period, so the engine's own clock, a little earlier, agrees."""
        out = {k: v for k, v in job.items() if k not in ('ages', 'vague')}
        age = job['ages'][0] + (0 if self.in_period(job) else DAY)
        out['postedAt'] = iso(now - timedelta(seconds=age)) if not math.isinf(age) else None
        return out

    async def read_lists(self, http, companies: dict[str, dict]) -> list[tuple[str, list[dict]]]:
        """Every company's job list, LISTS_AT_ONCE at a time; stops LIST_RESERVE_S before the run's time limit."""
        sem = asyncio.Semaphore(LISTS_AT_ONCE)
        done, lists, cut = 0, [], 0
        total = len(companies)

        async def one(slug: str):
            nonlocal done, cut
            async with sem:
                left = self.time_left()
                if left is not None and left < LIST_RESERVE_S:
                    cut += 1
                    return
                try:
                    found = await self.company_jobs(http, slug)
                except BoardError:
                    self.lists_failed += 1
                    return
                finally:
                    done += 1
                    self.progress = f'Read the job lists of {done:,} of {total:,} YC companies.'
                if found:
                    lists.append((slug, found))
        await asyncio.gather(*(one(s) for s in companies))
        if self.lists_failed:
            self.incomplete = True
            self.say('lists', f'The job lists of {self.lists_failed:,} of {total:,} YC companies could not be read, so '
                              f'their jobs are missing.')
        if cut:
            self.incomplete = True
            self.say('cut', f"The run's time limit left room to read the job lists of {total - cut:,} of {total:,} YC "
                            f'companies. Raise the run timeout to search them all.')
        if total and self.lists_failed == total:
            raise BoardError('no YC company job list could be read')
        if total and cut == total:
            raise BoardError("the run's time limit left no time to read the YC companies' job lists")
        return lists

    @staticmethod
    def time_left() -> float | None:
        from .. import engine
        return engine.seconds_left_in_run()

    # ---------- the run ----------

    def search_all(self, boards, cfg):
        """Every hiring company at once. The engine calls this right after it reads its clock, so its time is the run's."""
        self.cfg = cfg
        self.started = datetime.now(timezone.utc)
        return [EVERYONE]

    async def open(self, http, board):
        if board.key == EVERYONE.key:
            return {'everyone': True}
        if self.cfg is not None and self.cfg.job_urls:           # checking links: the job's own page says the rest
            return {'slug': board.key.lower()}
        typed = board.key
        found = await self.named(http, typed)
        if not found and SLUG.match(typed):          # an address the directory search does not find: try its page
            jobs = await self.company_jobs(http, typed.lower())
            return None if jobs is None else {'companies': {typed.lower(): {}}, 'lists': [(typed.lower(), jobs)]}
        if not found:
            return None
        if len(found) > 1 and self.cfg is not None:
            self.cfg.notes.append(f'"{typed}" names {len(found)} YC companies: {", ".join(list(found)[:5])}; all were '
                                  f'read.')
        return {'companies': found}

    async def pages(self, http, session, term, days):
        cfg = self.cfg
        if session.get('everyone'):
            self.progress = "Reading YC's company directory."
            companies = await self.hiring(http)
            lists = await self.read_lists(http, companies)
        else:
            companies = session['companies']
            lists = session.get('lists') or await self.read_lists(http, companies)
        self.progress = None
        now = self.started or datetime.now(timezone.utc)      # the engine's clock: it dates jobs by it
        listed = sorted((self.from_list(raw, companies.get(slug) or {}, slug, now) for slug, raws in lists for raw in raws),
                        key=lambda j: -int(j['id']) if j['id'].isdigit() else 0)
        self.listed += len(listed)
        filters = Filters(cfg) if cfg is not None else None
        loose = Filters(dataclasses.replace(cfg, search_terms=[])) if cfg is not None and cfg.search_descriptions else None
        wanted, rest = [], []
        for job in listed:
            (wanted if filters is None or self.can_match(job, filters, loose, now) else rest).append(job)
        for start in range(0, len(rest), 1000):             # the engine counts them: read, near misses
            yield [self.as_listed(j, now) for j in rest[start:start + 1000]]
        async for jobs in self.scan(http, wanted, filters, now):
            yield jobs

    async def scan(self, http, wanted: list[dict], filters: Filters | None, now: datetime):
        """The jobs that can match, newest first, opened a page at a time (PAGES_AT_ONCE at once); stops once maxJobs of
        them pass every filter, or at READ_BUDGET pages."""
        cfg = self.cfg
        held: set = set()
        sem = asyncio.Semaphore(PAGES_AT_ONCE)

        async def one(job: dict):
            async with sem:
                return await self.open_one(http, job)
        start = 0
        while start < len(wanted):
            if self.opened >= READ_BUDGET:
                self.incomplete = True
                self.say('budget', f'This run opened {self.opened:,} job pages, the most one run opens, so '
                                   f'{len(wanted) - start:,} jobs that can match were not checked.')
                break
            size = PAGE
            if cfg is not None and cfg.max_jobs:
                size = min(PAGE, max(10, math.ceil((cfg.max_jobs - len(held)) * 1.25)))
            part = wanted[start:start + size]
            start += len(part)
            opened = [j for j in await asyncio.gather(*(one(j) for j in part)) if j]
            if opened:
                yield opened
            if filters is not None and cfg.max_jobs:
                for job in opened:
                    if filters.passes(job, now) and self.fits(job) is True:
                        held.add(((job.get('company') or '').casefold(), *self.copy_key(job)))
                if len(held) >= cfg.max_jobs:
                    self.count_rest(wanted[start:], held)
                    break
        if self.unreadable:
            self.say('unreadable', f'{self.unreadable:,} YC job pages could not be read, so those jobs were left out.')

    def count_rest(self, rest: list[dict], held: set) -> None:
        """Jobs that can match and were not opened: none can be newer than the maxJobs held. Each counts as a match when
        its words are in its title and its date and place are certain, once per company, title and place as the engine
        counts; any other makes the count a floor."""
        cfg = self.cfg
        window = cfg.posted_within_days * DAY if cfg is not None and cfg.posted_within_days is not None else None
        placed_by_text = cfg is None or not cfg.locations          # a vague place matters only to a place filter
        sure, companies, seen = 0, set(), set(held)
        for job in rest:
            ages = job.get('ages') or (0.0, math.inf)
            certain = (placed_by_text or not job.get('vague')) and not (cfg is not None and cfg.search_descriptions) \
                and (window is None or ages[1] <= window)
            key = ((job.get('company') or '').casefold(), *self.copy_key(job))
            if not certain:
                self.count_is_floor = True
            elif key not in seen:
                seen.add(key)
                sure += 1
                companies.add(job.get('company'))
        self.unopened_matches += sure
        self.unopened_companies = frozenset(set(self.unopened_companies) | companies)

    # ---------- links ----------

    async def lookup(self, http, session, job_id):
        """One job -> ('open', job) | ('closed', None) | ('not found', None). A Work at a Startup id is looked up there
        first, for its company; a YC job address on its company's page."""
        if job_id.isdigit():
            return await self.lookup_waas(http, job_id)
        slug = session.get('slug') if isinstance(session, dict) else None
        return await self.lookup_yc(http, slug, job_id)

    async def lookup_yc(self, http, slug: str | None, code: str, job_id: str | None = None):
        if not slug:
            return 'not found', dict(NOBODY)
        response = await send(http, 'GET', JOB_PAGE.format(slug=slug, code=code))
        if response.status_code == 404:
            jobs = await self.company_jobs(http, slug)
            return ('closed' if jobs is not None else 'not found'), {**NOBODY, 'companySlug': slug} if jobs is not None \
                else dict(NOBODY)
        if response.status_code != 200:
            raise BoardError(f'HTTP {response.status_code} from the job page')
        page = inertia_page(response.text) or {}
        props = page.get('props') if isinstance(page.get('props'), dict) else {}
        raw, company = props.get('job'), props.get('company') if isinstance(props.get('company'), dict) else {}
        posting = job_posting(response.text)
        if not isinstance(raw, dict) or not raw.get('id') or not posting:
            return 'not found', dict(NOBODY)
        final = urlparse(str(response.url)).path.split('/')
        slug = final[2] if len(final) > 2 and final[1] == 'companies' else slug
        info = {'name': company.get('name'), 'website': company.get('website'), 'team_size': company.get('team_size'),
                'one_liner': company.get('one_liner'), 'batch': company.get('batch_name'),
                'status': company.get('ycdc_status')}
        directory = await self.named(http, slug)
        info.update({k: v for k, v in (directory.get(slug) or {}).items() if v not in (None, '', [])})
        return 'open', self.with_page(self.from_list(raw, info, slug), posting)

    async def lookup_waas(self, http, job_id: str):
        response = await send(http, 'GET', WAAS_JOB.format(id=job_id), headers={'Accept': 'text/html'})   # else 406
        if response.status_code == 404:
            return 'not found', dict(NOBODY)
        if response.status_code != 200:
            raise BoardError(f'HTTP {response.status_code} from Work at a Startup')
        page = inertia_page(response.text) or {}
        props = page.get('props') if isinstance(page.get('props'), dict) else {}
        slug = (props.get('company') or {}).get('slug') if isinstance(props.get('company'), dict) else None
        if not slug:
            return 'not found', dict(NOBODY)
        listed = next((j for j in await self.company_jobs(http, slug) or [] if str(j.get('id')) == job_id), None)
        if listed is None:                          # open on Work at a Startup, not on its YC page: closed there
            return 'closed', {**NOBODY, 'companySlug': slug}
        code = CODE.match(str(listed.get('url') or '').rsplit('/', 1)[-1])
        if not code:
            return 'not found', dict(NOBODY)
        return await self.lookup_yc(http, slug, code.group(1))
