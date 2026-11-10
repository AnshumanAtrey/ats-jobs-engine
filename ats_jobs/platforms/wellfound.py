"""
Wellfound (wellfound.com, formerly AngelList Talent), the startup job board, read through the search pages it serves to
visitors who are not logged in. No login, no key, no browser. Measured from this laptop on 2026-11-10:

- Cloudflare stands in front of the site (not DataDome: no DataDome cookie or script on any page). It answers plain HTTP
  with any ordinary user agent on the pages below and refuses a bare "python-httpx" agent (403). It puts a managed
  challenge (403, cf-mitigated: challenge) on the GraphQL API (/graphql) and on every company page (/company/<slug>,
  /jobs, /funding, /people), even to a browser's TLS with navigation headers, and a headless Chromium stayed on "Just a
  moment..." for 30 s there, so neither is read here.
- Search pages, server-rendered by Next.js with the search answer in __NEXT_DATA__ (apolloState):
  /role/<role>, /location/<place>, /role/l/<role>/<place>, /role/r/<role> (remote) and /remote, each paged with ?page=N.
  A page holds 20 startups and up to 3 jobs of each (highlightedJobListings: 1 to 3, never more, on 12,202 jobs read),
  with the job's full description, pay and equity text, places, remote setting, years of experience and posting time,
  and the startup's size, stage and badges. A page is 300-900 KB of HTML, 60-90 KB over the wire, under a second;
  past the last page the site answers 303 back to page 1, and a role or place it does not have answers 404.
- A role is a keyword search over whole jobs, not a job family: role/python-developer listed 764 jobs, 46 with Python in
  the title; role/leadership 7,455. Its pages come in Wellfound's own order, not by date: on every query measured the
  newest jobs sat on every page (the 100 newest of the US search on pages 1 to 132 of 167), so the newest jobs of a
  search are only known once all its pages are read, and every page is read here.
- The order also shifts while a search is read: Cloudflare caches each page for minutes (the cache key ignores unknown
  parameters; ?page=05 is a miss) and the site's own ranking breaks ties anew on each request (two reads of one page a
  second apart differed in 7 of 20 places), so one read of every page sees about 95% of a search's startups and meets a
  few twice (python-developer: 452 of 463; software-engineer: 1,631 of 1,766). Jobs are kept once here, by their id.
- The 3-a-startup cap hides most jobs of a broad search: north-america listed 15,851 jobs and showed 6,069; every
  continent plus remote, 578 pages, showed 12,518 jobs in 54 s at 8 calls at once (38.7 MB, 72 MB peak). The jobs a
  startup shows are not always its newest (49 of 323 startups capped in the US search had a newer job in San
  Francisco's), so a narrower search (a role, a place) shows more of each startup.
- The site's own role list (/browse/tech-jobs, 518 roles in 13 groups) and place list (/browse/tech-job-locations, 306
  cities, US states and other places) are data/wellfound_roles.tsv and data/wellfound_places.tsv
  (scripts/refresh_wellfound.py, which adds the countries and continents that have a page); a role or place missing
  there is tried by its slug (role/marketing, role/sales and location/kochi exist), and some places have slugs of their
  own (Bengaluru is bangalore, Chennai madras, Canada canada-startups).
- A place's page holds the jobs Wellfound places inside it, any level down: location/india holds Sonipat, HSR Layout and
  Vellore. So a job read from a place's page is given that place, as typed and as Wellfound names it, besides its own
  town names, which carry no country.
- A job page, /jobs/<id>-<slug> (the slug is required: a wrong one is 404), is server-rendered with JobPosting markup:
  datePosted equal to the search's liveStartAt, employment type, every place with region and country, base salary with
  currency and period, the company's website and markets; and in its HTML the remote policy, where it hires remotely,
  preferred time zones, visa sponsorship, relocation, skills, the company's headquarters and its funding. 80 KB, 16 KB
  over the wire, cached. A job taken down still answers 200, with "taken down" and no JobPosting markup.
- No limit was met from this laptop: 1,300 pages in 40 minutes at up to 1,149 a minute, 8 at once (Cloudflare's cache
  answered many of them: other readers keep the popular searches warm). Apify's addresses are untested here.

Pay and equity come from the text Wellfound shows ("$175k – $215k • 0.5% – 1%"). Its markup calls every amount yearly,
even an employer's ₹20,000 – ₹25,000, so salaryPeriod is year as Wellfound states it. liveStartAt is when the job went
live, again when it was reposted ("Reposted: today" on its page).

The engine's shared hooks (0.6.x): place_names() gives the search box every name of Wellfound's place pages, so "software
engineer in San Francisco" or "product manager in Kochi" reads the place, and alone at the end of an entry only the towns
and US states whose page listed 300 jobs or more ("data scientist london"); job_types adds cofounder, Wellfound's fourth
type besides full-time, contract and internship (442 of 12,460 jobs on 2026-11-10; none was part-time or temporary).
"""
import asyncio
import html as htmllib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from importlib import resources
from urllib.parse import unquote, urlparse

from .. import places as regions
from ..http import BoardError, BoardMissing, send
from ..rows import Term, classify_place, fold, index, plain, words
from .base import Board, Platform
from .cornerstone import text_of
from .workable import pay_text

ORIGIN = 'https://wellfound.com'
HOSTS = ('wellfound.com', 'www.wellfound.com', 'angel.co', 'www.angel.co')
EVERYONE = Board('*', 'Wellfound')            # the whole site, every startup at once
LISTED = Board('wellfound.com', 'Wellfound')  # what boards() lists: one job board
SHOWN = 3                     # jobs of one startup a search page shows
CONCURRENCY = 8               # pages read at once
RATE = 12.0                   # pages a second, held
BURST = 8
REFUSAL_WAITS = (5, 20, 60)   # seconds every call waits after Cloudflare refuses the address, try by try
ROTATIONS = 3                 # fresh proxy addresses a run tries after refusals, when a proxy is set
MAX_ROLES = 6                 # roles read for one typed word that is in several; more reads every job instead
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
LD_JSON = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
JOB_PATH = re.compile(r'^(\d{1,10})-([a-z0-9][a-z0-9-]*)$', re.I)
SLUG = re.compile(r'^[a-z0-9][a-z0-9._-]*$')
TAKEN_DOWN = 'recently taken down'
# Every job Wellfound places somewhere, by continent, and every remote job: 578 pages on 2026-11-10
EVERYWHERE = ('location/north-america', 'location/europe', 'location/asia', 'location/africa', 'location/oceania',
              'location/south-america', 'location/middle-east', 'location/central-america', 'remote')
CONTINENTS = {'north-america': 'North America', 'europe': 'Europe', 'asia': 'Asia', 'africa': 'Africa',
              'oceania': 'Oceania', 'south-america': 'South America', 'middle-east': 'Middle East',
              'central-america': 'Central America'}
