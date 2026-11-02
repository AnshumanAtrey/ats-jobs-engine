"""
Greenhouse job boards (job-boards.greenhouse.io, the older boards.greenhouse.io and the EU hosts), read through
Greenhouse's public Job Board API. No key, no browser. Measured 2026-11-02.

- GET https://api.greenhouse.io/v1/boards/<token>/jobs?pay_transparency=true: every open job of one board in a single
  answer, with no paging, no search and no order (the order changes from one call to the next). Each job has its
  title, location text, first_published, updated_at, absolute_url, company_name, requisition_id, language,
  application_deadline, the employer's custom fields (metadata) and the pay ranges the employer published
  (pay_input_ranges, in cents). Stripe: 748 jobs, 467 KB, 39 KB gzipped. content=true adds the description (escaped
  HTML), departments and offices: 5.8 MB, 2 MB gzipped, so descriptions are read only when asked for.
- GET .../<token>/jobs/<id>?pay_transparency=true: one job with its description. A closed job, an id that never
  existed and a job of another board all answer 404 "Job not found": Greenhouse does not tell them apart, here or
  on its job pages (a 302 to the board with ?error=true for each).
- GET .../<token>: the board's name; 404 "Job board not found" when there is no such board.

The same API answers, with the same data, at boards-api.greenhouse.io (the address in Greenhouse's documentation) and
at api.greenhouse.io (its older address). CloudFront in front of boards-api blocked this laptop with HTTP 403 for one
to two minutes once about 5,800 calls came in one minute (a steady 90 calls a second passed for 70 s, 110 did not);
api.greenhouse.io took 130 a second for 70 s with no block. So api.greenhouse.io is read first, and an address that
refuses (CloudFront's 403), answers with a page that is not the API's (api.greenhouse.io gives an HTML 404 for a path it
does not serve; api.eu.greenhouse.io a sign-in page) or does not answer after the retries (four read timeouts in a row
lost every Anduril job of one pass on 2026-11-02) goes to the back: every API answer is JSON, a missing board's 404
too. A scan of 16,000 board names that mostly do not exist (404) then got
406 from every Greenhouse address for this IP for 3 minutes, while 129,032 calls to live boards in the 41 minutes
before had not: reading the boards of the list makes few 404s. A refusal is waited out within the run's time. No answer carried a Retry-After.
A run is paced to 80 calls a second, below both, whatever the latency.

Dates carry a time, so no board order lets a run stop early: the 100 newest jobs came from boards at places 4 to 5,711
of the list. A pass over all 8,378 boards is 8,378 calls and 19.8 MB gzipped (pay ranges add 6.6%), 112 s on this
laptop. A description search downloads every description (about 600 MB gzipped), and a first look at each escaped
text skips the work on those that cannot match: 44 s of CPU instead of 150 s for kubernetes. One board's list with
descriptions holds up to about 150 MB while it is read (body and parsed JSON: Pulse 152 MB, Anduril 125, Prolific 115,
2026-11-03), and boards go biggest first, so the lists read at once follow the run's memory.

Tokens are case-insensitive (stripe, Stripe and STRIPE are one board) and hold letters, digits, - and _. EU boards
(job-boards.eu.greenhouse.io) use the same tokens and the same API. A board whose company shows its jobs on its own
site gives that site in absolute_url (stripe.com/jobs/search?gh_jid=...), so such a link is read through the
company's site, kept in the board list.

postedAt is first_published: the board pages' own data (published_at) equals it on 1,482 of 1,482 jobs (GitLab,
Anthropic, Fever), and the page uses it for its "New" tag. updated_at moves with every edit: 70% of 224,331 open jobs
were edited more than a week after they were first published, and 6,692 had an edit in the last 24 hours against 952
first published then. Greenhouse's job pages carry no JobPosting markup; employers' own career pages use either date.

Pay ranges have an amount and a currency but no period, and employers label them in their own words ("Hourly Pay
Range", "Zone 1 Pay Range"), so no period is stated; MongoDB publishes a 1-cent placeholder, which is no pay. Work type
and job type exist only as employers' custom fields, named freely (Employment Type, employment_type, Work Location
Type, Remote?), so a field is known by its words and read when its value says one known word; with no such field, the
location text says the work type ("Denver, CO - Hybrid", "Los Angeles, CA (On-site)"). Locations are free text,
several joined with ; or |; one employer pastes its job ad there.

robots.txt on boards-api.greenhouse.io and boards.greenhouse.io disallows /embed/ only, and the adapter reads no
/embed/ address.
"""
import asyncio
import html as htmllib
import os
import re
import time
from datetime import timezone
from urllib.parse import parse_qs, urlparse

