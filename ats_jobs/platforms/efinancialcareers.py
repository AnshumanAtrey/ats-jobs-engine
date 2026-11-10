"""
eFinancialCareers (efinancialcareers.com and its country sites: .co.uk, .sg, .hk, .de, .fr, .ch, -gulf.com and 23 more),
the job board for finance, banking, fintech and quant roles, read through the JSON APIs its own pages call. No login, no
key, no browser, no proxy. Measured from this laptop on 2026-11-10:

- GET https://job-search-api.efinancialcareers.com/v1/efc/jobs/search: one search of every country site at once. The
  sites differ only in what their pages send (countryCode2, currency, language), and every one answers the whole board:
  about 70,400 open jobs (Europe 40,000, North America 18,700, Asia 9,650, Australasia 1,215, the Gulf 790). What the
  search page sends: q (words, matched in titles, descriptions and companies, titles ranked first), location (one place,
  geocoded by the API with a radius: Zurich also finds Zug; a place it does not know is ignored, and a second one replaces
  the first), page and pageSize (up to 1,000), and filters.<name>=<value>|<value>: sectors, locationPath (Europe/United
  Kingdom), seniority, positionType, employmentType, workArrangementType, clientBrandNameFilter (a company), minSalary
  with salaryCurrency. countryCode2 lifts one country's jobs to the top (the pages send US when they know no other);
  empty, every country is listed by date. from + size stops at 10,000 ("Narrow the search with filters instead of paging
  deeper"). Its postedDate filter (ONE, THREE, SEVEN) cuts at a time that moves in steps (24.5 hours back at 10:28 UTC,
  23.2 at 10:17), so periods are checked here, on each job's own date. The x-api-key the pages send is not checked; it is
  read from the site's own script only if the API starts asking for it. 200 searches at 24 at once all answered: no limit
  seen. Every job carries its description (about 8 KB), so a page of 200 jobs is about 1.6 MB.
- Order: with no words, newest first by the listing date, except that UNPAID jobs (employers' career-site feeds) rank as
  if listed about 6 hours earlier: of the 10,000 newest on 2026-11-10, 3,025 sat behind an older job, none by more than
  5.9 hours, all UNPAID; among the PAID jobs alone and the UNPAID alone the order is exact to 3 seconds (global, UK and US
  lists, 10,000 each). filters.jobPaymentType=PAID (13,005 jobs) and UNPAID (57,402) split the list, so a newest-first
  search is read as one search of each kind, merged here; a kind a search does not split off can sit LATE behind. With
  words, by relevance,
  title matches first ("compliance": 934 title matches in the first 935 of 19,970; "quant": 288 in the first 775 of
  4,810), and sortBy is ignored: a search reads its pages until one holds no title match, then sorts them here.
- GET https://job-branding-facade.efinancialcareers.com/job/<job number>: one job: allocation_date (its first posting,
  the datePosted its own page gives Google on 6 of 6 pages), posted_date, the description as HTML, the company's ids, the
  job reference, the place, and the page's markup with the skills. 410 "Job has already expired" for a job the search
  still lists (44 of the second 1,000 newest), 404 for no such job. 81 calls a second at 32 at once.
- GET https://job-application.efinancialcareers.com/v1/jobs/<job id>/apply-information: the employer's own apply link
  (Workday, iCIMS) when the job is applied for there (988 of the 1,000 newest US jobs).
- GET https://company-profile-v2.efinancialcareers.com/efc/ui/companies/<brand id>?fallbackCompanyId=<company id>: the
  profile the job's page shows: the brand's own when it has one, else the account that posted the job, with its name and
  industry. HR & Recruitment marks a recruitment agency or job platform: Robert Half, Hays, Morgan McKinley, Selby
  Jennings (a brand's profile), and Workwise, which posts for German tax firms and banks under their names (the
  account's). Of the 1,480 newest jobs on 2026-11-10, 277 came from such an account, 361 from accounts in other
  industries, and 842 from accounts with no profile, among them agencies such as Michael Page and Gravitas: so agency is
  True, False, or None when the site has no profile to say.

Three dates. A paid job goes back to the top of the list every 6 days (90 of 196 jobs listed one morning had been
posted before, 85 of them exactly 6 days earlier), and the search's postedDate is that last listing; the job's own page
says "Posted 6 days ago" from posted_date, while its markup gives Google allocation_date, the first posting. postedAt is
allocation_date, so newest first and Posted in the last mean new jobs, and refreshedAt keeps the listing date. A job is
opened (one call) before it can count: that dates it, and drops the expired jobs the search still lists. Its listing
date is never earlier than its first posting, so a job whose listing date already fails a filter is not opened.

Pay: the text as the site shows it ("Competitive" on 82% of jobs), with the amounts the API stores when the text shows
pay (8%); an amount stored behind "Competitive" is left out, as the site leaves it out. Seniority is a filter of the
site's search and not a field of its jobs: a run that filters by it searches each level apart and names it on the row.

robots.txt of www.efinancialcareers.com disallows /v1, /v3 and /search on that host; the API hosts above have none (404).
"""
import asyncio
import heapq
import itertools
import json
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from importlib import resources
from urllib.parse import unquote, urlparse

from .. import places as regions
from ..http import BoardError, send
from ..inputs import _items, parse_bool, parse_choices
from ..rows import Filters, Term, classify_place, index, words
from .base import Board, Platform
from .cornerstone import text_of

