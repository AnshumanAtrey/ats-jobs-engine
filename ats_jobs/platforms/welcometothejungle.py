"""
Welcome to the Jungle (welcometothejungle.com, with the UK and US sites that were Otta), read through the search index
its own pages load and the job API they call. No login, no key of ours, no browser. Measured from this laptop on
2026-11-10:

- One Algolia index holds every open job of every country site: wttj_jobs_production_en, 84,849 jobs at 4,515
  companies (offices in France 54,471, the US 18,024, the UK 5,573, Canada 1,818, Germany 1,078, Spain 857, Ireland 590,
  the Netherlands 513, Italy 289, Belgium 260 and 239 more countries). The _fr, _es, _cs and _sk indexes hold the same
  jobs with the site's words in those languages. Its replica wttj_jobs_production_en_published_at_desc lists them
  newest first by published_at, the moment the job went live, which is the job page's JobPosting datePosted (9 of 9
  pages with markup). About 3,000 jobs go live a day: 12,500 in 7 days, 44,000 in 30. otta.com/jobs now redirects to
  uk.welcometothejungle.com ("Welcome To The Jungle UK (formerly Otta)"), the Otta app says "We're moving" to
  welcometothejungle.com, and Otta's UK and US companies have their jobs in this index (Monzo 51, Airbnb 78, Carwow 34,
  Funding Circle 28).
- The key is the site's own search-only key, handed to every visitor by GET https://www.welcometothejungle.com/api/env
  (PUBLIC_ALGOLIA_APPLICATION_ID, PUBLIC_ALGOLIA_API_KEY_CLIENT): rights search and browse on wttj_jobs_production*,
  wk_cms_organizations_production* and two more, valid only with Referer https://www.welcometothejungle.com/ (403
  "Method not allowed with this referer" without it). It is read once a run, never stored in this code. /api/env
  answers any user agent but the bare "Mozilla/5.0" (CloudFront 403).
- A search reaches 1,000 hits (paginationLimitedTo) at 1,000 a call, so the newest-first replica is paged on the
  posting time: the next page asks for published_at_timestamp at or below the last one seen and drops the jobs of that
  second already read. No second held more than 21 jobs in the whole index. Algolia applies these filters itself: words
  in the title (restrictSearchableAttributes name, plurals folded and every word a prefix, so engineers finds Engineer),
  the posting time, the office's country code, city, district or region (case and accents do not matter, punctuation
  does: "Saint-Denis" 270 jobs, "saint denis" none), contract type, remote policy, years of experience, the ad's language,
  company size, industry and company. A count past about 10,000 matches is Algolia's estimate (exhaustiveNbHits false:
  France 73,890 for 54,471 counted), so the engine counts the jobs it reads.
- Details: GET https://api.welcometothejungle.com/api/v3/organizations/<company slug>/jobs/<job slug>: the description,
  the experience wanted and the recruitment process as HTML, skills, tools, the start date, the company's website and,
  for a job posted through an outside ATS, its apply link. A closed job answers status archived with archived_at; a job
  that never existed answers 404. 200 calls at 16 at once: 55 a second, no refusal, 10 KB each.
- The job pages of welcometothejungle.com sit behind AWS WAF, which answered 202 with an empty challenge
  (x-amzn-waf-action) after 5 quick page loads from one address, while /api/env, the search page and the API host kept
  answering 200. The actor reads no job page.

What the site states: contract type on every job (full_time 65,965, internship 9,590, temporary 5,100, freelance 1,929,
apprenticeship 1,201, other 500, part_time 478, vie 60, graduate_program 22, volunteer 3), remote policy on 53% (no
15,777, partial 12,511, punctual 10,661, fulltime 6,359; unknown 39,541, of which 21 say remote or hybrid in the title),
pay on 34% (USD 14,179, EUR 13,264, GBP 890; yearly, monthly or daily), years of experience on 63% as a band
(LESS_THAN_6_MONTHS to MORE_THAN_15_YEARS; the index stores the band's lower end, each with one upper end), education on
20%, company headcount on 99%, industry on 97%, a summary and three key missions on every job. 62% of ads are written in
French, 37% in English.

Copies: a company's same title at the same office is one job here, as on the other job boards, unless the job's own
address (the place in its slug: Keolis lists 41 ambulance jobs at its Courbevoie head office whose slugs name Clisson,
Vertou or Poitiers) differs. That folds 2,607 of 84,849 jobs (3.1%), mostly reposts (ERA Immobilier's "Commercial(e)
immobilier" 18 times in Paris over three months) and store chains' openings in one town.
"""
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from urllib.parse import parse_qs, unquote, urlencode, urlparse

from .. import places as regions
from ..http import BoardError, send
from ..rows import Place, Term, dedupe_key, index, plain, terms, words
from .base import Board, Platform
from .cornerstone import text_of
from .workable import country_name, pay_text

SITE = 'https://www.welcometothejungle.com'
ENV = SITE + '/api/env'
REFERER = SITE + '/'
JOB_API = 'https://api.welcometothejungle.com/api/v3/organizations/{org}/jobs/{job}'
JOB_PAGE = SITE + '/en/companies/{org}/jobs/{job}'
INDEX = 'wttj_jobs_production_en'                   # every open job; _fr, _es, _cs, _sk hold the same jobs
NEWEST = INDEX + '_published_at_desc'               # its replica, newest first
COMPANIES = 'wk_cms_organizations_production'       # the companies, by name
PAGE = 1000                                          # hits a call, and the most one search reaches
HOSTS = ('www.welcometothejungle.com', 'welcometothejungle.com', 'm.welcometothejungle.com')
LANGS = {'en', 'fr', 'es', 'cs', 'sk', 'en-gb', 'en-us', 'de'}
SLUG = re.compile(r'^[a-z0-9][a-z0-9-]*$')
SLUG_PLACE = re.compile(r'^[^_]+_([a-z0-9-]+)')    # the place after the title: bi-engineer_lille_SKIIL_dr9y6Oa
EVERYONE = Board('*', 'Welcome to the Jungle')     # every job of every company at once
MANY_PLACES = 200                                    # more place values than this and places are checked here only
OFFICE_KEYS = ('city', 'state', 'country', 'country_code', 'district', 'local_city', 'local_district', 'local_state')
ATTRIBUTES = ['name', 'slug', 'wk_reference', 'published_at', 'published_at_timestamp', 'language',
              'contract_type', 'contract_duration_minimum', 'contract_duration_maximum', 'remote', 'salary_minimum',
              'salary_maximum', 'salary_currency', 'salary_period', 'experience_level_minimum', 'education_level',
              'offices.city', 'offices.state', 'offices.district', 'offices.country', 'offices.country_code',
              'offices.local_city', 'offices.local_state', 'offices.local_district', 'organization.name',
              'organization.slug', 'organization.nb_employees', 'organization.creation_year', 'organization.logo.url',
              'sectors.name', 'sectors.reference', 'sectors.parent_name', 'sectors.parent_reference',
              'new_profession.category_name', 'new_profession.sub_category_name', 'new_profession.pivot_name', 'benefits',
              'summary', 'key_missions']