# The business regions people type, as the pages Wellfound has for them (a continent, a country or a group)
REGION_PAGES = {
    'emea': ('europe', 'africa', 'middle-east', 'united-arab-emirates', 'israel', 'saudi-arabia', 'turkey'),
    'europe middle east africa': ('europe', 'africa', 'middle-east', 'united-arab-emirates', 'israel', 'saudi-arabia', 'turkey'),
    'apac': ('asia', 'oceania'), 'asia pacific': ('asia', 'oceania'), 'apj': ('asia', 'oceania'),
    'latam': ('south-america', 'central-america', 'mexico'), 'latin america': ('south-america', 'central-america', 'mexico'),
    'latin america caribbean': ('south-america', 'central-america', 'mexico'),
    'americas': ('north-america', 'south-america', 'central-america'), 'amer': ('north-america', 'south-america', 'central-america'),
    'eu': ('europe',), 'european union': ('europe',), 'eea': ('europe',), 'european economic area': ('europe',),
    'dach': ('germany', 'austria', 'switzerland'), 'nordics': ('scandinavia', 'finland', 'iceland'),
    'nordic': ('scandinavia', 'finland', 'iceland'), 'nordic countries': ('scandinavia', 'finland', 'iceland'),
    'mena': ('middle-east', 'united-arab-emirates', 'israel', 'saudi-arabia', 'egypt', 'morocco'),
    'gcc': ('united-arab-emirates', 'saudi-arabia', 'qatar', 'kuwait', 'oman', 'bahrain'),
    'gulf region': ('united-arab-emirates', 'saudi-arabia', 'qatar', 'kuwait', 'oman', 'bahrain'),
    'anz': ('australia', 'new-zealand'), 'australia new zealand': ('australia', 'new-zealand'),
    'australasia': ('australia', 'new-zealand'), 'uk i': ('united-kingdom', 'ireland'), 'uki': ('united-kingdom', 'ireland'),
    'uk ireland': ('united-kingdom', 'ireland'), 'benelux': ('belgium', 'netherlands', 'luxembourg'),
    'iberia': ('spain', 'portugal'), 'baltics': ('estonia', 'latvia', 'lithuania'),
}
JOB_TYPES = {'full-time': 'full-time', 'part-time': 'part-time', 'contract': 'contract', 'internship': 'intern',
             'cofounder': 'cofounder'}
SIZES = {'SIZE_1_10': '1-10', 'SIZE_11_50': '11-50', 'SIZE_51_200': '51-200', 'SIZE_201_500': '201-500',
         'SIZE_501_1000': '501-1000', 'SIZE_1001_5000': '1001-5000', 'SIZE_5000_PLUS': '5000+'}
POLICY = {'REMOTE': 'Remote only', 'ONSITE_OR_REMOTE': 'Onsite or remote', 'ONSITE': 'In office'}
# The currency of a pay amount by its sign when no code follows it ("$90k – $120k CAD"); a sign several currencies share
# (¥, ₨, kr) gives none here, and the job page's markup names it (details)
SIGNS = {'$': 'USD', '₹': 'INR', '£': 'GBP', '€': 'EUR', 'د.إ': 'AED', 'ر.س': 'SAR', 'ر.ق': 'QAR', 'د.ك': 'KWD',
         '₱': 'PHP', '₦': 'NGN', 'R$': 'BRL', 'R': 'ZAR', '₩': 'KRW', '₺': 'TRY', '₪': 'ILS', 'zł': 'PLN', 'Kč': 'CZK',
         'CHF': 'CHF', 'S$': 'SGD', 'A$': 'AUD', 'C$': 'CAD', 'HK$': 'HKD', 'NZ$': 'NZD', 'Rp': 'IDR', '฿': 'THB',
         '₫': 'VND', 'RM': 'MYR', 'KSh': 'KES', '₴': 'UAH', '₸': 'KZT', '৳': 'BDT', 'E£': 'EGP', 'ج.م': 'EGP'}
UNITS = {'': 1, 'k': 1_000, 'K': 1_000, 'm': 1_000_000, 'M': 1_000_000, 'L': 100_000, 'cr': 10_000_000, 'Cr': 10_000_000}
AMOUNT = re.compile(r'(?P<n>\d[\d,]*(?:\.\d+)?)\s?(?P<unit>k|K|m|M|L|cr|Cr)?(?![A-Za-z])')
EQUITY = re.compile(r'(\d+(?:\.\d+)?)\s?%(?:\s*[–-]\s*(\d+(?:\.\d+)?)\s?%)?')
# Names people type for a place Wellfound lists under another name
ALIASES = {'nyc': 'new york city', 'new york ny': 'new york city', 'sf': 'san francisco', 'bay area': 'san francisco bay area',
           'sf bay area': 'san francisco bay area', 'silicon valley': 'san francisco bay area', 'la': 'los angeles',
           'dc': 'washington dc', 'washington d c': 'washington dc', 'gurugram': 'gurgaon', 'bombay': 'mumbai'}


@dataclass
class Query:
    """One search of the site: its path, the places its jobs are given, and what its first page said."""
    path: str
    tag: list = field(default_factory=list)
    first: dict | None = None     # its first page (Apollo cache), read to plan the run
    total: int = 0                # jobs Wellfound lists for it
    pages: int = 0


class Refused(BoardError):
    """Cloudflare refused this address on every try."""


def iso(seconds) -> str | None:
    """1793665422 -> '2026-11-03T00:23:42Z'."""
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def slugify(text: str) -> str:
    """'San Francisco Bay Area' -> 'san-francisco-bay-area', as Wellfound writes its slugs."""
    return '-'.join(words(text))


def squeeze(text: str | None) -> str:
    """Lower case, accents dropped, words only, s plurals folded: the key the role and place lists are read by."""
    return ' '.join(fold(w) for w in words(text))


def unique(values) -> list[str]:
    return list(dict.fromkeys(' '.join(str(v).split()) for v in values or [] if isinstance(v, str) and v.strip()))


def bare(name: str) -> str:
    """'UI Designer (User Interface)' -> 'UI Designer'."""
    return ' '.join(re.sub(r'\([^)]*\)', ' ', name).split())


# ---------- the site's lists ----------

def _rows(name: str) -> tuple[list[list[str]], str | None]:
    text = resources.files('ats_jobs').joinpath(f'data/{name}').read_text(encoding='utf-8')
    rows, updated = [], None
    for line in text.splitlines():
        if line.startswith('#'):
            if 'updated' in line and updated is None:
                updated = line.split('updated', 1)[1].strip().split(' ')[0].strip(',:.')
            continue
        if line.strip():
            rows.append(line.split('\t'))
    return rows, updated


@dataclass(frozen=True)
class Role:
    slug: str
    name: str


@lru_cache(maxsize=1)
def role_list() -> tuple[tuple[Role, ...], dict[str, Role]]:
    """data/wellfound_roles.tsv -> (every role, its names and slug as squeezed words -> the role). "UI Designer (User
    Interface)" is found as ui designer and as the whole name; a bracketed short name (CTO, SDET) finds it too."""
    roles, by_name = [], {}
    for cols in _rows('wellfound_roles.tsv')[0]:
        role = Role(cols[0], cols[1] if len(cols) > 1 else cols[0])
        roles.append(role)
        for name in (bare(role.name), role.name, role.slug.replace('-', ' ')):
            for key in (squeeze(name), ''.join(squeeze(name).split())):
                if key:
                    by_name.setdefault(key, role)
        for short in re.findall(r'\(([A-Za-z]{2,6})\)', role.name):
            by_name.setdefault(squeeze(short), role)
    return tuple(roles), by_name


@dataclass(frozen=True)
class Place:
    slug: str
    name: str
    country: str | None = None     # a country's page: its name, which the engine's matching reads as the country
    kind: str = ''                 # city, state, region or country
    jobs: int = 0                  # jobs its page listed when the list was made


