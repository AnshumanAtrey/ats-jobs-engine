"""
Naukri.com, India's largest job board, read through the JSON API its own search page calls. No login, no API key, no
browser and no cookie. Measured from this laptop on 2026-11-08 (a home line's IPv4 and IPv6, and a Tor exit at netcup,
a German hosting company, as an address in a datacenter):

- GET https://www.naukri.com/jobapi/v3/search with keyword, location, experience, jobAge, wfhType, ctcFilter,
  qbusinessSize, jobPostType, qctopGroupId, sort=f (by date; r is relevance), noOfResults=100 and pageNo: up to 100
  jobs a page, newest first by Naukri's posted date, and noOfJobs, every match. Page 150 is the last one served (151
  answers 400 "Requested page number doesn't exists"), so one search reaches 15,000 jobs; the empty search lists
  692,632. A page of 100 takes 0.5 to 3 s. The keyword is matched in titles, key skills and text, so the words are
  matched in titles here; the filters are exact (100 of 100 jobs held the work type, experience and city asked for).
- GET https://www.naukri.com/jobapi/v4/job/<jobId>: one job with its description, job type, department, role,
  industry, education, key skills, applicants and the employer's website, and for a job Naukri copied from an
  employer's career site (11% of jobs) that site's own job page. A closed job, and a 12-digit id that never existed,
  answer 303 {"metaSearch": {"isExpiredJob": "1"}}; any other id answers 404.
- The handshake the page makes, rebuilt here: headers appid (109 to search, 121 for a job), systemid Naukri, and
  nkparam, the page's 512-bit RSA public key (PKCS#1 v1.5) over "v0|<milliseconds since 1970>|121_<srp or the job
  id>" in base64. Without nkparam the API answers 406 "recaptcha required". No cookie or earlier call is needed.
- Akamai in front reads the user agent: a bot's ("compatible; ...", the engine's own) gets 403 Access Denied, curl's
  and python-httpx's get no answer, a browser's passes, over Python's own TLS. A hosting address abroad (the Tor exit)
  was answered like the home line.
- Naukri limits each address. One address made about 420 job details in 7 minutes, in bursts of up to 100 a second:
  from then on every details call answered 406 "recaptcha required", still after an hour (calls every 4 minutes),
  while its searches kept answering, and the 406s that followed had Akamai refuse it everywhere (403) for 10 to 12
  minutes. Another address made 520 details at 4 a second without a refusal; with its searches and job pages, about 900
  calls in 25 minutes, its searches and details then answered 406 too. A single 406 also comes and goes (one search
  was refused twice, then answered). So calls wait their turn (PACE), a refusal is tried again after AGAIN_S, then
  believed: the run stops asking that endpoint and delivers what it read, with a note. A run of 100 jobs makes 2 to 20
  calls, and one more a job with details.
- Dates: createdDate (milliseconds) is when the job was last posted: an employer's repost moves it to now, and Naukri
  sorts and filters by it. The first six digits of the job id are the day it was first posted, DDMMYY (887 of 887
  jobs read: a real date, never after createdDate; 23% were posted again a week or more later). Internships carry no
  createdDate in the list, so their day comes from the id. jobAge 1 reached back 30 hours (to midnight of the day
  before, India time), so each bucket holds at least its days.
- A location is one text: "Hybrid - Hyderabad, Chennai, Bengaluru", "Remote", "Bengaluru(Hoskote)", "United States
  (USA), Singapore". Hybrid and Remote are the work type (wfhType 3 and 2; anything else is office work, 0). A city is
  given its state from Naukri's own list of 5,810 Indian cities (data/naukri-cities.tsv, scripts/refresh_naukri.py);
  a place that is neither on it nor a country names no country (Dubai, London), and Remote is remote in India.
"""
import asyncio
import base64
import re
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from importlib import resources
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .. import places
from ..http import BoardError, send
from ..inputs import IS_URL, WORK_TYPES, _clean, _items, parse_period
from ..rows import Filters, country_code, dedupe_key, job_places, words
from .base import Board, Platform
from .cornerstone import text_of

SITE = 'https://www.naukri.com'
SEARCH = SITE + '/jobapi/v3/search'
DETAILS = SITE + '/jobapi/v4/job/{job}'
HOSTS = ('naukri.com', 'www.naukri.com', 'm.naukri.com')
SEARCH_APP, JOB_APP = '109', '121'
# The public key the search page ships (its JS module 55841, export e$3), as of 2026-11-08.
KEY = ('MFwwDQYJKoZIhvcNAQEBBQADSwAwSAJBALrlQ+djR0RjJwBF1xuisHmdFv334MImK6LgzJhmLhN7B5yuEyaKoasgXQk3+OQglsOaBxEJ0j5PcTL3'
       'nbOvt80CAwEAAQ==')
AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36'
EVERYONE = Board('naukri', 'Naukri')      # every job on Naukri at once; also the board of every job link
PAGE = 100
LAST_PAGE = 150                           # Naukri serves no page past it: 15,000 jobs a search
PACE = 0.25                               # seconds between the starts of two calls
TYPE_LOOKUPS = 300                        # jobs a run opens to learn their job type, when one is asked for
ROTATIONS = 3                             # fresh proxy addresses a run tries after refusals, when a proxy is set
AGAIN_S = (3, 10)                         # waits before a refused call is made again, without a proxy
JOB_AGES = (1, 3, 7, 15, 30)              # Naukri's freshness choices, in days
WFH_TYPE = {'onsite': '0', 'remote': '2', 'hybrid': '3'}
WORK_TYPE = {'0': 'onsite', '1': 'remote', '2': 'remote', '3': 'hybrid'}     # 1 was a temporary work from home
# Naukri's company types (its business_size filter, qbusinessSize), as the search page names them.
COMPANY_TYPES = {'foreign-mnc': ('213', 'Foreign MNC'), 'indian-mnc': ('217', 'Indian MNC'),
                 'mnc': ('60', 'MNC'), 'corporate': ('211', 'Corporate'), 'startup': ('62', 'Startup'),
                 'unicorn': ('219', 'Unicorn'), 'govt-psu': ('215', 'Govt/PSU'), 'others': ('63', 'Others')}