SITE = 'https://www.efinancialcareers.com'
SEARCH = 'https://job-search-api.efinancialcareers.com/v1/efc/jobs/search'
DETAIL = 'https://job-branding-facade.efinancialcareers.com/job/{id}'
APPLY = 'https://job-application.efinancialcareers.com/v1/jobs/{id}/apply-information'
PROFILE = 'https://company-profile-v2.efinancialcareers.com/efc/ui/companies/{brand}'
EVERYONE = Board('*', 'eFinancialCareers')                      # the whole board, every company at once
LISTED = Board('efinancialcareers.com', 'eFinancialCareers', SITE)
PAGE = 200                    # jobs a call
WINDOW = 10_000               # rows one search reaches
LATE = timedelta(hours=6.5)   # how far behind its listing date a job can sit in the newest-first order (5.9 h seen)
JITTER = timedelta(minutes=1)  # how far behind it can sit among the jobs of its own kind, PAID or UNPAID (3 s seen)
KINDS = ('PAID', 'UNPAID')
CHUNK = 200                   # jobs handed on at a time, at most
MIN_CHUNK = 25                # and at least
OPEN_AT_ONCE = 32             # calls for single jobs (pages, profiles) at once: 81 a second at 32 answered
SCAN_JOBS = 1_000             # newest jobs read for a place the site cannot be asked about
NEVER = 2049                  # an expiry in this year or later is no expiry (2050-01-01)
AGENCY_INDUSTRY = 'HR_RECRUITMENT'
OLDEST = datetime.min.replace(tzinfo=timezone.utc)
HOST = re.compile(r'^efinancialcareers(?:-[a-z]+)?\.[a-z]{2,3}(?:\.[a-z]{2})?$')
JOB_PATH = re.compile(r'\.id(\d{4,10})$')
NOBODY = {'company': None, 'companySlug': None, 'companyWebsite': None}

# The site's seniority levels (its search filter's values): the form's value -> (the API's value, the site's name)
SENIORITY = {
    'intern-graduate': ('INTERN_GRADUATE', 'Intern/Graduate'), 'junior': ('JUNIOR', 'Junior'),
    'analyst': ('ANALYST', 'Analyst'), 'associate': ('ASSOCIATE_MID_LEVEL', 'Associate/Mid Level'),
    'avp-senior': ('AVP_SENIOR', 'AVP/Senior'), 'vp': ('VP_PRINCIPAL', 'Vice President/Principal'),
    'svp-head': ('SVP_HEAD_OF', 'Senior Vice President/Head of'), 'director': ('DIRECTOR', 'Director'),
    'managing-director': ('MANAGING_DIRECTOR', 'Managing Director'), 'c-suite': ('C_SUITE', 'C Suite'),
}
SENIORITY_WORDS = {
    'intern': 'intern-graduate', 'interns': 'intern-graduate', 'internship': 'intern-graduate',
    'graduate': 'intern-graduate', 'graduates': 'intern-graduate', 'grad': 'intern-graduate',
    'intern graduate': 'intern-graduate', 'entry level': 'junior', 'entry-level': 'junior', 'mid level': 'associate',
    'mid-level': 'associate', 'associate mid level': 'associate', 'avp': 'avp-senior', 'senior': 'avp-senior',
    'assistant vice president': 'avp-senior', 'avp senior': 'avp-senior', 'vice president': 'vp', 'principal': 'vp',
    'vice president principal': 'vp', 'svp': 'svp-head', 'senior vice president': 'svp-head', 'head of': 'svp-head',
    'head': 'svp-head', 'senior vice president head of': 'svp-head', 'md': 'managing-director',
    'managing director': 'managing-director', 'c suite': 'c-suite', 'c-level': 'c-suite', 'cxo': 'c-suite',
    'executive': 'c-suite',
}
SENIORITY_WORDS.update({key: key for key in SENIORITY})
SENIORITY_WORDS.update({code.lower(): key for key, (code, _) in SENIORITY.items()})
SENIORITY_WORDS.update({code.lower().replace('_', ' '): key for key, (code, _) in SENIORITY.items()})
SENIORITY_NAMES = {code: name for code, name in SENIORITY.values()}

# What people type for a sector, besides its name and its value in the form (data/efinancialcareers.tsv)
SECTOR_WORDS = {
    'quant': 'QUANTITATIVE_ANALYTICS', 'quants': 'QUANTITATIVE_ANALYTICS', 'quantitative': 'QUANTITATIVE_ANALYTICS',
    'investment banking': 'INVESTMENT_BANKING_M_A', 'ib': 'INVESTMENT_BANKING_M_A', 'm a': 'INVESTMENT_BANKING_M_A',
    'mergers and acquisitions': 'INVESTMENT_BANKING_M_A', 'private equity': 'PRIVATE_EQUITY_VENTURE_CAPITAL',
    'pe': 'PRIVATE_EQUITY_VENTURE_CAPITAL', 'venture capital': 'PRIVATE_EQUITY_VENTURE_CAPITAL',
    'vc': 'PRIVATE_EQUITY_VENTURE_CAPITAL', 'wealth management': 'PRIVATE_BANKING_WEALTH_MANAGEMENT',
    'private banking': 'PRIVATE_BANKING_WEALTH_MANAGEMENT', 'tech': 'INFORMATION_TECHNOLOGY', 'it': 'INFORMATION_TECHNOLOGY',
    'risk': 'RISK_MANAGEMENT', 'compliance': 'COMPLIANCE_LEGAL', 'legal': 'COMPLIANCE_LEGAL', 'hedge fund': 'HEDGE_FUNDS',
    'fixed income': 'DEBT_FIXED_INCOME', 'debt': 'DEBT_FIXED_INCOME', 'fx': 'FX_MONEY_MARKETS', 'forex': 'FX_MONEY_MARKETS',
    'money markets': 'FX_MONEY_MARKETS', 'hr': 'HR_RECRUITMENT', 'recruitment': 'HR_RECRUITMENT',
    'accounting': 'ACCOUNTING_FINANCE', 'finance': 'ACCOUNTING_FINANCE', 'audit': 'ACCOUNTING_FINANCE',
    'graduates': 'GRADUATES_INTERNSHIPS', 'internships': 'GRADUATES_INTERNSHIPS', 'sales': 'SALES_MARKETING',
    'marketing': 'SALES_MARKETING', 'investor relations': 'INVESTOR_RELATIONS_PR', 'pr': 'INVESTOR_RELATIONS_PR',
    'custody': 'GLOBAL_CUSTODY', 'ops': 'OPERATIONS',
}

# A job's contract (positionType) and hours (employmentType), as the list writes them or the job's page codes them
POSITION_KIND = {'permanent': None, 'contract': 'contract', 'self employed': 'contract', 'temporary': 'temporary',
                 'internships & graduate trainee': 'intern', 'internships and graduate trainee': 'intern'}
POSITION_NAMES = {'PERMANENT': 'Permanent', 'CONTRACT': 'Contract', 'SELF_EMPLOYED': 'Self Employed',
                  'TEMPORARY': 'Temporary', 'INTERNSHIPS_AND_GRADUATE_TRAINEE': 'Internships & Graduate Trainee'}