@lru_cache(maxsize=1)
def place_list() -> tuple[dict[str, Place], str | None]:
    """data/wellfound_places.tsv -> (squeezed name or slug -> the place, the date of the list). Lines: slug, name, state
    code, kind (city, state, region, country)."""
    rows, updated = _rows('wellfound_places.tsv')
    found: dict[str, Place] = {}
    for cols in rows:
        slug, name = cols[0], cols[1] if len(cols) > 1 else cols[0]
        kind = cols[3] if len(cols) > 3 else ''
        jobs = int(cols[4]) if len(cols) > 4 and cols[4].strip().isdigit() else 0
        place = Place(slug, name, name if kind == 'country' else None, kind, jobs)
        for key in (squeeze(name), squeeze(slug.replace('-', ' '))):
            if key:
                found.setdefault(key, place)
        for other in regions.OTHER_NAMES.get(' '.join(words(name)), ()):     # Bangalore for Bengaluru
            found.setdefault(squeeze(other), place)
    return found, updated


BARE_JOBS = 300       # a town or US state with this many jobs is read alone at the end of a search entry (place_names)
BARE_SHORT = ('nyc', 'sf', 'bay area', 'sf bay area', 'silicon valley')   # short names of the biggest, read alone too


@lru_cache(maxsize=1)
def place_names() -> tuple[frozenset[str], frozenset[str]]:
    """(every name of Wellfound's place pages and the names people type for them, in places.key form with and without
    accents; the towns and US states whose page listed BARE_JOBS jobs or more, with the short names of the biggest), for
    inputs.read_place_words: after in, at or near, any of them ("software engineer in Kochi"); alone at the end of a search
    entry, only the big ones ("data scientist london"), since a small town's name can be a word of a job title. Countries
    and regions the engine reads by itself."""
    every, bare = set(), set()
    for place in set(place_list()[0].values()):
        names = [place.name, *regions.OTHER_NAMES.get(' '.join(words(place.name)), ())]    # names, not slugs (madrid-colombia)
        keys = {k for n in names for k in (regions.key(n), regions.key(plain(n)))} - {''}
        every |= keys
        if place.kind in ('city', 'state') and place.jobs >= BARE_JOBS:
            bare |= keys
    every |= {regions.key(n) for pair in ALIASES.items() for n in pair}
    bare |= set(BARE_SHORT)
    return frozenset(every), frozenset(bare)


# ---------- one job ----------

def amount(text: str) -> tuple[float | None, str | None]:
    """'$175k' -> (175000, '$'); '₹1.2L' -> (120000, '₹'); 'د.إ120k' -> (120000, 'د.إ'): the sign is what comes before
    the number."""
    m = AMOUNT.search(text)
    if not m:
        return None, None
    return float(m.group('n').replace(',', '')) * UNITS[m.group('unit') or ''], text[:m.start()].strip() or None


def compensation(text: str | None) -> dict:
    """Wellfound's pay and equity text -> the salary and equity fields: '$175k – $215k • 0.5% – 1%', '₹6L – ₹9L • No
    equity', '$90k – $120k CAD', 'Up to $90k', '0.1% – 0.8%', 'No equity'."""
    out = {'salary': None, 'salaryMin': None, 'salaryMax': None, 'salaryCurrency': None, 'salaryPeriod': None,
           'offersEquity': None, 'equityMin': None, 'equityMax': None}
    for part in (p.strip() for p in (text or '').split('•')):
        if not part:
            continue
        if re.fullmatch(r'no equity', part, re.I):
            out['offersEquity'] = False
            continue
        if '%' in part:
            m = EQUITY.search(part)
            if m:
                low = float(m.group(1))
                out.update(offersEquity=True, equityMin=low, equityMax=float(m.group(2)) if m.group(2) else low)
            continue
        code = re.search(r'\b([A-Z]{3})\s*$', part)
        rest = re.sub(r'\b[A-Z]{3}\s*$', '', part).strip()
        upto = re.match(r'^up to\s+', rest, re.I)
        sides = [rest[upto.end():]] if upto else re.split(r'\s*[–-]\s*(?=\D?\D?\D?\s?\d)', rest)
        found = [amount(s) for s in sides if s.strip()]
        numbers = [v for v, _ in found if v]
        if not numbers:
            continue
        sign = next((s for _, s in found if s), None)
        currency = code.group(1) if code else SIGNS.get(sign or '')
        low, high = (None, numbers[0]) if upto else (numbers[0], numbers[-1])
        out.update(salaryMin=low, salaryMax=high, salaryCurrency=currency, salaryPeriod='year',
                   salary=pay_text(low, high, currency or sign, 'year'))
    return out


STAGES = ('early', 'growth', 'scale', 'public')      # Wellfound's company stage badges, measured 2026-11-10
CODES = {'usd', 'inr', 'eur', 'gbp', 'cad', 'aud', 'sgd', 'aed', 'chf', 'jpy', 'brl', 'mxn', 'pln', 'sek', 'nok', 'dkk',
         'ils', 'hkd', 'nzd', 'zar', 'ngn', 'php', 'idr', 'myr', 'thb', 'vnd', 'krw', 'try', 'czk', 'sar', 'qar', 'kes',
         'pkr', 'egp', 'cny', 'twd', 'uah', 'ron', 'huf', 'bdt', 'lkr', 'npr', 'cop', 'clp', 'ars', 'pen'}


def read_pay(value, note) -> tuple[float, str | None] | None:
    """minSalary as people type it -> (yearly amount, currency): 120000, '120k', '$120k', '20L INR', '60000 EUR', '€60k'.
    A number with no currency is US dollars, the currency of most pay on Wellfound, with a note: compared with jobs in
    every currency, ₹1.2L (about $1,400) would pass 120k."""
    if value is None or value == '' or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return (float(value), None) if value > 0 else None
    text = str(value).strip()
    code = next((w.upper() for w in re.findall(r'[A-Za-z]{3}', text) if w.lower() in CODES), None)
    number, sign = amount(re.sub(r'\b[A-Za-z]{3}\b', ' ', text) if code else text)
    if not number or number <= 0:
        note(f'Could not read minSalary "{value}" as a yearly amount such as 120000 USD, 120k or 20L INR, so pay was '
             f'not filtered.')
        return None
    currency = code or SIGNS.get(sign or '')
    if not currency:
        note(f'Read minSalary "{value}" as US dollars a year; type a currency after it for another, such as 20L INR.')
    return number, currency or 'USD'


def read_stages(value, note) -> list[str]:
    """companyStage -> Wellfound's stages asked for: 'Early Stage', 'seed' (early), 'growth', 'late' (scale), 'public'."""
    items = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    words_of = [w for item in items if isinstance(item, str) for w in re.split(r'[,;\n]+', item) if w.strip()]
    aliases = {'seed': 'early', 'startup': 'early', 'late': 'scale', 'scaleup': 'scale', 'ipo': 'public', 'listed': 'public'}
    out, unknown = [], []
    for raw in words_of:
        word = raw.strip().lower().replace(' stage', '').replace('-stage', '').strip()
        stage = word if word in STAGES else aliases.get(word)
        if stage and stage not in out:
            out.append(stage)
        elif not stage:
            unknown.append(raw.strip())
    if unknown:
        note(f'companyStage has no option {", ".join(unknown)}: its options are {", ".join(STAGES)}.')
    return [] if len(out) == len(STAGES) else out


