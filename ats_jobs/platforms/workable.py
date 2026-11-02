"""
Workable (apply.workable.com) jobs: each company's career page through its own JSON, and a keyword across every
company through Workable's job board, the search behind jobs.workable.com. No key. Measured on 2026-11-02:

- GET https://apply.workable.com/api/v1/widget/accounts/<account>: one company's career page in one answer, the
  documented jobs widget (?details=true adds descriptions, 6.6 times the size). A job in several places comes once per
  place. published_on is the date the job page gives Google (datePosted, 10 of 10 pages rendered; created_at differed
  on 8 of them). No search, no order, no work type beyond telecommuting. 404 "Not Found" is no such account; the old
  https://www.workable.com/api/accounts/<account> redirects here.
- GET https://apply.workable.com/api/v2/accounts/<account>/jobs/<shortcode>: one job, the call its page makes: the
  description, requirements and benefits, pay (salary_from, salary_to, currency, frequency) when shown, workplace
  (on_site, hybrid, remote), type (full, part, contract, temporary, other), departments, published, state. 404 "Job
  not found" for a job that closed and for a code that never existed alike (12 old links): both read as closed.
- GET https://jobs.workable.com/api/v1/jobs?query=<words>&day_range=<1|7|30>&pageToken=<token>: Workable's job board,
  every published job of every company once per place (170,374 on 2026-11-02: 49,006 remote, 28,317 hybrid, 93,051
  on site), 20 a page with the description, created (the time of the listing, the datePosted of its job board page),
  workplace, employment type, department and the company's name and website. A word search comes back by relevance,
  and even the list with no word strays from newest first by up to 34 hours, so each day window (since yesterday, the
  last week, month, then all) is read whole and sorted here. Featured jobs show the time they were featured: their own
  answer (GET .../jobs/<id>, which also has pay) gives the real one. 410 is a job gone, 404 one that never was. It
  allowed about 600 searches from one address, then refused that address for a day ("contact us ... for our jobs XML
  feed", Retry-After 86,400) while the address's other family kept working.
- GET https://www.workable.com/boards/workable.xml: the public XML feed of the same job board (help.workable.com
  article 4420464031767: hourly, no filters), 861 MB, oldest listing first. Not read at run time: its first byte came
  after 19 to 23 s and 7 of 8 downloads were cut at 60 to 66 s (one brotli download finished at 51 s), and half a feed
  holds only old jobs; Cloudflare refused an address for an hour after about 9 downloads. The refresh script reads it.

Each run takes the route that fits it: no keyword reads the career pages, biggest company first, and stops once
maxJobs jobs posted today are held (dates are days); a keyword reads the job board (dates have a time). Named
companies always read their career pages.

Accounts are lower case (apply.workable.com/BIFFA answers 404, its page redirects to /oops); shortcodes are ten hex
digits in any case. Old links: <account>.workable.com/... and /<account>/jobs/<number> redirect to /<account>/j/<code>;
apply.workable.com/j/<code> redirects to the account's job, or to /oops once the job is gone. robots.txt of
apply.workable.com disallows nothing; jobs.workable.com disallows only its search pages.

Limits from one address: apply.workable.com answered 3 calls a second for 2 minutes and refused about half of 4 a
second after 70 s (HTTP 429, Retry-After 0, gone within seconds), so its calls wait their turn (PACE). The job board
answered searches one after another (each page's token leads to the next) and job answers at 4 a second.
"""
import asyncio
import re
import time
from urllib.parse import unquote, urlparse

from ..http import BoardError, send
from ..inputs import JOB_TYPE_ALIASES
from ..rows import folded_text, index, terms
from .base import Board, Platform
from .cornerstone import text_of