COMPANY_TYPE_WORDS = {'foreign mnc': 'foreign-mnc', 'foreign': 'foreign-mnc', 'indian mnc': 'indian-mnc', 'mnc': 'mnc',
                      'multinational': 'mnc', 'corporate': 'corporate', 'startup': 'startup', 'start up': 'startup',
                      'unicorn': 'unicorn', 'govt': 'govt-psu', 'government': 'govt-psu', 'psu': 'govt-psu',
                      'govt psu': 'govt-psu', 'public sector': 'govt-psu', 'others': 'others', 'other': 'others'}
POSTED_BY = {'company': '1', 'consultant': '2'}
POSTED_BY_WORDS = {'company': 'company', 'companies': 'company', 'employer': 'company', 'direct': 'company',
                   'consultant': 'consultant', 'consultants': 'consultant', 'recruiter': 'consultant',
                   'agency': 'consultant', 'staffing': 'consultant', 'placement': 'consultant'}
# Naukri's pay bands in lakhs a year (ctcFilter): (lowest, highest, id); 501 is 5 crore and more.
PAY_BANDS = ((0, 3, '0to3'), (3, 6, '3to6'), (6, 10, '6to10'), (10, 15, '10to15'), (15, 25, '15to25'),
             (25, 50, '25to50'), (50, 75, '50to75'), (75, 100, '75to100'), (100, 500, '100to500'), (500, None, '501'))
LAKH = 100_000
JOB_PATH = re.compile(r'^job-listings-.*?(\d{12})$')
JOB_ID = re.compile(r'^\d{12}$')
OVERVIEW = re.compile(r'^(.+)-overview-(\d+)$')        # a company's page: the number is its group
CAREERS = re.compile(r'^(.+)-jobs-careers-\d+$')        # a company's jobs page: the number is the page's own
SEARCH_PATH = re.compile(r'^(?:(.+?)-)?jobs(?:-in-(.+))?$')
IST = timezone(timedelta(hours=5, minutes=30))
NOT_SHOWN = {'not disclosed', 'not mentioned', ''}
STIPEND = re.compile(r'^([\d,]+)(?:\s*-\s*([\d,]+))?\s*/\s*month', re.I)


class Refused(BoardError):
    """Naukri refused this address (a captcha demand, or Akamai's Access Denied): not asked again this run."""


def _integers(der: bytes) -> list[int]:
    """The INTEGERs of a DER structure, read through its SEQUENCEs and BIT STRINGs: an RSA public key's modulus and
    exponent."""
    found, i = [], 0
    while i + 2 <= len(der):
        tag, size = der[i], der[i + 1]
        i += 2
        if size & 0x80:
            count = size & 0x7F
            size, i = int.from_bytes(der[i:i + count], 'big'), i + count
        if tag == 0x30:              # SEQUENCE: read what it holds
            continue
        if tag == 0x03:              # BIT STRING: skip its unused-bits byte and read what it holds
            i += 1
            continue
        if tag == 0x02:
            found.append(int.from_bytes(der[i:i + size], 'big'))
        i += size
    return found


MODULUS, EXPONENT = _integers(base64.b64decode(KEY))


def nkparam(page: str, now_ms: int | None = None, modulus: int = MODULUS, exponent: int = EXPONENT) -> str:
    """The page's nkparam header: "v0|<ms>|121_<page>" encrypted with Naukri's public key, PKCS#1 v1.5 (what the page's
    JSEncrypt does), in base64."""
    message = f'v0|{int(time.time() * 1000) if now_ms is None else now_ms}|121_{page}'.encode()
    size = (modulus.bit_length() + 7) // 8
    filler = bytes(secrets.randbelow(255) + 1 for _ in range(size - 3 - len(message)))     # random, never zero
    block = int.from_bytes(b'\x00\x02' + filler + b'\x00' + message, 'big')
    return base64.b64encode(pow(block, exponent, modulus).to_bytes(size, 'big')).decode()


def headers(page: str, appid: str) -> dict:
    return {'User-Agent': AGENT, 'Accept': 'application/json', 'appid': appid, 'systemid': 'Naukri',
            'nkparam': nkparam(page)}


def refusal(response) -> str | None:
    """Why Naukri refused this address, or None: a captcha demand from Naukri, or Akamai's Access Denied page."""
    text = response.text[:300]
    if response.status_code == 406 and 'recaptcha' in text.lower():
        return 'it asked for a captcha'
    if response.status_code == 403 and 'access denied' in text.lower():
        return 'Access Denied'
    return None


def answer(response) -> dict:
    if response.status_code != 200:
        raise BoardError(f'HTTP {response.status_code}')
    try:
        data = response.json()
    except ValueError as exc:
        raise BoardError('the answer was not JSON') from exc
    if not isinstance(data, dict):
        raise BoardError('the answer was not an object')
    return data


# ---------- places ----------

