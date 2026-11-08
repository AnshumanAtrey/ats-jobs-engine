"""
One run of a job actor, for any platform (ats_jobs/platforms): searches every board live at run time by title,
place, work type, job type and posting date, and delivers the newest jobs first.

- Boards: data/<platform>.tsv, every board found and checked by that platform's refresh script. The companies
  input searches others too.
- Search: each board is read page by page, up to `Platform.concurrency` at once. A platform that searches words
  itself gets one term at a time; every job is still checked here (rows.Filters). Jobs that pass are kept once per
  company, title and place (the newest), then the newest maxJobs of all boards are delivered. Only that many are held
  in memory (a heap). A platform that pages newest first stops as soon as a board can no longer make the cut.
- Job links mode (jobUrls): each link is looked up live: open, closed, or not found.
- Charging: one event per delivered row, "job" for a row without details and for a closed or not-found link,
  "job-details" for a row with details. The SDK delivers only the rows the spending limit can pay for. The engine
  charges no start fee; a pricing with Apify's apify-actor-start event has it charged by Apify before the run. No input
  stops a run: unreadable fields fall back to their defaults with a note.
"""
import asyncio
import heapq
import itertools
import json
import math
import time
import zlib
from collections import Counter
from datetime import datetime, timedelta, timezone

from apify import Actor

from .http import STATS, BoardError, BoardMissing, client
from .inputs import Config, parse_input
from .platforms import PLATFORMS, Board, Platform
from .rows import Filters, job_row, link_row, parse_time

LINK_EVENT = 'job'                 # these two must equal the event keys of the pricing in .actor/store.json
DETAILS_EVENT = 'job-details'
START_EVENT = 'apify-actor-start'  # Apify's own run start event, charged before the run when the pricing defines it
RUN_SAFETY_MARGIN_S = 30           # stop reading boards this close to the run's time limit and deliver
PROGRESS_EVERY_S = 5
PUSH_CHUNK = 500
OLDEST = datetime.min.replace(tzinfo=timezone.utc)
STOP_REASONS = {
    'limit': 'your spending limit for this run was reached. Raise the limit to get the rest.',
    'time': 'the run was about to reach its time limit, so the jobs read by then were used. '
            'Raise the run timeout to read the rest.',
}
# a search that finds nothing names the filter that stood in the way (rows.Filters.misses) and how to loosen it
FILTER_NAMES = {'employmentType': 'job type', 'workType': 'work type', 'location': 'location',
                'postedWithin': 'posting date', 'experienceYears': 'years of experience'}
FILTER_FIXES = {'employmentType': 'clear Job type', 'workType': 'clear Work type and any "Remote" typed in location',
                'location': 'add a wider place, such as a region or Worldwide', 'postedWithin': 'pick a longer period',
                'experienceYears': 'type a wider range of years or leave it empty'}


def period_text(days: float) -> str:
    """30 -> '30 days', 1 -> '24 hours', 0.5 -> '12 hours', 2 -> '48 hours': postedWithin as people say it."""
    hours = days * 24
    if hours <= 48 and abs(hours - round(hours)) < 1e-6:
        return '1 hour' if round(hours) == 1 else f'{round(hours)} hours'
    return f'{days:g} days'


async def safe_status(message: str) -> None:
    """Set the run's status message without ever failing the run."""
    try:
        await Actor.set_status_message(message[:500])
    except Exception as exc:  # noqa: BLE001
        Actor.log.debug(f'status message stored but not confirmed by the SDK: {exc}')


def seconds_left_in_run() -> float | None:
    """Seconds until the platform stops this run, or None when not on the platform."""
    config = getattr(Actor, 'configuration', None) or getattr(Actor, 'config', None)
    timeout_at = getattr(config, 'timeout_at', None) if config else None
    if not timeout_at:
        return None
    return (timeout_at - datetime.now(timezone.utc)).total_seconds()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def plural(n: int, word: str) -> str:
    return f'{n:,} {word}' + ('' if n == 1 else 's')


def name_index(platform: Platform, boards: list[Board]) -> dict[str, list[str]]:
    """Company names, board keys and a platform's aliases, lower case and squeezed, -> board keys."""
    names: dict[str, list[str]] = {}
    for board in boards:
        name = board.name.lower()
        for key in (name, ''.join(ch for ch in name if ch.isalnum()), board.key.lower(),
                    *(a.lower() for a in platform.aliases(board))):
            if key and board.key not in names.setdefault(key, []):
                names[key].append(board.key)
    return names