from ..http import BoardError, BoardMissing, send
from ..inputs import JOB_TYPE_ALIASES, WORK_TYPE_ALIASES
from ..rows import Term, parse_time, terms
from .base import Board, Platform
from .cornerstone import text_of
from .deel import pay_text

HOSTS = ('api.greenhouse.io', 'boards-api.greenhouse.io')     # one API; the first answers more calls before a block
BOARD_HOST = re.compile(r'^(?:job-)?boards(?:\.eu)?\.greenhouse\.io$')
API_HOST = re.compile(r'^(?:boards-)?api(?:\.eu)?\.greenhouse\.io$')
APP_HOST = 'app.greenhouse.io'
NOT_BOARDS = {'embed', 'internal_job_board', 'live_preview', 'badges', 'seek', 'liveness', 'readiness', 'users'}
HOSTED_JOB = 'https://job-boards.greenhouse.io/{token}/jobs/{job}'
TOKEN = re.compile(r'^[a-z0-9][a-z0-9_-]{0,99}$', re.I)
JOB_ID = re.compile(r'\d{1,20}')     # the digits a job id starts with: 8638232002 in /jobs/8638232002&gh_src=x
INVISIBLE = re.compile(r'[​-‍⁠﻿]')     # zero-width characters pasted with a link
LINK_END = '.,;:!?)]}>"\''           # punctuation a link pasted from a sentence ends with
DOMAIN = re.compile(r'^(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)/?$')
SECOND_LEVEL = {'co', 'com', 'org', 'net', 'ac', 'gov', 'edu'}            # acme.co.uk
COMPANY_SUFFIX = re.compile(r'[\s,]+(?:inc|llc|ltd|limited|corp|corporation|gmbh|plc)\.?$')
BLOCK_WAIT_S = (30, 60, 90)          # blocks lifted 2 (CloudFront 403) and 3 minutes (406) after the last call
RATE = 80                            # calls a second at most for a run: 4,800 a minute, under the 5,800 a minute
                                     # that made CloudFront block boards-api, whatever the latency from Apify
HEAVY_MB = 150                       # the most one list with descriptions held while read: Pulse 152 MB, Anduril
RUN_MB = 400                         # 125, Prolific 115 (2026-11-03); and what the rest of a run keeps
SENTENCE = re.compile(r'[a-z]\.\s+[A-Z]')
ODD_ENTITY = re.compile(r'&amp;(?!(?:nbsp|amp|quot|lt|gt|apos|[lr][sd]quo|[mn]dash|hellip|bull|middot);)#?\w+;', re.I)
# Employers name their custom fields freely ('Work Location Type', 'employment_type', 'Remote?', 'Job Location Type
# (Hybrid, Onsite, Remote)'), so a field is known by its words. A field that lists places is not a work type.
WORK_FIELD = re.compile(r'\b(?:remote|workplace|work (?:location|arrangement|model|setting|type|style|environment|mode)'
                        r'|working (?:model|arrangement)|location type)\b')
JOB_FIELD = re.compile(r'\b(?:employment|time type|job type|position type|worker type|employee type|contract type)\b')
NOT_WORK_FIELD = re.compile(r'\b(?:countries|country|states|cities|locations|wrapping)\b')
NOT_JOB_FIELD = re.compile(r'\b(?:length|duration|date|start|end|category)\b')
LETTERS = re.compile(r'[a-z]+')


