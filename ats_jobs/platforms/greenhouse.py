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
answers with CloudFront's 403 goes to the back. No answer carried a 429 or a Retry-After.

Tokens are case-insensitive (stripe, Stripe and STRIPE are one board) and hold letters, digits, - and _. EU boards
(job-boards.eu.greenhouse.io) use the same tokens and the same API. A board whose company shows its jobs on its own
site gives that site in absolute_url (stripe.com/jobs/search?gh_jid=...), so such a link is read through the
company's site, kept in the board list.

postedAt is first_published: the board pages' own data (published_at) equals it on 1,482 of 1,482 jobs (GitLab,
Anthropic, Fever), and the page uses it for its "New" tag. updated_at moves with every edit (it differed on 1,283 of
them). Greenhouse's job pages carry no JobPosting markup; employers' own career pages use either date.

Pay ranges have an amount and a currency but no period, and employers label them in their own words ("Hourly Pay
Range", "Zone 1 Pay Range"), so no period is stated. Work type and job type exist only as employers' custom fields
(Employment Type, Time Type, Workplace Type, Remote...), read when their value says one known word. Locations are
free text, several joined with ; or |; one employer pastes its job ad there.

robots.txt on boards-api.greenhouse.io and boards.greenhouse.io disallows /embed/ only, and the adapter reads no
/embed/ address.
"""
import asyncio
import html as htmllib
import re
from datetime import timezone
from urllib.parse import parse_qs, urlparse

from ..http import BoardError, BoardMissing, send
from ..inputs import JOB_TYPE_ALIASES, WORK_TYPE_ALIASES
from ..rows import parse_time
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
JOB_ID = re.compile(r'^\d{1,20}$')
BLOCK_WAIT_S = (20, 40, 60)          # a CloudFront block lifted within two minutes of the last call
SENTENCE = re.compile(r'[a-z]\.\s+[A-Z]')
JOB_TYPE_FIELDS = {'employment type', 'time type', 'job type', 'employment status', 'position type',
                   'type of employment', 'contract type', 'worker type', 'employee type'}
WORK_TYPE_FIELDS = {'workplace type', 'location type', 'remote', 'work type', 'work arrangement', 'workplace',
                    'remote options', 'work location', 'remote worker', 'remote status', 'work model',
                    'working model', 'work setting', 'remote eligible', 'remote work', 'workplace model'}
LETTERS = re.compile(r'[a-z]+')


def phrases(aliases: dict[str, str]) -> list[tuple[str, str]]:
    """{'on-site': 'onsite'} -> [(' on site ', 'onsite')]: the words of each alias, padded, to find inside a value."""
    return [(f' {" ".join(LETTERS.findall(word))} ', kind) for word, kind in aliases.items()]


WORK_PHRASES, JOB_PHRASES = phrases(WORK_TYPE_ALIASES), phrases(JOB_TYPE_ALIASES)


def clean(value) -> str:
    return ' '.join(str(value).split()) if value is not None else ''


def utc(value: str | None) -> str | None:
    """'2026-10-19T11:17:24-04:00' -> '2026-10-19T15:17:24Z', so dates sort as text across employers."""
    moment = parse_time(value)
    return moment.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z') if moment else None


def places_of(location: dict | None) -> list[dict]:
    """'New York, NY; San Francisco, CA | Remote' -> one place each. A job ad pasted as the location is no place."""
    text = clean((location or {}).get('name'))
    if len(text) > 300 and len(SENTENCE.findall(text)) >= 2:
        return []
    return [{'name': part} for part in dict.fromkeys(p.strip() for p in re.split(r'[;|]', text)) if part]


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


def custom_fields(metadata: list | None) -> tuple[str | None, str | None]:
    """The employer's custom fields -> (work type, job type), when a known field names one."""
    work = kind = None
    for field in metadata if isinstance(metadata, list) else ():
        if not isinstance(field, dict):
            continue
        name, value = clean(field.get('name')).lower(), field.get('value')
        if name in WORK_TYPE_FIELDS and work is None:
            work = 'remote' if value is True else stated(value, WORK_PHRASES)
        elif name in JOB_TYPE_FIELDS and kind is None:
            kind = stated(value, JOB_PHRASES)
    return work, kind


def pay_of(ranges: list | None) -> tuple[str | None, float | None, float | None, str | None]:
    """Greenhouse pay ranges -> (text of every range, lowest, highest, currency). The numbers are those of the first
    range's currency; amounts come in cents."""
    good = []
    for r in ranges if isinstance(ranges, list) else []:
        if not isinstance(r, dict) or not r.get('currency_type'):
            continue
        low, high = (r.get(k) / 100 if isinstance(r.get(k), (int, float)) and r.get(k) > 0 else None
                     for k in ('min_cents', 'max_cents'))
        if low is not None or high is not None:
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


def blocked(response) -> bool:
    """CloudFront's own refusal (an HTML page from server CloudFront), not an answer of the API."""
    return response.status_code == 403 and ('cloudfront' in response.headers.get('server', '').lower()
                                            or 'text/html' in response.headers.get('content-type', ''))


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
        self._sites: dict[str, str] | None = None

    # ---------- links and names ----------

    def sites(self) -> dict[str, str]:
        """Company career sites (from the board list) -> board token, for links such as stripe.com/jobs?gh_jid=1."""
        if self._sites is None:
            seen: dict[str, set] = {}
            try:
                boards = self.boards()[0]
            except FileNotFoundError:             # before the first refresh wrote the list
                boards = []
            for board in boards:
                host = (urlparse(board.website or '').hostname or '').lower().removeprefix('www.')
                if host:
                    seen.setdefault(host, set()).add(board.key.lower())
            self._sites = {host: next(iter(keys)) for host, keys in seen.items() if len(keys) == 1}
        return self._sites

    def aliases(self, board: Board) -> list[str]:
        host = (urlparse(board.website or '').hostname or '').lower()
        return list(dict.fromkeys(h for h in (host, host.removeprefix('www.')) if h))

    def read_link(self, text):
        text = text.strip()
        link = text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}'
        try:
            parsed = urlparse(link)
            host = (parsed.hostname or '').lower() if ' ' not in text.strip() else ''
        except ValueError:
            return None
        query = {k.lower(): v[0].strip() for k, v in parse_qs(parsed.query).items() if v}
        parts = [p for p in parsed.path.split('/') if p]
        token = job = None
        if BOARD_HOST.match(host) or host == APP_HOST:
            if parts[:1] == ['embed']:
                token, job = query.get('for'), query.get('token') or query.get('gh_jid')
            elif host != APP_HOST and parts and parts[0].lower() not in NOT_BOARDS:
                token = parts[0]
                job = parts[2] if len(parts) > 2 and parts[1].lower() == 'jobs' else query.get('gh_jid')
        elif API_HOST.match(host):
            if parts[:2] == ['v1', 'boards'] and len(parts) > 2:
                token = parts[2]
                job = parts[4] if len(parts) > 4 and parts[3] == 'jobs' else query.get('id')
        elif host and JOB_ID.match(query.get('gh_jid', '')):
            token = self.sites().get(host.removeprefix('www.'))
            return (token, query['gh_jid']) if token else None
        else:
            return None
        token = token.lower() if token and TOKEN.match(token) else None
        return (token, job if job and JOB_ID.match(job) else None) if token else (None, None)

    def guess_board(self, name):
        typed = name.strip().lower()
        return typed if TOKEN.match(typed) else re.sub(r'[^a-z0-9]', '', typed) or None

    # ---------- reading ----------

    async def get(self, http, path: str, params: dict | None = None):
        """One answer of the API from an address that is not blocking this run. BoardError when both keep refusing."""
        from ..engine import RUN_SAFETY_MARGIN_S, seconds_left_in_run     # the engine imports the platforms
        for wait in (*BLOCK_WAIT_S, None):
            for host in list(self.hosts):
                response = await send(http, 'GET', f'https://{host}/v1/boards/{path}', params=params)
                if not blocked(response):
                    return response
                if self.hosts[0] == host:
                    self.hosts.append(self.hosts.pop(0))
            left = seconds_left_in_run()
            if wait is None or (left is not None and left < wait + RUN_SAFETY_MARGIN_S + 10):
                break
            await asyncio.sleep(wait)
        raise BoardError('Greenhouse refused the calls (HTTP 403 from both API addresses), as it does for a minute or '
                         'two after too many calls from one IP address')

    async def open(self, http, board):
        token = board.key.strip().lower()
        return {'token': token} if TOKEN.match(token) else None

    def job(self, session: dict, record: dict) -> dict:
        job_id = str(record.get('id'))
        places = places_of(record.get('location'))
        work, kind = custom_fields(record.get('metadata'))
        salary, low, high, currency = pay_of(record.get('pay_input_ranges'))
        html = htmllib.unescape(record['content']).strip() or None if isinstance(record.get('content'), str) else None
        url = record.get('absolute_url') or HOSTED_JOB.format(token=session['token'], job=job_id)
        return {
            'id': job_id,
            'company': clean(record.get('company_name')) or None,
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
        params = {'pay_transparency': 'true', **({'content': 'true'} if self.description_terms else {})}
        response = await self.get(http, f'{session["token"]}/jobs', params)
        if response.status_code == 404:
            raise BoardMissing(session['token'])
        records = answer(response).get('jobs')
        if not isinstance(records, list):
            raise BoardError('the job list was not a list')
        yield [self.job(session, r) for r in records if isinstance(r, dict) and r.get('id')]

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