APPLY = 'https://apply.workable.com'
WIDGET = APPLY + '/api/v1/widget/accounts/{account}'
JOB = APPLY + '/api/v2/accounts/{account}/jobs/{code}'
SHORT_LINK = APPLY + '/j/{code}'
APPLY_LINK = APPLY + '/j/{code}/apply'
OLD_JOB = APPLY + '/{account}/jobs/{number}'
BOARD_HOST = 'jobs.workable.com'
SEARCH = f'https://{BOARD_HOST}/api/v1/jobs'
BOARD_JOB = SEARCH + '/{id}'
EVERYONE = Board('*', 'the Workable job board')   # every company at once; also the board of a link naming no account
HOSTS = ('apply.workable.com', 'www.workable.com', 'workable.com')
OWN_SITES = {'www', 'apply', 'jobs', 'resources', 'help', 'partners', 'partnerhelp', 'get', 'jobseekers', 'events',
             'blog', 'api', 'status', 'developers', 'app', 'mail', 'email'}      # Workable's own sites, not accounts
NOT_ACCOUNTS = {'j', 'api', 'oops', 'cdn-cgi', 'static', 'assets', 'embed', 'boards'}
ACCOUNT = re.compile(r'^[a-z0-9][a-z0-9-]{0,99}$')
CODE = re.compile(r'^[0-9a-f]{10}$', re.I)
NUMBER = re.compile(r'^\d{1,9}$')
BOARD_ID = re.compile(r'^(?:[1-9A-HJ-NP-Za-km-z]{20,24}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$')
CODE_IN_TEXT = re.compile(r'/j/([0-9a-f]{10})(?![0-9a-z])', re.I)
WINDOWS = ('1', '7', '30', None)       # the job board's day_range: since yesterday, last week, last month, any time
WINDOW_DAYS = {'1': 1, '7': 7, '30': 30}
PAGE = 100                             # job board jobs handed to the engine at a time
PACE = 0.4                    # seconds between two calls to apply.workable.com: 2.5 a second, under the 3 measured
WORKPLACE = {'remote': 'remote', 'hybrid': 'hybrid', 'on_site': 'onsite'}
TYPE = {'full': 'full-time', 'part': 'part-time', 'contract': 'contract', 'temporary': 'temporary'}
PERIOD = {'year': 'year', 'month': 'month', 'week': 'week', 'day': 'day', 'hour': 'hour'}


def clean(text) -> str:
    return ' '.join(str(text or '').split())


def place(entry: dict) -> dict | None:
    """{'city': 'High Wycombe', 'region': 'England', 'country': 'United Kingdom'} -> one place."""
    city, region, country = (clean(entry.get(k)) or None for k in ('city', 'region', 'country'))
    name = ', '.join(dict.fromkeys(p for p in (city, region, country) if p))
    return {'name': name, 'city': city, 'region': region, 'country': country} if name else None


def job_type(text: str | None) -> str | None:
    """'Full-time', 'Part-time', 'Contract', 'Temporary', 'Internship' -> the engine's job types; 'Other' -> None."""
    return JOB_TYPE_ALIASES.get(clean(text).lower())


def pay_text(low, high, currency: str | None, period: str | None) -> str | None:
    """28000, 32000, 'GBP', 'year' -> 'GBP 28,000 - 32,000 per year'. One amount when both are equal or one is missing."""
    amounts = list(dict.fromkeys(f'{v:,.2f}'.removesuffix('.00') for v in (low, high) if isinstance(v, (int, float)) and v > 0))
    if not amounts:
        return None
    return ' '.join(p for p in (currency, ' - '.join(amounts), f'per {period}' if period else None) if p)


def pay(low, high, currency, frequency) -> dict:
    """The salary fields of a row, filled only when Workable shows an amount."""
    period = PERIOD.get(frequency)
    shown = pay_text(low, high, currency or None, period)

    def amount(value):
        return value if shown and isinstance(value, (int, float)) and value > 0 else None
    return {'salary': shown, 'salaryMin': amount(low), 'salaryMax': amount(high),
            'salaryCurrency': (currency or None) if shown else None, 'salaryPeriod': period if shown else None}


def window(days: int | None) -> str | None:
    """The engine's days (the period asked plus one) -> the smallest job board window that holds the period."""
    if days is None:
        return None
    return next((w for w, n in WINDOW_DAYS.items() if days - 1 <= n), None)


def answer(response) -> dict | list:
    if response.status_code != 200:
        raise BoardError(f'HTTP {response.status_code}')
    try:
        return response.json()
    except ValueError as exc:
        raise BoardError('the answer was not JSON') from exc


