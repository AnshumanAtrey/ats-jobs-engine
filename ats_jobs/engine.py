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
  "job-details" for a row with details. The SDK delivers only the rows the spending limit can pay for. No start
  fee. No input stops a run: unreadable fields fall back to their defaults with a note.
"""
import asyncio
import heapq
import itertools
import json
import math
import time
import zlib
from datetime import datetime, timedelta, timezone

from apify import Actor

from .http import BoardError, BoardMissing, client
from .inputs import Config, parse_input
from .platforms import PLATFORMS, Board, Platform
from .rows import Filters, dedupe_key, job_row, link_row, parse_time

LINK_EVENT = 'job'                 # these two must equal the event keys of the pricing in .actor/store.json
DETAILS_EVENT = 'job-details'
RUN_SAFETY_MARGIN_S = 30           # stop reading boards this close to the run's time limit and deliver
PROGRESS_EVERY_S = 5
PUSH_CHUNK = 500
OLDEST = datetime.min.replace(tzinfo=timezone.utc)
STOP_REASONS = {
    'limit': 'your spending limit for this run was reached. Raise the limit to get the rest.',
    'time': 'the run was about to reach its time limit, so the boards read so far were used. '
            'Raise the run timeout to read every board.',
}


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
        self.phase = ''
        self.boards_total = self.boards_read = self.boards_matched = self.details_read = self.details_wanted = 0
        self.jobs_listed = self.matching = self.duplicates = 0
        self.companies_matched: set[str] = set()
        self.boards_missing: list[str] = []
        self.board_errors: list[dict] = []
        self.links = {'open': 0, 'closed': 0, 'notFound': 0, 'couldNotCheck': 0}
        self.mode = 'check' if cfg.job_urls else 'search'
        by_key = {b.key.lower(): b for b in boards}
        self.by_key = by_key
        self.where = f'{platform.name} {platform.site_word}'

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
        progress = asyncio.create_task(self.progress_loop())
        try:
            await asyncio.gather(*coros)
        finally:
            progress.cancel()

    def row(self, board: Board, job: dict, details: bool) -> dict:
        row = job_row(self.platform.key, company(board), job, self.checked_at, details)
        return {k: v for k, v in row.items() if k not in self.platform.never}

    def board_error(self, key: str, exc: Exception) -> None:
        self.board_errors.append({'board': key, 'error': str(exc)[:200]})

    # ---------- search ----------

    async def search(self) -> None:
        cfg, platform = self.cfg, self.platform
        filters = Filters(cfg)
        now = datetime.now(timezone.utc)
        targets = [self.board(k) for k in cfg.companies] or self.boards
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
            return heap[0][0] if cfg.max_jobs and len(heap) >= cfg.max_jobs else None

        def keep(posted: datetime, board: Board, job: dict) -> None:
            if cfg.include_details:
                texts[(board.key, job['id'])] = zlib.compress(json.dumps(
                    [job.get('description'), job.get('descriptionHtml')]).encode('utf-8'))
            job.pop('description', None)
            job.pop('descriptionHtml', None)
            item = (posted, next(order), board, job)
            if cfg.max_jobs is None:
                kept.append(item)
            elif cfg.max_jobs > 0:
                if len(heap) < cfg.max_jobs:
                    heapq.heappush(heap, item)
                else:
                    dropped = heapq.heappushpop(heap, item)
                    texts.pop((dropped[2].key, dropped[3]['id']), None)

        def take(board: Board, jobs: list[dict], server_match: bool) -> bool:
            """A page of one board. True when it held a job that passes."""
            hit = False
            self.jobs_listed += len(jobs)
            for job in jobs:
                if not job.get('id') or not filters.passes(job, now, server_match):
                    continue
                hit = True
                posted = parse_time(job.get('postedAt')) or OLDEST
                key = (board.name.casefold(), *dedupe_key(job))
                if key in seen:
                    self.duplicates += 1
                    if posted <= seen[key]:
                        continue
                else:
                    self.matching += 1
                seen[key] = posted
                self.companies_matched.add(board.name)
                keep(posted, board, job)
            return hit

        async with client(platform.actor) as http:
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
                    hit = False
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
                                # a page of a newest-first board can end older than anything still wanted
                                last, cut = parse_time(jobs[-1].get('postedAt')) or OLDEST, floor()
                                by_date = platform.newest_first and not (term and platform.ranked_search)
                                if by_date and ((cut and last < cut) or (window and last < window)):
                                    break
                    except BoardMissing:
                        self.boards_missing.append(board.key)
                    except BoardError as exc:
                        self.board_error(board.key, exc)
                    finally:
                        self.boards_read += 1
                        self.boards_matched += 1 if hit else 0

            self.phase = 'boards'
            await self.run_all([one(b) for b in targets])

            chosen = sorted(kept if cfg.max_jobs is None else heap, key=lambda item: (item[0], item[1]), reverse=True)
            if cfg.include_details:
                await self.read_details(http, chosen, texts, sessions)

        self.phase = 'saving'
        rows = []
        delivered = set()
        for _, _, board, job in chosen:
            key = (board.name.casefold(), *dedupe_key(job))
            if key in delivered:         # an older copy that was kept before the newer one came
                continue
            delivered.add(key)
            has_details = cfg.include_details and bool(job.get('description') or job.get('descriptionHtml'))
            rows.append((self.row(board, job, cfg.include_details), DETAILS_EVENT if has_details else LINK_EVENT))
        await self.deliver_all(rows)

    async def read_details(self, http, chosen: list, texts: dict, sessions: dict) -> None:
        platform = self.platform
        self.details_wanted = len(chosen)
        sem = asyncio.Semaphore(platform.concurrency)

        async def one(item: tuple) -> None:
            _, _, board, job = item
            blob = texts.pop((board.key, job['id']), None)
            if blob:
                job['description'], job['descriptionHtml'] = json.loads(zlib.decompress(blob))
            async with sem:
                if self.out_of_time():
                    return
                try:
                    job.update(await platform.details(http, sessions.get(board.key), job))
                except BoardError as exc:
                    self.board_error(board.key, exc)
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
        found: dict[tuple, tuple] = {}     # (board, job id) -> (status, job)
        sem = asyncio.Semaphore(platform.concurrency)

        async with client(platform.actor) as http:
            async def one(key: str, job_ids: list[str]) -> None:
                async with sem:
                    try:
                        session = await platform.open(http, self.board(key))
                        if session is None:
                            self.boards_missing.append(key)
                            found.update({(key, j): ('not found', None) for j in job_ids})
                            return
                        for job_id in job_ids:
                            found[(key, job_id)] = await platform.lookup(http, session, job_id)
                    except BoardError as exc:
                        self.board_error(key, exc)
                        found.update({(key, j): ('error', None) for j in job_ids if (key, j) not in found})

            self.phase = 'links'
            await self.run_all([one(key, ids) for key, ids in by_board.values()])

        rows = []
        for key, job_id, url in cfg.job_urls:
            status, job = found.get((key, job_id), ('error', None))
            if status == 'error':
                self.links['couldNotCheck'] += 1
                continue
            self.links['notFound' if status == 'not found' else status] += 1
            board = self.board(key)
            if status == 'open':
                rows.append((self.row(board, job, cfg.include_details),
                             DETAILS_EVENT if cfg.include_details else LINK_EVENT))
            else:
                row = link_row(platform.key, company(board), job_id, url, status, self.checked_at)
                rows.append(({k: v for k, v in row.items() if k not in platform.never}, LINK_EVENT))
        await self.deliver_all(rows)

    # ---------- summary ----------

    def ran(self) -> bool:
        if self.mode == 'check':
            return sum(self.links.values()) > self.links['couldNotCheck']
        return self.boards_read > len(self.board_errors)

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
            text = f'No {self.where} could be read: {first}.'
        else:
            where = plural(self.boards_read, self.where)
            people = plural(len(self.companies_matched), 'company')
            if self.delivery.rows:
                newest = ('' if cfg.max_jobs is None or self.matching <= self.delivery.rows
                          else f', saved the {self.delivery.rows:,} newest')
                text = f'Found {plural(self.matching, "matching job")} at {people} on {where}{newest} in {seconds} s.'
                if cfg.max_jobs is None or self.matching <= self.delivery.rows:
                    text = (f'Found and saved {plural(self.delivery.rows, "matching job")} at {people} '
                            f'on {where} in {seconds} s.')
            elif self.matching and cfg.max_jobs == 0:
                text = f'Found {plural(self.matching, "matching job")} on {where} in {seconds} s (maxJobs is 0).'
            else:
                text = f'No open job matched on {where} in {seconds} s. Try fewer words, another place or a longer period.'
        if self.stop:
            text += f' Stopped early: {STOP_REASONS[self.stop]}'
        if cfg.companies and self.boards_missing:
            text += f' No {self.where} for: {", ".join(self.boards_missing[:5])}.'
        if cfg.skipped:
            text += f' Skipped {plural(len(cfg.skipped), "entry")}: {cfg.skipped[0][1]}'
        return text.replace('entrys', 'entries').replace('companys', 'companies')

    def output(self, status: str) -> dict:
        cfg = self.cfg
        search = self.mode == 'search'
        return {
            'status': status,
            'message': self.message(),
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
            'links': self.links if not search else None,
            'boardsNotFound': self.boards_missing[:100] if (cfg.companies or not search) else [],
            'boardsWithErrors': self.board_errors[:100],
            'notes': cfg.notes,
            'skipped': [{'input': text, 'reason': why} for text, why in cfg.skipped],
            'settings': {
                'searchTerms': cfg.search_terms, 'excludeTerms': cfg.exclude_terms, 'location': cfg.locations,
                'workType': cfg.work_types, 'employmentType': cfg.job_types,
                'postedWithinDays': cfg.posted_within_days, 'maxJobs': cfg.max_jobs,
                'includeDetails': cfg.include_details, 'searchDescriptions': cfg.search_descriptions,
                'companies': cfg.companies,
            },
            'companyList': {'boards': len(self.boards), 'updated': self.updated},
            'finishedAt': now_iso() if status != 'running' else None,
        }

    async def write_output(self, final: bool) -> None:
        status = ('partial' if self.stop else 'done') if self.ran() else 'failed'
        try:
            await Actor.set_value('OUTPUT', self.output(status if final else 'running'))
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
            f' companies={len(cfg.companies) or f"all {len(boards):,}"} payPerEvent={delivery.ppe}')
        for note in cfg.notes:
            Actor.log.warning(note)
        for text, why in cfg.skipped:
            Actor.log.warning(f'Skipped "{text}": {why}')
        run = Run(platform, cfg, boards, updated, delivery)
        await run.write_output(final=False)
        first_event = DETAILS_EVENT if cfg.include_details and not cfg.job_urls else LINK_EVENT
        if not delivery.can_pay(first_event):
            message = ('The maximum cost of this run is too low for one job, so nothing was searched and nothing '
                       'was charged. Raise the maximum cost per run and start again.')
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