def work_type(remote: bool, kind: str | None, flexible: bool | None) -> str:
    """In office with work-from-home flexibility is hybrid; a job Wellfound marks remote (remote only, onsite or remote,
    or remote with no policy) is remote; anything else is onsite."""
    if kind == 'ONSITE' and flexible:
        return 'hybrid'
    return 'remote' if remote else 'onsite'


def policy(kind: str | None, flexible: bool | None) -> str | None:
    """Wellfound's words for its remote setting: Remote only, Onsite or remote, In office - WFH flexibility, In office."""
    text = POLICY.get(kind or '')
    return f'{text} - WFH flexibility' if text and kind == 'ONSITE' and flexible else text


def apollo(page: str) -> dict:
    """A search page -> its Apollo cache (apolloState.data)."""
    m = NEXT_DATA.search(page)
    if not m:
        raise BoardError('the page held no search data')
    try:
        return json.loads(m.group(1))['props']['pageProps']['apolloState']['data']
    except (ValueError, KeyError, TypeError) as exc:
        raise BoardError('the search data had another shape') from exc


def deref(data: dict, value):
    return data.get(value['__ref']) if isinstance(value, dict) and '__ref' in value else value


def results(data: dict) -> dict:
    """The search answer of a page: totals, pages and its startups."""
    talent = (data.get('ROOT_QUERY') or {}).get('talent') or {}
    return next((v for k, v in talent.items() if k.startswith('seoLandingPageJobSearchResults') and isinstance(v, dict)),
                {})


def page_place(data: dict) -> str | None:
    """The place a place's page is for, as Wellfound names it (its NewTag)."""
    return next((v.get('displayName') for k, v in data.items() if k.startswith('NewTag:') and v.get('displayName')), None)


# ---------- the platform ----------