def company(board: Board) -> dict:
    return {'name': board.name, 'slug': board.key, 'website': board.website}


class Delivery:
    """Pushes rows charged per event and notices the spending limit."""

    def __init__(self):
        self.cm = Actor.get_charging_manager()
        self.ppe = self.cm.get_pricing_info().is_pay_per_event
        self.rows = 0
        self.limit = False

    def can_pay(self, event: str) -> bool:
        if self.ppe and not self.limit:
            left = self.cm.calculate_max_event_charge_count_within_limit(event)
            self.limit = left is not None and left < 1
        return not self.limit

    async def push(self, rows: list[dict], event: str) -> int:
        """Returns how many rows were delivered: the SDK drops the rows the limit cannot pay for."""
        if not rows:
            return 0
        result = await Actor.push_data(rows, charged_event_name=event)
        done = result.charged_count if self.ppe else len(rows)
        if self.ppe and (result.event_charge_limit_reached or done < len(rows)):
            self.limit = True
        self.rows += done
        return done


class Run:
    def __init__(self, platform: Platform, cfg: Config, boards: list[Board], updated: str | None, delivery: Delivery):
        self.platform, self.cfg, self.boards, self.updated, self.delivery = platform, cfg, boards, updated, delivery
        self.started = time.monotonic()
        self.checked_at = now_iso()
        self.stop: str | None = None
        self.enough = False
        self.cut_short = False             # a newest-first board stopped where its jobs could no longer make the cut
        self.phase = ''
        self.boards_total = self.boards_read = self.boards_matched = self.details_read = self.details_wanted = 0
        self.boards_failed = self.details_failed = 0     # boards whose list, and jobs whose details, could not be read
        self.jobs_listed = self.matching = self.duplicates = 0
        self.older_copies = 0              # jobs held twice: a newer copy came after the older was kept
        self.near_misses = 0               # jobs with the words (or any job, with no words) that missed a filter
        self.missed_first: Counter = Counter()   # the first filter each near miss failed
        self.missed_only: Counter = Counter()    # near misses one filter alone left out, with words typed
        self.companies_matched: set[str] = set()
        self.boards_missing: list[str] = []
        self.board_errors: list[dict] = []
        self.boards_failed = 0             # boards that could not be read; a job whose details failed is not one
        self.links = {'open': 0, 'closed': 0, 'notFound': 0, 'couldNotCheck': 0}
        self.mode = 'check' if cfg.job_urls else 'search'
        self.feed: Board | None = None     # the one place listing every company's jobs, when a search reads it
        by_key = {b.key.lower(): b for b in boards}
        self.by_key = by_key
        self.where = f'{platform.name} {platform.site_word}'
        self.input_notes = len(cfg.notes)  # notes past these come from the platform while it reads: the message says them

    # ---------- shared helpers ----------

    def board(self, key: str) -> Board:
        return self.by_key.get(key.lower()) or Board(key, key.split('/')[0])

    def out_of_time(self) -> bool:
        left = seconds_left_in_run()
        if left is not None and left < RUN_SAFETY_MARGIN_S:
            self.stop = self.stop or 'time'
            return True
        return False

    def progress_line(self) -> str:
        if self.phase == 'boards' and self.platform.progress:      # a platform that works before its first page
            return self.platform.progress
        if self.phase == 'boards' and self.feed:
            return f'Read {self.jobs_listed:,} jobs of {self.feed.name}, {plural(self.matching, "matching job")} so far.'
        if self.phase == 'boards':
            return (f'Read {self.boards_read:,} of {self.boards_total:,} {self.where}s, '
                    f'{plural(self.matching, "matching job")} so far.')
        if self.phase == 'details':
            return f'Read the details of {self.details_read:,} of {self.details_wanted:,} jobs.'
        if self.phase == 'links':
            return f'Checked {sum(self.links.values()):,} of {len(self.cfg.job_urls):,} job links.'
        return 'Working.'

    async def progress_loop(self) -> None:
        while True:
            await asyncio.sleep(PROGRESS_EVERY_S)
            await safe_status(self.progress_line())

    async def run_all(self, coros) -> None:
        """The calls together, given up RUN_SAFETY_MARGIN_S before the run's time limit: a call still out then would
        let the platform stop the run before anything is delivered (one Lever list took six minutes to arrive)."""
        progress = asyncio.create_task(self.progress_loop())
        left = seconds_left_in_run()
        try:
            await asyncio.wait_for(asyncio.gather(*coros),
                                   None if left is None else max(left - RUN_SAFETY_MARGIN_S, 0))
        except TimeoutError:
            self.stop = self.stop or 'time'
        finally:
            progress.cancel()

    def row(self, board: Board, job: dict, details: bool) -> dict:
        row = job_row(self.platform.key, company(board), job, self.checked_at, details)
        row.update({k: job.get(k) for k in self.platform.extra})
        return {k: v for k, v in row.items() if k not in self.platform.never}

    def board_error(self, key: str, exc: Exception) -> None:
        """A board, or a job's details, that could not be read. Anything but a BoardError is a record of a shape the
        platform did not expect, named by its type: it costs that board, never the run."""
        text = str(exc) if isinstance(exc, BoardError) else f'{type(exc).__name__}: {exc}'
        self.board_errors.append({'board': key, 'error': text[:200]})

    # ---------- search ----------

    async def search(self) -> None:
        cfg, platform = self.cfg, self.platform
        platform.description_terms = tuple(cfg.search_terms) if cfg.search_descriptions else ()
        now = datetime.now(timezone.utc)
        everyone = platform.search_all(self.boards, cfg)
        filters = Filters(cfg)              # after search_all, which may drop a filter its platform can never meet
        targets = [self.board(k) for k in cfg.companies] or everyone
        if not cfg.companies and len(everyone) == 1 and everyone[0] not in self.boards:
            self.feed = everyone[0]         # one place listing every company, not one of the boards
        self.boards_total = len(targets)
        terms = (cfg.search_terms if platform.server_search and cfg.search_terms else [None])
        # a job dated yesterday can be under a day old, so the platform is asked for a day more
        days = math.ceil(cfg.posted_within_days) + 1 if cfg.posted_within_days is not None else None
        window = now - timedelta(days=days) if days else None
        heap: list = []                     # (posted, order, board, job): the newest maxJobs so far
        kept: list = []                     # every match when maxJobs is empty
        texts: dict[tuple, bytes] = {}      # (board, job id) -> compressed description, with details on
        seen: dict[tuple, datetime] = {}    # (company, title, place) -> newest date
        sessions: dict[str, object] = {}    # board key -> session, with details on
        order = itertools.count()

        def floor() -> datetime | None:
            """The date a job must beat to be delivered; None until maxJobs are held."""
            return heap[0][0] if cfg.max_jobs and len(heap) >= cfg.max_jobs + self.older_copies else None

        def nothing_can_beat_this() -> bool:
            """maxJobs jobs dated today are held: no other board can have a newer one."""
            cut = floor()
            return cut is not None and cut.date() >= now.date() and cut.time() == OLDEST.time()

        def keep(posted: datetime, board: Board, job: dict) -> None:
            if cfg.include_details:
                texts[(board.key, job['id'])] = zlib.compress(json.dumps(
                    [job.get('description'), job.get('descriptionHtml')]).encode('utf-8'))
            job.pop('description', None)
            job.pop('descriptionHtml', None)
            # a job's rank orders jobs of one day newest first (Instahyre's growing ids); else the order they came in
            item = (posted, job['rank'] if isinstance(job.get('rank'), int) else next(order), board, job)
            if cfg.max_jobs is None:
                kept.append(item)
            elif cfg.max_jobs > 0:
                if len(heap) < cfg.max_jobs + self.older_copies:
                    heapq.heappush(heap, item)
                else:
                    dropped = heapq.heappushpop(heap, item)
                    texts.pop((dropped[2].key, dropped[3]['id']), None)

        def take(board: Board, jobs: list[dict], server_match: bool) -> bool:
            """A page of one board. True when it held a job that passes."""
            hit = False
            self.jobs_listed += len(jobs)
            for job in jobs:
                if not job.get('id'):
                    continue
                missed = filters.misses(job, now, server_match, every=bool(filters.terms))
                if missed is not None and (not missed or filters.terms):
                    fit = platform.fits(job)     # this platform's own filter (Hirist: years of experience)
                    if fit is False:
                        missed = None            # its words were not there after all: no near miss
                    elif fit is not True:
                        missed = (*missed, fit)
                if missed:
                    self.near_misses += 1
                    self.missed_first[missed[0]] += 1
                    if len(missed) == 1:
                        self.missed_only[missed[0]] += 1
                if missed != ():
                    continue
                hit = True
                posted = parse_time(job.get('postedAt')) or OLDEST
                name = job.get('company') or board.name      # jobs of a place for every company name their own
                key = (name.casefold(), *platform.copy_key(job))
                if key in seen:
                    self.duplicates += 1
                    if posted <= seen[key]:
                        continue
                    self.older_copies += 1
                else:
                    self.matching += 1
                seen[key] = posted
                self.companies_matched.add(name)
                keep(posted, board, job)
            return hit

        async with client(platform.actor, platform.concurrency * 2) as http:
            sem = asyncio.Semaphore(platform.concurrency)

            async def placed(session, jobs: list[dict]) -> list[dict]:
                """With a place filter, a job whose title can match and whose list entry names no place is opened."""
                if filters.places:
                    for i, job in enumerate(jobs):
                        if platform.vague_place(job) and filters.title_ok(job.get('title'), job.get('description')):
                            jobs[i] = await platform.details(http, session, job)
                return jobs

            async def one(board: Board) -> None:
                async with sem:
                    if self.out_of_time():
                        return
                    if nothing_can_beat_this():
                        self.enough = True
                        return
                    hit, read, paged = False, True, False
                    try:
                        session = await platform.open(http, board)
                        if session is None:
                            self.boards_missing.append(board.key)
                            return
                        if cfg.include_details:
                            sessions[board.key] = session
                        for term in terms:
                            page = 0
                            async for jobs in platform.pages(http, session, term, days):
                                page += 1
                                paged = True
                                jobs = await placed(session, jobs)
                                on_page = take(board, jobs, term is not None)
                                hit = hit or on_page
                                if self.out_of_time() or not jobs:
                                    break
                                # ranked by relevance, a page with no job for this word means the rest have none
                                if term and platform.ranked_search and not on_page and not cfg.search_descriptions:
                                    break
                                if cfg.max_jobs is not None and platform.page_cap and page >= platform.page_cap:
                                    break
                                # a page of a newest-first board can end older than anything still wanted; a platform
                                # that sorts by another time than postedAt names it listedAt (rows.py)
                                last = parse_time(jobs[-1].get('listedAt') or jobs[-1].get('postedAt')) or OLDEST
                                cut = floor()
                                by_date = platform.newest_first and not (term and platform.ranked_search)
                                if by_date and ((cut and last < cut) or (window and last < window)):
                                    self.cut_short = self.cut_short or bool(cut and last < cut)
                                    break
                    except BoardMissing:
                        self.boards_missing.append(board.key)
                    except asyncio.CancelledError:
                        # given up at the time limit: a board that gave no page is not read, so a run where nothing
                        # answered fails; one cut after a page was read in part, and its jobs are used (Cutshort's one
                        # feed, cut after 1,450 jobs, failed the run with 18 jobs saved, 2026-11-08)
                        read = paged
                        raise
                    except Exception as exc:  # noqa: BLE001  a BoardError, or a record the platform did not expect
                        self.board_error(board.key, exc)
                        self.boards_failed += 1
                    finally:
                        if read:
                            self.boards_read += 1
                            self.boards_matched += 1 if hit else 0

            self.phase = 'boards'
            await self.run_all([one(b) for b in targets])
            # boards cut, or never opened, at the time limit held jobs that were never counted: the count is a floor
            self.cut_short = self.cut_short or self.stop == 'time'
            # matches the platform counted without handing them over (Platform.unopened_matches): read and matching,
            # never kept
            self.jobs_listed += platform.unopened_matches
            self.matching += platform.unopened_matches
            self.companies_matched |= set(platform.unopened_companies)

            chosen = self.newest_copies(sorted(kept if cfg.max_jobs is None else heap,
                                               key=lambda item: (item[0], item[1]), reverse=True))
            self.details_wanted = len(chosen) if cfg.include_details else 0
            event = DETAILS_EVENT if cfg.include_details else LINK_EVENT
            # A push chunk at a time: a chunk's details are read just before it is delivered and let go after, so memory
            # holds one chunk of descriptions, and no details are read for jobs past the spending limit (an every-job run
            # with details peaked at 2.1 GB when every description was read first, Lever live review 2026-11-03).
            for start in range(0, len(chosen), PUSH_CHUNK):
                if not self.delivery.can_pay(event):
                    self.stop = 'limit'
                    break
                part = chosen[start:start + PUSH_CHUNK]
                if cfg.include_details:
                    await self.read_details(http, part, texts, sessions)
                self.phase = 'saving'
                rows = []
                for _, _, board, job in part:
                    has_details = cfg.include_details and bool(job.get('description') or job.get('descriptionHtml'))
                    rows.append((self.row(board, job, cfg.include_details), DETAILS_EVENT if has_details else LINK_EVENT))
                await self.deliver_all(rows)
                for _, _, _, job in part:
                    job.pop('description', None)
                    job.pop('descriptionHtml', None)
                if self.stop == 'limit':
                    break

        self.phase = 'saving'

    def newest_copies(self, items: list) -> list:
        """Newest first -> each job once, up to maxJobs: an older copy is still held when a newer one came after it."""
        copies, chosen = set(), []
        for item in items:
            key = ((item[3].get('company') or item[2].name).casefold(), *self.platform.copy_key(item[3]))
            if key not in copies:
                copies.add(key)
                chosen.append(item)
        return chosen[:self.cfg.max_jobs]

    async def read_details(self, http, chosen: list, texts: dict, sessions: dict) -> None:
        """The details of one push chunk of the chosen jobs; search() sets details_wanted for the whole run."""
        platform = self.platform
        # the listing's own descriptions are already in hand: restore them before the time-limited calls, so a run that
        # reaches its time limit still delivers them (the live review of 2026-11-03 found a cut run delivered none)
        for _, _, board, job in chosen:
            blob = texts.pop((board.key, job['id']), None)
            if blob:
                job['description'], job['descriptionHtml'] = json.loads(zlib.decompress(blob))
        sem = asyncio.Semaphore(platform.concurrency)

        async def one(item: tuple) -> None:
            _, _, board, job = item
            async with sem:
                if self.out_of_time():
                    return
                try:
                    job.update(await platform.details(http, sessions.get(board.key), job))
                except Exception as exc:  # noqa: BLE001  a BoardError, or a record the platform did not expect
                    self.board_error(board.key, exc)
                    self.details_failed += 1
                finally:
                    self.details_read += 1

        self.phase = 'details'
        await self.run_all([one(item) for item in chosen])

    async def deliver_all(self, rows: list[tuple[dict, str]]) -> None:
        """Rows in order, in chunks of one event each, until the spending limit."""
        for event, group in itertools.groupby(rows, key=lambda pair: pair[1]):
            batch = [row for row, _ in group]
            for i in range(0, len(batch), PUSH_CHUNK):
                if not self.delivery.can_pay(event):
                    self.stop = 'limit'
                    return
                await self.delivery.push(batch[i:i + PUSH_CHUNK], event)
                if self.delivery.limit:
                    self.stop = 'limit'
                    return

    # ---------- job links ----------

    async def check_links(self) -> None:
        cfg, platform = self.cfg, self.platform
        by_board: dict[str, tuple[str, list]] = {}
        for key, job_id, _ in cfg.job_urls:
            by_board.setdefault(key.lower(), (key, []))[1].append(job_id)
        # (board in lower case, job id) -> (status, job): links are grouped by the board in lower case, so a company's
        # links typed in other cases (BoschGroup, BOSCHGROUP) find their answers too
        found: dict[tuple, tuple] = {}
        sem = asyncio.Semaphore(platform.concurrency)

        async with client(platform.actor, platform.concurrency * 2) as http:
            async def one(key: str, job_ids: list[str]) -> None:
                board = key.lower()
                async with sem:
                    try:
                        session = await platform.open(http, self.board(key))
                        if session is None:
                            self.boards_missing.append(key)
                            found.update({(board, j): ('not found', None) for j in job_ids})
                            return
                        for job_id in job_ids:
                            found[(board, job_id)] = await platform.lookup(http, session, job_id)
                    except Exception as exc:  # noqa: BLE001  a BoardError, or a record the platform did not expect
                        self.board_error(key, exc)
                        found.update({(board, j): ('error', None) for j in job_ids if (board, j) not in found})

            self.phase = 'links'
            await self.run_all([one(key, ids) for key, ids in by_board.values()])

        rows, opened = [], set()
        for key, job_id, url in cfg.job_urls:
            status, job = found.get((key.lower(), job_id), ('error', None))
            if status == 'error':
                self.links['couldNotCheck'] += 1
                continue
            self.links['notFound' if status == 'not found' else status] += 1
            board = self.board(key)
            if status == 'open':
                row = self.row(board, job, cfg.include_details)
                if (row['jobId'], row['jobUrl']) in opened:      # the same job pasted in two link forms: one row
                    continue
                opened.add((row['jobId'], row['jobUrl']))
                rows.append((row, DETAILS_EVENT if cfg.include_details else LINK_EVENT))
            else:
                who = company(board) if job is None else {      # a lookup can name the company of a job that is gone
                    'name': job.get('company'), 'slug': job.get('companySlug'), 'website': job.get('companyWebsite')}
                row = {**link_row(platform.key, who, job_id, url, status, self.checked_at), **dict.fromkeys(platform.extra)}
                rows.append(({k: v for k, v in row.items() if k not in platform.never}, LINK_EVENT))
        await self.deliver_all(rows)

    # ---------- summary ----------

    def ran(self) -> bool:
        if self.mode == 'check':
            return sum(self.links.values()) > self.links['couldNotCheck']
        return self.boards_read > self.boards_failed

    def answered(self) -> int:
        """Boards whose jobs were read: not missing, not failing."""
        return self.boards_read - len(self.boards_missing) - self.boards_failed

    def reading(self) -> str:
        """The message while the run works."""
        if self.mode == 'check':
            return f'Checking {plural(len(self.cfg.job_urls), "job link")} on {self.platform.name}.'
        return f'Reading {plural(len(self.cfg.companies) or len(self.boards), self.where)}.'

    def near_miss_hint(self) -> str:
        """Why a search found nothing, from the near misses: the jobs that had the words, the filter that alone left
        them out, and how to loosen it. Without words, the filter that left out the most jobs."""
        cfg = self.cfg
        names = {**FILTER_NAMES, **{k: v[0] for k, v in self.platform.filter_words.items()}}
        fixes = {**FILTER_FIXES, **{k: v[1] for k, v in self.platform.filter_words.items()}}
        if not self.near_misses:
            filtered = bool(cfg.locations or cfg.work_types or cfg.job_types or cfg.posted_within_days is not None
                            or cfg.experience is not None or any(self.platform.settings().values()))
            if cfg.search_terms and self.platform.server_filters and filtered:
                # the platform's own search dropped the jobs outside the filters before the engine saw them
                return (f' {self.platform.name} has no job with your words inside your other filters: try fewer '
                        f'words, or loosen the place, period or other filters.')
            if cfg.search_terms:
                wider = '' if cfg.search_descriptions else ', or search descriptions too'
                return f' No job title had your words: try fewer or other words{wider}.'
            return ''
        if not cfg.search_terms:
            top, n = self.missed_first.most_common(1)[0]
            return (f' The {names.get(top, top)} filter left out the most jobs read: {n:,} of {self.near_misses:,}. To '
                    f'see more, {fixes.get(top, f'clear {top}')}.')
        text = f' {plural(self.near_misses, "job")} had your words but not your other filters'
        only = self.missed_only.most_common()
        if not only:
            return text + ': each missed more than one of them, so loosen several.'
        alone = ', '.join(f'the {names.get(k, k)} alone left out {n:,}' for k, n in only)
        return f'{text}: {alone}. To see them, {fixes.get(only[0][0], f'clear {only[0][0]}')}.'

    def message(self) -> str:
        cfg, seconds, name = self.cfg, round(time.monotonic() - self.started), self.platform.name
        if self.mode == 'check':
            links = self.links
            text = (f'Checked {plural(sum(links.values()) - links["couldNotCheck"], "job link")} in {seconds} s: '
                    f'{links["open"]:,} open, {links["closed"]:,} closed, {links["notFound"]:,} not found on {name}.')
            if links['couldNotCheck']:
                text += f' {plural(links["couldNotCheck"], "link")} could not be checked ({name} did not answer).'
        elif not self.ran():
            first = self.board_errors[0]['error'] if self.board_errors else f'no {self.where} could be read'
            text = f'{self.feed.name[0].upper()}{self.feed.name[1:]} could not be read: {first}.' if self.feed else \
                f'No {self.where} could be read: {first}.'
        else:
            where = self.feed.name if self.feed else plural(self.answered(), self.where)
            people = plural(len(self.companies_matched), 'company')
            if self.delivery.rows:
                # boards cut short were not read to their end, so what they held below the cut was never counted; a
                # platform's count is a floor when matches may hide among the records it did not open
                floor = self.cut_short or self.platform.count_is_floor
                every = not floor and self.delivery.rows >= self.matching
                newest = '' if every else f', saved the {self.delivery.rows:,} newest'
                counted = ('at least ' if floor else '') + plural(self.matching, 'matching job')
                text = f'Found {counted} at {people} on {where}{newest} in {seconds} s.'
                if every:
                    text = (f'Found and saved {plural(self.delivery.rows, "matching job")} at {people} '
                            f'on {where} in {seconds} s.')
                    # fewer than asked and a posting period set (30 days by default on the India boards, owner 2026-11-09):
                    # say the period left the older jobs out, or a short list reads as all the board has (Hirist, 30 jobs)
                    if cfg.posted_within_days and cfg.max_jobs and self.delivery.rows < cfg.max_jobs:
                        text += (f' Only jobs posted in the last {period_text(cfg.posted_within_days)} were looked for: '
                                 f'pick a longer period in postedWithin to get older ones too.')
            elif self.matching and cfg.max_jobs == 0:
                text = f'Found {plural(self.matching, "matching job")} on {where} in {seconds} s (maxJobs is 0).'
            elif not self.answered():
                text = f'Nothing was read in {seconds} s.'
            else:
                text = f'No open job matched on {where} in {seconds} s.{self.near_miss_hint()}'
                for note in cfg.notes[:self.input_notes]:     # how the input was read can be why nothing matched
                    text += f' {note}'
            if self.boards_failed:
                text += f' {plural(self.boards_failed, self.where)} could not be read: boardsWithErrors in OUTPUT says why.'
            if self.details_failed:
                text += (f' The details of {plural(self.details_failed, "job")} could not be read, so they were saved '
                         f'without them.')
        if self.enough:
            text += f' It stopped reading there: {cfg.max_jobs:,} jobs from today were found, and nothing can be newer.'
        if self.stop:
            text += f' Stopped early: {STOP_REASONS[self.stop]}'
        if cfg.companies and self.boards_missing:
            text += f' No {self.where} for: {", ".join(self.boards_missing[:5])}.'
        for note in cfg.notes[self.input_notes:]:
            text += f' {note}'
        if cfg.skipped:
            text += f' Skipped {plural(len(cfg.skipped), "entry")}: {cfg.skipped[0][1]}'
        return text.replace('entrys', 'entries').replace('companys', 'companies')

    def output(self, status: str) -> dict:
        cfg = self.cfg
        search = self.mode == 'search'
        return {
            'status': status,
            'message': self.reading() if status == 'running' else self.message(),
            'mode': self.mode,
            'platform': self.platform.key,
            'jobsSaved': self.delivery.rows,
            'matchingJobs': self.matching if search else None,
            'boardsSearched': self.boards_total if search else None,
            'boardsRead': self.boards_read if search else None,
            'boardsWithAMatch': self.boards_matched if search else None,
            'companiesWithMatches': len(self.companies_matched) if search else None,
            'jobsRead': self.jobs_listed if search else None,
            'duplicatesLeftOut': self.duplicates,
            'nearMisses': {'jobs': self.near_misses, 'leftOutByOneFilterOnly': dict(self.missed_only),
                           'firstFilterMissed': dict(self.missed_first)} if search else None,
            'links': self.links if not search else None,
            'boardsNotFound': self.boards_missing[:100] if (cfg.companies or not search) else [],
            'boardsWithErrors': self.board_errors[:100],
            'notes': cfg.notes,
            'skipped': [{'input': text, 'reason': why} for text, why in cfg.skipped],
            'settings': {
                'searchTerms': cfg.search_terms, 'excludeTerms': cfg.exclude_terms, 'location': cfg.locations,
                'remoteOnlyIn': cfg.remote_places,
                'workType': cfg.work_types, 'employmentType': cfg.job_types,
                'postedWithinDays': cfg.posted_within_days, 'maxJobs': cfg.max_jobs,
                'includeDetails': cfg.include_details, 'searchDescriptions': cfg.search_descriptions,
                'companies': cfg.companies, **self.platform.settings(),
                **({'experienceYears': [cfg.experience[0], None if math.isinf(cfg.experience[1]) else cfg.experience[1]]}
                   if cfg.experience is not None else {}),     # years asked for; None: no ceiling (5+)
            },
            'companyList': {'boards': len(self.boards), 'updated': self.updated},
            'traffic': {'calls': STATS['calls'], 'averageSeconds': round(STATS['seconds'] / (STATS['calls'] or 1), 2),
                        'retries': dict(STATS['retries'])},
            'finishedAt': now_iso() if status != 'running' else None,
        }

    def status(self) -> str:
        """failed when nothing could be read; partial when the run stopped early or the platform read only part of what
        it lists (a feed cut short); else done."""
        if not self.ran():
            return 'failed'
        return 'partial' if self.stop or self.board_errors or self.platform.incomplete else 'done'

    async def write_output(self, final: bool) -> None:
        try:
            await Actor.set_value('OUTPUT', self.output(self.status() if final else 'running'))
        except Exception as exc:  # noqa: BLE001
            Actor.log.warning(f'Could not write the OUTPUT record: {exc}')