# contract_type -> the engine's job type; vie, graduate_program, volunteer and other have none (contractType keeps them)
CONTRACT = {'full_time': 'full-time', 'part_time': 'part-time', 'freelance': 'contract', 'internship': 'intern',
            'temporary': 'temporary', 'apprenticeship': 'apprenticeship'}
JOB_TYPE_CONTRACT = {kind: contract for contract, kind in CONTRACT.items()}
REMOTE = {'fulltime': 'remote', 'partial': 'hybrid', 'punctual': 'hybrid', 'no': 'onsite'}
REMOTE_POLICY = {'fulltime': 'full', 'partial': 'partial', 'punctual': 'occasional', 'no': 'none'}
WORK_REMOTE = {'remote': ('fulltime',), 'hybrid': ('partial', 'punctual'), 'onsite': ('no',)}
PERIODS = {'yearly': 'year', 'monthly': 'month', 'daily': 'day', 'hourly': 'hour'}
A_YEAR = {'year': 1, 'month': 12, 'day': 218}       # 218 working days, the French forfait-jours year of a day rate
# years of experience: the band's lower end, as the index stores it -> its upper end (None: no ceiling)
BANDS = {0.0: 0.5, 0.5: 1.0, 1.0: 2.0, 2.0: 3.0, 3.0: 4.0, 4.0: 5.0, 5.0: 7.0, 7.0: 10.0, 10.0: 15.0, 15.0: None}
LEVELS = {'LESS_THAN_6_MONTHS': 0.0, '6_MONTHS_TO_1_YEAR': 0.5, '1_TO_2_YEARS': 1.0, '2_TO_3_YEARS': 2.0,
          '3_TO_4_YEARS': 3.0, '4_TO_5_YEARS': 4.0, '5_TO_7_YEARS': 5.0, '7_TO_10_YEARS': 7.0, '10_TO_15_YEARS': 10.0,
          'MORE_THAN_15_YEARS': 15.0}
EDUCATION = {'no_diploma': 'No diploma', 'cap': 'CAP (vocational)', 'bep': 'BEP (vocational)', 'bac': 'Baccalaureate',
             'bac_1': 'Bac+1', 'bac_2': 'Bac+2', 'bac_3': "Bac+3 (bachelor's)", 'bac_4': 'Bac+4',
             'bac_5': "Bac+5 (master's)", 'phd': 'PhD'}
# company size, as the site groups companies: name -> (fewest, most) employees
SIZES = {'under-15': (1, 14), '15-50': (15, 49), '50-250': (50, 249), '250-2000': (250, 1999), 'over-2000': (2000, None)}
SIZE_ALIASES = {'<15': 'under-15', '< 15': 'under-15', 'less than 15': 'under-15', 'fewer than 15': 'under-15',
                '1-15': 'under-15', '1-14': 'under-15', 'under 15': 'under-15', '15-49': '15-50', '50-249': '50-250',
                '250-1999': '250-2000', '>2000': 'over-2000', '> 2000': 'over-2000', '2000+': 'over-2000',
                'more than 2000': 'over-2000', 'over 2000': 'over-2000'}     # entries split at commas: no 2,000
LANGUAGE_NAMES = {'english': 'en', 'french': 'fr', 'francais': 'fr', 'german': 'de', 'deutsch': 'de', 'spanish': 'es',
                  'espanol': 'es', 'italian': 'it', 'italiano': 'it', 'portuguese': 'pt', 'dutch': 'nl', 'czech': 'cs',
                  'danish': 'da', 'polish': 'pl', 'swedish': 'sv', 'chinese': 'zh', 'slovak': 'sk'}
ENV_KEYS = re.compile(r'"?(PUBLIC_ALGOLIA_APPLICATION_ID|PUBLIC_ALGOLIA_API_KEY_CLIENT)"?\s*:\s*"([^"]+)"')


def algolia_headers(app: str, key: str) -> dict:
    """What the site's own pages send to Algolia: the key is valid only with a Referer on welcometothejungle.com."""
    return {'X-Algolia-Application-Id': app, 'X-Algolia-API-Key': key, 'Referer': REFERER, 'Origin': SITE,
            'Content-Type': 'application/json', 'Accept': 'application/json'}


def read_env(text: str) -> tuple[str, str] | None:
    """The body of /api/env -> (Algolia application id, search key), None when it holds neither."""
    found = dict(ENV_KEYS.findall(text or ''))
    app, key = found.get('PUBLIC_ALGOLIA_APPLICATION_ID'), found.get('PUBLIC_ALGOLIA_API_KEY_CLIENT')
    return (app, key) if app and key else None


def fold(text: str | None) -> str:
    """'Île-de-France' -> 'ile de france': lower case, no accents, words only."""
    return ' '.join(words(text))


def clean(text) -> str:
    return ' '.join(str(text or '').split())


def place_text(*parts) -> str | None:
    """'Lille', 'Hauts-de-France', 'France' -> 'Lille, Hauts-de-France, France'; a part said twice is said once."""
    return ', '.join(dict.fromkeys(clean(p) for p in parts if clean(p))) or None