def matches(term, job: dict) -> bool:
    """A word in a career page job's title, or in its description when the page gave one (as a search would)."""
    if term.in_title(index(job.get('title'))):
        return True
    if not job.get('description'):
        return False
    text, words = folded_text(job['description'])
    return term.in_text(text, words)


class Workable(Platform):
    key = 'workable'
    name = 'Workable'
    site_word = 'career page'
    actor = 'workable-jobs-search'
    board_example = 'https://apply.workable.com/huggingface/'
    job_example = 'https://apply.workable.com/huggingface/j/DB4D7C0EC8/'
    concurrency = 4               # career pages at once; PACE keeps their rate
    server_search = True          # the job board searches words; a career page is filtered here
    newest_first = True           # the job board's windows are sorted here; a career page is one page
    never = ('team', 'offersEquity', 'expiresAt')

    def __init__(self):
        self._turn = asyncio.Lock()
        self._last = 0.0
        self._known: dict[str, Board] | None = None
        self._names: dict[str, str] | None = None
        self.cfg = None

    # ---------- links and names ----------

    def read_link(self, text):
        link = text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}'
        parsed = urlparse(link)
        host = (parsed.hostname or '').lower() if ' ' not in text.strip() else ''
        if not host.endswith('workable.com'):
            return None
        raw = [unquote(p) for p in parsed.path.split('/') if p]
        parts = [p.lower() for p in raw]
        found = CODE_IN_TEXT.search(unquote(parsed.query))       # ?lng=en/j/<code>, as some job boards mangle links
        if host == BOARD_HOST:                                   # Workable's job board: its own job ids
            if len(parts) > 1 and parts[0] == 'view' and BOARD_ID.match(raw[1]):
                return EVERYONE.key, raw[1]
            return (None, None) if parts[:1] in ([], ['company'], ['search']) else None
        if host in HOSTS:
            if parts[:3] in (['api', 'v1', 'widget'], ['api', 'v3', 'accounts'], ['api', 'v2', 'accounts'],
                             ['api', 'v1', 'accounts']) and len(parts) > 3:
                parts = parts[4:] if parts[2] == 'widget' else parts[3:]
                parts = [parts[0], 'j', parts[2]] if len(parts) > 2 and parts[1] == 'jobs' else parts[:1]
            elif parts[:2] == ['api', 'accounts'] and len(parts) > 2:          # the old www.workable.com API
                parts = parts[2:3]
            elif host != 'apply.workable.com' and parts[:1] != ['j']:
                return (None, None) if parts[:2] == ['boards', 'workable.xml'] else None
        elif host.endswith('.workable.com'):
            account = host.removesuffix('.workable.com')
            if account in OWN_SITES or '.' in account:
                return None
            parts = [account, *parts]
        else:
            return None
        if not parts:
            return None, None
        if parts[0] == 'j':
            code = parts[1] if len(parts) > 1 and CODE.match(parts[1]) else None
            return (EVERYONE.key, code.upper()) if code else (None, None)
        account = parts[0]
        if account in NOT_ACCOUNTS or not ACCOUNT.match(account):
            return None, None
        job = None
        if len(parts) > 2 and parts[1] == 'j' and CODE.match(parts[2]):
            job = parts[2].upper()
        elif len(parts) > 3 and parts[1:3] == ['jobs', 'view'] and CODE.match(parts[3].removesuffix('.md')):
            job = parts[3].removesuffix('.md').upper()
        elif len(parts) > 2 and parts[1] in ('jobs', 'j') and NUMBER.match(parts[2]):
            job = parts[2]                         # an old job number: its page redirects to the shortcode
        elif found:
            job = found.group(1).upper()
        return account, job

    def aliases(self, board: Board) -> list[str]:
        """A company's website finds it too: biffa.co.uk -> biffa."""
        if not board.website:
            return []
        host = urlparse(board.website if '://' in board.website else f'https://{board.website}').hostname or ''
        return [host.removeprefix('www.')] if host else []

    def guess_board(self, name):
        slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
        return slug if ACCOUNT.match(slug) else None

    def known(self) -> dict[str, Board]:
        if self._known is None:
            self._known = {b.key: b for b in self.boards()[0]}
        return self._known

    def names(self) -> dict[str, str]:
        """Company name, folded -> its account, for the names only one account on our list carries."""
        if self._names is None:
            seen: dict[str, list[str]] = {}
            for board in self.known().values():
                seen.setdefault(board.name.casefold(), []).append(board.key)
            self._names = {name: keys[0] for name, keys in seen.items() if len(keys) == 1}
        return self._names

    def search_all(self, boards, cfg):
        self.cfg = cfg
        return [EVERYONE] if cfg.search_terms else boards

    # ---------- calls to apply.workable.com, one at a time ----------

    async def call(self, http, method: str, url: str, **kwargs):
        async with self._turn:
            wait = self._last + PACE - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
        return await send(http, method, url, **kwargs)

    async def career_page(self, http, account: str, descriptions: bool = False) -> dict | None:
        """The widget answer, or None when there is no such account."""
        if not ACCOUNT.match(account):
            return None
        url = WIDGET.format(account=account) + ('?details=true' if descriptions else '')
        response = await self.call(http, 'GET', url)
        if response.status_code == 404:
            return None
        data = answer(response)
        if not isinstance(data, dict) or not isinstance(data.get('jobs'), list):
            raise BoardError('the career page answer had no job list')
        return data

    # ---------- one company's career page ----------

    async def open(self, http, board):
        if board.key == EVERYONE.key:
            return {'everyone': True}
        wanted = bool(self.cfg and self.cfg.search_descriptions and self.cfg.search_terms)
        data = await self.career_page(http, board.key, descriptions=wanted)
        if data is None:
            return None
        known = self.known().get(board.key)
        return {'account': board.key, 'name': clean(data.get('name')) or board.name,
                'website': known.website if known else None, 'jobs': data['jobs']}

    def from_widget(self, session: dict, entries: list[dict]) -> list[dict]:
        """Widget entries -> jobs, one per shortcode with every place of it."""
        jobs: dict[str, dict] = {}
        for entry in entries:
            code = clean(entry.get('shortcode')).upper()
            if not CODE.match(code):
                continue
            places = [p for p in (place(loc) for loc in entry.get('locations') or [entry]) if p]
            if code in jobs:
                held = jobs[code]
                held['places'] += [p for p in places if p['name'] not in {q['name'] for q in held['places']}]
                continue
            html = entry.get('description') or None
            jobs[code] = {
                'id': code,
                'company': session['name'],
                'companySlug': session['account'],
                'companyWebsite': session.get('website'),
                'title': clean(entry.get('title')),
                'places': places,
                'workType': 'remote' if entry.get('telecommuting') is True else None,
                'employmentType': job_type(entry.get('employment_type')),
                'department': clean(entry.get('department')) or None,
                'reference': clean(entry.get('code')) or None,
                'postedAt': clean(entry.get('published_on')) or None,
                'dateOnly': True,
                'jobUrl': SHORT_LINK.format(code=code),
                'applyUrl': APPLY_LINK.format(code=code),
                'description': text_of(html),
                'descriptionHtml': html,
            }
        for job in jobs.values():
            job['location'] = job['places'][0]['name'] if job['places'] else None
            job['otherLocations'] = [p['name'] for p in job['places'][1:]]
        return list(jobs.values())

    async def pages(self, http, session, term, days):
        if session.get('everyone'):
            async for page in self.board_pages(http, term, days):
                yield page
            return
        jobs = self.from_widget(session, session['jobs'])
        if term:        # a career page has no search: the word is matched here, as the job board would have
            wanted = terms([term])
            jobs = [j for j in jobs if wanted and matches(wanted[0], j)]
        if jobs:
            yield jobs

    # ---------- every company: Workable's job board ----------

    def from_board(self, job_id: str, data: dict) -> dict:
        """A job of Workable's job board: created is the datePosted of its page; it names no account or shortcode."""
        company = data.get('company') or {}
        name = clean(company.get('title')) or None
        texts = [clean(t) for t in data.get('locations') or [] if clean(t) and clean(t) != 'TELECOMMUTE']
        first = data.get('location') or {}
        places = [{'name': t} for t in dict.fromkeys(texts)]
        if places and first.get('countryName'):
            places[0].update(city=clean(first.get('city')) or None, region=clean(first.get('subregion')) or None,
                             country=clean(first.get('countryName')))
        sections = [x for x in (data.get(k) for k in ('description', 'requirementsSection', 'benefitsSection')) if x]
        html = ''.join(sections) or None
        url = clean(data.get('url')) or f'https://{BOARD_HOST}/view/{job_id}'
        remote = 'remote' if 'TELECOMMUTE' in (data.get('locations') or []) else None
        return {
            'id': job_id, 'company': name, 'companySlug': self.names().get(name.casefold()) if name else None,
            'companyWebsite': clean(company.get('website')) or None, 'title': clean(data.get('title')),
            'places': places, 'location': places[0]['name'] if places else None,
            'otherLocations': [p['name'] for p in places[1:]],
            'workType': WORKPLACE.get(data.get('workplace')) or remote,
            'employmentType': job_type(data.get('employmentType')), 'department': clean(data.get('department')) or None,
            **pay(data.get('salaryFrom'), data.get('salaryTo'), data.get('salaryCurrencyIsoCode'), data.get('salaryFrequency')),
            'postedAt': clean(data.get('created')) or None, 'dateOnly': False, 'jobUrl': url, 'applyUrl': url,
            'description': text_of(html), 'descriptionHtml': html,
        }

    async def search(self, http, params: dict) -> list[dict]:
        """Every job of one job board search, page after page (each page's token leads to the next)."""
        items, token = [], None
        while True:
            response = await send(http, 'GET', SEARCH, params={**params, **({'pageToken': token} if token else {})})
            if response.status_code == 429:
                raise BoardError("HTTP 429: Workable's job board allows a few hundred searches a day from one address. "
                                 'Try again later, or name companies to read their career pages')
            data = answer(response)
            page = data.get('jobs') if isinstance(data, dict) else None
            if not isinstance(page, list):
                raise BoardError('the job board answer had no job list')
            items += page
            token = data.get('nextPageToken')
            if not token or not page:
                return items

    async def board_pages(self, http, term, days):
        """A word across every company, newest first: each day window read whole and sorted, the jobs already given
        left out. The engine stops at the window that can no longer make the cut."""
        given: set[str] = set()
        for span in (window(days),) if days is not None else WINDOWS:
            params = {'query': term or ''} | ({'day_range': span} if span else {})
            jobs = []
            for item in await self.search(http, params):
                job_id = clean(item.get('id'))
                if not job_id or job_id in given:
                    continue
                given.add(job_id)
                if item.get('isFeatured'):     # its created is when it was featured: its own answer has the real one
                    found = await send(http, 'GET', BOARD_JOB.format(id=job_id))
                    if found.status_code != 200:
                        continue
                    item = {**item, 'created': (answer(found) or {}).get('created')}
                jobs.append(self.from_board(job_id, item))
            jobs.sort(key=lambda j: j['postedAt'] or '', reverse=True)
            for i in range(0, len(jobs), PAGE):
                yield jobs[i:i + PAGE]

    # ---------- one job ----------

    async def job_answer(self, http, account: str, code: str) -> dict | None:
        """The job's own JSON, or None when Workable says there is no such job."""
        response = await self.call(http, 'GET', JOB.format(account=account, code=code))
        if response.status_code == 404:
            return None
        data = answer(response)
        if not isinstance(data, dict):
            raise BoardError('the job answer was not an object')
        return data

    def enrich(self, job: dict, data: dict) -> dict:
        sections = [s for s in (data.get(k) for k in ('description', 'requirements', 'benefits')) if s]
        html = ''.join(sections) or job.get('descriptionHtml')
        places = [p for p in (place(loc) for loc in data.get('locations') or []) if p] or job.get('places') or []
        departments = ', '.join(clean(d) for d in data.get('department') or [] if clean(d)) or None
        return {
            **job,
            'title': clean(data.get('title')) or job.get('title'),
            'places': places,
            'location': places[0]['name'] if places else job.get('location'),
            'otherLocations': [p['name'] for p in places[1:]],
            'workType': WORKPLACE.get(data.get('workplace')) or ('remote' if data.get('remote') is True else job.get('workType')),
            'employmentType': TYPE.get(data.get('type')) or job.get('employmentType'),
            'department': departments or job.get('department'),
            'reference': clean(data.get('code')) or job.get('reference'),
            **pay(data.get('salary_from'), data.get('salary_to'), data.get('salary_currency_iso_code'), data.get('salary_frequency')),
            'postedAt': job.get('postedAt') or clean(data.get('published'))[:10] or None,
            'description': text_of(html),
            'descriptionHtml': html,
        }

    async def account_of(self, http, code: str) -> str | None:
        """apply.workable.com/j/<code> redirects to /<account>/j/<code>, or to /oops when the job is gone."""
        response = await self.call(http, 'GET', SHORT_LINK.format(code=code), follow_redirects=False)
        found = re.match(r'^(?:https://apply\.workable\.com)?/([a-z0-9-]+)/j/', response.headers.get('location') or '')
        if response.status_code in (301, 302, 307, 308):
            return found.group(1) if found and found.group(1) not in NOT_ACCOUNTS else None
        if response.status_code == 404:
            return None
        raise BoardError(f'HTTP {response.status_code}')

    async def code_of(self, http, account: str, number: str) -> str | None:
        """An old job number -> its shortcode, from the redirect of /<account>/jobs/<number>."""
        response = await self.call(http, 'GET', OLD_JOB.format(account=account, number=number), follow_redirects=False)
        found = re.search(r'/j/([0-9A-Fa-f]{10})', response.headers.get('location') or '')
        if response.status_code in (301, 302, 307, 308, 404):
            return found.group(1).upper() if found else None
        raise BoardError(f'HTTP {response.status_code}')

    async def details(self, http, session, job):
        if not CODE.match(job['id']):                   # a job board job: its own answer adds the pay
            response = await send(http, 'GET', BOARD_JOB.format(id=job['id']))
            if response.status_code != 200:
                return job
            return {**self.from_board(job['id'], answer(response)), 'companySlug': job.get('companySlug')}
        account = job.get('companySlug') or (session or {}).get('account') or await self.account_of(http, job['id'])
        data = await self.job_answer(http, account, job['id']) if account else None
        return self.enrich(job, data) if data else job

    async def lookup(self, http, session, job_id):
        if BOARD_ID.match(job_id) and not CODE.match(job_id):     # a jobs.workable.com link: 410 is gone, 404 never was
            response = await send(http, 'GET', BOARD_JOB.format(id=job_id))
            if response.status_code in (404, 410):
                return ('closed' if response.status_code == 410 else 'not found'), None
            data = answer(response)
            if not isinstance(data, dict) or data.get('state') not in (None, 'published'):
                return 'closed', None
            return 'open', self.from_board(job_id, data)
        account = session.get('account')
        if NUMBER.match(job_id):
            job_id = await self.code_of(http, account, job_id) if account else None
            if not job_id:
                return 'closed', None
        if not account:                     # a link with no account: its redirect names it
            account = await self.account_of(http, job_id)
            session = await self.open(http, self.known().get(account) or Board(account, account)) if account else None
            if session is None:
                return 'closed', {'company': None, 'companySlug': None, 'companyWebsite': None}
        listed = {job['id']: job for job in self.from_widget(session, session['jobs'])}
        data = await self.job_answer(http, account, job_id)
        if job_id not in listed and (data is None or data.get('state') not in (None, 'published')):
            return 'closed', {'company': session['name'], 'companySlug': account, 'companyWebsite': session.get('website')}
        job = listed.get(job_id) or {
            'id': job_id, 'company': session['name'], 'companySlug': account, 'companyWebsite': session.get('website'),
            'title': '', 'places': [], 'dateOnly': True, 'jobUrl': SHORT_LINK.format(code=job_id),
            'applyUrl': APPLY_LINK.format(code=job_id)}
        return 'open', self.enrich(job, data) if data else job
