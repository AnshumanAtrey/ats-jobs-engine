"""
SmartRecruiters career sites (careers.smartrecruiters.com/<company>, jobs.smartrecruiters.com/<company>/<id>), read
through SmartRecruiters' public Posting API. No key. Measured 2026-11-02:

- GET https://api.smartrecruiters.com/v1/companies/<company>/postings?limit=100&offset=<n>: a company's open postings,
  newest first by releasedDate, 100 a page at most, also when searched. Filters: q, releasedAfter, country (a lower-case
  code: de finds 746 Bosch jobs, DE none), region, city, language, locationType. q reads titles and descriptions for
  any of its words, stemmed ("software engineer" is software or engineer, engineers is engineer); a quoted phrase finds
  the phrase; "&&" between three words finds nothing, so it is never sent. A company is read in any case, and one that
  does not exist answers 200 with no posting, like one that is not hiring; one that locked the API answers 401
  (CHECK24). The list has no pay and no description.
- GET .../postings/<id> (id, uuid or jobAdId): the job ad's four HTML sections, active, postingUrl, applyUrl, and
  compensation {min, max, currency, period} on 34 of 400 sampled jobs, 9 of them a maximum alone, which the job page
  calls "up to". A closed posting answers 200 with active false (ServiceNow's 2021 ids); one that never existed 404
  with JSON code RESOURCE_NOT_FOUND; another company's id 400 with code ILLEGAL_ARGUMENT naming the companyIdentifier;
  text that is no id a plain-text 400 "Bad Request" (2026-11-03). A path that does not exist (/postingz, /v2/...) is
  Cloudflare's HTML 404, and a refused parameter (limit=-1, releasedAfter=garbage) a 400 VALIDATION_ERROR.
- GET https://smrtr.io/<code>: SmartRecruiters' short link of one job (share and job-alert links) answers 301 to the
  job's link on jobs.smartrecruiters.com; an unknown code answers 404. Codes mind case: 8pL2n is an Anglo American job,
  8PL2N a Visa one (2026-11-03), so every short link waits under one board, smrtr.io, until its lookup reads it.
- GET https://jobs.smartrecruiters.com/sr-jobs/company-name-lookup?q=<company>: the identifier and name of a company,
  "" when there is none: how a typed company is told apart from one with no open job. It knows only the companies in
  SmartRecruiters' job search ("" for AbbVie, 1,989 open jobs), so a company's postings are asked first.

postedAt is releasedDate: the job page's JobPosting microdata gives the same instant as datePosted on 11 of 11 pages
(2024-12 to 2026-11) and on 3 from 2017 and 2023. The page's validThrough (3 of the 11) is not in the API, so rows carry
no expiry. A job page answers with any slug or none; links built here use SmartRecruiters' slug rule (399 of 400
postingUrls). jobs.smartrecruiters.com serves every company: www.smartrecruiters.com/<company>/<id> answers 301 to it,
careers.smartrecruiters.com/<company>/<id> 302 to the company's page, and no regional host (eu, us) resolves.
Cloudflare fronts the API and limits the calls of one address. From one laptop on 2026-11-03, 3,000 list calls 128 in
flight got 37 answers 429 (Cloudflare error 1015, retry-after 0) from 7.8 s in; 96 in flight got 99, at 121.9 calls a
second; 80 in flight none, at 115.9 a second (on 2026-11-02, 128 in flight had met none, so the limit moves). Calls in
flight cap the rate only while answers are slow (0.6 s here), and nearer SmartRecruiters' servers, as on Apify, 64 in
flight could pass it: a run keeps 64 in flight and is paced to RATE calls a second whatever the latency. A pass over
every page at 64 took 147.6 s with no retry; at 128, 333.2 s with 67 retries.
api.smartrecruiters.com's robots.txt allows /v1/companies/ to LinkedInBot and disallows every path to anyone else; the
Posting API is the one SmartRecruiters documents for career sites, and it needs no key.

A posting stays open until its company closes it, and many never do: of the 12,593 career sites with open postings,
9,650 had posted nothing for two years, with 180,513 of the 423,303 postings (Adeeba e Services 23,736 from 2017,
McKesson 38 from 2023), while 606 had posted within a day. Their job pages still mark them up as JobPostings.
data/smartrecruiters.tsv keeps those sites as #~ comment lines, which runs leave out and read only when named.

Sandbox, test and demo accounts post tests, templates and copies of real jobs: on 2026-11-03, 22 of the sites read in
runs held 3,073 such postings (SopraSteriaSandbox 'Test_Rohan_Noida', LinkedIn QA 'Test offer feilds', Acme Corp. demo
'Product Manager'), and 33 real sites held 748 more (Pilot Flying J's '(J_SYNC_TEST)Deli Baker', Christian Brothers
Automotive's 'TEST KS007 - Blue Valley General Application'). The refresh writes such accounts as #! comment lines,
read only when named, and a job whose title says it is a test is passed over (TEST_TITLE): never Test Engineer or
Sample Preparer, nor Lely's 'Test – en Service Engineer' (Dutch: test and service).

The job search on jobs.smartrecruiters.com (sr-jobs/search) spans companies but answers only the newest 100 of a query
(offset and limit are ignored), leaves out the 1,387 hiring sites that opted out (AbbVie, City of New York) and 446 of
the 4,909 newest postings of 80 companies it does hold (some almost all), with fewer fields: it cannot vouch that no
board holds anything newer, so every listed board is read, and it only helps to find companies
(scripts/refresh_smartrecruiters.py).
"""
import asyncio
import html as htmllib
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from importlib import resources
from urllib.parse import parse_qs, unquote, urlparse