@dataclass(frozen=True)
class PlaceList:
    spelled: dict          # each name and other name, folded -> [(field, the index's spelling)]
    region_of: dict        # (city folded, country code) -> its one region, for an office that states none
    bare_cities: dict      # region folded -> its cities whose offices state no region (San Francisco for California)
    updated: str | None


@lru_cache(maxsize=1)
def place_list() -> PlaceList:
    """data/welcometothejungle_places.tsv (scripts/refresh_welcometothejungle.py) as the adapter reads it."""
    spelled: dict[str, list[tuple[str, str]]] = {}
    region_of: dict[tuple[str, str], str] = {}
    bare_cities: dict[str, list[str]] = {}
    updated = None
    text = resources.files('ats_jobs').joinpath('data/welcometothejungle_places.tsv').read_text(encoding='utf-8')
    for line in text.splitlines():
        if line.startswith('#'):
            if 'updated' in line and updated is None:
                updated = line.split('updated', 1)[1].strip().split(':')[0].strip()
            continue
        parts = line.split('\t') + [''] * 7
        value, field, code = parts[0], parts[1], parts[2]
        if not value or field not in ('city', 'district', 'state'):
            continue
        for name in [value] + [n for n in parts[4].split(',') if n.strip()]:
            key = fold(name)
            if key and (field, value) not in spelled.setdefault(key, []):
                spelled[key].append((field, value))
        if field == 'city' and parts[5]:
            region_of[(fold(value), code)] = parts[5]
            if parts[6].isdigit() and int(parts[6]) > 0:
                bare_cities.setdefault(fold(parts[5]), []).append(value)
    return PlaceList(spelled, region_of, bare_cities, updated)


@lru_cache(maxsize=1)
def place_names() -> tuple[frozenset[str], frozenset[str]]:
    """(every city, department and region name of the list, the cities and regions with 300 or more jobs), in
    places.key form with and without accents: the place words a search box takes, for inputs.read_place_words (after in
    or at, any of them; alone at the end, only the big towns and regions, since a department such as Nord or Var, or a
    small town's name, can be a word of a job title)."""
    every, major = set(), set()
    text = resources.files('ats_jobs').joinpath('data/welcometothejungle_places.tsv').read_text(encoding='utf-8')
    for line in text.splitlines():
        parts = line.split('\t')
        if line.startswith('#') or len(parts) < 4 or parts[1] not in ('city', 'district', 'state'):
            continue
        names = [parts[0]] + [n for n in (parts[4].split(',') if len(parts) > 4 else []) if n.strip()]
        keys = {k for n in names for k in (regions.key(n), regions.key(plain(n)))} - {''}
        every |= keys
        if parts[1] != 'district' and parts[3].isdigit() and int(parts[3]) >= 300:
            major |= keys
    return frozenset(every), frozenset(major)


@lru_cache(maxsize=1)
def industry_list() -> list[tuple[str, str, str, str]]:
    """data/welcometothejungle_industries.tsv -> [(reference, name, parent reference, parent name)]."""
    text = resources.files('ats_jobs').joinpath('data/welcometothejungle_industries.tsv').read_text(encoding='utf-8')
    out = []
    for line in text.splitlines():
        parts = line.split('\t')
        if not line.startswith('#') and len(parts) >= 4 and parts[0]:
            out.append((parts[0], parts[1], parts[2], parts[3]))
    return out


def number(value) -> float | None:
    """60000, '60,000', '60k', '60 000 EUR' -> 60000.0; anything else -> None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    text = str(value or '').strip().lower().replace(',', '').replace(' ', '').replace(' ', '')
    found = re.match(r'^[^\d]*(\d+(?:\.\d+)?)(k)?', text)
    if not found:
        return None
    amount = float(found.group(1)) * (1000 if found.group(2) else 1)
    return amount if amount > 0 else None


def slug_place(slug: str | None) -> str:
    """'ambulancier-diplome-d-etat-f-h_clisson' -> 'clisson': the place the site wrote into the job's address."""
    found = SLUG_PLACE.match(slug or '')
    return found.group(1) if found else ''


def yearly(job: dict) -> float | None:
    """The most a job pays a year: the top of its range, or its one amount, in its own currency."""
    period = A_YEAR.get(job.get('salaryPeriod'))
    amount = job.get('salaryMax') or job.get('salaryMin')
    return amount * period if period and isinstance(amount, (int, float)) else None