class Wellfound(Platform):
    key = 'wellfound'
    name = 'Wellfound'
    site_word = 'job board'
    actor = 'wellfound-jobs-search'
    board_example = 'https://wellfound.com/company/calm'
    job_example = 'https://wellfound.com/jobs/4965854-software-engineer'
    concurrency = CONCURRENCY
    server_filters = True        # a typed place goes to Wellfound's own place pages
    job_types = ('cofounder',)          # Wellfound's own type besides the engine's five: 442 of 12,460 jobs (2026-11-10)
    job_type_aliases = {'cofounder': 'cofounder', 'co-founder': 'cofounder', 'co founder': 'cofounder',
                        'founder': 'cofounder', 'founding partner': 'cofounder'}
    filter_words = {'minSalary': ('pay', 'type a lower minSalary, another currency, or clear it'),
                    'companyStage': ('company stage', 'pick more stages in companyStage or clear it')}
    never = ('department', 'team', 'expiresAt', 'reference')
    extra = ('salaryMin', 'salaryMax', 'salaryCurrency', 'salaryPeriod', 'offersEquity', 'equityMin', 'equityMax',
             'experienceMin', 'experienceMax', 'remoteIn', 'remotePolicy', 'role', 'companySize', 'companyStage',
             'companyTagline', 'companyLogo', 'companyBadges', 'visaSponsorship', 'relocation', 'timezones', 'skills',
             'industries', 'companyLocation', 'companyFunding', 'fundingRounds', 'latestRound', 'latestRoundDate',
             'reposted')
    DETAILS_ONLY = ('visaSponsorship', 'relocation', 'timezones', 'skills', 'industries', 'companyLocation',
                    'companyFunding', 'fundingRounds', 'latestRound', 'latestRoundDate', 'reposted')

    def __init__(self):
        self.cfg = None
        self.min_pay: tuple[float, str | None] | None = None    # minSalary: (yearly amount, currency or None)
        self.stages: list[str] = []                               # companyStage: early, growth, scale, public
        self.queries: list[Query] = []          # the run's searches, for its notes and settings
        self.firsts: dict[str, dict | None] = {}   # path -> its first page, read once (None: no such search)
        self.seen: set[str] = set()             # job ids already handed to the engine
        self.told: set[str] = set()
        self.pace = Pace()
        self._read = None                       # every job of the run's searches, once a company had to be found in them
        self._read_lock = asyncio.Lock()
        self._plan_lock = asyncio.Lock()
        self._planned: list[Query] | None = None
        self._proxy_input = None                # the run's proxyConfiguration, when it names a proxy
        self._proxy = None
        self._via = None                        # the client on the proxy address in use
        self._rotations = 0
        self._pages_read = 0

    # ---------- links ----------

    def boards(self):
        return [LISTED], place_list()[1]

    def read_link(self, text):
        """A Wellfound link -> (company slug, job key). A job page gives '<id>-<slug>', the key its page needs; a company
        page names that company; a search page (role, location, remote) is read for its words and places."""
        link = text.strip().rstrip('.,;:!?')
        link = link if re.match(r'^[a-z][a-z0-9+.-]*://', link, re.I) else f'https://{link}'
        parsed = urlparse(link)
        host = (parsed.hostname or '').lower() if ' ' not in text.strip() else ''
        if host not in HOSTS:
            return None
        parts = [unquote(p) for p in parsed.path.split('/') if p]
        if 'jobs' in parts:
            at = parts.index('jobs')
            found = JOB_PATH.match(parts[at + 1]) if at + 1 < len(parts) else None
            if found:
                return EVERYONE.key, f'{found.group(1)}-{found.group(2).lower()}'
        if len(parts) >= 2 and parts[0].lower() == 'company' and SLUG.match(parts[1].lower()):
            return parts[1].lower(), None
        return None, None

    def search_link(self, text):
        """/role/<role>, /role/l/<role>/<place>, /role/r/<role>, /location/<place>, /remote -> (words, places)."""
        link = text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}'
        parts = [unquote(p).lower() for p in urlparse(link).path.split('/') if p]
        roles = {r.slug: bare(r.name) for r in role_list()[0]}
        listed = {p.slug: p.name for p in place_list()[0].values()}

        def role(slug):
            return roles.get(slug) or slug.replace('-', ' ')

        def where(slug):
            return listed.get(slug) or slug.replace('-', ' ').title()
        if len(parts) == 2 and parts[0] == 'role':
            return [role(parts[1])], []
        if len(parts) == 4 and parts[:2] == ['role', 'l']:
            return [role(parts[2])], [where(parts[3])]
        if len(parts) == 3 and parts[:2] == ['role', 'r']:
            return [role(parts[2])], ['Remote']
        if len(parts) == 2 and parts[0] == 'location':
            return [], [where(parts[1])]
        if parts == ['remote']:
            return [], ['Remote']
        return [], []

    def guess_board(self, name):
        return ' '.join(name.split()) or None

    def place_names(self):
        return place_names()

    # ---------- the run's settings ----------

    def read_input(self, raw, cfg):
        self.cfg = cfg
        proxy = raw.get('proxyConfiguration')
        if isinstance(proxy, dict) and (proxy.get('useApifyProxy') or proxy.get('proxyUrls')):
            self._proxy_input = proxy        # kept out of the settings: proxy addresses can hold a password
        absent = [t for t in cfg.job_types if t in ('part-time', 'temporary')]
        if absent:          # none of 12,460 jobs read on 2026-11-10 had either type
            cfg.job_types = [t for t in cfg.job_types if t not in absent]
            self.note(f'Wellfound has no {" or ".join(absent)} job type, so it was left out: its types are full-time, '
                      f'contract, intern and cofounder. Type {absent[0]} in Job titles to find (searchTerms) to find it in '
                      f'titles.')
        self.min_pay = read_pay(raw.get('minSalary'), self.note)
        self.stages = read_stages(raw.get('companyStage'), self.note)

    def fits(self, job):
        """Wellfound's own filters, on every job whose words match: the top of its yearly pay reaches minSalary in that
        currency (a job that shows no pay, or pay in another currency, does not), and its company is at a stage asked
        for (a company with no stage badge is not)."""
        if self.min_pay:
            low, currency = self.min_pay
            top = job.get('salaryMax') or job.get('salaryMin')
            if not top or top < low or job.get('salaryCurrency') != currency:
                return 'minSalary'
        if self.stages and (job.get('companyStage') or '').split(' ')[0].lower() not in self.stages:
            return 'companyStage'
        return True

    def settings(self):
        return {'minSalary': {'amount': self.min_pay[0], 'currency': self.min_pay[1]} if self.min_pay else None,
                'companyStage': self.stages or None,
                'wellfoundSearches': [{'page': f'{ORIGIN}/{q.path}', 'jobsListed': q.total, 'pages': q.pages}
                                      for q in self.queries] or None,
                'proxy': True if self._proxy_input else None}

    def note(self, text: str, key: str | None = None) -> None:
        """A note for the run's message and OUTPUT, once."""
        if self.cfg is not None and (key or text) not in self.told:
            self.told.add(key or text)
            self.cfg.notes.append(text)

    def search_all(self, boards, cfg):
        self.cfg = cfg
        return [EVERYONE]

    # ---------- calls ----------

    async def route(self, http):
        """The client a call goes through: the run's own, or one through the proxy the user set (proxyConfiguration)."""
        if not self._proxy_input:
            return http
        if self._via is None:
            if self._proxy is None:
                from apify import Actor
                try:
                    self._proxy = await Actor.create_proxy_configuration(actor_proxy_input=self._proxy_input)
                except Exception as exc:  # noqa: BLE001  a proxy the platform cannot use (a typo, socks5://)
                    why = (str(exc).splitlines() or [type(exc).__name__])[0][:160]
                    self.note(f'Connection (proxyConfiguration) could not be used ({why}), so the run used its own '
                              f'address.', key='proxy')
                if self._proxy is None:
                    self._proxy_input = None
                    return http
            import httpx
            url = await self._proxy.new_url(session_id=f'wellfound_{self._rotations}')
            self._via = httpx.AsyncClient(proxy=url, timeout=httpx.Timeout(30.0, connect=15.0),
                                          headers={'User-Agent': http.headers.get('User-Agent', ''),
                                                   'Accept-Encoding': 'gzip'})
        return self._via

    async def get(self, http, path: str, follow: bool = False):
        """One page in its turn; a search page without following redirects (303 is the end of a search). Cloudflare's
        refusal makes every call wait, slows the pace and tries again; through a proxy it moves the run to a fresh
        address. A refusal is 403, or 429 with a challenge, which send() retries 3 times on its own first: on
        2026-11-10 this laptop met 429 on every page after about 1,500 pages in 15 minutes (two whole-site reads 6
        minutes apart)."""
        tries = 0
        while True:
            via = await self.route(http)
            await self.pace.turn()
            try:
                response = await send(via, 'GET', f'{ORIGIN}/{path.lstrip("/")}', follow_redirects=follow,
                                      headers={'Accept': 'text/html,application/xhtml+xml'})
                status, mitigated = response.status_code, response.headers.get('cf-mitigated')
            except BoardError as exc:
                if str(exc) != 'HTTP 429':
                    raise
                status, mitigated = 429, None
            if status not in (403, 429):
                return response
            self.pace.slow()
            if self._proxy_input and self._rotations < ROTATIONS:
                if via is self._via:
                    self._rotations += 1
                    self._via = None
                continue
            if tries < len(REFUSAL_WAITS):
                self.pace.hold(REFUSAL_WAITS[tries])
                tries += 1
                continue
            why = f'HTTP {status}' + (' with a challenge' if mitigated else '')
            later = ('the proxy\'s addresses were refused too' if self._proxy_input else
                     'run again in 10 minutes, or turn on Apify Proxy in Connection (proxyConfiguration)')
            where = (f'after {self._pages_read:,} pages, so the run saved what it had read' if self._pages_read else
                     'before any page was read')
            self.note(f'Wellfound\'s Cloudflare check refused this address ({why}) {where}: {later}.', key='refused')
            raise Refused(f'Wellfound refused this address ({why})')

    async def page(self, http, path: str, number: int) -> dict | None:
        """One page of a search (its Apollo cache); None when Wellfound has no such search (404) or the page is past
        its end (303 to the first page)."""
        if number == 1 and path in self.firsts:
            return self.firsts[path]
        response = await self.get(http, path + (f'?page={number}' if number > 1 else ''))
        if response.status_code in (301, 302, 303, 307, 308, 404, 410):
            data = None
        elif response.status_code != 200:
            raise BoardError(f'HTTP {response.status_code} on wellfound.com/{path}')
        else:
            self._pages_read += 1
            data = apollo(response.text)
        if number == 1:
            self.firsts[path] = data
        return data

    async def exists(self, http, path: str) -> bool:
        return await self.page(http, path, 1) is not None

    # ---------- planning ----------

    def roles_for(self, term: str) -> tuple[list[Role], str]:
        """A typed job title -> the listed roles to read and how it was read: 'exact' (a role of that name), 'within'
        (the most specific roles inside its words: senior python developer reads Python Developer), else 'around' with
        the roles that hold every word (python: Python Developer), which plan() reads only when the word has no page of
        its own (marketing has one, outside the list)."""
        roles, by_name = role_list()
        key = squeeze(term)
        exact = by_name.get(key) or by_name.get(''.join(key.split()))
        if exact:
            return [exact], 'exact'
        inside = index(term)
        within = [r for r in roles if Term(bare(r.name)).in_title(inside)]
        if within:
            most = max(len(words(bare(r.name))) for r in within)
            return [r for r in within if len(words(bare(r.name))) == most], 'within'
        wanted = Term(term)
        return sorted((r for r in roles if wanted.in_title(index(bare(r.name)))),
                      key=lambda r: (len(words(bare(r.name))), r.name)), 'around'

    async def place_pages(self, http, text: str) -> list[tuple[str, list]] | None:
        """A typed place -> [(the slug of a Wellfound place page that holds it, the places its jobs are given)], [] when
        Wellfound has none, None when every job must be read and matched by the place's name."""
        typed = {'name': text}
        listed = place_list()[0]
        key = ALIASES.get(squeeze(text), squeeze(text))
        if key in listed:
            place = listed[key]
            if squeeze(place.name) != squeeze(text):
                self.note(f'Read "{text}" in location as {place.name}, as Wellfound names it.')
            return [(place.slug, [typed, {'name': place.name, 'country': place.country}])]
        kind, codes = classify_place(text)
        if kind == 'region':
            group = REGION_PAGES.get(' '.join(words(text)).replace('&', ' ')) or REGION_PAGES.get(regions.key(text))
            slugs = list(group) if group else [s for s, name in CONTINENTS.items() if regions.REGIONS.get(regions.key(name), frozenset()) & codes]
            pages = []
            for slug in slugs:
                if await self.exists(http, f'location/{slug}'):
                    shown = CONTINENTS.get(slug) or next((p.name for p in listed.values() if p.slug == slug),
                                                         slug.replace('-', ' ').title())
                    pages.append((slug, [typed, {'name': shown}]))
            if pages:
                self.note(f'Read "{text}" in location as Wellfound\'s place pages for '
                          f'{", ".join(tagged["name"] for _, (_, tagged) in pages)}.')
                return pages
            self.note(f'Wellfound has no page for {text}, so every job was read and matched by place.')
            return None
        if kind == 'country':
            name = country_name(next(iter(codes)))
            place = listed.get(squeeze(name))
            if place:
                return [(place.slug, [typed, {'name': place.name, 'country': place.name}])]
            for slug in dict.fromkeys(s for s in (slugify(name), slugify(text)) if s):
                if await self.exists(http, f'location/{slug}'):
                    return [(slug, [typed, {'name': name, 'country': name}])]
            self.note(f'Wellfound has no page for {text}, so every job was read and matched by place.')
            return None
        slug = slugify(text)
        if slug and await self.exists(http, f'location/{slug}'):
            return [(slug, [typed])]
        from .naukri import state_of        # an Indian town Wellfound has no page for: its state's page
        state = state_of(text)
        if state and await self.exists(http, f'location/{slugify(state)}'):
            self.note(f'Wellfound has no page for {text}, so the jobs of {state} were read and matched by place.')
            return [(slugify(state), [])]
        self.note(f'Wellfound has no page for {text}, so every job was read and matched by place.')
        return None

    async def plan(self, http) -> list[Query]:
        """The run's searches, planned once: each typed title's roles at each typed place (or alone), the remote searches
        when only remote jobs are wanted, every job when a title names no role or nothing is typed."""
        async with self._plan_lock:
            if self._planned is None:
                self._planned = await self._plan(http)
        return self._planned

    async def _plan(self, http) -> list[Query]:
        cfg = self.cfg
        roles: list[Role] = []
        unmatched: list[str] = []            # typed titles no role fits: matched by title in whatever is read
        everything = not cfg.search_terms
        for term in cfg.search_terms:
            found, how = self.roles_for(term)
            if how == 'around':
                slug = slugify(term)
                if slug and await self.exists(http, f'role/{slug}'):
                    roles.append(Role(slug, term))
                    self.note(f'Read "{term}" through Wellfound\'s role page wellfound.com/role/{slug}.')
                    continue
                if not found or len(found) > MAX_ROLES:
                    everything = True
                    unmatched.append(f'"{term}"' + (f' (it is in {len(found)} of its roles)' if found else ''))
                    continue
            roles += [r for r in found if r not in roles]
            if how != 'exact':
                self.note(f'Read "{term}" through Wellfound\'s role{"s" if len(found) > 1 else ""} '
                          f'{", ".join(bare(r.name) for r in found)}; jobs are kept by their titles.')
        role_slugs = [] if everything else list(dict.fromkeys(r.slug for r in roles))
        if unmatched:
            read = ('every remote job on Wellfound' if cfg.work_types == ['remote'] else
                    'the jobs of the places you typed' if cfg.locations else 'every job on Wellfound')
            self.note(f'Wellfound has no role for {", ".join(unmatched)}, so {read} '
                      f'{"was" if read.startswith("every") else "were"} read and matched by title.')
        paths: list[tuple[str, list]] = []
        if cfg.work_types == ['remote']:         # remote jobs only, anywhere they hire: the remote searches
            paths = [(f'role/r/{s}', []) for s in role_slugs] or [('remote', [])]
        elif cfg.locations:
            for place in cfg.locations:
                if regions.world_query(regions.tokens(place)):    # open to anyone anywhere: only remote jobs can be
                    paths += [(f'role/r/{s}', []) for s in role_slugs] or [('remote', [])]
                    continue
                found = await self.place_pages(http, place)
                if found is None:
                    paths += [(p, []) for p in EVERYWHERE]
                    continue
                for slug, tag in found:
                    paths += ([(f'role/l/{r}/{slug}', tag) for r in role_slugs] if role_slugs
                              else [(f'location/{slug}', tag)])
        elif role_slugs:
            paths = [(f'role/{r}', []) for r in role_slugs]
        else:
            paths = [(p, []) for p in EVERYWHERE]
        queries: dict[str, Query] = {}
        for path, tag in paths:
            query = queries.setdefault(path, Query(path))
            query.tag += [t for t in tag if t not in query.tag]
        await asyncio.gather(*(self.page(http, path, 1) for path in queries))
        out = []
        for path, query in queries.items():
            query.first = self.firsts.get(path)
            if query.first is None:
                self.note(f'Wellfound has no search page wellfound.com/{path}, so it was skipped.')
                continue
            found = results(query.first)
            query.total, query.pages = int(found.get('totalJobCount') or 0), int(found.get('pageCount') or 1)
            out.append(query)
        self.queries = out
        return out

    # ---------- reading ----------

    async def read_all(self, http, queries: list[Query]):
        """Every page of every search, 8 at once, as they come; the first pages are in hand from planning."""
        out: asyncio.Queue = asyncio.Queue()
        sem = asyncio.Semaphore(CONCURRENCY)

        async def one(query: Query, number: int) -> None:
            try:
                async with sem:
                    data = await self.page(http, query.path, number)
                await out.put((query, data))
            except Exception as exc:  # noqa: BLE001  handed to the reader, which raises it
                await out.put((query, exc))

        for query in queries:
            out.put_nowait((query, query.first))
        tasks = [asyncio.ensure_future(one(q, n)) for q in queries for n in range(2, q.pages + 1)]
        waiting, failed, first_error = len(queries) + len(tasks), 0, None
        try:
            while waiting:
                query, data = await out.get()
                waiting -= 1
                if isinstance(data, Refused):
                    self.incomplete = True
                    return
                if isinstance(data, Exception):          # one page lost after its retries: the others are read
                    failed, first_error = failed + 1, first_error or data
                    continue
                if data is not None:
                    yield query, data
        finally:
            for task in tasks:
                task.cancel()
        if failed:                  # a search with no first page failed at planning, before anything was saved
            self.incomplete = True
            self.note(f'{failed:,} of Wellfound\'s search pages could not be read ({first_error}), so their jobs are '
                      f'missing.', key='failed pages')

    def jobs_of(self, query: Query, data: dict) -> list[dict]:
        """The jobs of one page not handed over before, each given its search's places."""
        jobs = []
        for ref in results(data).get('startups') or []:
            startup = deref(data, ref) or {}
            for listing in startup.get('highlightedJobListings') or []:
                record = deref(data, listing) or {}
                if not record.get('id') or str(record['id']) in self.seen:
                    continue
                self.seen.add(str(record['id']))
                jobs.append(self.job(record, startup, data, query.tag))
        return jobs

    async def pages(self, http, session, term, days):
        if session.get('company'):
            name = session['company']
            jobs = [j for j in await self.read_once(http) if self.of_company(j, name)]
            if not jobs:
                raise BoardMissing(name)
            yield jobs
            return
        async for query, data in self.read_all(http, await self.plan(http)):
            jobs = self.jobs_of(query, data)
            if jobs:                # an empty page ends a board for the engine: one whose jobs all came before is skipped
                yield jobs
        self.cap_note()

    async def read_once(self, http) -> list[dict]:
        """Every job of the run's searches, read once and shared by the companies that are found by name in them."""
        async with self._read_lock:
            if self._read is None:
                jobs = []
                async for query, data in self.read_all(http, await self.plan(http)):
                    jobs += self.jobs_of(query, data)
                self._read = jobs
                self.cap_note()
        return self._read

    @staticmethod
    def of_company(job: dict, name: str) -> bool:
        return (name.lower() == (job.get('companySlug') or '').lower()
                or Term(name).in_title(index(job.get('company'))))

    def cap_note(self) -> None:
        listed, shown = sum(q.total for q in self.queries), len(self.seen)
        if listed <= shown:
            return
        if len(self.queries) == 1:
            self.note(f'Wellfound lists {listed:,} jobs for this search and shows visitors at most {SHOWN} of each '
                      f'startup: the run read the {shown:,} it shows.', key='cap')
        else:
            self.note(f'Wellfound\'s {len(self.queries)} searches list {listed:,} jobs, a job in two of them counted twice, '
                      f'and show visitors at most {SHOWN} of each startup in each: the run read the {shown:,} different '
                      f'jobs they show.', key='cap')

    def job(self, record: dict, startup: dict, data: dict, tag: list) -> dict:
        names = unique(record.get('locationNames'))
        remote = bool(record.get('remote'))
        config = deref(data, record.get('remoteConfig')) or {}
        kind, flexible = config.get('kind'), config.get('wfhFlexible')
        accepted = unique(record.get('acceptedRemoteLocationNames'))
        remote_in = (accepted or ['Everywhere']) if remote else []     # a remote job naming no place hires everywhere
        badges = [deref(data, b) or {} for b in startup.get('badges') or []]
        jid, slug = str(record['id']), record.get('slug') or slugify(record.get('title') or '')
        link = f'{ORIGIN}/jobs/{jid}-{slug}'
        low, high = record.get('yearsExperienceMin'), record.get('yearsExperienceMax')
        return {
            'id': jid,
            'company': ' '.join((startup.get('name') or '').split()) or None,
            'companySlug': startup.get('slug'),
            'companyWebsite': None,
            'title': ' '.join((record.get('title') or '').split()),
            'location': names[0] if names else ('Remote' if remote else None),
            'otherLocations': names[1:],
            'places': [{'name': n} for n in names + remote_in] + [dict(t) for t in tag if t.get('name')],
            'workType': work_type(remote, kind, flexible),
            'employmentType': JOB_TYPES.get(record.get('jobType'), record.get('jobType')),
            **compensation(record.get('compensation')),
            'postedAt': iso(record.get('liveStartAt')),
            'jobUrl': link,
            'applyUrl': link,
            'experienceMin': low if isinstance(low, (int, float)) and not isinstance(low, bool) else None,
            'experienceMax': high if isinstance(high, (int, float)) and not isinstance(high, bool) else None,
            'description': record.get('description') or None,
            'remoteIn': remote_in,
            'remotePolicy': policy(kind, flexible),
            'role': record.get('primaryRoleTitle') or None,
            'companySize': SIZES.get(startup.get('companySize')),
            'companyStage': next((b.get('label') for b in badges if b.get('name') == 'COMPANY_STAGE_BADGE'), None),
            'companyTagline': ' '.join((startup.get('highConcept') or '').split()) or None,
            'companyLogo': startup.get('logoUrl') or None,
            'companyBadges': [b.get('label') for b in badges if b.get('label') and b.get('name') != 'COMPANY_STAGE_BADGE'],
            **dict.fromkeys(self.DETAILS_ONLY),
        }

    # ---------- the job page ----------

    async def job_page(self, http, key: str) -> tuple[str, str | None]:
        """('open' | 'closed' | 'not found', the page). A job taken down answers 410 (or 200) with its page saying so
        and no JobPosting markup; a job that never was, or a wrong slug, 404."""
        response = await self.get(http, f'jobs/{key}', follow=True)
        if response.status_code == 404:
            return 'not found', None
        if response.status_code not in (200, 410):
            raise BoardError(f'HTTP {response.status_code}')
        page = response.text
        return ('closed' if response.status_code == 410 or TAKEN_DOWN in page or not markup(page) else 'open'), page

    async def details(self, http, session, job):
        status, page = await self.job_page(http, job['jobUrl'].rsplit('/', 1)[-1])
        return {**job, **read_page(page, job)} if status == 'open' else job

    async def lookup(self, http, session, job_id):
        status, page = await self.job_page(http, job_id)
        nobody = {'company': None, 'companySlug': None, 'companyWebsite': None}
        if status == 'not found':
            return 'not found', nobody
        if status == 'closed':
            return 'closed', {**nobody, **company_of(page)}
        return 'open', job_from_page(page, job_id)

    async def open(self, http, board):
        if board.key in (EVERYONE.key, LISTED.key):
            return {'everyone': True}
        return {'company': board.key}