async def main(platform_key: str) -> None:
    platform = PLATFORMS[platform_key]()
    async with Actor:
        raw = await Actor.get_input() or {}
        boards, updated = platform.boards()
        cfg = parse_input(raw, platform, name_index(platform, boards))   # never raises: unreadable fields fall back with a note
        delivery = Delivery()
        Actor.log.info(
            f'{platform.name} jobs: {"checking " + plural(len(cfg.job_urls), "job link") if cfg.job_urls else "searching"}'
            f' | terms={cfg.search_terms or "any"} exclude={cfg.exclude_terms or "none"} location={cfg.locations or "any"}'
            f' workType={cfg.work_types or "any"} employmentType={cfg.job_types or "any"}'
            f' postedWithinDays={cfg.posted_within_days or "any"} maxJobs={cfg.max_jobs if cfg.max_jobs is not None else "all"}'
            f' details={cfg.include_details} searchDescriptions={cfg.search_descriptions}'
            f' companies={len(cfg.companies) or (f"all {len(boards):,}" if boards else "all")} payPerEvent={delivery.ppe}'
            + (f' experienceYears={cfg.experience[0]:g}' + ('+' if math.isinf(cfg.experience[1]) else f'-{cfg.experience[1]:g}')
               if cfg.experience is not None else ''))
        for note in cfg.notes:
            Actor.log.warning(note)
        for text, why in cfg.skipped:
            Actor.log.warning(f'Skipped "{text}": {why}')
        run = Run(platform, cfg, boards, updated, delivery)
        await run.write_output(final=False)
        first_event = DETAILS_EVENT if cfg.include_details and not cfg.job_urls else LINK_EVENT
        if not delivery.can_pay(first_event):
            # Apify charges a start event, when the pricing has one, before the run begins
            started = delivery.ppe and START_EVENT in delivery.cm.get_pricing_info().per_event_prices
            message = ('The maximum cost of this run is too low for one job, so nothing was searched and nothing '
                       f'{"else " if started else ""}was charged. Raise the maximum cost per run and start again.')
            await Actor.set_value('OUTPUT', {'status': 'done', 'message': message, 'jobsSaved': 0})
            await safe_status(message)
            return
        if cfg.job_urls:
            await run.check_links()
        else:
            await run.search()
        message = run.message()
        Actor.log.info(message)
        await run.write_output(final=True)
        if run.ran():
            await safe_status(message)
        else:
            await Actor.fail(status_message=message[:500])