# the engine's job types -> what the site's own filter is sent, when a set of them can be said in one search
JOB_TYPE_FILTERS = {'contract': ('filters.positionType', ('CONTRACT', 'SELF_EMPLOYED')),
                    'temporary': ('filters.positionType', ('TEMPORARY',)),
                    'intern': ('filters.positionType', ('INTERNSHIPS_AND_GRADUATE_TRAINEE',)),
                    'part-time': ('filters.employmentType', ('PART_TIME',))}
WORK_KIND = {'remote': 'remote', 'temporarily remote': 'remote', 'hybrid': 'hybrid', 'flexible': 'hybrid',
             'in office': 'onsite', 'in-office': 'onsite'}
WORK_FILTERS = {'remote': ('REMOTE', 'TEMPORARILY_REMOTE'), 'hybrid': ('HYBRID', 'FLEXIBLE'), 'onsite': ('IN_OFFICE',)}

CURRENCIES = ('USD', 'GBP', 'EUR', 'CHF', 'HKD', 'SGD', 'INR', 'AUD', 'MYR', 'AED', 'JPY', 'SAR', 'CNY', 'QAR', 'CAD',
              'BHD', 'KWD', 'ZAR', 'SEK', 'DKK', 'NOK', 'NZD')       # the pay currencies of the site's filter
CURRENCY_SIGNS = (('HK$', 'HKD'), ('S$', 'SGD'), ('A$', 'AUD'), ('C$', 'CAD'), ('NZ$', 'NZD'), ('US$', 'USD'),
                  ('£', 'GBP'), ('€', 'EUR'), ('₹', 'INR'), ('¥', 'JPY'), ('$', 'USD'))


def search_params() -> dict:
    """What every search sends: no country lifted to the top, so every country is listed by date."""
    return {'countryCode2': '', 'page': 1, 'pageSize': PAGE}


def fold(text: str | None) -> str:
    """'Investment Banking/M&A' -> 'investment banking m a': lower case, words only."""
    return ' '.join(words((text or '').replace('&', ' ')))


@lru_cache(maxsize=1)
def taxonomy() -> tuple[dict, dict, dict, str | None]:
    """data/efinancialcareers.tsv -> (sector or sub-sector value -> its name, what is typed for a sector -> its value,
    country code -> the site's place value, the list's date)."""
    names, typed, countries, updated = {}, {}, {}, None
    text = resources.files('ats_jobs').joinpath('data/efinancialcareers.tsv').read_text(encoding='utf-8')
    for line in text.splitlines():
        if line.startswith('#'):
            if 'updated' in line and updated is None:
                updated = line.split('updated', 1)[1].strip().split(':')[0].strip()
            continue
        parts = line.split('\t')
        if len(parts) < 4:
            continue
        kind, value, name, form = parts[:4]
        if kind in ('SECTOR', 'SUBSECTOR'):
            names[value] = name
            if kind == 'SECTOR':
                for word in (value, name, form):
                    typed[fold(word)] = value
        elif kind == 'PLACE' and form:
            countries.setdefault(form, value)
    for word, value in SECTOR_WORDS.items():
        typed.setdefault(fold(word), value)
    return names, typed, countries, updated


def iso(value) -> str | None:
    """'2026-11-10T09:20:41.543Z' -> '2026-11-10T09:20:41Z'."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        t = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def stamp(job: dict) -> datetime:
    """A job's listing date as a time; the oldest time when it has none."""
    value = iso(job.get('listedAt') or job.get('postedAt'))
    return datetime.fromisoformat(value.replace('Z', '+00:00')) if value else OLDEST


def bound(read: dict, kinds) -> datetime:
    """The newest listing date a search can still hand on, from the oldest date read of each kind of job it holds (PAID,
    UNPAID): within a kind the order is exact, and a kind not seen yet can sit LATE behind what was read."""
    expected = set(kinds) | {k for k in KINDS if k in read}
    return max((read[kind] + JITTER) if kind in read else (read['any'] + LATE) for kind in expected)


def amount(value) -> float | int | None:
    try:
        number = float(str(value).replace(',', ''))
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return int(number) if number.is_integer() else number


def pay(record: dict) -> dict:
    """The pay fields: the text the site shows, and the amounts the API stores when that text shows pay. When it stores one
    amount for a range the text shows, the top of the range is not known."""
    text = ' '.join(str(record.get('salary') or '').split()) or None
    low, high = amount(record.get('minSalary')), amount(record.get('maxSalary'))
    if not text or not re.search(r'\d', text) or not (low or high):
        return {'salary': text, 'salaryMin': None, 'salaryMax': None, 'salaryCurrency': None, 'salaryPeriod': None}
    if low and high and low > high:
        low, high = high, low
    if low and low == high and len(set(re.findall(r'\d[\d,.]*', text))) > 1:
        high = None
    return {'salary': text, 'salaryMin': low, 'salaryMax': high,
            'salaryCurrency': record.get('salaryCurrency') or None, 'salaryPeriod': None}


def job_type(position: str | None, hours: str | None) -> str | None:
    """('Contract', 'Full time') -> 'contract'; ('PERMANENT', 'PART_TIME') -> 'part-time'."""
    kind = POSITION_KIND.get((position or '').replace('_', ' ').casefold())
    if kind:
        return kind
    hours = (hours or '').replace('_', ' ').casefold()
    return 'part-time' if hours == 'part time' else 'full-time' if hours == 'full time' else None


def sector_names(values) -> list[str]:
    """['ACCOUNTING_FINANCE/TAX', 'SALES_MARKETING'] -> ['Accounting & Finance: Tax', 'Sales & Marketing']: an Other
    sub-sector says nothing and is left out."""
    names = taxonomy()[0]
    out = []
    for value in values or []:
        if not isinstance(value, str) or not value:
            continue
        top = value.partition('/')[0]
        name = names.get(top) or top.replace('_', ' ').title()
        child = names.get(value) if '/' in value else None
        text = f'{name}: {child}' if child and child.casefold() != 'other' else name
        if text not in out:
            out.append(text)
    return out