from .. import places
from ..http import BoardError, BoardMissing, send
from ..inputs import JOB_TYPE_ALIASES
from ..rows import WORD, Term, country_code, index, is_country
from .base import Board, Platform
from .cornerstone import text_of

try:
    import pycountry
except ImportError:                    # the actor installs it; without it a country keeps its code
    pycountry = None

API = 'https://api.smartrecruiters.com/v1/companies/{company}/postings'
NAME_LOOKUP = 'https://jobs.smartrecruiters.com/sr-jobs/company-name-lookup'
JOB_LINK = 'https://jobs.smartrecruiters.com/{company}/{job}'
MAIN_SITE = {'www.smartrecruiters.com', 'smartrecruiters.com'}     # old company and job links, and the vendor's site
SHORT = 'smrtr.io'                                          # smrtr.io/<code>: one job, read where it leads
HOSTS = {'careers.smartrecruiters.com', 'jobs.smartrecruiters.com', 'api.smartrecruiters.com', SHORT, *MAIN_SITE}
SHORT_CODE = re.compile(r'^[A-Za-z0-9]{3,16}$')
APP_PATHS = {'oneclick-ui', 'external-referrals'}           # .../company/<company>/(publication|job)/<id>
COMPANY_FIRST = {'ni', 'my-applications'}                   # /ni/<company>/<uuid>-<slug>, /my-applications/<company>/...
NO_COMPANY = {'sr-jobs', 'cdn-cgi', 'static', '_incapsula_resource', 'robots.txt', 'favicon.ico', 'sitemap.xml',
              'widget', 'job-search'}
# www.smartrecruiters.com/<company> answers 301 to the company's page (any word does), so only its own sections, from
# its sitemap of 2026-11-03, are no company; none is a company key on file (cityfibre and smartrecruiters are, so the
# sitemap is not used whole)
VENDOR = {'pricing', 'resources', 'blog', 'customers', 'about-us', 'contact-us', 'get-a-demo', 'events', 'webinars',
          'partners', 'marketplace', 'integrations', 'industries', 'legal', 'privacy', 'terms-of-use', 'sign-in',
          'signin', 'news', 'case-studies', 'ebooks', 'glossary', 'docs', 'why-smartrecruiters', 'hiring-software'}