@lru_cache(maxsize=1)
def cities() -> dict[str, str]:
    """Naukri's Indian cities, by each of their names in words -> the state (data/naukri-cities.tsv)."""
    text = resources.files('ats_jobs').joinpath('data/naukri-cities.tsv').read_text(encoding='utf-8')
    found = {}
    for line in text.splitlines():
        if line and not line.startswith('#'):
            city, _, state = line.partition('\t')
            found.setdefault(' '.join(words(city)), state.strip())
    return found


def state_of(name: str) -> str | None:
    """'Bengaluru', 'Gurugram' (Gurgaon on Naukri's list), 'Mumbai (All Areas)' -> the state, or None."""
    for part in (name, re.sub(r'\(.*?\)', ' ', name), *re.split(r'\s*/\s*', name)):
        key = ' '.join(words(part))
        for each in (key, *places.OTHER_NAMES.get(key, ())):
            if each in cities():
                return cities()[each]
    return None


def split_places(text: str | None) -> tuple[str | None, list[str]]:
    """'Hybrid - Hyderabad, Chennai' -> ('hybrid', ['Hyderabad', 'Chennai']); 'Remote' -> ('remote', ['Remote']);
    'Pune' -> ('onsite', ['Pune']). Commas inside brackets stay: 'Bengaluru(Whitefield, Marathahalli)'."""
    text = ' '.join((text or '').split())
    if not text:
        return None, []
    kind = 'onsite'
    head = re.match(r'^(hybrid|remote|temp\.?\s*wfh)\s*-\s*', text, re.I)
    if head:
        kind = 'hybrid' if head.group(1).lower() == 'hybrid' else 'remote'
        text = text[head.end():]
    elif text.lower().startswith('remote'):
        kind = 'remote'
    names, depth, current = [], 0, ''
    for ch in text:
        depth += (ch == '(') - (ch == ')')
        if ch == ',' and depth <= 0:
            names.append(current.strip())
            current = ''
        else:
            current += ch
    names.append(current.strip())
    return kind, [n for n in names if n]


def place(name: str) -> dict:
    """One place of a job: its city (without the locality in brackets), state and country."""
    city = re.sub(r'\s*\(.*?\)\s*', ' ', name).strip() or name
    codes = job_places({'places': [{'name': name}]})[1]
    if codes:
        code = sorted(codes)[0]
        return {'name': name, 'country': country_name(code)}
    state = state_of(name)
    if state:
        return {'name': name, 'city': city, 'region': state, 'country': 'India'}
    if places.entry_kind(places.tokens(name)) == 'remote':
        return {'name': name, 'country': 'India'}
    return {'name': name, 'city': city}


def country_name(code: str) -> str:
    try:
        import pycountry
        found = pycountry.countries.get(alpha_2=code)
        return getattr(found, 'common_name', None) or found.name
    except (ImportError, AttributeError, LookupError):
        return code


# ---------- values ----------

def first_posted(job_id: str | None) -> str | None:
    """'040926016871' -> '2026-09-04': the day Naukri first listed the job (its id starts with DDMMYY)."""
    found = re.match(r'^(\d{2})(\d{2})(\d{2})\d{6,}$', job_id or '')
    if not found:
        return None
    try:
        return date(2000 + int(found.group(3)), int(found.group(2)), int(found.group(1))).isoformat()
    except ValueError:
        return None


def from_ms(value) -> str | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if value else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def from_india_time(value: str | None) -> str | None:
    """'2026-11-07 05:31:32' (India time, as the details give it) -> '2026-11-07T00:01:32Z'."""
    try:
        moment = datetime.strptime((value or '').strip(), '%Y-%m-%d %H:%M:%S').replace(tzinfo=IST)
    except ValueError:
        return None
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def number(value) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def rating(value) -> float | None:
    try:
        found = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return found if found > 0 else None


def pay(detail: dict | None, label: str | None) -> dict:
    """The pay Naukri shows, as text and numbers: '7-17 Lacs PA' with 700000 and 1700000 a year; an internship's
    '5,000/month' with 5000 a month, read from the text (its numbers are hidden). 'Not disclosed' gives nothing."""
    detail = detail or {}
    text = clean(label or detail.get('label') or detail.get('salaryText'))
    if text.lower() in NOT_SHOWN:
        return {'salary': None}
    out = {'salary': text}
    low, high = number(detail.get('minimumSalary')), number(detail.get('maximumSalary'))
    stipend = STIPEND.match(text)
    if not detail.get('hideSalary') and (low or high):
        out.update({'salaryMin': low or None, 'salaryMax': high or None,
                    'salaryCurrency': detail.get('currency') or 'INR', 'salaryPeriod': 'year'})
    elif stipend:
        low = int(stipend.group(1).replace(',', ''))
        out.update({'salaryMin': low, 'salaryMax': int((stipend.group(2) or stipend.group(1)).replace(',', '')),
                    'salaryCurrency': 'INR', 'salaryPeriod': 'month'})
    return out


def employment_type(info: dict) -> str | None:
    """Naukri's 'Full Time, Permanent', 'Part Time, Temporary/Contractual' and its jobType -> one engine job type."""
    text = (info.get('employmentType') or '').lower()
    if (info.get('jobType') or '').lower() == 'internship' or 'intern' in text:
        return 'intern'
    if 'contract' in text or 'freelance' in text:
        return 'contract'
    if 'part time' in text:
        return 'part-time'
    if 'temporary' in text:
        return 'temporary'
    if 'full time' in text:
        return 'full-time'
    return None