def expiry(value) -> str | None:
    found = iso(value)
    return found if found and int(found[:4]) < NEVER else None


def skills_of(meta) -> list[str]:
    """The skills in the job page's markup ('NBFI,Credit,Finance'), each once."""
    try:
        content = json.loads(((meta or {}).get('microdata') or {}).get('content') or '{}')
    except (TypeError, ValueError, AttributeError):
        return []
    raw = content.get('skills') if isinstance(content, dict) else None
    found: list[str] = []
    for part in (raw.split(',') if isinstance(raw, str) else raw if isinstance(raw, list) else []):
        text = ' '.join(str(part).split())
        if text and text.casefold() not in (s.casefold() for s in found):
            found.append(text)
    return found


def read_pay(value, notes: list[str]) -> tuple[float | None, str | None]:
    """'£100k', '150,000 USD', 'HK$1m', 120000 -> (the amount, its currency or None)."""
    if value is None or value == '' or isinstance(value, bool):
        return None, None
    if isinstance(value, (int, float)):
        return (float(value), None) if value > 0 else (None, None)
    text = str(value).strip()
    currency = next((code for sign, code in CURRENCY_SIGNS if sign in text.upper()), None)
    code = next((c.upper() for c in re.findall(r'[A-Za-z]{3}', text) if c.upper() in CURRENCIES), None)
    number = re.search(r'(\d[\d,]*(?:\.\d+)?)\s*([kKmM])?', text)
    if not number:
        notes.append(f'Could not read minSalary "{value}" as an amount, such as 100000 or £100k, so pay is not filtered.')
        return None, None
    found = float(number.group(1).replace(',', '')) * {'k': 1_000, 'm': 1_000_000}.get((number.group(2) or '').lower(), 1)
    return (found, code or currency) if found > 0 else (None, None)