COMPANY = re.compile(r'^[A-Za-z0-9-]{1,100}$')
POSTING = re.compile(r'^(\d{6,})(?:-|$)')
UUID = re.compile(r'^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:-|$)', re.I)
ENTITY = re.compile(r'&(?:#\d+|#x[0-9a-f]+|[a-z]+);', re.I)
PAGE_SIZE = 100
RATE = 80                    # calls a second at most for a run: Cloudflare refused one address at about 116 to 122
# 401 is a company that locked its Posting API (CHECK24, 1 of about 16,000 companies on 2026-11-02). A company that does
# not exist answers 200 with no posting, so on the list endpoint a 400 is a parameter it refused and a 404 a path that
# moved (Cloudflare's HTML page): both stay errors, so an API change fails the run instead of finding nothing. No company
# answered 403; it is what a blocked address gets, so it stays an error too
LOCKED = 401
# A board's first page is short, because most boards only confirm that they hold nothing newer than the jobs already
# kept. With no word every board has jobs and its newest one decides (an empty form read 12,102 boards in 112 s with
# 1, 129 s with 10); a word search is slower per call and many boards hold a few matches (202 s with 10, 262 s with 1).
FIRST_PAGE, FIRST_PAGE_SEARCHED = 1, 10
SECTIONS = ('companyDescription', 'jobDescription', 'qualifications', 'additionalInformation')
# casual (2,239 postings, 2,114 in Australia and 124 in New Zealand, 2026-11-03) is hourly work with no set hours: cooks,
# crew and shop staff at Guzman y Gomez, Endeavour Group and JB Hi-Fi, 68 of them for Christmas. Part-time is the
# nearest of the five job types; typed in the job type field, casual reads the same way
JOB_TYPE = {'permanent': 'full-time', 'full-time': 'full-time', 'part-time': 'part-time', 'contract': 'contract',
            'intern': 'intern', 'temporary': 'temporary', 'casual': 'part-time'}
# codes SmartRecruiters stores that are no ISO country: Kosovo (44 postings), unknown (XX 36) and UN (3), 2026-11-03
ODD_COUNTRIES = {'XK': 'Kosovo', 'XX': None, 'UN': None}
PERIOD = {'YEARLY': 'year', 'MONTHLY': 'month', 'WEEKLY': 'week', 'DAILY': 'day', 'HOURLY': 'hour'}
PER = {'year': 'a year', 'month': 'a month', 'week': 'a week', 'day': 'a day', 'hour': 'an hour'}
# data/smartrecruiters.tsv writes a career site with nothing posted in two years, and a sandbox, test or demo account,
# as comment lines with these starts: runs leave them out, and a company typed in the companies field still finds them
DORMANT, TEST_SITE = '#~\t', '#!\t'
LEFT_OUT = (DORMANT, TEST_SITE)
# A posting that says it is a test, measured on every posting of 2026-11-03 (748 on 33 real sites, every shape read): an
# integration's copies, '(J_SYNC_TEST)Deli Baker' (Pilot Flying J, 311); 'TEST KS007 - Blue Valley General Application'
# (Christian Brothers Automotive, 335); 'Test Job 2', 'DC TEST Job Template', 'Driver Helper (Job Template)'; a test that
# says do not apply ('TEST - bitte nicht bewerben: Restaurant Manager'); a title that is only test words ('test', 'TEST-2',
# 'Demo New 2023'). 'do not apply' alone is not one (ITPractice's real '... PLEASE DO NOT APPLY UNLESS YOU ARE VERY FLUENT')
TEST_TITLE = re.compile(
    r'J_SYNC_TEST|(?-i:^TEST [A-Z]{2}\d{3} - )|\btest[\s_-]*jobs?\b|\bjob template\b'
    r'|\btest\b.*\b(?:do not apply|nicht bewerben)\b|\b(?:do not apply|nicht bewerben)\b.*\btest\b'
    r'|^\W*(?:(?:test(?:ing)?|demo|dummy|sample|template)\W*)+(?:(?:job|posting|position|role|ad|offer|new|copy)\W*)*\d*\W*$',
    re.I)


def squeeze(text: str) -> str:
    return re.sub(r'[^0-9a-z]+', '', text.casefold())


def left_out_sites(text: str) -> dict[str, str]:
    """The #~ and #! lines of the company file -> {company name, its squeezed form or key, folded: key}. Every site's own
    key comes first: AccionLabs2, named AccionLabs, sits above the site AccionLabs in the file (18 such pairs, 2026-11-02)."""
    sites = [(line.split('\t', 1)[1].split('\t') + [''])[:2] for line in text.splitlines() if line.startswith(LEFT_OUT)]
    found = {key.strip().casefold(): key.strip() for key, _ in sites if key.strip()}
    for key, name in sites:
        for word in (name, squeeze(name)):
            if word.strip() and key.strip():
                found.setdefault(word.strip().casefold(), key.strip())
    return found