class WelcomeToTheJungle(Platform):
    key = 'welcometothejungle'
    name = 'Welcome to the Jungle'
    site_word = 'company page'
    actor = 'welcome-to-the-jungle-jobs-search'
    board_example = 'https://www.welcometothejungle.com/en/companies/doctolib'
    job_example = 'https://www.welcometothejungle.com/en/companies/docaposte-1/jobs/ingenieur-devops-sre-f-h_sophia-antipolis'
    concurrency = 16              # job details at once (55 a second held, 2026-11-10); the search is one call at a time
    server_search = True          # the words go to Algolia, which searches titles; every title is checked here too
    server_filters = True         # places, period, contract, remote, experience, language, size and industry go to Algolia
    newest_first = True           # the replica lists jobs newest first, also with words
    never = ('team', 'offersEquity', 'expiresAt')
    job_types = ('apprenticeship',)                     # work-study (alternance), a contract of its own on the site
    job_type_aliases = {'apprenticeship': 'apprenticeship', 'apprentice': 'apprenticeship', 'work study': 'apprenticeship',
                        'work-study': 'apprenticeship', 'workstudy': 'apprenticeship', 'alternance': 'apprenticeship',
                        'fixed term': 'temporary', 'fixed-term': 'temporary', 'cdd': 'temporary', 'cdi': 'full-time',
                        'stage': 'intern'}
    extra = ('salaryMin', 'salaryMax', 'salaryCurrency', 'salaryPeriod', 'experienceMin', 'experienceMax',
             'contractType', 'contractDurationMin', 'contractDurationMax', 'remotePolicy', 'education', 'jobLanguage',
             'role', 'roleCategory', 'industry', 'companySize', 'companyFounded', 'companyLogo', 'summary', 'keyMissions',
             'benefits', 'skills', 'tools', 'startDate')
    filter_words = {'minSalary': ('pay', 'type a lower Minimum pay or leave it empty'),
                    'companySize': ('company size', 'pick more Company sizes or clear them'),
                    'industries': ('industry', 'type a wider industry or clear Industries'),
                    'jobLanguage': ('language of the job ad', 'clear Language of the ad')}

    def __init__(self):
        self.cfg = None
        self.credentials: tuple[str, str] | None = None
        self._lock = asyncio.Lock()
        self.min_salary: float | None = None
        self.sizes: list[str] = []
        self.industries: list[str] = []
        self.languages: list[str] = []
        self.facets: list[list[str]] = []        # Algolia's facet filters of the run, each list one OR group
        self.numbers: list[list[str]] = []       # its numeric filters
        self.places_local = False                # a typed place is not on the list: places are checked here only
        self.told: set[str] = set()
        self._shared: dict = {}                   # one object for equal offices, perks and industries (share)

    # ---------- boards, links and settings ----------

    def boards(self):
        return [], None               # no list of boards: the index holds every company

    def search_all(self, boards, cfg):
        self.cfg = cfg
        self.route(cfg)
        return [EVERYONE]

    def copy_key(self, job):
        """One job per company, title and office, unless the place in the job's own address differs (a head office that
        lists one job per town)."""
        return (*dedupe_key(job), job.get('_slugPlace') or '')

    def place_names(self):
        return place_names()

    def note(self, text: str, key: str | None = None) -> None:
        if self.cfg is not None and (key or text) not in self.told:
            self.told.add(key or text)
            self.cfg.notes.append(text)

    def share(self, key, make):
        """The object made for this key the first time it was met: a company's jobs repeat its offices, perks and
        industries, and a run that keeps every job held 81,442 of them in 590 MB at the peak before (2026-11-10). Shared
        values are never changed, by this adapter or the engine."""
        found = self._shared.get(key)
        if found is None:
            found = self._shared[key] = make()
        return found

    def read_link(self, text):
        """A Welcome to the Jungle link -> (company slug, 'company/job slug'): a job page names both, a company page the
        company, and a search page (/en/jobs?query=...) neither, so the engine reads it as a search."""
        text = text.strip().rstrip('.,;:!?')
        link = text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}'
        parsed = urlparse(link)
        host = (parsed.hostname or '').lower() if ' ' not in text else ''
        if host not in HOSTS:
            return None
        parts = [unquote(p) for p in parsed.path.split('/') if p]
        if parts and parts[0].lower() in LANGS:
            parts = parts[1:]
        if len(parts) >= 2 and parts[0].lower() == 'companies' and SLUG.match(parts[1].lower()):
            company = parts[1].lower()
            if len(parts) >= 4 and parts[2].lower() == 'jobs' and parts[3]:
                return company, f'{company}/{parts[3]}'
            return company, None
        return None, None

    def search_link(self, text):
        """A search page of the site -> the places it asks for besides ?query=: its country filter and its place
        (refinementList[offices.country_code][]=FR, aroundQuery=Paris, France)."""
        parsed = urlparse(text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}')
        query = parse_qs(parsed.query)
        where = [code.strip().upper() for key, values in query.items() if 'country_code' in key
                 for value in values for code in value.split(',') if code.strip()]
        where += [v.split(',')[0].strip() for v in query.get('aroundQuery', []) if v.split(',')[0].strip()]
        return [], where

    def guess_board(self, name):
        return ' '.join(name.split()) or None

    def read_input(self, raw, cfg):
        """This site's own filters (minSalary, companySize, industries, jobLanguage) and the contract and remote filters
        of a pasted search link; what the site cannot say is read with a note."""
        self.cfg = cfg
        self.min_salary = None
        value = raw.get('minSalary')
        if value not in (None, '', 0, '0'):
            self.min_salary = number(value)
            if self.min_salary is None:
                cfg.notes.append(f'Could not read minSalary "{value}" as an amount a year, such as 50000, so every pay '
                                 f'is kept.')
        self.sizes = self._choices(raw.get('companySize'), SIZES, SIZE_ALIASES, cfg.notes)
        self.industries = [t for t in _items(raw.get('industries'))]
        self.languages = self._languages(raw.get('jobLanguage'))
        self._link_filters(raw, cfg)
        world = [p for p in cfg.locations if regions.world_query(regions.tokens(p))]
        if world:
            cfg.locations = [p for p in cfg.locations if p not in world]
            cfg.remote_places = [p for p in cfg.remote_places if p not in world]
            cfg.notes[:] = [n for n in cfg.notes if not any(n.startswith(f'Read "{p}" in location as jobs open worldwide')
                                                            for p in world)]
            cfg.notes.append(f'Read "{world[0]}" in location as every country: Welcome to the Jungle gives every job an '
                             f'office country and marks none as open worldwide. Pick Remote in Work type for fully '
                             f'remote jobs.')

    @staticmethod
    def _choices(value, allowed: dict, aliases: dict, notes: list[str]) -> list[str]:
        """Company sizes as typed -> the site's groups; empty for every size."""
        chosen, unknown = [], []
        for token in _items(value):
            text = ' '.join(token.lower().split())
            name = text if text in allowed else aliases.get(text) or aliases.get(text.replace(' ', ''))
            if name is None:
                unknown.append(token)
            elif name not in chosen:
                chosen.append(name)
        if unknown:
            notes.append(f'companySize has no option {", ".join(unknown)}, so it was left out. The options are '
                         f'{", ".join(allowed)}.')
        return chosen if len(chosen) < len(allowed) else []

    def _languages(self, value) -> list[str]:
        """'en', 'English', 'French' -> ['en', 'fr']: the language codes of the ads to keep."""
        chosen = []
        for token in _items(value):
            text = fold(token)
            code = LANGUAGE_NAMES.get(text) or (text if re.fullmatch(r'[a-z]{2}', text) else None)
            if code is None:
                try:
                    import pycountry
                    found = pycountry.languages.lookup(token.strip())
                    code = getattr(found, 'alpha_2', None)
                except (ImportError, LookupError):
                    code = None
            if code and code not in chosen:
                chosen.append(code)
            elif not code and self.cfg is not None:
                self.cfg.notes.append(f'jobLanguage has no language "{token}", so it was left out. Type a language such '
                                      f'as English, French or a code such as en.')
        return chosen

    def _link_filters(self, raw, cfg) -> None:
        """The contract types and remote policies of a pasted search link, when the form leaves them empty."""
        kinds, remote = [], []
        for token in _links(raw.get('searchTerms')):
            if self.read_link(token) != (None, None):
                continue
            query = parse_qs(urlparse(token if '://' in token else f'https://{token}').query)
            for key, values in query.items():
                for value in (v.strip().lower() for vs in values for v in vs.split(',') if v.strip()):
                    if 'contract_type' in key and CONTRACT.get(value) and CONTRACT[value] not in kinds:
                        kinds.append(CONTRACT[value])
                    if key.startswith('refinementList[remote]') and REMOTE.get(value) and REMOTE[value] not in remote:
                        remote.append(REMOTE[value])
        if kinds and not cfg.job_types:
            cfg.job_types = kinds
            cfg.notes.append(f'Read the contract types of the pasted link: {", ".join(kinds)}.')
        if remote and not cfg.work_types:
            cfg.work_types = remote
            cfg.notes.append(f'Read the remote filter of the pasted link: {", ".join(remote)}.')

    def settings(self):
        return {'minSalary': self.min_salary, 'companySize': self.sizes or None, 'industries': self.industries or None,
                'jobLanguage': self.languages or None}

    # ---------- what Algolia is asked ----------

    def route(self, cfg) -> None:
        """The run's filters, as Algolia applies them: every one is a superset of what the engine and fits() keep, since
        every job is checked here again."""
        facets, numbers = [], []
        self.places_local = False
        values = self.place_filters(cfg.locations)
        if values:
            facets.append(values)
        if cfg.work_types and not cfg.remote_places:
            facets.append([f'remote:{v}' for kind in cfg.work_types for v in WORK_REMOTE.get(kind, ())])
        if cfg.job_types:
            facets.append([f'contract_type:{JOB_TYPE_CONTRACT[k]}' for k in cfg.job_types if k in JOB_TYPE_CONTRACT]
                          or ['contract_type:none'])
        if self.languages:
            facets.append([f'language:{code}' for code in self.languages])
        industries = self.industry_filters()
        if industries:
            facets.append(industries)
        if cfg.experience is not None:
            low, high = cfg.experience
            fitting = [m for m, top in BANDS.items() if m <= high and (top is None or top >= low)]
            numbers.append([f'experience_level_minimum={m:g}' for m in fitting] or ['experience_level_minimum=-1'])
        if self.sizes:
            numbers.append([f'organization.nb_employees:{SIZES[s][0]} TO {SIZES[s][1] or 10 ** 9}' for s in self.sizes])
        if self.min_salary:
            numbers.append(['salary_minimum>0', 'salary_maximum>0'])
        self.facets, self.numbers = facets, numbers

    def place_filters(self, typed: list[str]) -> list[str]:
        """Typed places -> one OR group of office filters; empty when there is none, or when one of them is not on the
        list (then every job is checked here for every place, the newest first). A region also asks for its cities whose
        offices state no region, as the jobs give them that region (places)."""
        out, unknown = [], []
        known = place_list()
        for text in typed:
            place = Place(text)
            if place.kind == 'world':
                continue
            if place.kind in ('country', 'region'):
                out += [f'offices.country_code:{code}' for code in sorted(place.countries)]
                continue
            found = []
            for form in dict.fromkeys(w for w in (place.words, *place.also) if w):
                found += [(field, value) for name, pairs in known.spelled.items() if f' {form} ' in f' {name} '
                          for field, value in pairs]
            found += [('city', city) for field, value in list(found) if field == 'state'
                      for city in known.bare_cities.get(fold(value), [])]
            if not found:
                unknown.append(text)
                continue
            out += [f'offices.{field}:{value}' for field, value in dict.fromkeys(found)]
        if unknown or len(out) > MANY_PLACES:
            self.places_local = True
            if unknown:
                self.note(f'{", ".join(unknown)} {"is" if len(unknown) == 1 else "are"} not among the places Welcome to the '
                          f'Jungle lists, so every job was checked for {"it" if len(unknown) == 1 else "them"} by name, '
                          f'the newest first. Its places are towns, departments, regions and countries, such as Paris, '
                          f'Hauts-de-Seine, Ile-de-France or France.')
            return []
        return list(dict.fromkeys(out))

    def industry_filters(self) -> list[str]:
        """Typed industries -> the industries of the list whose name or group holds the words, as one OR group; empty
        when one of them is on no list (then they are checked here)."""
        out = []
        for term in terms(self.industries):
            found = []
            for ref, name, parent_ref, parent in industry_list():
                if term.in_title(index(name)):
                    found.append(f'sectors.reference:{ref}')
                if term.in_title(index(parent)):
                    found.append(f'sectors.parent_reference:{parent_ref}')
            if not found:
                self.note(f'"{term.text}" is not among the industries Welcome to the Jungle lists, so every job was '
                          f'checked for it by name. Its industries include Software, FinTech / InsurTech, Banking, '
                          f'Health, Retail and Media.', key=f'industry:{term.text}')
                return []
            out += found
        return list(dict.fromkeys(out))

    def search_params(self, session: dict, term: str | None, days: int | None) -> dict:
        params = {'query': '', 'hitsPerPage': PAGE, 'attributesToRetrieve': ATTRIBUTES, 'attributesToHighlight': [],
                  'attributesToSnippet': [], 'analytics': False, 'clickAnalytics': False, 'enableABTest': False,
                  'removeWordsIfNoResults': 'none'}
        if term:
            params['query'] = ' '.join(Term(term).tokens)        # plurals folded: engineers asks for engineer...
            params['queryType'] = 'prefixAll'                     # ...and every word is a prefix, so Engineers comes too
            searched = ['name', 'summary', 'profile'] if self.cfg and self.cfg.search_descriptions else ['name']
            params['restrictSearchableAttributes'] = searched
        facets = [list(group) for group in self.facets]
        if session.get('orgs'):
            facets.append([f'organization.slug:{slug}' for slug in session['orgs']])
        numbers = [list(group) for group in self.numbers]
        if days:
            numbers.append([f'published_at_timestamp>={int(time.time()) - days * 86400}'])
        if facets:
            params['facetFilters'] = facets
        if numbers:
            params['numericFilters'] = numbers
        return params

    # ---------- calls ----------

    async def keys(self, http, fresh: bool = False) -> tuple[str, str]:
        """The site's Algolia application and search key, read from /api/env once a run (again when Algolia refuses it)."""
        async with self._lock:
            if self.credentials and not fresh:
                return self.credentials
            response = await send(http, 'GET', ENV, headers={'Accept': '*/*', 'Referer': REFERER})
            found = read_env(response.text) if response.status_code == 200 else None
            if not found:
                raise BoardError(f'Welcome to the Jungle did not hand out its search key (HTTP {response.status_code})')
            self.credentials = found
            return found

    async def algolia(self, http, index: str, params: dict, path: str = 'query') -> dict:
        """One Algolia call of the site's own key; a refused key is read again once."""
        body = {'params': encode(params)} if path == 'query' else params
        for fresh in (False, True):
            app, key = await self.keys(http, fresh)
            response = await send(http, 'POST', f'https://{app.lower()}-dsn.algolia.net/1/indexes/{index}/{path}',
                                  json=body, headers=algolia_headers(app, key))
            if response.status_code == 403 and not fresh:
                continue
            if response.status_code != 200:
                try:
                    why = response.json().get('message')
                except ValueError:
                    why = None
                raise BoardError(f'Welcome to the Jungle search answered HTTP {response.status_code}'
                                 + (f': {why}' if why else ''))
            try:
                return response.json()
            except ValueError as exc:
                raise BoardError('Welcome to the Jungle search did not answer JSON') from exc
        raise BoardError('Welcome to the Jungle search refused its own key')

    async def open(self, http, board):
        if board.key == EVERYONE.key:
            return {'orgs': None}
        if self.cfg is not None and self.cfg.job_urls:
            return {'orgs': [board.key]}          # a checked link names the company's slug itself
        slugs = await self.find_company(http, board.key)
        return {'orgs': slugs} if slugs else None

    async def find_company(self, http, text: str) -> list[str]:
        """A company typed by its name or slug -> its slugs on the site (Qonto has two): those with open jobs, else the
        ones whose name is the text (nothing open there now); [] when the site has no such company."""
        want = ''.join(words(text))
        candidates = [text.strip().lower()] if SLUG.match(text.strip().lower()) else []
        found = await self.algolia(http, COMPANIES, {'query': text, 'hitsPerPage': 20,
                                                     'attributesToRetrieve': ['name', 'slug']})
        hits = [h for h in found.get('hits') or [] if h.get('slug')]
        exact = [h['slug'] for h in hits if ''.join(words(h.get('name'))) == want]
        longer = [h['slug'] for h in hits if ''.join(words(h.get('name'))).startswith(want)]
        candidates = list(dict.fromkeys(candidates + (exact or longer[:5])))
        if not candidates:
            return []
        counted = await self.algolia(http, INDEX, {'query': '', 'hitsPerPage': 0, 'facets': ['organization.slug'],
                                                  'facetFilters': [[f'organization.slug:{c}' for c in candidates]],
                                                  'maxValuesPerFacet': 100})
        hiring = (counted.get('facets') or {}).get('organization.slug') or {}
        with_jobs = [c for c in candidates if hiring.get(c)]
        if with_jobs and (exact or longer or with_jobs != candidates):
            names = ', '.join(with_jobs[:5])
            if names.lower() != text.strip().lower():
                self.note(f'Read the company "{text}" as {names}.', key=f'company:{text}')
        return with_jobs or list(dict.fromkeys(exact)) or ([candidates[0]] if candidates[0] == text.strip().lower() else [])

    async def pages(self, http, session, term, days):
        """Newest first, PAGE jobs a call; past the 1,000-hit window by the posting time of the last job read."""
        params = self.search_params(session, term, days)
        top, edge = None, set()                  # the last page's oldest second, and its jobs of that second
        while True:
            asked = dict(params)
            if top is not None:
                asked['numericFilters'] = [*params.get('numericFilters', []), [f'published_at_timestamp<={top}']]
            hits = [h for h in (await self.algolia(http, NEWEST, asked)).get('hits') or [] if h.get('objectID')]
            fresh = [h for h in hits if h['objectID'] not in edge]
            if top is not None and hits and not fresh and len(hits) >= PAGE:
                # a whole page within one second, all of it read before: that second whole, then strictly older
                fresh = [h for h in await self.second(http, params, top) if h['objectID'] not in edge]
                if fresh:
                    yield [self.job(h) for h in fresh]
                top, edge = top - 1, set()
                continue
            if not fresh:
                return
            yield [self.job(h) for h in fresh]
            if len(hits) < PAGE:
                return
            top = hits[-1].get('published_at_timestamp')
            if not isinstance(top, int):
                return
            edge = {h['objectID'] for h in hits if h.get('published_at_timestamp') == top}

    async def second(self, http, params: dict, moment: int) -> list[dict]:
        """Every job of one second of the search (params, without its paging), by Algolia's browse, which has no
        1,000-hit window."""
        asked = {k: v for k, v in params.items() if k not in ('analytics', 'clickAnalytics', 'enableABTest')}
        asked['numericFilters'] = [*params.get('numericFilters', []), [f'published_at_timestamp={moment}']]
        body = {'params': encode(asked)}
        out, cursor = [], None
        while True:
            data = await self.algolia(http, NEWEST, body if cursor is None else {'cursor': cursor}, path='browse')
            out += [h for h in data.get('hits') or [] if h.get('objectID')]
            cursor = data.get('cursor')
            if not cursor:
                return out

    # ---------- jobs ----------

    @staticmethod
    def places(offices: list[dict]) -> tuple[list[dict], list[str]]:
        """Offices -> (places, as the engine reads them, and the location texts shown). Each office gives its city,
        region and country, and a second entry with its department and its names in its own language (Bretagne for
        Brittany), so either finds it. An office with no region gets the one the index gives its city in that country
        (London: England, San Francisco: California)."""
        found, shown = [], []
        region_of = place_list().region_of
        for office in offices:
            city, state, country = (clean(office.get(k)) or None for k in ('city', 'state', 'country'))
            code = clean(office.get('country_code')).upper() or None
            country = country or (country_name(code) if code else None)
            state = state or (region_of.get((fold(city), code or '')) if city else None)
            # "San Mateo, CA" with its region known is shown "San Mateo, California, United States"
            text = place_text(re.sub(r',\s*[A-Z]{2,3}$', '', city) if city and state else city, state, country)
            if not text:
                continue
            shown.append(text)
            found.append({'name': text, 'city': city, 'region': state, 'country': country or code})
            local = place_text(*(office.get(k) for k in ('district', 'local_city', 'local_district', 'local_state')
                                 if fold(office.get(k)) not in {fold(city), fold(state), ''}))
            if local:
                found.append({'name': local, 'country': country or code})
        return found, shown

    def job(self, hit: dict) -> dict:
        org = hit.get('organization') or {}
        company_slug, slug = clean(org.get('slug')) or None, clean(hit.get('slug')) or None
        offices = [o for o in hit.get('offices') or [] if isinstance(o, dict)]
        found, shown = self.share(('offices', *(tuple(o.get(k) for k in OFFICE_KEYS) for o in offices)),
                                  lambda: self.places(offices))
        low, high = (v if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None
                     for v in (hit.get('salary_minimum'), hit.get('salary_maximum')))
        currency = clean(hit.get('salary_currency')) or None
        period = PERIODS.get(hit.get('salary_period'))
        shown_pay = pay_text(low, high, currency, period) if (low or high) else None
        level = hit.get('experience_level_minimum')
        level = float(level) if isinstance(level, (int, float)) and not isinstance(level, bool) else None
        profession = hit.get('new_profession') or {}
        sectors = self.share(('sectors', *((s.get('name'), s.get('parent_name')) for s in hit.get('sectors') or []
                                           if isinstance(s, dict))),
                             lambda: [(clean(s.get('name')), clean(s.get('parent_name'))) for s in hit.get('sectors') or []
                                      if isinstance(s, dict)])
        perks = [clean(b) for b in hit.get('benefits') or [] if clean(b)]
        perks = self.share(('benefits', *perks), lambda: perks) if perks else None
        url = JOB_PAGE.format(org=company_slug, job=slug) if company_slug and slug else None
        logo = (org.get('logo') or {}).get('url') if isinstance(org.get('logo'), dict) else None
        return {
            'id': f'{company_slug}/{slug}' if company_slug and slug else hit.get('objectID'),
            'company': _intern(clean(org.get('name'))),
            'companySlug': _intern(company_slug),
            'companyWebsite': None,
            'title': clean(hit.get('name')),
            'location': shown[0] if shown else None,
            'otherLocations': shown[1:],
            'places': found,
            'workType': REMOTE.get(hit.get('remote')),
            'employmentType': CONTRACT.get(hit.get('contract_type')),
            'department': _intern(clean(profession.get('sub_category_name'))),
            'salary': shown_pay,
            'salaryMin': low if shown_pay else None,
            'salaryMax': high if shown_pay else None,
            'salaryCurrency': currency if shown_pay else None,
            'salaryPeriod': period if shown_pay else None,
            'postedAt': hit.get('published_at') or None,
            'jobUrl': url,
            'applyUrl': url,
            'reference': clean(hit.get('wk_reference')) or None,
            'experienceMin': level,
            'experienceMax': BANDS.get(level) if level is not None else None,
            'contractType': hit.get('contract_type') or None,
            'contractDurationMin': hit.get('contract_duration_minimum'),
            'contractDurationMax': hit.get('contract_duration_maximum'),
            'remotePolicy': REMOTE_POLICY.get(hit.get('remote')),
            'education': EDUCATION.get(hit.get('education_level')),
            'jobLanguage': _intern(hit.get('language')),
            'role': _intern(clean(profession.get('pivot_name'))),
            'roleCategory': _intern(clean(profession.get('category_name'))),
            'industry': _intern(', '.join(dict.fromkeys(name for name, _ in sectors if name))),
            'companySize': org.get('nb_employees') if isinstance(org.get('nb_employees'), int) else None,
            'companyFounded': org.get('creation_year') if isinstance(org.get('creation_year'), int) else None,
            'companyLogo': _intern(logo),
            'summary': clean(hit.get('summary')) or None,
            'keyMissions': [clean(m) for m in hit.get('key_missions') or [] if clean(m)] or None,
            'benefits': perks,
            'skills': None,
            'tools': None,
            'startDate': None,
            '_slugPlace': slug_place(slug),
            '_sectors': sectors,
        }

    def fits(self, job):
        """This site's own filters, by their input names."""
        if self.min_salary and not (yearly(job) or 0) >= self.min_salary:
            return 'minSalary'
        if self.sizes:
            size = job.get('companySize')
            if not isinstance(size, int) or not any(SIZES[s][0] <= size <= (SIZES[s][1] or size) for s in self.sizes):
                return 'companySize'
        if self.industries:
            wanted = terms(self.industries)
            if not any(t.in_title(index(text)) for name, parent in job.get('_sectors') or [] for text in (name, parent)
                       for t in wanted):
                return 'industries'
        if self.languages and job.get('jobLanguage') not in self.languages:
            return 'jobLanguage'
        return True

    # ---------- details and links ----------

    async def detail(self, http, job_id: str) -> tuple[int, dict | None]:
        """'company/job slug' -> (HTTP status, the job's record on the site's API)."""
        company, _, slug = job_id.partition('/')
        if not company or not slug:
            return 404, None
        response = await send(http, 'GET', JOB_API.format(org=company, job=slug),
                              headers={'Accept': 'application/json', 'wttj-user-language': 'en'})
        if response.status_code == 404:
            return 404, None
        if response.status_code != 200:
            raise BoardError(f'the job API answered HTTP {response.status_code}')
        try:
            record = response.json().get('job')
        except (ValueError, AttributeError) as exc:
            raise BoardError('the job API did not answer JSON') from exc
        if not isinstance(record, dict):
            raise BoardError('the job API answered no job')
        return 200, record

    @staticmethod
    def described(record: dict) -> dict:
        """A record of the job API -> the description fields: the job, the experience wanted and the recruitment
        process, as the job page shows them, and the skills, tools, start date and apply link."""
        sections = [('', record.get('description')),
                    ('Preferred experience', record.get('looking_for_candidate_description') or record.get('profile')),
                    ('Recruitment process', record.get('recruitment_process'))]
        html = ''.join((f'<h2>{title}</h2>' if title else '') + body for title, body in sections
                       if isinstance(body, str) and body.strip()) or None
        org = record.get('organization') or {}
        out = {'description': text_of(html), 'descriptionHtml': html,
               'skills': [n for n in (_name(s.get('name')) for s in record.get('skills') or [] if isinstance(s, dict)) if n]
               or None,
               'tools': [clean(t.get('name')) for t in record.get('tools') or [] if isinstance(t, dict)
                         and clean(t.get('name'))] or None,
               'startDate': record.get('start_date') or None}
        if clean(record.get('apply_url')):
            out['applyUrl'] = clean(record.get('apply_url'))
        if clean(org.get('media_website_url')):
            out['companyWebsite'] = clean(org.get('media_website_url'))
        return out

    async def details(self, http, session, job):
        status, record = await self.detail(http, job['id'])
        if record is None:
            return job
        return {**job, **self.described(record)}

    async def lookup(self, http, session, job_id):
        status, record = await self.detail(http, job_id)
        if record is None:
            return 'not found', None
        if record.get('status') != 'published':
            org = record.get('organization') or {}
            return 'closed', {'company': _intern(clean(org.get('name'))), 'companySlug': clean(org.get('slug')) or None,
                              'companyWebsite': clean(org.get('media_website_url')) or None}
        return 'open', self.from_detail(record)

    def from_detail(self, record: dict) -> dict:
        """A job from the job API alone, for a checked link: the same fields as a search row, from that record."""
        org = record.get('organization') or {}
        offices = []
        for office in record.get('offices') or []:
            if not isinstance(office, dict):
                continue
            parts = [p.strip() for p in str(office.get('address') or '').split(',') if p.strip()]
            state = parts[-2] if len(parts) >= 3 and not re.search(r'\d', parts[-2]) else None
            offices.append({'city': office.get('city'), 'state': state, 'country_code': office.get('country_code'),
                            'district': office.get('district'), 'local_city': office.get('local_city'),
                            'local_district': office.get('local_district')})
        profession = record.get('profession') or {}
        level = LEVELS.get(record.get('experience_level'))
        hit = {
            'name': record.get('name'), 'slug': record.get('slug'), 'objectID': record.get('wttj_reference'),
            'wk_reference': record.get('wk_reference'), 'published_at': record.get('published_at'),
            'language': record.get('language'), 'contract_type': record.get('contract_type'),
            'contract_duration_minimum': record.get('contract_duration_min'),
            'contract_duration_maximum': record.get('contract_duration_max'), 'remote': record.get('remote'),
            'salary_minimum': record.get('salary_min'), 'salary_maximum': record.get('salary_max'),
            'salary_currency': record.get('salary_currency'), 'salary_period': record.get('salary_period'),
            'experience_level_minimum': level, 'education_level': record.get('education_level'), 'offices': offices,
            'organization': {'name': org.get('name'), 'slug': org.get('slug'), 'logo': org.get('logo')},
            'sectors': [{'name': s.get('name'), 'parent_name': s.get('parent_name')} for s in org.get('sectors') or []
                        if isinstance(s, dict)],
            'new_profession': {'category_name': _name(profession.get('category_name')),
                               'sub_category_name': _name(profession.get('sub_category_name'))},
            'summary': record.get('summary'), 'key_missions': record.get('key_missions'),
        }
        return {**self.job(hit), **self.described(record)}