def key_skills(info: dict) -> list[str]:
    skills = info.get('keySkills') or {}
    found = [s.get('label') for part in ('preferred', 'other') for s in skills.get(part) or [] if isinstance(s, dict)]
    return list(dict.fromkeys(s.strip() for s in found if s and s.strip()))


def education(info: dict | None) -> str | None:
    """{'ug': ['B.Tech/B.E. in Any Specialization'], 'pg': ['Any Postgraduate']} -> 'UG: B.Tech/B.E. in Any
    Specialization; PG: Any Postgraduate'."""
    info = info or {}
    parts = [f'{label}: {", ".join(v for v in info.get(key) or [] if v)}' for key, label in
             (('ug', 'UG'), ('pg', 'PG'), ('ppg', 'Doctorate')) if any(info.get(key) or [])]
    return '; '.join(parts) or None


def clean(text) -> str:
    return ' '.join(str(text or '').split())


class Naukri(Platform):
    key = 'naukri'
    name = 'Naukri'
    site_word = 'company page'
    actor = 'naukri-jobs-search'
    board_example = 'https://www.naukri.com/tata-capital-overview-172058'
    job_example = ('https://www.naukri.com/job-listings-python-developer-cgi-hyderabad-chennai-bengaluru-5-to-8-years-'
                   '051126035541')
    concurrency = 4               # calls out at once; PACE spaces their starts
    server_search = True          # Naukri searches the words; titles are matched here
    newest_first = True           # sort=f: newest first, also with a word
    never = ('team', 'offersEquity', 'expiresAt')
    # Naukri's own fields on every row: the list's (experience, key skills, pay as numbers, rating, first posting day),
    # and with details the job page's (industry, role, education, applicants, who posted it)
    extra = ('experience', 'experienceMin', 'experienceMax', 'skills', 'salaryMin', 'salaryMax', 'salaryCurrency',
             'salaryPeriod', 'firstPostedAt', 'companyRating', 'companyReviews', 'vacancies', 'walkIn', 'summary',
             'companyLogo', 'industry', 'role', 'roleCategory', 'education', 'applicants', 'views', 'postedBy')

    def __init__(self):
        self.cfg = None
        self._turn = asyncio.Lock()
        self._last = 0.0
        self._calls = 0
        self._pages = 0               # search pages that answered this run
        self._refused: dict[str, str] = {}      # endpoint -> why Naukri refused this address
        self._told: set[str] = set()
        self._typed = 0               # jobs opened to learn their job type
        self._proxy_input = None      # the run's proxyConfiguration, when it names a proxy
        self._proxy = None            # its ProxyConfiguration
        self._via = None              # the client on the proxy address in use
        self._rotations = 0           # proxy addresses dropped after a refusal
        self.options: dict = {}       # Naukri's own settings of the run (read_input), in OUTPUT's settings

    # ---------- boards, links and settings ----------

    def boards(self):
        return [], None               # no list of boards: Naukri searches every company itself

    def search_all(self, boards, cfg):
        self.cfg = cfg
        return [EVERYONE]

    def copy_key(self, job):
        """One title at one place is one job unless Naukri lists it for other experience or pay (one company's two
        posts of a title, 7-12 and 12-15 years)."""
        return (*dedupe_key(job), job.get('experience') or '', job.get('salary') or '')

    def read_link(self, text):
        text = text.strip().rstrip('.,;:!?')
        if JOB_ID.match(text):
            return EVERYONE.key, text
        link = text if IS_URL.match(text) else f'https://{text}'
        parsed = urlparse(link)
        host = (parsed.hostname or '').lower() if ' ' not in text else ''
        if host not in HOSTS:
            return None
        path = unquote(parsed.path).strip('/').lower()
        job = JOB_PATH.match(path)
        if job:
            return EVERYONE.key, job.group(1)
        query = parse_qs(parsed.query)
        if not path and query.get('jobId'):
            found = query['jobId'][0].strip()
            return (EVERYONE.key, found) if JOB_ID.match(found) else (None, None)
        group = OVERVIEW.match(path)
        if group:
            return group.group(2), None
        company = CAREERS.match(path)
        if company:
            return company.group(1).replace('-', ' '), None
        return None, None

    def search_link(self, text):
        """A Naukri search page: ?k= and ?l= when the search box made it, else its address, as in
        /python-developer-jobs-in-bangalore. India as the place is every job, so it is no place."""
        parsed = urlparse(text if IS_URL.match(text) else f'https://{text}')
        query = parse_qs(parsed.query)
        asked = [v.strip() for v in query.get('k', []) if v.strip()]
        where = [v.strip() for v in query.get('l', []) if v.strip()]
        shown = SEARCH_PATH.match(unquote(parsed.path).strip('/').lower())
        if shown and not asked and shown.group(1):
            asked = [shown.group(1).replace('-', ' ')]
        if shown and not where and shown.group(2):
            where = [shown.group(2).replace('-', ' ')]
        return asked, [w for w in where if w.lower() != 'india']

    def settings(self):
        return dict(self.options)

    def guess_board(self, name):
        found = CAREERS.match(name.lower())
        return (found.group(1).replace('-', ' ') if found else ' '.join(name.split())) or None

    def searches(self, token: str) -> bool:
        """A Naukri link the search box reads as a search (inputs.route_links): it shows words or a place."""
        text = _clean(token)
        parsed = urlparse(text if IS_URL.match(text) else f'https://{text}')
        if (parsed.hostname or '').lower() not in HOSTS or self.read_link(text) != (None, None):
            return False
        query = parse_qs(parsed.query)
        words, where = self.search_link(text)
        return bool(words or where or any(v.strip() for key in ('query', 'q', 'keywords', 'location')
                                          for v in query.get(key, [])))

    def read_input(self, raw, cfg):
        """Naukri's own fields (experience, minSalaryLakhs, companyType, postedBy, proxyConfiguration) and the filters
        of a pasted search link (experience, jobAge, wfhType, ctcFilter, qbusinessSize, jobPostType, qctopGroupId); the
        form's own values win over a link's."""
        self.cfg = cfg
        notes = cfg.notes
        linked: dict[str, list[str]] = {}
        for token in _items(raw.get('searchTerms')):
            if self.searches(token):
                text = _clean(token)
                query = parse_qs(urlparse(text if IS_URL.match(text) else f'https://{text}').query)
                for key, values in query.items():
                    linked.setdefault(key, []).extend(v for value in values for v in value.split(',') if v.strip())
        options: dict = {}
        years = raw.get('experience')
        if years in (None, '') and linked.get('experience'):
            years = linked['experience'][0]
        if years not in (None, ''):
            found = number(years)
            if found is None or not 0 <= found <= 50:
                notes.append(f'Could not read experience "{years}" as a number of years, so every experience is kept.')
            else:
                options['experience'] = found
        lakhs = raw.get('minSalaryLakhs')
        if lakhs in (None, '', 0) and linked.get('ctcFilter'):
            bands = [low for low, _, band in PAY_BANDS if band in linked['ctcFilter']]
            lakhs = min(bands) if bands else None
        if lakhs not in (None, '', 0):
            try:
                found_lakhs = float(str(lakhs).strip().lower().removesuffix('lpa').removesuffix('lakhs').strip())
            except ValueError:
                found_lakhs = None
            if found_lakhs is None or found_lakhs < 0:
                notes.append(f'Could not read minSalaryLakhs "{lakhs}" as lakhs a year, so every pay is kept.')
            elif found_lakhs > 0:
                options['minSalaryLakhs'] = found_lakhs
        chosen = self.choices(raw.get('companyType'), 'companyType', COMPANY_TYPES, COMPANY_TYPE_WORDS, notes)
        if not chosen and linked.get('qbusinessSize'):
            by_id = {value[0]: key for key, value in COMPANY_TYPES.items()}
            chosen = [by_id[v] for v in linked['qbusinessSize'] if v in by_id]
        if chosen:
            options['companyType'] = chosen
        poster = self.choices(raw.get('postedBy'), 'postedBy', POSTED_BY, POSTED_BY_WORDS, notes)
        if not poster and linked.get('jobPostType'):
            poster = [key for key, value in POSTED_BY.items() if value in linked['jobPostType']]
        if len(poster) == 1:
            options['postedBy'] = poster[0]
        if cfg.posted_within_days is None and linked.get('jobAge'):
            cfg.posted_within_days = parse_period(linked['jobAge'][0], notes)
        if not cfg.work_types and linked.get('wfhType'):
            kinds = {value: key for key, value in WFH_TYPE.items()}
            cfg.work_types = [kinds[v] for v in dict.fromkeys(linked['wfhType']) if v in kinds]
            if len(cfg.work_types) == len(WORK_TYPES):
                cfg.work_types = []
        for group in linked.get('qctopGroupId') or []:
            if group.isdigit() and group not in cfg.companies:
                cfg.companies.append(group)
        proxy = raw.get('proxyConfiguration')
        if isinstance(proxy, dict) and (proxy.get('useApifyProxy') or proxy.get('proxyUrls')):
            self._proxy_input = proxy     # kept out of options: proxy addresses can hold a password
        self.options = options

    @staticmethod
    def choices(value, key: str, allowed: dict, aliases: dict, notes: list[str]) -> list[str]:
        """Typed values of a choice field -> its keys, in any case or as everyday words; unknown ones dropped, with a
        note."""
        chosen, unknown = [], []
        for token in _items(value):
            word = ' '.join(token.lower().replace('_', ' ').replace('/', ' ').split())
            found = word.replace(' ', '-') if word.replace(' ', '-') in allowed else aliases.get(word)
            if found is None:
                unknown.append(token)
            elif found not in chosen:
                chosen.append(found)
        if unknown:
            notes.append(f'{key} has no option {", ".join(unknown)}, so it was left out. The options are '
                         f'{", ".join(allowed)}.')
        return chosen if len(chosen) < len(allowed) else []

    # ---------- calls ----------

    def tell(self, kind: str, note: str) -> None:
        if kind not in self._told and self.cfg is not None:
            self._told.add(kind)
            self.cfg.notes.append(note)

    async def route(self, http):
        """The client a call goes through: the run's own, or one through the proxy the user set (proxyConfiguration),
        on the proxy session of this run's current address."""
        if not self._proxy_input:
            return http
        if self._via is None:
            if self._proxy is None:
                from apify import Actor
                try:
                    self._proxy = await Actor.create_proxy_configuration(actor_proxy_input=self._proxy_input)
                except Exception as exc:  # noqa: BLE001  a proxy the platform cannot use (socks5://, a typo)
                    why = str(exc).splitlines()[0][:160]
                    self.tell('proxy', f'Connection (proxyConfiguration) could not be used ({why}), so the run used its '
                                       f'own address.')
                    self._proxy = None
                if self._proxy is None:          # a setting that names no proxy: the run's own address
                    self._proxy_input = None
                    return http
            url = await self._proxy.new_url(session_id=f'naukri_{self._rotations}')
            self._via = httpx.AsyncClient(proxy=url, timeout=httpx.Timeout(30.0, connect=15.0), follow_redirects=True)
        return self._via

    async def call(self, http, url: str, params: dict | None, page: str, appid: str, kind: str, **kwargs):
        """One call in its turn, PACE after the one before it. Refused when Naukri refused this address on this kind
        of call (search or details) earlier in the run, or now: then the run stops asking it (a refusal held for over
        an hour when calls went on, see the docstring). A single 406 also comes now and then (the first call of one
        search was refused twice and answered on the third, 2026-11-08), so a refusal is believed after AGAIN_S more
        tries. Through a proxy, a refusal moves the run to a fresh proxy address instead, ROTATIONS times at most."""
        tries = 0
        while True:
            if kind in self._refused:
                raise Refused(f'Naukri refused this address ({self._refused[kind]})')
            via = await self.route(http)
            async with self._turn:
                wait = self._last + PACE - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last = time.monotonic()
                self._calls += 1
            response = await send(via, 'GET', url, params=params, headers=headers(page, appid), **kwargs)
            why = refusal(response)
            if not why:
                return response
            if self._proxy_input and self._rotations < ROTATIONS:
                if via is self._via:              # the first refusal on this address moves the run to a new one
                    self._rotations += 1
                    self._via = None
                continue
            if not self._proxy_input and tries < len(AGAIN_S):
                await asyncio.sleep(AGAIN_S[tries])
                tries += 1
                continue
            self._refused.setdefault(kind, why)
            what = 'search pages' if kind == 'search' else 'job details'
            later = ('the proxy\'s addresses were refused too' if self._proxy_input else
                     'run again later, or turn on Apify Proxy in Connection (proxyConfiguration)')
            self.tell(kind, f'Naukri refused this address ({why}) for {what} after {self._calls:,} calls, so the run '
                            f'stopped asking it for them and saved what it had read. A refusal lasts from minutes to '
                            f'about an hour: {later}.')
            raise Refused(f'Naukri refused this address ({why})')

    # ---------- searching ----------

    async def open(self, http, board):
        if board.key == EVERYONE.key:
            return {'groups': []}
        if board.key.isdigit():
            return {'groups': [board.key], 'name': None}
        data = answer(await self.call(http, SEARCH, {**self.base(), 'keyword': board.key, 'noOfResults': 20,
                                                     'sort': 'r', 'pageNo': 1}, 'srp', SEARCH_APP, 'search'))
        wanted = squeeze(board.key)
        clusters = (data.get('clusters') or {}).get('topGroupId') or []
        listed = [(str(c.get('id')), clean(c.get('label'))) for c in clusters]
        groups = ([g for g in listed if squeeze(g[1]) == wanted]
                  or [g for g in listed if squeeze(g[1]).startswith(wanted)])
        if not groups:
            groups = list(dict.fromkeys((str(r.get('groupId')), clean(r.get('companyName')))
                                        for r in data.get('jobDetails') or []
                                        if r.get('groupId') and squeeze(r.get('companyName')) == wanted))
        if not groups:
            return None
        return {'groups': [g for g, _ in groups], 'name': groups[0][1]}

    def base(self) -> dict:
        return {'noOfResults': PAGE, 'urlType': 'search_by_keyword', 'searchType': 'adv', 'src': 'jobsearchDesk',
                'latLong': ''}

    def query(self, session: dict, term: str | None) -> dict:
        """The search Naukri runs: the word and every filter it can apply itself. The engine checks each job too."""
        cfg = self.cfg
        query = {**self.base(), 'sort': 'f'}
        if term:
            query['keyword'] = term
        if cfg and cfg.job_types == ['intern']:
            # Naukri's internship search, which lists internships only: "marketing internship" gave 4,263 of them, and
            # every job on its first page an internship, where "marketing" gave 1 in 200 (2026-11-08)
            query['keyword'] = f'{term} internship' if term else 'internship'
        where = self.server_places()
        if where:
            query['location'] = ','.join(where)
        if session.get('groups'):
            query['qctopGroupId'] = session['groups']
        days = cfg.posted_within_days if cfg else None
        if days is not None:
            age = next((a for a in JOB_AGES if days <= a), None)
            if age:
                query['jobAge'] = age
        if cfg and cfg.work_types:
            query['wfhType'] = [WFH_TYPE[w] for w in cfg.work_types if w in WFH_TYPE]
        options = self.options
        if options.get('experience') is not None:
            query['experience'] = options['experience']
        if options.get('minSalaryLakhs'):
            query['ctcFilter'] = [band for _, high, band in PAY_BANDS
                                  if high is None or high > options['minSalaryLakhs']]
        if options.get('companyType'):
            query['qbusinessSize'] = [COMPANY_TYPES[k][0] for k in options['companyType']]
        if options.get('postedBy'):
            query['jobPostType'] = POSTED_BY[options['postedBy']]
        return query

    def server_places(self) -> list[str]:
        """The typed places Naukri can search itself: cities, states and countries other than India. When one of them
        cannot be sent (India, a region, worldwide), none is, and every place is checked here."""
        found = []
        for text in (self.cfg.locations if self.cfg else []):
            toks = places.tokens(text)
            if places.world_query(toks) or places.region_query(toks) is not None:
                return []
            if country_code(text, allow_codes=True) == 'IN':
                return []
            found.append(text)
        return found

    @staticmethod
    def windows(query: dict) -> list[int | None]:
        """The freshness windows a search reads, in turn. Naukri answers a search with no words, place or company
        unevenly: four calls in a row listed 511,439 to 692,506 jobs, each with other newest jobs, and office work
        within 30 days 276,558 then 289,230, while a word, a place or the last 24 hours (2,978 jobs) gave the same
        answer every time (2026-11-08). Such a search reads the last day, then 3, 7, 15 and 30 days, each window
        starting where the one before ended, so it also reaches past one search's 15,000 jobs. Jobs posted over 30 days
        ago are left out of it; the newest 15,000 of all Naukri were under two days old."""
        if query.get('keyword') or query.get('location') or query.get('qctopGroupId'):
            return [query.get('jobAge')]
        last = query.get('jobAge') or JOB_AGES[-1]
        return [age for age in JOB_AGES if age <= last]

    async def pages(self, http, session, term, days):
        query = self.query(session, term)
        steps = self.windows(query)
        seen: set[str] = set()
        listed = 0                        # jobs of the window before: the next one starts past them
        for age in steps:
            window = {**query, 'jobAge': age} if age else query
            first = listed // PAGE + 1
            for n in range(first, LAST_PAGE + 1):
                try:
                    response = await self.call(http, SEARCH, {**window, 'pageNo': n}, 'srp', SEARCH_APP, 'search')
                except Refused:
                    if not self._pages:
                        raise             # nothing was read: the run fails and says why
                    self.incomplete = True
                    return
                if response.status_code == 400 and n > 1:
                    break                 # past the last page Naukri serves
                data = answer(response)
                self._pages += 1
                records = [r for r in data.get('jobDetails') or [] if isinstance(r, dict) and r.get('jobId')]
                fresh = [r for r in records if str(r['jobId']) not in seen]
                seen.update(str(r['jobId']) for r in records)
                jobs = [self.job(r) for r in fresh]
                if self.cfg is not None and self.cfg.job_types:
                    await self.typed(http, jobs)
                yield jobs
                total = number(data.get('noOfJobs')) or 0
                if not records or n * PAGE >= total:
                    break
                if n == LAST_PAGE and age == steps[-1]:
                    self.tell('cap', f'Naukri shows at most {LAST_PAGE * PAGE:,} jobs for one search, and this one '
                                     f'matched {total:,}: add a place, a period or more words to reach the rest.')
            listed = max(listed, total)

    def fits(self, job: dict) -> bool:
        """The filters only Naukri has, checked on what the job shows: experience and pay. Company type and who posted
        the job are not shown in the list, so Naukri's own filter decides them; hidden pay is decided by Naukri too."""
        years = self.options.get('experience')
        if years is not None and job.get('experienceMin') is not None and job.get('experienceMax') is not None:
            if not job['experienceMin'] <= years <= job['experienceMax']:
                return False
        lakhs, top = self.options.get('minSalaryLakhs'), job.get('salaryMax') or job.get('salaryMin')
        return not (lakhs and job.get('salaryPeriod') == 'year' and top and top < lakhs * LAKH)

    async def typed(self, http, jobs: list[dict]) -> None:
        """A job type was asked for, and the list names it only for internships: the jobs whose title can match are
        opened for theirs, TYPE_LOOKUPS at most a run."""
        if set(self.cfg.job_types) <= {'intern'}:
            return                        # every internship is marked in the list, so a job without the mark is none
        title_ok = Filters(self.cfg).title_ok
        wanted = [j for j in jobs if not j.get('employmentType') and title_ok(j.get('title')) and self.fits(j)]
        for job in wanted:
            if self._typed >= TYPE_LOOKUPS or 'details' in self._refused:
                self.tell('types', f'Naukri names the job type only on the job\'s own page, so the run opened '
                                   f'{self._typed:,} jobs to read it, the most it opens for that; jobs after that '
                                   f'were left out.')
                return
            self._typed += 1
            try:
                job.update(await self.details(http, None, job))
            except BoardError:
                continue

    def job(self, record: dict) -> dict:
        """One job of a search page -> the engine's job shape."""
        job_id = str(record.get('jobId'))
        holders = {p.get('type'): p.get('label') for p in record.get('placeholders') or [] if isinstance(p, dict)}
        kind, names = split_places(holders.get('location'))
        spots = [place(n) for n in names]
        link = SITE + (record.get('jdURL') or f'/job-listings-{job_id}')
        intern = 'duration' in holders           # an internship shows its length in the list
        posted = from_ms(record.get('createdDate'))
        money = pay(record.get('salaryDetail'), holders.get('salary'))
        review = record.get('ambitionBoxData') or {}
        skills = [s.strip() for s in (record.get('tagsAndSkills') or '').split(',') if s.strip()]
        low, high = number(record.get('minimumExperience')), number(record.get('maximumExperience'))
        return {
            'id': job_id,
            'company': clean(record.get('companyName')) or None,
            'companySlug': record.get('staticUrl') or None,
            'title': clean(record.get('title')),
            'location': names[0] if names else None,
            'otherLocations': names[1:],
            'places': spots,
            'workType': kind,
            'employmentType': 'intern' if intern else None,
            'salary': money.get('salary'),
            'postedAt': posted or first_posted(job_id),
            'dateOnly': not posted,
            'jobUrl': link,
            'applyUrl': link,
            'experience': clean(record.get('experienceText') or holders.get('experience')) or None,
            'experienceMin': low,
            'experienceMax': high,
            'skills': list(dict.fromkeys(skills)),
            'salaryMin': money.get('salaryMin'),
            'salaryMax': money.get('salaryMax'),
            'salaryCurrency': money.get('salaryCurrency'),
            'salaryPeriod': money.get('salaryPeriod'),
            'firstPostedAt': first_posted(job_id),
            'companyRating': rating(review.get('AggregateRating')),
            'companyReviews': number(review.get('ReviewsCount')) or None,
            'vacancies': number(record.get('vacancy')) or None,
            'walkIn': bool(record.get('walkinJob')),
            'summary': text_of(record.get('jobDescription')),
            'companyLogo': record.get('logoPathV3') or record.get('logoPath') or None,
        }

    # ---------- one job ----------

    async def _details(self, http, job_id: str):
        return await self.call(http, DETAILS.format(job=job_id), {'microsite': 'y'}, job_id, JOB_APP, 'details',
                               follow_redirects=False)     # a closed job answers 303: read it, do not follow it

    def enrich(self, job: dict, info: dict) -> dict:
        """A job with what its own page adds: description, job type, department, role, industry, education, key
        skills, applicants, the employer's website and, for a job copied from a career site, that site's page."""
        html = info.get('description') or None
        detail_places = [clean(p.get('label')) for p in info.get('locations') or [] if isinstance(p, dict)]
        detail_places = [p for p in detail_places if p]
        money = pay(info.get('salaryDetail'), None)
        company = info.get('companyDetail') or {}
        found = {
            **job,
            'skills': key_skills(info) or job.get('skills') or [],
            'industry': clean(info.get('industry')) or None,
            'role': clean(info.get('jobRole')) or None,
            'roleCategory': clean(info.get('roleCategory')) or None,
            'education': education(info.get('education')),
            'applicants': number(info.get('applyCount')),
            'views': number(info.get('viewCount')),
            'postedBy': None if info.get('consultant') is None else ('consultant' if info['consultant'] else 'company'),
            'vacancies': number(info.get('vacancy')) or job.get('vacancies'),
            'title': clean(info.get('title')) or job.get('title'),
            'employmentType': employment_type(info) or job.get('employmentType'),
            'workType': WORK_TYPE.get(str(info.get('wfhType'))) or job.get('workType'),
            'department': clean(info.get('functionalArea')) or None,
            'reference': clean(info.get('referenceCode')) or None,
            'companyWebsite': clean(company.get('websiteUrl')) or None,
            'applyUrl': info.get('applyRedirectUrl') or job.get('applyUrl'),
            'description': text_of(html),
            'descriptionHtml': html,
        }
        if money.get('salaryMin') or money.get('salaryMax'):
            found.update({k: money.get(k) for k in ('salaryMin', 'salaryMax', 'salaryCurrency', 'salaryPeriod')})
        posted = from_india_time(info.get('createdDate'))
        if job.get('dateOnly') and posted:           # an internship's list gives only its first day; its page, the date
            found.update({'postedAt': posted, 'dateOnly': False})
        if detail_places and not job.get('places'):
            found.update({'location': detail_places[0], 'otherLocations': detail_places[1:],
                          'places': [place(p) for p in detail_places]})
        return found

    async def details(self, http, session, job):
        response = await self._details(http, job['id'])
        if response.status_code != 200:
            return job                    # closed since the search, or not answered: saved without details
        info = answer(response).get('jobDetails')
        return self.enrich(job, info) if isinstance(info, dict) else job

    async def lookup(self, http, session, job_id):
        response = await self._details(http, job_id)
        if response.status_code == 303 or 'isExpiredJob' in response.text[:200]:
            return 'closed', None
        if response.status_code == 404:
            return 'not found', None
        info = answer(response).get('jobDetails')
        if not isinstance(info, dict):
            raise BoardError('the job answer had no job')
        return 'open', self.from_details(job_id, info)

    def from_details(self, job_id: str, info: dict) -> dict:
        """A job read from its own page alone, for a checked link."""
        names = [clean(p.get('label')) for p in info.get('locations') or [] if isinstance(p, dict) and p.get('label')]
        link = info.get('staticUrl') or f'{SITE}/job-listings-{job_id}'
        money = pay(info.get('salaryDetail'), None)
        posted = from_india_time(info.get('createdDate'))
        job = {
            'id': job_id,
            'company': clean((info.get('companyDetail') or {}).get('name')) or None,
            'companySlug': info.get('staticCompanyName') or None,
            'title': clean(info.get('title')),
            'location': names[0] if names else None,
            'otherLocations': names[1:],
            'places': [place(n) for n in names],
            'workType': WORK_TYPE.get(str(info.get('wfhType'))),
            'salary': money.get('salary'),
            'postedAt': posted or first_posted(job_id),
            'dateOnly': not posted,
            'jobUrl': link,
            'applyUrl': link,
            'experience': clean(info.get('experienceText')) or None,
            'experienceMin': number(info.get('minimumExperience')),
            'experienceMax': number(info.get('maximumExperience')),
            'skills': [],
            'salaryMin': money.get('salaryMin'),
            'salaryMax': money.get('salaryMax'),
            'salaryCurrency': money.get('salaryCurrency'),
            'salaryPeriod': money.get('salaryPeriod'),
            'firstPostedAt': first_posted(job_id),
            'vacancies': number(info.get('vacancy')) or None,
            'walkIn': bool(info.get('walkIn')),
            'summary': clean(info.get('shortDescription')).replace('|', '; ') or None,
            'companyLogo': info.get('clientLogo') or None,
        }
        return self.enrich(job, info)


def squeeze(text) -> str:
    return re.sub(r'[^a-z0-9]', '', str(text or '').lower())