def posting_id(part: str) -> str | None:
    """'744000159116379-pmi-project-lead-em' -> '744000159116379'; '<uuid>-process-workers' -> the uuid. The uuid is
    tried first: 2.3% of uuids start with eight digits (25250225-d0ed-...), which would read as a numeric id."""
    found = UUID.match(part) or POSTING.match(part)
    return found.group(1).lower() if found else None


def slug(title: str) -> str:
    """SmartRecruiters' own link slug: accents dropped, any other run of characters a dash ('Anlæg Øst' -> 'anl-g-st')."""
    plain = ''.join(c for c in unicodedata.normalize('NFKD', title) if not unicodedata.combining(c)).lower()
    return re.sub(r'[^a-z0-9]+', '-', plain)


def obj(value) -> dict:
    """A field that should be an object; anything else reads as empty (no live record had another shape, 2026-11-03)."""
    return value if isinstance(value, dict) else {}


def clean(text) -> str:
    """Spaces squeezed and entities read. A number reads as its digits; any other value that is not text as nothing."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        text = str(text)
    if not isinstance(text, str):
        return ''
    text = ' '.join(text.split())
    return htmllib.unescape(text) if ENTITY.search(text) else text


def country_name(code: str | None, full: str | None) -> str | None:
    """'cn', 'Shanghai, Shanghai, China' -> 'China': SmartRecruiters' word when it names that country, else ISO's."""
    code = code.strip().upper() if isinstance(code, str) else ''
    if not code or code in ODD_COUNTRIES:
        return ODD_COUNTRIES.get(code)
    last = full.rsplit(',', 1)[-1].strip() if isinstance(full, str) else ''
    if last and country_code(last, allow_codes=False) == code:
        return last
    found = pycountry.countries.get(alpha_2=code) if pycountry else None
    return (getattr(found, 'common_name', None) or found.name) if found else code


def world_region(text: str | None) -> bool:
    """'Europe', 'ASIA', 'Australia & New Zealand', 'GLOBAL': a part of the world, not a state ('South Australia')."""
    toks = places.tokens(text or '')
    return bool(toks) and (places.world_query(toks) or bool(places.regions_in(toks, is_country)))


def place(location: dict) -> dict | None:
    """A posting's address -> one place: 'Singapore, , Singapore' reads 'Singapore', 'Shanghai, Shanghai, China'
    reads 'Shanghai, China'. A part of the world typed as the region of an office address is a label, not where the
    job is: Turner & Townsend files Amsterdam under "Europe" (1,173 onsite and hybrid postings on the 2,943 career
    sites carried one, 2026-11-02), and "Germany" found them. A remote posting keeps it."""
    city, region = clean(location.get('city')) or None, clean(location.get('region')) or None
    country = country_name(location.get('country'), location.get('fullLocation'))
    if region and country and not location.get('remote') and world_region(region):
        region = None
    parts = []
    for part in (city, region, country):
        if part and (not parts or parts[-1].casefold() != part.casefold()):
            parts.append(part)
    return {'name': ', '.join(parts), 'city': city, 'region': region, 'country': country} if parts else None


def job_type(kind: dict | None) -> str | None:
    kind = obj(kind)
    return JOB_TYPE.get(clean(kind.get('id'))) or JOB_TYPE_ALIASES.get(clean(kind.get('label')).lower())


def pay(compensation: dict | None) -> dict:
    """{'min': 60000, 'max': 70000, 'currency': 'USD', 'period': 'YEARLY'} -> the salary fields. 0 to 0 is no pay."""
    comp = obj(compensation)
    comp = comp.get('salary') if isinstance(comp.get('salary'), dict) else comp
    low, high = (v if isinstance(v, (int, float)) and v > 0 else None for v in (comp.get('min'), comp.get('max')))
    if low is None and high is None:
        return {}
    currency, period = comp.get('currency') or None, PERIOD.get(str(comp.get('period') or '').upper())
    amounts = ' - '.join(dict.fromkeys(f'{v:,.2f}'.removesuffix('.00') for v in (low, high) if v is not None))
    text = ' '.join(p for p in (currency, amounts, PER.get(period)) if p)
    if low is None:                    # a maximum alone is a ceiling: the job page says "up to USD 18.50 - hourly"
        text = f'up to {text}'
    return {'salary': text, 'salaryMin': low, 'salaryMax': high, 'salaryCurrency': currency, 'salaryPeriod': period}


def job_ad(record: dict) -> str | None:
    """The four sections of the job ad as one HTML text, each under its own heading."""
    sections = obj(obj(record.get('jobAd')).get('sections'))
    parts = []
    for key in SECTIONS:
        section = obj(sections.get(key))
        text = section.get('text').strip() if isinstance(section.get('text'), str) else ''
        if text:
            title = htmllib.escape(clean(section.get('title')))
            parts.append(f'<h3>{title}</h3>{text}' if title else text)
    return ''.join(parts) or None


def missing(response) -> bool:
    """SmartRecruiters' own word that a posting cannot be read: JSON code RESOURCE_NOT_FOUND (404, an id that never
    existed), ILLEGAL_ARGUMENT naming the companyIdentifier (400, another company's posting), or a locked company (401).
    A 400 or 404 in any other shape (plain 'Bad Request', Cloudflare's HTML) is not, and stays an error."""
    if response.status_code == LOCKED:
        return True
    if response.status_code not in (400, 404):
        return False
    try:
        data = response.json()
    except ValueError:
        return False
    code = data.get('code') if isinstance(data, dict) else None
    return code == 'RESOURCE_NOT_FOUND' or (code == 'ILLEGAL_ARGUMENT' and 'companyIdentifier' in str(data.get('message')))


def answer(response) -> dict:
    if response.status_code != 200:
        raise BoardError(f'HTTP {response.status_code}')
    try:
        data = response.json()
    except ValueError as exc:
        raise BoardError('the answer was not JSON') from exc
    if not isinstance(data, dict):
        raise BoardError('the answer was not a JSON object')
    return data


class SmartRecruiters(Platform):
    key = 'smartrecruiters'
    name = 'SmartRecruiters'
    site_word = 'career site'
    actor = 'smartrecruiters-jobs-search'
    board_example = 'https://careers.smartrecruiters.com/BoschGroup'
    job_example = 'https://jobs.smartrecruiters.com/BoschGroup/744000159116379-pmi-project-lead-em'
    concurrency = 64             # calls in flight; RATE paces them (96 and 128 in flight met Cloudflare's limit)
    server_search = True
    newest_first = True
    never = ('otherLocations', 'team', 'expiresAt', 'offersEquity', 'companyWebsite')
    _known: set[str] | None = None
    _left_out: dict[str, str] | None = None
    rate, next_call = RATE, 0.0

    def read_link(self, text):
        link = text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}'
        parsed = urlparse(link)
        host = (parsed.hostname or '').lower() if ' ' not in text.strip() else ''
        if host not in HOSTS:
            return None
        parts = [p for p in (unquote(p).strip() for p in parsed.path.split('/')) if p]   # BoschGroup%20 is BoschGroup
        query = parse_qs(parsed.query)
        named = (query.get('dcr_ci') or query.get('company') or [None])[0]
        if host == SHORT:                # the code keeps its case: the job is known only once the link is followed
            return (SHORT, parts[0]) if len(parts) == 1 and SHORT_CODE.match(parts[0]) else (None, None)
        if host == 'api.smartrecruiters.com':
            if len(parts) >= 3 and parts[:2] == ['v1', 'companies']:
                return parts[2], posting_id(parts[4]) if len(parts) > 4 and parts[3] == 'postings' else None
            return None
        if not parts or parts[0].lower() in NO_COMPANY:
            return named, None
        first = parts[0].lower()
        if first in APP_PATHS:
            if len(parts) >= 3 and parts[1] == 'company':
                return parts[2], posting_id(parts[4]) if len(parts) > 4 and parts[3] in ('publication', 'job') else None
            return named, None
        if first in COMPANY_FIRST:
            if len(parts) < 2:
                return named, None
            return parts[1], posting_id(parts[2]) if first == 'ni' and len(parts) > 2 else None
        job = posting_id(parts[1]) if len(parts) > 1 else None
        if host in MAIN_SITE and ((len(parts) > 1 and not job) or first in VENDOR):
            return None                  # www.smartrecruiters.com/resources/...: SmartRecruiters' own pages
        return parts[0], job

    def guess_board(self, name):
        left_out = self.left_out()
        found = left_out.get(name.strip().casefold()) or left_out.get(squeeze(name))
        if found:
            return found
        squeezed = re.sub(r'[^A-Za-z0-9-]+', '', name)
        return squeezed if COMPANY.match(squeezed) else None

    def left_out(self) -> dict[str, str]:
        if self._left_out is None:
            self._left_out = left_out_sites(resources.files('ats_jobs').joinpath(f'data/{self.key}.tsv').read_text(encoding='utf-8'))
        return self._left_out

    def known(self) -> set[str]:
        if self._known is None:
            self._known = {board.key.lower() for board in self.boards()[0]} | {k.lower() for k in self.left_out().values()}
        return self._known

    async def pace(self) -> None:
        """At most self.rate calls a second for the whole run: each call takes the next free moment."""
        if self.rate:
            now = time.monotonic()
            begin = max(now, self.next_call)
            self.next_call = begin + 1 / self.rate
            if begin > now:
                await asyncio.sleep(begin - now)

    async def open(self, http, board):
        if board.key == SHORT:
            return {'short': True}
        if not COMPANY.match(board.key):
            return None
        session = {'company': board.key}
        if board.key.lower() in self.known():
            return session
        # not on our list. A company with open jobs shows them; the company lookup tells one with none from one that
        # does not exist, but it knows only the companies in SmartRecruiters' job search (not AbbVie, 1,989 jobs)
        try:
            newest = (await self._list(http, session, {'limit': 1})).get('content') or []
        except BoardMissing:
            return None
        if newest:
            return {'company': clean(obj(obj(newest[0]).get('company')).get('identifier')) or board.key}
        await self.pace()
        response = await send(http, 'GET', NAME_LOOKUP, params={'q': board.key})
        if response.status_code != 200:
            raise BoardError(f'HTTP {response.status_code}')
        try:
            found = response.json()
        except ValueError as exc:
            raise BoardError('the company lookup was not JSON') from exc
        return {'company': found['identifier']} if isinstance(found, dict) and found.get('identifier') else None

    def job(self, session: dict, record: dict) -> dict:
        location = obj(record.get('location'))
        spot = place(location)
        company = obj(record.get('company'))
        job_id = str(record.get('id') or '')
        title = clean(record.get('name')) if isinstance(record.get('name'), str) else ''
        posted = record.get('releasedDate') if isinstance(record.get('releasedDate'), str) else None
        link = JOB_LINK.format(company=clean(company.get('identifier')) or session['company'],
                               job=f'{job_id}-{slug(title)}' if re.search(r'[a-z0-9]', slug(title)) else job_id)
        return {
            'id': job_id,
            'company': clean(company.get('name')) or None,
            'title': title,
            'location': spot['name'] if spot else None,
            'places': [spot] if spot else [],
            'workType': 'remote' if location.get('remote') else 'hybrid' if location.get('hybrid') else 'onsite',
            'employmentType': job_type(record.get('typeOfEmployment')),
            'department': clean(obj(record.get('department')).get('label')) or None,
            'postedAt': posted,
            'dateOnly': False,
            'jobUrl': link,
            'applyUrl': f'{link}?oga=true',
            'reference': clean(record.get('refNumber')) or None,
        }

    def enrich(self, job: dict, record: dict) -> dict:
        html = job_ad(record)
        return {**job, **pay(record.get('compensation')),
                'jobUrl': clean(record.get('postingUrl')) or job.get('jobUrl'),
                'applyUrl': clean(record.get('applyUrl')) or job.get('applyUrl'),
                'description': text_of(html), 'descriptionHtml': html}

    async def _list(self, http, session: dict, params: dict) -> dict:
        await self.pace()
        response = await send(http, 'GET', API.format(company=session['company']), params=params)
        if response.status_code == LOCKED:
            raise BoardMissing(session['company'])
        return answer(response)

    async def _pages(self, http, session: dict, params: dict):
        offset, size = 0, FIRST_PAGE_SEARCHED if 'q' in params else FIRST_PAGE
        while True:
            data = await self._list(http, session, {**params, 'limit': size, 'offset': offset})
            records = [r for r in data.get('content') or [] if isinstance(r, dict) and r.get('id')]
            if records:                  # a test posting loses its id: the engine passes over it but sees the page end
                yield [{**job, 'id': None} if TEST_TITLE.search(job['title']) else job
                       for job in (self.job(session, r) for r in records)]
            offset += len(data.get('content') or [])
            if not records or offset >= (data.get('totalFound') or 0):
                return
            size = PAGE_SIZE

    async def pages(self, http, session, term, days):
        if session.get('short'):         # a short link typed as a company names one job, not a career site
            raise BoardMissing(SHORT)
        params = {}
        if days:
            after = datetime.now(timezone.utc) - timedelta(days=days)
            params['releasedAfter'] = after.strftime('%Y-%m-%dT%H:%M:%S.000Z')
        said = WORD.findall(term.casefold()) if term else []     # sent with its accents: SmartRecruiters minds them
        if not said:
            async for page in self._pages(http, session, params):
                yield page
            return
        stream = self._pages(http, session, {**params, 'q': ' '.join(said)})
        if len(said) == 1 or not self.search_descriptions:
            async for page in stream:              # any word: the engine keeps the titles that hold every word
                yield page
            return
        # descriptions searched: a job counts when its title holds every word or its text holds the phrase. The
        # phrase search is read as far as the any-word pages go; a job that is neither loses its id, so the engine
        # passes over it and still sees where the page ends.
        wanted, phrases = Term(term), self._pages(http, session, {**params, 'q': f'"{" ".join(said)}"'})
        phrase_ids, phrase_end, phrases_done = set(), None, False
        async for page in stream:
            end = page[-1]['postedAt'] or ''
            while not phrases_done and (phrase_end is None or phrase_end >= end):
                try:
                    more = await anext(phrases)
                except StopAsyncIteration:
                    phrases_done = True
                    break
                phrase_ids.update(j['id'] for j in more)
                phrase_end = more[-1]['postedAt'] or ''
            yield [j if j['id'] in phrase_ids or wanted.in_title(index(j['title'])) else {**j, 'id': None} for j in page]

    async def _detail(self, http, session: dict, job_id: str) -> dict | None:
        """One posting, or None when SmartRecruiters says it cannot be read (missing()). BoardError for any other answer."""
        await self.pace()
        response = await send(http, 'GET', f'{API.format(company=session["company"])}/{job_id}')
        return None if missing(response) else answer(response)

    async def details(self, http, session, job):
        record = await self._detail(http, session, job['id'])
        return job if record is None else self.enrich(job, record)

    async def lookup(self, http, session, job_id):
        if session.get('short'):
            return await self.short_link(http, job_id)
        record = await self._detail(http, session, job_id)
        if record is None:
            return 'not found', None
        if record.get('active') is not True:
            return 'closed', None
        return 'open', self.enrich(self.job(session, record), record)

    async def short_link(self, http, code: str) -> tuple[str, dict | None]:
        """smrtr.io/<code>: where it leads (one call that does not follow it), then that job. The job carries its
        company, so a row names it rather than smrtr.io where the engine reads companySlug from the job."""
        await self.pace()
        response = await send(http, 'GET', f'https://{SHORT}/{code}', follow_redirects=False)
        if response.status_code == 404:
            return 'not found', None
        if response.status_code not in (301, 302, 303, 307, 308):
            raise BoardError(f'HTTP {response.status_code} from {SHORT}')
        link = self.read_link(response.headers.get('location') or '')
        if not link or not link[0] or not link[1] or link[0] == SHORT:
            return 'not found', None
        session = await self.open(http, Board(link[0], link[0]))
        record = await self._detail(http, session, link[1]) if session else None
        if record is None:
            return 'not found', None
        company = {'company': clean(obj(record.get('company')).get('name')) or None, 'companySlug': session['company']}
        if record.get('active') is not True:
            return 'closed', company
        return 'open', {**self.enrich(self.job(session, record), record), **company}