class Pace:
    """Calls in their turn: BURST at once, then rate a second (RATE, halved after each refusal down to one a second);
    after a refusal every call waits."""

    def __init__(self):
        self.rate = RATE
        self.tokens, self.at, self.until = float(BURST), time.monotonic(), 0.0
        self.lock = asyncio.Lock()

    async def turn(self) -> None:
        async with self.lock:
            now = time.monotonic()
            if self.until > now:
                await asyncio.sleep(self.until - now)
                now = time.monotonic()
            self.tokens = min(BURST, self.tokens + (now - self.at) * self.rate)
            self.at = now
            if self.tokens < 1:
                await asyncio.sleep((1 - self.tokens) / self.rate)
                self.tokens, self.at = 1.0, time.monotonic()
            self.tokens -= 1

    def hold(self, seconds: float) -> None:
        self.until = max(self.until, time.monotonic() + seconds)

    def slow(self) -> None:
        self.rate = max(1.0, self.rate / 2)


@lru_cache(maxsize=512)
def country_name(code: str) -> str:
    """'US' -> 'United States', the name Wellfound's country pages carry."""
    try:
        import pycountry
        found = pycountry.countries.get(alpha_2=code)
        return getattr(found, 'common_name', None) or getattr(found, 'name', None) or code
    except (ImportError, LookupError, KeyError):
        return code