class EFinancialCareers(Platform):
    key = 'efinancialcareers'
    name = 'eFinancialCareers'
    site_word = 'company page'
    actor = 'efinancialcareers-jobs-search'
    board_example = 'https://www.efinancialcareers.com/jobs/at-S&P-Global'
    job_example = ('https://www.efinancialcareers.com/jobs-United_States-NY-New_York-Associate_Director_-_Non-Bank_'
                   'Financial_Institutions_New_York_City_Denver_or_Toronto.id24879038')
    concurrency = 16              # companies read at once (and half the connection pool); jobs open OPEN_AT_ONCE at a time
    server_search = True          # pages() gets each word: the site searches it, and the titles are matched here
    newest_first = True           # pages come newest listed first, with or without words (sorted here)
    server_filters = True         # place, sector, seniority, job and work type and pay go to the site's own search
    never = ('department', 'team', 'offersEquity', 'companyWebsite')
    extra = ('salaryMin', 'salaryMax', 'salaryCurrency', 'salaryPeriod', 'contractType', 'seniority', 'sectors', 'skills',
             'agency', 'postedBy', 'postedByIndustry', 'summary', 'refreshedAt')
    filter_words = {'sector': ('sector', 'clear Sector'),
                    'minSalary': ('minimum pay', 'type a lower Minimum pay or leave it empty'),
                    'excludeAgencies': ('no-agency filter', 'untick No agency jobs')}

    def __init__(self):
        self.cfg = None
        self.filters: Filters | None = None
        self.places: list[dict] = []          # one search each: {'filters.locationPath': ...} or {'location': text}
        self.scan_places: list[str] = []      # places the site cannot be asked about: its newest jobs are checked here
        self.levels: list[str] = []           # seniority values asked for: one search each
        self.sectors: list[str] = []          # sector values asked for
        self.min_pay: tuple[float, str] | None = None
        self.exclude_agencies = False
        self.server: dict = {}                # the site's own filters, sent with every search
        self.key_header: dict = {}            # x-api-key, only once the API asks for it
        self.base_total: int | None = None
        self.unknown: dict[str, bool] = {}    # a place sent as location -> whether the site ignores it
        self.profiles: dict = {}              # (brand, account) -> the profile's (name, industry value, industry name)
        self.noted: dict[str, int] = {}       # a note's key -> its place in cfg.notes
        self.expired = 0                      # jobs the search listed whose own page says they expired or are gone
        self.unreadable = 0                   # jobs whose own page could not be read
        self.windows: dict[str, int] = {}
        self._sem: asyncio.Semaphore | None = None

    # ---------- what people type ----------

    def boards(self):
        return [LISTED], taxonomy()[3]

    def read_link(self, text):
        """An eFinancialCareers link (any of its country sites) -> (board, job number). A job page gives its number, a
        company's jobs page (/jobs/at-<company>) the company, any other page of the site (None, None)."""
        text = text.strip().rstrip('.,;:!?')
        link = text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}'
        parsed = urlparse(link)
        host = re.sub(r'^(?:www|m)\.', '', (parsed.hostname or '').lower()) if ' ' not in text else ''
        if not HOST.match(host):
            return None
        path = unquote(parsed.path).rstrip('/')
        found = JOB_PATH.search(path)
        if found:
            return EVERYONE.key, found.group(1)
        company = re.search(r'/jobs/at-([^/]+)$', path)
        if company:
            return company.group(1).replace('-', ' ').strip() or None, None
        return None, None

    def search_link(self, text):
        """A search page of the site: /jobs/quant/in-london, /jobs/in-singapore, /jobs/risk-analyst -> (words, places)."""
        link = text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}'
        parts = [unquote(p) for p in urlparse(link).path.split('/') if p]
        if not parts or parts[0].lower() not in ('jobs', 'emploi', 'lavoro'):
            return [], []
        asked, where = [], []
        for part in parts[1:]:
            if part.lower().startswith('in-'):
                where.append(part[3:].replace('-', ' ').strip())
            elif not part.lower().startswith('at-'):
                asked.append(part.replace('-', ' ').strip())
        return [a for a in asked if a], [w for w in where if w]

    def guess_board(self, name):
        return ' '.join(name.split()) or None

    def read_input(self, raw, cfg):
        """The site's own fields: seniority, sector, minimum pay with its currency, and no agency jobs."""
        self.cfg = cfg
        notes = cfg.notes
        chosen = parse_choices(raw.get('seniority'), 'seniority', tuple(SENIORITY), SENIORITY_WORDS, notes)
        self.levels = [SENIORITY[key][0] for key in chosen]
        self.sectors = self.read_sectors(raw.get('sector'), notes)
        found, currency = read_pay(raw.get('minSalary'), notes)
        picked = str(raw.get('salaryCurrency') or '').strip().upper()
        if picked and picked not in CURRENCIES:
            notes.append(f'salaryCurrency has no option {raw.get("salaryCurrency")}, so it was left out. The options are '
                         f'{", ".join(CURRENCIES)}.')
            picked = ''
        currency = currency or picked or None
        if found and not currency:
            notes.append(f'minSalary {raw.get("minSalary")} names no currency, so pay is not filtered: pick one in '
                         f'salaryCurrency or type it with the amount, such as 100000 GBP or £100k.')
        self.min_pay = (found, currency) if found and currency else None
        self.exclude_agencies = parse_bool(raw.get('excludeAgencies'), False)

    @staticmethod
    def read_sectors(value, notes: list[str]) -> list[str]:
        """Typed sectors -> the site's values: a form value (quantitative-analytics), the site's name (Quantitative
        Analytics), its value (QUANTITATIVE_ANALYTICS, or a sub-sector such as ASSET_MANAGEMENT/PORTFOLIO_MANAGER) or a
        common word (quant, investment banking, private equity)."""
        names, typed, _, _ = taxonomy()
        found, unknown = [], []
        for token in _items(value):
            text = ' '.join(token.split())
            key = text.upper().replace(' ', '_').replace('-', '_') if '/' in text else None
            code = key if key and key in names else typed.get(fold(text))
            if code is None:
                unknown.append(text)
            elif code not in found:
                found.append(code)
        if unknown:
            notes.append(f'sector has no option {", ".join(unknown)}, so it was left out. Pick from the list or type a '
                         f'sector as eFinancialCareers names it, such as Quantitative Analytics.')
        return found

    # ---------- the run's settings ----------

    def note(self, text: str, key: str | None = None) -> None:
        """A note for the run's message and OUTPUT: one per key, the newest text kept."""
        if self.cfg is None:
            return
        key = key or text
        if key in self.noted:
            self.cfg.notes[self.noted[key]] = text
        else:
            self.noted[key] = len(self.cfg.notes)
            self.cfg.notes.append(text)

    def search_all(self, boards, cfg):
        self.cfg = cfg
        self.settle(cfg)
        return [EVERYONE]

    def settle(self, cfg) -> None:
        """What the site's own search is asked, and the places each search covers."""
        server: dict = {}
        if self.sectors:
            server['filters.sectors'] = '|'.join(self.sectors)
        if self.min_pay:
            server['filters.minSalary'] = f'{self.min_pay[0]:g}'
            server['filters.salaryCurrency'] = self.min_pay[1]
        if cfg.work_types:
            server['filters.workArrangementType'] = '|'.join(v for kind in cfg.work_types for v in WORK_FILTERS[kind])
            self.note('eFinancialCareers states remote, hybrid or in-office on about 1 job in 25, so only those jobs can '
                      'match Work type.', key='work')
        types = set(cfg.job_types)
        if types == {'full-time'}:
            server.update({'filters.positionType': 'PERMANENT', 'filters.employmentType': 'FULL_TIME'})
        elif types and all(t in JOB_TYPE_FILTERS for t in types) and len({JOB_TYPE_FILTERS[t][0] for t in types}) == 1:
            field = JOB_TYPE_FILTERS[next(iter(types))][0]
            server[field] = '|'.join(v for t in cfg.job_types for v in JOB_TYPE_FILTERS[t][1])
        self.server = server
        gulf = [p for p in cfg.locations if fold(p) in ('gulf', 'the gulf')]
        if gulf:
            cfg.locations = list(dict.fromkeys('GCC' if p in gulf else p for p in cfg.locations))
            self.note('Read "Gulf" in location as the GCC: the UAE, Saudi Arabia, Qatar, Kuwait, Oman and Bahrain.')
        countries = taxonomy()[2]
        paths, cities, scans, empty = [], [], [], []
        for text in cfg.locations:
            toks = regions.tokens(text)
            if regions.world_query(toks) or regions.entry_kind(toks) == 'remote':
                scans.append(text)
                continue
            kind, codes = classify_place(text)
            if kind in ('country', 'region'):
                found = [countries[c] for c in sorted(codes) if c in countries]
                paths += [p for p in found if p not in paths]
                if not found:
                    empty.append(text)
                continue
            cities.append(text)
        self.places = ([{'filters.locationPath': '|'.join(paths)}] if paths else []) + [{'location': c} for c in cities]
        self.scan_places = scans
        if empty:
            self.note(f'eFinancialCareers lists no jobs in {", ".join(empty)}.', key='empty places')
        if scans:
            self.note(f'Every eFinancialCareers job names a city, so {", ".join(scans)} was looked for in its '
                      f'{SCAN_JOBS:,} newest jobs only.', key='scan')

    def settings(self):
        return {'seniority': [SENIORITY_NAMES[c] for c in self.levels] or None,
                'sector': [taxonomy()[0].get(c, c) for c in self.sectors] or None,
                'minSalary': f'{self.min_pay[0]:g} {self.min_pay[1]}' if self.min_pay else None,
                'excludeAgencies': self.exclude_agencies or None,
                'eFinancialCareersSearch': {**self.server, 'places': self.places} if (self.server or self.places) else None}

    def fits(self, job: dict) -> bool | str:
        """The site's own filters, checked on what each job shows: sector, minimum pay, no agencies. Pay the site stores but
        does not show was decided by the site's own filter, as on the site."""
        if self.sectors:
            have = [s for s in job.get('sectorValues') or [] if isinstance(s, str)]
            if not any(s == c or s.startswith(c + '/') for s in have for c in self.sectors):
                return 'sector'
        if self.min_pay:
            top = job.get('salaryMax') or job.get('salaryMin')
            if top and job.get('salaryCurrency') == self.min_pay[1] and top < self.min_pay[0]:
                return 'minSalary'
        if self.exclude_agencies and job.get('agency') is True:
            return 'excludeAgencies'
        return True

    # ---------- calls ----------

    def sem(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(OPEN_AT_ONCE)
        return self._sem

    async def api_key(self, http) -> dict:
        """The x-api-key the site's own pages send, read from its script, for an API that starts asking for it."""
        try:
            page = (await send(http, 'GET', SITE + '/jobs')).text
            runtime = re.search(r'src="(/apps/host/shell/browser/runtime\.[0-9a-f]+\.js)"', page).group(1)
            main = re.search(r'src="(/apps/host/shell/browser/main\.[0-9a-f]+\.js)"', page).group(1)
            table = re.search(r'\{((?:\d+:"[0-9a-f]+",?)+)\}\[\w+\]\+"\.m?js"', (await send(http, 'GET', SITE + runtime)).text)
            hashes = dict(re.findall(r'(\d+):"([0-9a-f]+)"', table.group(1)))
            for number in re.findall(r'\.e\((\d+)\)', (await send(http, 'GET', SITE + main)).text):
                if number in hashes:
                    script = (await send(http, 'GET', f'{SITE}/apps/host/shell/browser/{number}.{hashes[number]}.js')).text
                    found = re.search(r'jobSearchUrl:"https://job-search-api\.efinancialcareers\.com",[^{}]*?'
                                      r'jobSearchApiKey:"([A-Za-z0-9]+)"', script)
                    if found:
                        return {'x-api-key': found.group(1)}
        except (AttributeError, BoardError):
            pass
        return {}

    async def search(self, http, params: dict) -> dict:
        """One search call -> its answer; past the 10,000-row window, {'beyond': True}."""
        response = await send(http, 'GET', SEARCH, params=params, headers=self.key_header or None)
        if response.status_code in (401, 403) and not self.key_header:
            self.key_header = await self.api_key(http)
            if self.key_header:
                response = await send(http, 'GET', SEARCH, params=params, headers=self.key_header)
        if response.status_code == 400 and 'maximum result window' in response.text:
            return {'beyond': True}
        if response.status_code != 200:
            raise BoardError(f'the search answered HTTP {response.status_code}')
        try:
            data = response.json()
        except ValueError as exc:
            raise BoardError('the search answer was not JSON') from exc
        if not isinstance(data, dict) or not isinstance(data.get('data'), list):
            raise BoardError('the search answer had no job list')
        return data

    async def total(self, http, params: dict) -> int:
        data = await self.search(http, {**params, 'page': 1, 'pageSize': 1})
        return int(((data.get('meta') or {}).get('totalResults')) or 0)

    async def job_page(self, http, number: str) -> tuple[int, dict | None]:
        """One job's record from the site's job page service: (200, record), (410, None) expired, (404, None) none."""
        response = await send(http, 'GET', DETAIL.format(id=number))
        if response.status_code in (400, 404, 410):
            return (410 if response.status_code == 410 else 404), None
        if response.status_code != 200:
            raise BoardError(f'the job page answered HTTP {response.status_code}')
        try:
            data = response.json()
        except ValueError as exc:
            raise BoardError('the job page was not JSON') from exc
        record = data.get('data') if isinstance(data, dict) else None
        if not isinstance(record, dict):
            raise BoardError('the job page had no job')
        return 200, {**record, '_meta': data.get('meta')}

    async def profile(self, http, brand: str | None, company: str | None) -> tuple | None:
        """The profile a job's page shows (the brand's, else the posting account's) -> (name, industry value, industry
        name); None when there is none. Read once a run for each brand and account."""
        if not (brand or company):
            return None
        key = (brand, company)
        if key not in self.profiles:
            self.profiles[key] = asyncio.ensure_future(self._profile(http, brand, company))
        return await asyncio.shield(self.profiles[key])

    async def _profile(self, http, brand, company):
        try:
            async with self.sem():
                response = await send(http, 'GET', PROFILE.format(brand=brand or company),
                                      params={'fallbackCompanyId': company} if company and brand else None)
            if response.status_code != 200:
                return None
            data = response.json().get('data') or {}
        except (BoardError, ValueError, AttributeError):
            return None
        industry = data.get('industry') if isinstance(data.get('industry'), dict) else {}
        name = ' '.join(str(data.get('name') or '').split()) or None
        return (name, industry.get('id'), industry.get('en') or industry.get('id')) if (name or industry.get('id')) else None

    async def who(self, http, jobs: list[dict]) -> None:
        """Each job's poster, from the profile its page shows: postedBy, its industry, and whether it is an agency."""
        found = await asyncio.gather(*[self.profile(http, job.get('brandId'), job.get('accountId')) for job in jobs])
        for job, profile in zip(jobs, found):
            job['postedBy'] = profile[0] if profile else None
            job['postedByIndustry'] = profile[2] if profile else None
            job['agency'] = None if not profile or not profile[1] else profile[1] == AGENCY_INDUSTRY

    # ---------- jobs ----------

    def job(self, record: dict, level: str | None = None) -> dict:
        """A job from the search's list, dated by its listing until it is opened."""
        where = record.get('jobLocation') or {}
        name = where.get('displayName') or ', '.join(p for p in (where.get('city'), where.get('country')) if p)
        listed = iso(record.get('postedDate'))
        path = str(record.get('detailsPageUrl') or '')
        link = SITE + path if path.startswith('/') else None
        company = ' '.join(str(record.get('companyName') or record.get('clientBrandName') or '').split())
        sectors = [s for s in record.get('sectors') or [] if isinstance(s, str)]
        return {
            'id': str(record.get('jobId') or ''),
            'key': record.get('id'),
            'company': company or 'Not disclosed',
            'companySlug': None,
            'title': ' '.join(str(record.get('title') or '').split()),
            'location': name or None,
            'places': [{'name': name, 'city': where.get('city'), 'region': where.get('state'),
                        'country': where.get('country')}] if name else [],
            'workType': WORK_KIND.get(str(record.get('workArrangementType') or '').casefold()),
            'employmentType': job_type(record.get('positionType'), record.get('employmentType')),
            **pay(record),
            'postedAt': listed,
            'listedAt': listed,
            'expiresAt': expiry(record.get('expirationDate')),
            'jobUrl': link,
            'applyUrl': link,
            'contractType': record.get('positionType') or None,
            'seniority': SENIORITY_NAMES.get(level) if level else None,
            'sectors': sector_names(sectors),
            'sectorValues': sectors,
            'skills': [],
            'agency': None,
            'postedBy': None,
            'postedByIndustry': None,
            'summary': ' '.join(str(record.get('jobSummary') or record.get('summary') or '').split()) or None,
            'refreshedAt': listed,
            'paid': record.get('jobPaymentType'),
        }

    def enrich(self, job: dict, record: dict) -> dict:
        """A job and its own page's record: its first posting, description, reference, skills and its company's ids."""
        first = iso(record.get('allocation_date')) or iso(record.get('posted_date'))
        listed = job.get('listedAt')
        html = record.get('description') or None
        slug = re.search(r'/jobs/at-([^/?#]+)', str((record.get('urls') or {}).get('brand_url') or ''))
        return {**job,
                'postedAt': min(first, listed) if first and listed else first or listed,
                'description': text_of(html), 'descriptionHtml': html,
                'reference': record.get('job_reference') or None,
                'skills': skills_of(record.get('_meta')),
                'brandId': (record.get('brand') or {}).get('brand_id'), 'accountId': record.get('company_id'),
                'companySlug': unquote(slug.group(1)) if slug else None,
                'expiresAt': expiry(record.get('expiration_date')) or job.get('expiresAt')}

    async def opened(self, http, job: dict) -> dict | None:
        """The job dated by its first posting; None when its own page says it expired or is gone, or cannot be read."""
        try:
            async with self.sem():
                status, record = await self.job_page(http, job['id'])
        except BoardError:
            self.unreadable += 1
            return None
        if record is None:
            self.expired += 1
            return None
        return self.enrich(job, record)

    def worth_opening(self, job: dict, now: datetime, term: str | None) -> bool:
        """A job is opened when it passes every filter with its listing date, the newest its first posting can be."""
        if self.filters is None:
            self.filters = Filters(self.cfg)
        return self.filters.misses(job, now, term is not None) == () and self.fits(job) is True

    async def handed(self, http, chunk: list[dict], term: str | None) -> list[dict]:
        """A chunk in listing order with every job worth it opened; the others go as they are (whatever their first posting,
        they miss a filter), and a job whose own page says it expired, or cannot be read, is left out."""
        now = datetime.now(timezone.utc)
        wanted = sorted(i for i, job in enumerate(chunk) if self.cfg is not None and self.worth_opening(job, now, term))

        async def one(job: dict) -> dict | None:
            found = await self.opened(http, job)
            # its poster, when it still passes once dated by its first posting: the others are never kept
            if found is not None and self.worth_opening(found, now, term):
                await self.who(http, [found])
            return found
        done = dict(zip(wanted, await asyncio.gather(*[one(chunk[i]) for i in wanted])))
        if self.expired:
            self.note(f'{self.expired:,} jobs eFinancialCareers still listed had expired (their own pages say so), so they '
                      f'were left out.', key='expired')
        if self.unreadable:
            self.note(f'The pages of {self.unreadable:,} jobs could not be read, so they were left out: their first '
                      f'posting is not known.', key='unreadable')
        return [done[i] if i in done else job for i, job in enumerate(chunk) if i not in done or done[i] is not None]

    # ---------- searches ----------

    def searches(self, session: dict) -> list[tuple[dict, str | None]]:
        """(what the site is asked, seniority level) for each search of a run: one per place asked and per level."""
        base = {**search_params(), **self.server}
        if session.get('brand'):
            base['filters.clientBrandNameFilter'] = session['brand']
        places = [*self.places, *([{'scan': True}] if self.scan_places else [])] or [{}]
        return [({**base, **place, **({'filters.seniority': level} if level else {})}, level)
                for place in places for level in (self.levels or [None])]

    async def known(self, http, place: str) -> bool:
        """Whether the site reads a place sent as location: one it does not know leaves the search unfiltered."""
        if place not in self.unknown:
            if self.base_total is None:
                self.base_total = await self.total(http, search_params())
            self.unknown[place] = await self.total(http, {**search_params(), 'location': place}) >= self.base_total
            if self.unknown[place]:
                self.note(f'eFinancialCareers does not know the place {place}, so its {SCAN_JOBS:,} newest jobs were '
                          f'checked for it by name.', key=f'unknown {place}')
        return not self.unknown[place]

    async def stream(self, http, params: dict, level: str | None):
        """The pages of one search, in the site's order, as jobs."""
        params = dict(params)
        scan = bool(params.pop('scan', False))
        if 'location' in params and not await self.known(http, params['location']):
            params.pop('location')
            scan = True
        cap = SCAN_JOBS if scan else WINDOW
        page, read = 1, 0
        while read < cap:
            size = min(PAGE, cap - read)
            data = await self.search(http, {**params, 'page': page, 'pageSize': size})
            if data.get('beyond'):
                return
            records = [r for r in data['data'] if isinstance(r, dict) and r.get('jobId')]
            total = int(((data.get('meta') or {}).get('totalResults')) or 0)
            yield [self.job(r, level) for r in records]
            read += size
            page += 1
            if not records or read >= total:
                return
            if read >= WINDOW:
                self.window(params, total)
                return

    def window(self, params: dict, total: int) -> None:
        self.incomplete = True
        self.windows[params.get('q') or params.get('location') or 'every job'] = total
        listed = ', '.join(f'{what} had {count:,}' for what, count in self.windows.items())
        self.note(f'eFinancialCareers shows the {WINDOW:,} first jobs of one search: {listed}, so the rest were not '
                  f'reached. Narrow it by place, sector or seniority to reach them.', key='window')

    async def ranked(self, http, searches: list, term: str) -> list[dict]:
        """Every job of the searches for one word, read until a page holds no title match (the site ranks title matches
        first), or every job they find when descriptions are searched too; newest listed first."""
        wanted = Term(term)
        every = bool(self.cfg is not None and self.cfg.search_descriptions)
        found: dict[str, dict] = {}

        async def one(params, level):
            async for jobs in self.stream(http, {**params, 'q': term}, level):
                hit = False
                for job in jobs:
                    found.setdefault(job['id'], job)
                    hit = hit or wanted.in_title(index(job['title']))
                if not hit and not every:
                    return
        await asyncio.gather(*[one(params, level) for params, level in searches])
        return sorted(found.values(), key=stamp, reverse=True)

    async def by_date(self, http, searches: list, size: int = CHUNK):
        """The jobs of every search, newest listed first: a job is handed on once no search can still list a newer one."""
        split = [({**params, 'filters.jobPaymentType': kind}, level, (kind,)) for params, level in searches for kind in KINDS]
        streams = [self.stream(http, params, level) for params, level, _ in split]
        kinds = [k for _, _, k in split]
        low: dict[int, dict | None] = dict.fromkeys(range(len(streams)))  # open searches -> oldest date read, by kind
        held: list = []                                                      # (-listing time, order, job)
        seen: set[str] = set()
        order = itertools.count()
        try:
            while True:
                if not low:
                    line = OLDEST                       # every search read to its end: the rest goes
                elif any(v is None for v in low.values()):
                    line = None                         # a search not read yet: nothing can go
                else:
                    line = max(bound(v, kinds[i]) for i, v in low.items())
                ready = []
                while held and line is not None and len(ready) < size and -held[0][0] >= line.timestamp():
                    ready.append(heapq.heappop(held)[2])
                if ready:
                    yield ready
                    continue
                if not low:
                    return
                pick = next((i for i, v in low.items() if v is None), None)
                if pick is None:
                    pick = max(low, key=lambda i: bound(low[i], kinds[i]))   # the search that holds the line back
                try:
                    jobs = await anext(streams[pick])
                except StopAsyncIteration:
                    del low[pick]
                    continue
                read = low[pick] or {}
                for job in jobs:
                    when = stamp(job)
                    kind = job.get('paid') if job.get('paid') in KINDS else None
                    read['any'] = min(read.get('any', when), when)
                    if kind:
                        read[kind] = min(read.get(kind, when), when)
                    if job['id'] and job['id'] not in seen:
                        seen.add(job['id'])
                        heapq.heappush(held, (-when.timestamp(), next(order), job))
                low[pick] = read or {'any': OLDEST}
        finally:
            for stream in streams:
                await stream.aclose()

    def chunk(self) -> int:
        """Jobs handed on at a time: half of maxJobs, so a run opens few jobs past the last one it needs."""
        wanted = self.cfg.max_jobs if self.cfg is not None else None
        return CHUNK if not wanted else max(MIN_CHUNK, min(CHUNK, wanted // 2))

    async def pages(self, http, session, term, days):
        searches = self.searches(session)
        size = self.chunk()
        if term:
            jobs = await self.ranked(http, searches, term)
            for i in range(0, len(jobs), size):
                yield await self.handed(http, jobs[i:i + size], term)
        else:
            async for chunk in self.by_date(http, searches, size):
                yield await self.handed(http, chunk, term)

    async def open(self, http, board):
        if board.key in (EVERYONE.key, LISTED.key):
            return {'everyone': True}
        name = await self.company(http, board.key)
        if name is None:
            return None
        if name.casefold() != board.key.casefold():
            self.note(f'Read the company "{board.key}" as {name}' + ('' if name.endswith('.') else '.'), key=f'company {board.key}')
        return {'brand': name}

    async def company(self, http, typed: str) -> str | None:
        """A typed company -> the name the site files its jobs under: the name itself when the site knows it, else the
        company of the jobs the site finds for it whose name holds every typed word."""
        typed = ' '.join(typed.split())
        if await self.total(http, {**search_params(), 'filters.clientBrandNameFilter': typed}):
            return typed
        data = await self.search(http, {**search_params(), 'q': typed, 'pageSize': 1, 'facets': 'clientBrandNameFilter'})
        facets = {f.get('facetName'): f.get('facetResults') or [] for f in (data.get('meta') or {}).get('facetQueryResults') or []}
        names = [r.get('value') for r in facets.get('clientBrandNameFilter', []) if isinstance(r.get('value'), str)]
        wanted = Term(typed)
        same = [n for n in names if fold(n) == fold(typed)]
        holding = [n for n in names if wanted.in_title(index(n))]
        return (same or holding or [None])[0]

    # ---------- details and links ----------

    async def details(self, http, session, job):
        """The employer's own apply link, when the job is applied for on its site."""
        if not job.get('key'):
            return job
        response = await send(http, 'GET', APPLY.format(id=job['key']))
        try:
            data = (response.json().get('data') or {}) if response.status_code == 200 else {}
        except (ValueError, AttributeError):
            data = {}
        outside = data.get('external_job_application_url')
        return {**job, 'applyUrl': outside} if data.get('is_external_job_application') and outside else job

    async def lookup(self, http, session, job_id):
        if not job_id.isdigit():
            return 'not found', dict(NOBODY)
        status, record = await self.job_page(http, job_id)
        if record is None:
            return ('closed' if status == 410 else 'not found'), dict(NOBODY)
        job = self.enrich(self.from_page(record, job_id), record)
        await self.who(http, [job])
        return 'open', job

    def from_page(self, record: dict, number: str) -> dict:
        """A job from its own page's record alone, for a checked link: the fields of the list that its page has too."""
        place = record.get('location') or {}
        urls = record.get('urls') or {}
        link = urls.get('canonical_url') or urls.get('job_primary_url') or (record.get('seo_urls') or {}).get('us_US')
        sectors = []
        for s in record.get('sectors') or []:
            if isinstance(s, dict) and (s.get('sub_sector') or s.get('sector')):
                sectors.append(str(s.get('sub_sector') or s.get('sector')).replace('__', '/'))
        job = self.job({
            'jobId': number, 'id': record.get('jobId'), 'title': record.get('title'),
            'companyName': (record.get('brand') or {}).get('name'),
            'jobLocation': {'city': place.get('city'), 'state': place.get('state'), 'country': place.get('country'),
                            'displayName': ', '.join(p for p in (place.get('city'), place.get('country')) if p)},
            'postedDate': record.get('posted_date'), 'salary': record.get('salary'),
            'positionType': POSITION_NAMES.get(record.get('position_type'), record.get('position_type')),
            'employmentType': record.get('employment_type'), 'expirationDate': record.get('expiration_date'),
            'sectors': sectors, 'detailsPageUrl': urlparse(link).path if link else None, 'summary': record.get('summary'),
        })
        return job