def phrases(aliases: dict[str, str]) -> list[tuple[str, str]]:
    """{'on-site': 'onsite'} -> [(' on site ', 'onsite')]: the words of each alias, padded, to find inside a value."""
    return [(f' {" ".join(LETTERS.findall(word))} ', kind) for word, kind in aliases.items()]


WORK_PHRASES, JOB_PHRASES = phrases(WORK_TYPE_ALIASES), phrases(JOB_TYPE_ALIASES)
# In a location, Office names a place (Tokyo Office) and Flexible a choice of places (Flexible - Any SpaceX Site)
PLACE_WORK_PHRASES = phrases({k: v for k, v in WORK_TYPE_ALIASES.items() if k not in ('office', 'flexible')})


def clean(value) -> str:
    return ' '.join(str(value).split()) if value is not None else ''


def utc(value) -> str | None:
    """'2026-10-19T11:17:24-04:00' -> '2026-10-19T15:17:24Z', so dates sort as text across employers."""
    moment = parse_time(value) if isinstance(value, str) else None
    return moment.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z') if moment else None


def places_of(location) -> list[dict]:
    """'New York, NY; San Francisco, CA | Remote' -> one place each. A job ad pasted as the location is no place."""
    text = clean(location.get('name')) if isinstance(location, dict) else ''
    if len(text) > 300 and len(SENTENCE.findall(text)) >= 2:
        return []
    return [{'name': part} for part in dict.fromkeys(p.strip() for p in re.split(r'[;|]', text)) if part]


def job_id(text) -> str | None:
    found = JOB_ID.match(text) if isinstance(text, str) else None
    return found.group(0) if found else None


def stated(value, known: list[tuple[str, str]]) -> str | None:
    """A custom field's value -> the one kind its words name, else None ('Regular', 'Full-time or Part-time')."""
    if isinstance(value, list):
        value = ' / '.join(str(v) for v in value if isinstance(v, (str, int, float)))
    if not isinstance(value, str):
        return None
    text = f' {" ".join(LETTERS.findall(value.lower()))} '
    kinds = {kind for words, kind in known if words in text}
    if kinds == {'hybrid', 'onsite'}:          # "Hybrid, 3 days in office"
        return 'hybrid'
    return kinds.pop() if len(kinds) == 1 else None


def field_name(name) -> str:
    """'Employment_Type' -> 'employment type', 'Remote?' -> 'remote'."""
    return ' '.join(re.findall(r'[a-z0-9]+', str(name or '').lower()))


def custom_fields(metadata: list | None) -> tuple[str | None, str | None]:
    """The employer's custom fields -> (work type, job type), when a field about one names one. A work-type field
    can also state the job type ('Work Type: Full-time')."""
    work = kind = None
    for field in metadata if isinstance(metadata, list) else ():
        if not isinstance(field, dict):
            continue
        name, value = field_name(field.get('name')), field.get('value')
        if WORK_FIELD.search(name) and not NOT_WORK_FIELD.search(name):
            if work is None:
                work = 'remote' if value is True else stated(value, WORK_PHRASES)
            if kind is None:
                kind = stated(value, JOB_PHRASES)
        elif JOB_FIELD.search(name) and not NOT_JOB_FIELD.search(name) and kind is None:
            kind = stated(value, JOB_PHRASES)
    return work, kind


def pay_of(ranges: list | None) -> tuple[str | None, float | None, float | None, str | None]:
    """Greenhouse pay ranges -> (text of every range, lowest, highest, currency). The numbers are those of the first
    range's currency; amounts come in cents. A range under one unit is a placeholder (MongoDB's 1 cent), not pay."""
    good = []
    for r in ranges if isinstance(ranges, list) else []:
        if not isinstance(r, dict) or not r.get('currency_type'):
            continue
        low, high = (r.get(k) / 100 if isinstance(r.get(k), (int, float)) and r.get(k) > 0 else None
                     for k in ('min_cents', 'max_cents'))
        if max((v for v in (low, high) if v is not None), default=0) >= 1:
            good.append((low, high, r['currency_type']))
    if not good:
        return None, None, None, None
    text = '; '.join(dict.fromkeys(pay_text(low, high if high != low else None, cur) for low, high, cur in good))
    currency = good[0][2]
    amounts = [v for low, high, cur in good if cur == currency for v in (low, high) if v is not None]
    return text, min(amounts), max(amounts), currency