# ---------- reading a job page ----------

LABEL = re.compile(r'<span class="text-md font-semibold">(.*?)</span>', re.S)
FUNDING = re.compile(r'<div class="text-xs[^"]*">(AMOUNT RAISED|FUNDED OVER)</div><div[^>]*>(.*?)</div>', re.S)
ROUND = re.compile(r'^(.+?) - (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{4})$')


def clean(fragment: str) -> str:
    return ' '.join(htmllib.unescape(re.sub(r'<[^>]+>', ' ', fragment.replace('<!-- -->', ''))).split())


def element(page: str, start: int) -> str:
    """The div that opens at start, up to its own closing tag."""
    depth = 0
    for m in re.finditer(r'<div\b|</div>', page[start:]):
        depth += 1 if m.group() == '<div' else -1
        if depth == 0:
            return page[start:start + m.end()]
    return page[start:]


def texts(fragment: str) -> list[str]:
    """Every piece of text a fragment shows, in order: tags split them, separators (• | - +) dropped."""
    pieces = (clean(t) for t in re.split(r'<[^>]+>', fragment.replace('<!-- -->', '')))
    return [t for t in pieces if re.search(r'\w', t)]


def facts(page: str) -> dict[str, list[str]]:
    """The job page's fact grid (Visa Sponsorship, Remote Work Policy, Preferred Timezones, Skills...): each label, from
    its span, and the texts of the div it opens."""
    out: dict[str, list[str]] = {}
    for mark in LABEL.finditer(page or ''):
        label = clean(mark.group(1))
        block = element(page, page.rfind('<div', 0, mark.start()))
        if label and label not in out:
            out[label] = texts(block[block.find(mark.group(0)) + len(mark.group(0)):])
    return out