def encode(params: dict) -> str:
    """Search parameters -> Algolia's params string (lists, objects and booleans as JSON)."""
    return urlencode({k: json.dumps(v) if isinstance(v, (list, dict, bool)) else v for k, v in params.items()})


def _intern(text) -> str | None:
    """A text met on many jobs, kept once in memory (company names, categories, logos)."""
    return sys.intern(text) if isinstance(text, str) and text else None


def _items(value) -> list[str]:
    """A list, or one string, -> its entries (commas, semicolons and new lines split)."""
    raw = value if isinstance(value, list) else [value] if isinstance(value, (str, int, float)) else []
    return [part.strip() for item in raw if isinstance(item, (str, int, float)) and not isinstance(item, bool)
            for part in re.split(r'[,;\n\r\t]+', str(item)) if part.strip()]


def _links(value) -> list[str]:
    """The entries of searchTerms that are links, whole (a link's own commas kept)."""
    raw = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    return [line.strip() for item in raw if isinstance(item, str) for line in re.split(r'[;\n\r\t]+', item)
            if re.match(r'^(?:https?://)?(?:www\.)?welcometothejungle\.com/', line.strip(), re.I)]


def _name(value) -> str | None:
    """A name the API gives in several languages ({'en': ..., 'fr': ...}) -> the English one, else the first."""
    if isinstance(value, dict):
        value = value.get('en') or next((v for v in value.values() if v), None)
    return clean(value) or None