def names(items: list | None) -> str | None:
    """[{'name': 'Data Engineering'}] -> 'Data Engineering'."""
    found = [clean(i.get('name')) for i in items if isinstance(i, dict)] if isinstance(items, list) else []
    return ', '.join(dict.fromkeys(n for n in found if n)) or None


def may_hold(content: str, wanted: list[Term]) -> bool:
    """A cheap first look at a description, still escaped: can it hold one of the words searched? Every word of a term
    is a prefix of the word it folds from, so a word that is not in the text is not in the description either. A
    letter written as an entity would hide a word, so such text is looked at in full (none in 2,765 jobs)."""
    text = content.casefold()
    return bool(ODD_ENTITY.search(text)) or any(all(t in text for t in term.tokens) or (term.joined or '\0') in text
                                                for term in wanted)


def heavy_at_once() -> int:
    """Lists with descriptions read at once. Boards are read biggest first, and each of the biggest holds up to about
    150 MB while it is read, so the number follows the run's memory: 4 at 1 GB, 10 at 2 GB, 24 at 4 GB
    (ACTOR_MEMORY_MBYTES is set on Apify; 32 when it is not)."""
    memory = int(os.environ.get('ACTOR_MEMORY_MBYTES') or 0)
    return max(1, min(32, (memory - RUN_MB) // HEAVY_MB)) if memory else 32


def from_api(response) -> bool:
    """The API answers in JSON, its 404s for a missing board or a closed job included. Anything else came from in
    front of it: a block page, a sign-in page, an HTML 404 for a path it does not serve."""
    return 'json' in response.headers.get('content-type', '')


def blocked(response) -> bool:
    """A refusal of this IP address: CloudFront's 403 page on one address, or the 406 page every Greenhouse address
    gave after a scan of 16,000 board names that mostly do not exist."""
    return response.status_code in (403, 406) and not from_api(response)


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


class Greenhouse(Platform):
    key = 'greenhouse'
    name = 'Greenhouse'
    site_word = 'job board'
    actor = 'greenhouse-jobs-search'
    board_example = 'https://job-boards.greenhouse.io/gitlab'
    job_example = 'https://job-boards.greenhouse.io/gitlab/jobs/8638232002'
    concurrency = 32
    never = ('team', 'salaryPeriod', 'offersEquity', 'city', 'region', 'country', 'countryCode')

    def __init__(self):
        self.hosts = list(HOSTS)          # this run's order: an address that blocks goes to the back
        self.heavy = asyncio.Semaphore(heavy_at_once())
        self.rate, self.next_call = RATE, 0.0
        self._sites: dict[str, str] | None = None
        self._tokens: set[str] | None = None

    # ---------- links and names ----------

    def listed(self) -> list[Board]:
        try:
            return self.boards()[0]
        except FileNotFoundError:                 # before the first refresh wrote the list
            return []

    def sites(self) -> dict[str, str]:
        """Company career sites (from the board list) -> board token, for links such as stripe.com/jobs?gh_jid=1."""
        if self._sites is None:
            seen: dict[str, set] = {}
            for board in self.listed():
                host = (urlparse(board.website or '').hostname or '').lower().removeprefix('www.')
                if host:
                    seen.setdefault(host, set()).add(board.key.lower())
            self._sites = {host: next(iter(keys)) for host, keys in seen.items() if len(keys) == 1}
        return self._sites

    def tokens(self) -> set[str]:
        if self._tokens is None:
            self._tokens = {board.key.lower() for board in self.listed()}
        return self._tokens

    def aliases(self, board: Board) -> list[str]:
        host = (urlparse(board.website or '').hostname or '').lower()
        return list(dict.fromkeys(h for h in (host, host.removeprefix('www.')) if h))

    def read_link(self, text):
        text = INVISIBLE.sub('', text).strip().rstrip(LINK_END)
        link = text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}'
        try:
            parsed = urlparse(link)
            host = (parsed.hostname or '').lower() if ' ' not in text else ''
        except ValueError:
            return None
        query = {k.lower(): v[0].strip() for k, v in parse_qs(parsed.query).items() if v}
        parts = [p for p in parsed.path.split('/') if p]
        lower = [p.lower() for p in parts]
        token = job = None
        if BOARD_HOST.match(host) or host == APP_HOST:
            if lower[:1] == ['embed']:
                token, job = query.get('for'), query.get('token') or query.get('gh_jid')
            elif host != APP_HOST and parts and lower[0] not in NOT_BOARDS:
                token = parts[0]
                job = parts[2] if len(parts) > 2 and lower[1] == 'jobs' else query.get('gh_jid')
        elif API_HOST.match(host):
            if lower[:2] == ['v1', 'boards'] and len(parts) > 2:
                token = parts[2]
                job = parts[4] if len(parts) > 4 and lower[3] == 'jobs' else query.get('id')
        elif host and job_id(query.get('gh_jid')):
            token = self.sites().get(host.removeprefix('www.'))
            return (token, job_id(query['gh_jid'])) if token else None
        else:
            return None
        token = token.lower() if token and TOKEN.match(token) else None
        return (token, job_id(job)) if token else (None, None)

    def guess_board(self, name):
        """A company typed that the names do not find -> the token to try: 'Acme Corp' -> acmecorp, 'anthropic.com' ->
        anthropic. A company suffix stays (tokens keep Inc 198 times against 67, LLC 60 against 16, on the list of
        2026-11-02) unless the name without it is a board on the list: 'Databricks Inc' -> databricks."""
        typed = name.strip().lower()
        domain = DOMAIN.match(typed)
        if domain:
            labels = domain.group(1).split('.')
            typed = labels[-3] if len(labels) > 2 and labels[-2] in SECOND_LEVEL else labels[-2]
        squeezed = typed if TOKEN.match(typed) else re.sub(r'[^a-z0-9]', '', typed)
        bare = re.sub(r'[^a-z0-9]', '', COMPANY_SUFFIX.sub('', typed))
        return bare if bare and bare != squeezed and bare in self.tokens() else squeezed or None

    # ---------- reading ----------

    async def pace(self) -> None:
        """At most self.rate calls a second for the whole run: each call takes the next free moment."""
        if self.rate:
            now = time.monotonic()
            begin = max(now, self.next_call)
            self.next_call = begin + 1 / self.rate
            if begin > now:
                await asyncio.sleep(begin - now)

    async def get(self, http, path: str, params: dict | None = None):
        """One answer of the API, from whichever address gives one. An address that refuses this IP, answers with a page
        that is not the API's, or does not answer after send()'s retries goes to the back, so the run keeps to the one
        that works. A refusal by both is waited out within the run's time; BoardError when no address answered."""
        from ..engine import RUN_SAFETY_MARGIN_S, seconds_left_in_run     # the engine imports the platforms
        for wait in (*BLOCK_WAIT_S, None):
            refused, failed = None, None
            for host in list(self.hosts):
                await self.pace()
                try:
                    response = await send(http, 'GET', f'https://{host}/v1/boards/{path}', params=params)
                except BoardError as exc:                   # timeouts, DNS, TLS or 5xx after the retries
                    failed = str(exc) or type(exc).__name__
                else:
                    if from_api(response):
                        return response
                    if blocked(response):
                        refused = response.status_code
                    else:
                        failed = f'HTTP {response.status_code} with a page that is not the API\'s answer'
                if self.hosts[0] == host:
                    self.hosts.append(self.hosts.pop(0))
            left = seconds_left_in_run()
            if refused is None or wait is None or (left is not None and left < wait + RUN_SAFETY_MARGIN_S + 10):
                break
            await asyncio.sleep(wait)
        if refused:
            raise BoardError(f'Greenhouse refused the calls from this IP address (HTTP {refused} from both API '
                             'addresses): it blocks an address that made too many calls for a while, so try again later')
        raise BoardError(f'neither Greenhouse API address answered ({failed})')

    async def open(self, http, board):
        token = board.key.strip().lower()
        return {'token': token} if TOKEN.match(token) else None

    def job(self, session: dict, record: dict) -> dict:
        number = str(record.get('id'))
        places = places_of(record.get('location'))
        work, kind = custom_fields(record.get('metadata'))
        if work is None:                    # 'Denver, CO - Hybrid', 'Los Angeles, CA (On-site)', 'Hamburg (hybrid)'
            work = stated(' / '.join(p['name'] for p in places), PLACE_WORK_PHRASES)
        salary, low, high, currency = pay_of(record.get('pay_input_ranges'))
        html = htmllib.unescape(record['content']).strip() or None if isinstance(record.get('content'), str) else None
        url = record.get('absolute_url')
        url = url if isinstance(url, str) and url else HOSTED_JOB.format(token=session['token'], job=number)
        return {
            'id': number,
            'company': clean(record.get('company_name')) or None if isinstance(record.get('company_name'), str) else None,
            'title': clean(record.get('title')),
            'location': places[0]['name'] if places else None,
            'otherLocations': [p['name'] for p in places[1:]],
            'places': places,
            'workType': work,
            'employmentType': kind,
            'department': names(record.get('departments')),
            'salary': salary,
            'salaryMin': low,
            'salaryMax': high,
            'salaryCurrency': currency,
            'postedAt': utc(record.get('first_published')),
            'expiresAt': utc(record.get('application_deadline')),
            'dateOnly': False,
            'jobUrl': url,
            'applyUrl': url,
            'reference': clean(record.get('requisition_id')) or None,
            'description': text_of(html),
            'descriptionHtml': html,
        }

    async def pages(self, http, session, term, days):
        wanted = terms(list(self.description_terms))
        if not wanted:
            yield await self._list(http, session, {'pay_transparency': 'true'}, wanted)
            return
        async with self.heavy:             # released before the page is handed over: an engine that stops early
            jobs = await self._list(http, session, {'pay_transparency': 'true', 'content': 'true'}, wanted)
        yield jobs                          # never resumes the generator, which would keep the slot

    async def _list(self, http, session: dict, params: dict, wanted: list[Term]) -> list[dict]:
        response = await self.get(http, f'{session["token"]}/jobs', params)
        if response.status_code == 404:
            raise BoardMissing(session['token'])
        records = answer(response).get('jobs')
        del response                        # the raw body of a big board with descriptions runs to tens of MB
        if not isinstance(records, list):
            raise BoardError('the job list was not a list')
        records = [r for r in records if isinstance(r, dict) and r.get('id')]
        for r in records if wanted else ():
            if not may_hold(r.get('content') if isinstance(r.get('content'), str) else '', wanted):
                r['content'] = None         # cannot match: not unescaped, stripped and searched (500 us a job)
        jobs = []
        for r in records:
            try:
                jobs.append(self.job(session, r))
            except (AttributeError, TypeError, ValueError):     # a record of a shape never seen: one job is left out,
                continue                                        # never the board or the run
        return jobs

    async def _one(self, http, session: dict, job_id: str):
        return await self.get(http, f'{session["token"]}/jobs/{job_id}', {'pay_transparency': 'true'})

    async def details(self, http, session, job):
        if job.get('descriptionHtml') or not session:      # the list was read with descriptions
            return job
        response = await self._one(http, session, job['id'])
        if response.status_code != 200:                    # closed since the list was read
            return job
        full = self.job(session, answer(response))
        return {**job, **{k: v for k, v in full.items() if v not in (None, [], '')}}

    async def lookup(self, http, session, job_id):
        response = await self._one(http, session, job_id)
        if response.status_code == 200:
            return 'open', self.job(session, answer(response))
        if response.status_code != 404:
            raise BoardError(f'HTTP {response.status_code}')
        if 'exists' not in session:                        # 404 for a missing board too: ask the board itself
            board = await self.get(http, session['token'])
            if board.status_code not in (200, 404):
                raise BoardError(f'HTTP {board.status_code}')
            session['exists'] = board.status_code == 200
        return ('closed' if session['exists'] else 'not found'), None