def markup(page: str | None) -> dict:
    """The page's JobPosting markup, or {}."""
    for m in LD_JSON.finditer(page or ''):
        try:
            found = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(found, dict) and found.get('@type') == 'JobPosting':
            return found
    return {}


def yes_no(values: list[str] | None) -> bool | None:
    """['Not Available'] -> False, ['Allowed'] -> True."""
    text = ' '.join(values or []).lower()
    if text.startswith(('not ', 'no ')):
        return False
    return True if text.startswith(('available', 'allowed', 'yes')) else None


def split_list(text: str | None) -> list[str]:
    """'SaaS, Health and Wellness, and Consumer Lending' -> ['SaaS', 'Health and Wellness', 'Consumer Lending']: split at
    the commas only, since a market's own name can hold "and"."""
    return unique(re.split(r',\s*(?:and\s+)?', text or ''))


def funding(page: str) -> dict:
    """The funding box: the amount raised ('$115M'), the number of rounds, and the round it shows, the newest Wellfound
    records ('Series D2 - Jan 2022' on Brex's page, after its larger Series D): its name and month."""
    money = {label: clean(value) for label, value in FUNDING.findall(page or '')}
    rounds = re.search(r'\d+', money.get('FUNDED OVER', ''))
    name = month = None
    at = (page or '').find('>Rounds</div>')
    if at >= 0:
        for text in texts(element(page, page.find('<div', at))):
            m = ROUND.match(text)
            if m:
                name = m.group(1)
                month = datetime.strptime(f'{m.group(2)} {m.group(3)}', '%b %Y').strftime('%Y-%m')
                break
    return {'companyFunding': money.get('AMOUNT RAISED') or None,
            'fundingRounds': int(rounds.group()) if rounds else None, 'latestRound': name, 'latestRoundDate': month}


def read_page(page: str, job: dict) -> dict:
    """What a job page adds to a job: the description, every place with its country, pay as the markup states it, the
    company's website and markets, and the fact grid and funding of its HTML."""
    found = markup(page)
    grid = facts(page)
    html = found.get('description') or None
    out: dict = {'description': text_of(html) or job.get('description'), 'descriptionHtml': html}
    org = found.get('hiringOrganization') or {}
    if org.get('sameAs'):
        out['companyWebsite'] = org['sameAs']
    structured = []
    for entry in found.get('jobLocation') or []:
        address = (entry or {}).get('address') or {} if isinstance(entry, dict) else {}
        city, region, country = (address.get(k) for k in ('addressLocality', 'addressRegion', 'addressCountry'))
        name = ', '.join(p for p in (city, region, country) if p)
        if name:
            structured.append({'name': name, 'city': city, 'region': region, 'country': country})
    if structured:
        out['places'] = structured + [p for p in job.get('places') or [] if p not in structured]
    salary = found.get('baseSalary') or {}
    value = salary.get('value') or {} if isinstance(salary, dict) else {}
    if isinstance(value, dict) and (value.get('minValue') or value.get('maxValue')):
        period = {'YEAR': 'year', 'MONTH': 'month', 'WEEK': 'week', 'DAY': 'day', 'HOUR': 'hour'}.get(
            str(value.get('unitText') or '').upper()) or job.get('salaryPeriod')
        low, high = value.get('minValue'), value.get('maxValue')
        currency = salary.get('currency') or job.get('salaryCurrency')
        out.update(salaryMin=low, salaryMax=high, salaryCurrency=currency, salaryPeriod=period,
                   salary=pay_text(low, high, currency, period))
    posted = re.search(r'>\s*(Reposted|Posted):\s*<', page or '')
    out.update({
        'remotePolicy': next(iter(grid.get('Remote Work Policy') or []), None) or job.get('remotePolicy'),
        'remoteIn': job.get('remoteIn') or grid.get('Hires remotely in') or [],     # the search's list is the clean one
        'visaSponsorship': yes_no(grid.get('Visa Sponsorship')),
        'relocation': yes_no(grid.get('Relocation')),
        'timezones': split_list(', '.join(grid.get('Preferred Timezones') or [])) or None,
        'skills': grid.get('Skills') or None,
        'industries': split_list(found.get('industry')) or None,
        'companyLocation': ', '.join(grid.get('Company Location') or []) or None,
        **funding(page),
        'reposted': None if not posted else posted.group(1) == 'Reposted',
    })
    return out


def company_of(page: str | None) -> dict:
    """The company a job page names in its "Learn more about" link: its name and slug."""
    m = re.search(r'href="/company/([^"/?#]+)">Learn more about <!-- -->(.*?)<', page or '')
    return {'company': clean(m.group(2)), 'companySlug': m.group(1)} if m else {}


def job_from_page(page: str, key: str) -> dict:
    """A job from its page alone, for a checked link."""
    found = markup(page)
    org = found.get('hiringOrganization') or {}
    policy_text = next(iter(facts(page).get('Remote Work Policy') or []), None)
    remote = found.get('jobLocationType') == 'TELECOMMUTE'
    names = unique((entry.get('address') or {}).get('addressLocality') or (entry.get('address') or {}).get('addressRegion')
                   for entry in found.get('jobLocation') or [] if isinstance(entry, dict))
    employment = {'FULL_TIME': 'full-time', 'PART_TIME': 'part-time', 'CONTRACTOR': 'contract', 'INTERN': 'intern',
                  'TEMPORARY': 'temporary'}.get(str(found.get('employmentType') or '').upper())
    years = re.search(r'>(\d+) years? of exp<', page or '')
    link = f'{ORIGIN}/jobs/{key}'
    job = {
        'id': str((found.get('identifier') or {}).get('value') or key.split('-', 1)[0]),
        'company': org.get('name'), 'companySlug': None, 'companyWebsite': None, **company_of(page),
        'title': ' '.join((found.get('title') or '').split()),
        'location': names[0] if names else ('Remote' if remote else None), 'otherLocations': names[1:], 'places': [],
        'workType': 'hybrid' if policy_text and 'flexib' in policy_text.lower() else ('remote' if remote else 'onsite'),
        'employmentType': employment, **compensation(None), 'postedAt': found.get('datePosted'),
        'jobUrl': link, 'applyUrl': link, 'experienceMin': int(years.group(1)) if years else None, 'experienceMax': None,
        'remoteIn': [], 'remotePolicy': policy_text, 'role': None, 'companySize': None, 'companyStage': None,
        'companyTagline': None, 'companyLogo': org.get('logo'), 'companyBadges': [],
        **dict.fromkeys(Wellfound.DETAILS_ONLY),
    }
    job.update(read_page(page, job))
    return job
