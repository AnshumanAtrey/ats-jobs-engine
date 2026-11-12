"""
One run over many platforms at once, for the all-in-one job actor: the same input searched on every board the user
picked, all of them in parallel, then the newest maxJobs of all of them delivered newest first, each job once.

- platforms: the boards to search, by key or by name ("Greenhouse", "hiring cafe", "YC"); empty or "all" is every
  board. A name no board answers to is skipped with a note.
- Each platform reads the shared input the way its own actor reads it (inputs.parse_input), minus what belongs to
  another board: a link pasted in searchTerms or jobUrls goes only to the platform it is a link of, and a platform
  that none of the pasted links or companies belongs to sits the run out instead of searching everything.
- Search: every platform gathers its own newest maxJobs at the same time (engine.Run.gather, each with its own HTTP
  client and board concurrency, so a run takes about as long as its slowest board, not the sum of them). Workday and
  Cornerstone (APART) search in a process of their own when other boards run too: they open a TLS connection to every
  company's own host, thousands a search, and in one event loop those handshakes starved every other board. Then the
  newest maxJobs of them all are chosen, and a job listed on two platforms is kept once: the same job link, or the
  same company, title and place. They are delivered newest first, a push chunk at a time, each chunk's details read
  by its platforms in parallel just before.
- Charging: the job actors' two events, "job" and "job-details", one per delivered row whatever its platform; and,
  when the pricing has it, "board", one per board a search reads, charged as the search starts. Boards past what the
  run's maximum cost can pay for (keeping room for one job) sit the run out.
- Job links: each platform checks its own links, all at once; rows come back in the order the links were typed.
- OUTPUT: the run's totals, and one entry per platform with its own counts, message and errors.
"""
import asyncio
import contextlib
import importlib
import itertools
import math
import multiprocessing
import re
import time
from urllib.parse import parse_qsl, urlencode, urlparse

from apify import Actor

from . import engine
from .engine import DETAILS_EVENT, LINK_EVENT, PUSH_CHUNK, START_EVENT, Delivery, Run, name_index, now_iso, plural
from .http import STATS, BoardError
from .inputs import DORK_OPERATOR, DORK_TEXT, IS_URL, _clean, _items, _links_whole, parse_input
from .platforms import PLATFORMS
from .rows import dedupe_key, fold, parse_time, words

# The order boards are offered and reported in: the big global sources first, then company career sites, startup and
# niche boards, then India's job boards.
ORDER = ('hiringcafe', 'greenhouse', 'lever', 'workday', 'ashby', 'smartrecruiters', 'workable', 'cornerstone', 'deel',
         'ycombinator', 'wellfound', 'welcometothejungle', 'efinancialcareers', 'naukri', 'foundit', 'instahyre',
         'hirist', 'cutshort')
ALIASES = {      # everyday names beyond each platform's key and name
    'hiring cafe': 'hiringcafe', 'hiring.cafe': 'hiringcafe',
    'yc': 'ycombinator', 'ycombinator jobs': 'ycombinator', 'work at a startup': 'ycombinator',
    'workatastartup': 'ycombinator',
    'wttj': 'welcometothejungle', 'otta': 'welcometothejungle', 'welcome to the jungle': 'welcometothejungle',
    'efc': 'efinancialcareers', 'efinancial careers': 'efinancialcareers',
    'monster india': 'foundit', 'foundit.in': 'foundit',
    'smart recruiters': 'smartrecruiters', 'myworkdayjobs': 'workday', 'ashbyhq': 'ashby',
}
EVERY = {'all', 'any', 'every', 'everything', 'all boards', 'every board', 'all platforms', 'every platform'}
COMPANY_WORDS = re.compile(r'\b(?:inc|incorporated|llc|ltd|limited|corp|corporation|co|company|plc|gmbh|ag|sa|sas|bv|'
                           r'pvt|private|pte|pty|group|holdings?)\b\.?')
TRACKING = re.compile(r'^(?:utm_\w*|src|source|ref|referr?er|gh_src|oga|lever-\w+|trk\w*|tracking\w*|campaign\w*|'
                      r'rx_\w+|iis|iisn|ccuid|mode|lang|locale)$', re.I)
PROGRESS_EVERY_S = engine.PROGRESS_EVERY_S
# Searched in a process of their own when the run has other boards: each search opens a TLS connection to thousands of
# companies' own hosts. In one event loop on Apify (4 GB, one core), 18 boards averaged 8.3 s a call and 7 of them were
# cut at their time limit; the same run without these two averaged 1.4 s a call and read every board (runs
# X9NvV6dABaRPIkSO9 and 4PN9lxELbKGrZsF5a, 2026-11-13).
APART = ('workday', 'cornerstone')
BOARD_EVENT = 'board'    # one per board a search reads, when the actor's pricing has it
BOARD_SECONDS = 240      # a board still reading after this long in a run over many stops, and its jobs so far are used


def read_seconds(value, notes: list[str]) -> float | None:
    """secondsPerBoard -> seconds, or None for no limit of its own (the run's timeout still holds). Missing: 240."""
    if value is None:
        return BOARD_SECONDS
    if str(value).strip().lower() in ('', '0', 'none', 'no limit', 'unlimited'):
        return None
    try:
        seconds = float(str(value).strip().lower().removesuffix('s').strip())
    except ValueError:
        notes.append(f'Could not read secondsPerBoard "{value}" as a number of seconds, so it is {BOARD_SECONDS}.')
        return BOARD_SECONDS
    if seconds < 30:
        notes.append(f'secondsPerBoard {value} is under 30, so it is 30.')
        return 30.0
    return seconds


def squeeze(text: str) -> str:
    return re.sub(r'[^a-z0-9]', '', str(text).lower())


def known() -> list[str]:
    """Every platform the engine has, in ORDER (any not in ORDER last)."""
    return [k for k in ORDER if k in PLATFORMS] + [k for k in PLATFORMS if k not in ORDER]


def read_platforms(value, notes: list[str], skipped: list[tuple[str, str]]) -> list[str]:
    """The platforms input -> platform keys, in the order typed. Empty or "all" -> every platform."""
    names = {}
    for key in known():
        names[squeeze(key)] = key
        names[squeeze(PLATFORMS[key].name)] = key
    for alias, key in ALIASES.items():
        if key in PLATFORMS:
            names[squeeze(alias)] = key
    tokens = _items(value)
    if not tokens or any(' '.join(t.lower().split()) in EVERY for t in tokens):
        return known()
    chosen = []
    for token in tokens:
        key = names.get(squeeze(token))
        if key is None:
            skipped.append((token, f'"{token}" is not a board this actor reads. The boards: '
                                   f'{", ".join(PLATFORMS[k].name for k in known())}.'))
        elif key not in chosen:
            chosen.append(key)
    if not chosen:
        notes.append('None of the boards in platforms could be read, so every board was searched.')
        return known()
    return chosen


def company_key(name: str | None) -> str:
    return squeeze(COMPANY_WORDS.sub(' ', (name or '').lower()))


def link_key(url: str | None) -> str | None:
    """A job link without its scheme, www, fragment, tracking parameters and trailing / - _: the same posting read on two
    platforms (HiringCafe's apply link to a SmartRecruiters job ends in "-"; a career page names its job in ?gh_jid=)."""
    if not url or not isinstance(url, str):
        return None
    parsed = urlparse(url.strip())
    host = (parsed.hostname or '').lower().removeprefix('www.')
    path = parsed.path.rstrip('/-_').lower()
    query = sorted((k.lower(), v) for k, v in parse_qsl(parsed.query) if not TRACKING.match(k))
    if not host or not (path or query):
        return None
    return f'{host}{path}' + (f'?{urlencode(query)}' if query else '')


def city_key(job: dict) -> str:
    """The first part of a job's main location, folded: "London, England, United Kingdom" and "London, UK" are London."""
    first = re.split(r'[,(/|;]', job.get('location') or '')[0]
    return ' '.join(fold(w) for w in words(first))


def same_job_keys(job: dict, company: str) -> list[tuple]:
    """What shows one job listed on two platforms: the same company and title in the same city, or at the same posting
    time to the second (HiringCafe copies a career site's own time), or the same job link."""
    title = dedupe_key(job)[0]
    keys = [('job', company, title, city_key(job))]
    posted = None if job.get('dateOnly') else parse_time(job.get('postedAt'))
    if posted and (posted.hour, posted.minute, posted.second) != (0, 0, 0):
        keys.append(('time', company, title, posted.replace(microsecond=0).isoformat()))
    return keys


class Tally:
    """What one platform's Run reads of the delivery in a run over many: the shared spending limit, its own rows."""

    def __init__(self, shared: Delivery):
        self.shared, self.rows = shared, 0

    @property
    def ppe(self) -> bool:
        return self.shared.ppe

    @property
    def limit(self) -> bool:
        return self.shared.limit

    def can_pay(self, event: str) -> bool:
        return self.shared.can_pay(event)


class Many:
    def __init__(self, raw: dict, actor: str, delivery):
        self.raw, self.actor, self.delivery = raw or {}, actor, delivery
        self.started = time.monotonic()
        self.notes: list[str] = []
        self.skipped: list[tuple[str, str]] = []
        self.keys = read_platforms(self.raw.get('platforms'), self.notes, self.skipped)
        self.board_seconds = read_seconds(self.raw.get('secondsPerBoard'), self.notes)
        self.took: dict[str, float] = {}             # platform -> seconds its search took
        self.sat_out: dict[str, str] = {}          # platform -> why it did not run
        self.runs: dict[str, Run] = {}
        self.tallies: dict[str, Tally] = {}
        self.cross_duplicates: dict[str, int] = {}  # platform -> its jobs left out as another platform's
        self.stop: str | None = None
        self.mode = 'search'
        self.http: dict[str, object] = {}
        self.raws: dict[str, dict] = {}              # platform -> the input it read (a worker reads it again)
        self.build()

    # ---------- the input, per platform ----------

    def build(self) -> None:
        platforms = {k: PLATFORMS[k]() for k in self.keys}
        for platform in platforms.values():
            platform.actor = self.actor                  # the user agent names the actor that runs
        words, term_links, typed_links = self.route_terms(platforms)
        job_links = self.route_jobs(platforms)
        companies = _items(self.raw.get('companies'))
        checking = any(job_links.values())
        if _items(self.raw.get('jobUrls')) and not checking:
            self.notes.append('None of the job links is a link of the boards searched, so the run searched instead.')
        self.mode = 'check' if checking else 'search'
        per_platform_notes: dict[str, list[str]] = {}
        per_platform_skipped: dict[str, list[tuple[str, str]]] = {}
        for key, platform in platforms.items():
            if checking and not job_links[key]:
                self.sat_out[key] = 'none of the job links is one of its links'
                continue
            if not checking and typed_links and not words and not term_links[key]:
                self.sat_out[key] = 'none of the links in searchTerms is one of its links'
                continue
            raw = dict(self.raw)
            raw.pop('platforms', None)
            raw.pop('secondsPerBoard', None)
            raw['searchTerms'] = words + term_links[key]
            raw['jobUrls'] = job_links[key]
            boards, updated = platform.boards()
            cfg = parse_input(raw, platform, name_index(platform, boards))
            if companies and not cfg.companies and not checking:
                self.sat_out[key] = 'none of the companies has a board on it'
                continue
            tally = Tally(self.delivery)
            run = Run(platform, cfg, boards, updated, tally)
            run.quiet = True
            self.runs[key], self.tallies[key], self.raws[key] = run, tally, raw
            per_platform_notes[key] = [n for n in cfg.notes if not n.startswith('None of the companies could be read')]
            per_platform_skipped[key] = cfg.skipped
        self.merge_notes(per_platform_notes, per_platform_skipped)

    def route_terms(self, platforms: dict) -> tuple[list[str], dict[str, list[str]], bool]:
        """searchTerms -> (words for every platform, each platform's own links, whether any link was typed). A link no
        platform reads is skipped; a Google dork stays words, which each platform reads as its own actor would."""
        words: list[str] = []
        links: dict[str, list[str]] = {k: [] for k in platforms}
        typed = False
        for token in _links_whole(self.raw.get('searchTerms')):
            text = _clean(token)
            if DORK_OPERATOR.search(text) or DORK_TEXT.search(text):
                words.append(token)
                continue
            owners = [k for k, p in platforms.items() if p.read_link(text) is not None]
            if owners:
                typed = True
                for k in owners:
                    links[k].append(token)
            elif IS_URL.match(text):      # skipped, as one actor skips another site's link: the run still searches
                self.skipped.append((token, f'"{text}" is not a link of the boards searched.'))
            else:
                words.append(token)
        return words, links, typed

    def route_jobs(self, platforms: dict) -> dict[str, list[str]]:
        """jobUrls -> each platform's own job links. A link of no board searched is skipped."""
        links: dict[str, list[str]] = {k: [] for k in platforms}
        for token in _items(self.raw.get('jobUrls')):
            text = _clean(token)
            owners = [k for k, p in platforms.items() if (p.read_link(text) or (None, None))[1]]
            if owners:
                links[owners[0]].append(token)
            else:
                self.skipped.append((token, f'"{text}" is not a job link of the boards searched.'))
        return links

    def merge_notes(self, notes: dict[str, list[str]], skipped: dict[str, list[tuple[str, str]]]) -> None:
        """A note every platform made once, as it is; a note of some platforms with their names. An entry skipped by
        every platform that ran once; one some platforms read is no loss."""
        ran = list(notes)
        seen: set[str] = set()
        for key in ran:
            for note in notes[key]:
                if note in seen:
                    continue
                seen.add(note)
                holders = [k for k in ran if note in notes[k]]
                self.notes.append(note if len(holders) == len(ran) else
                                  f'{", ".join(PLATFORMS[k].name for k in holders)}: {note}')
        texts: dict[str, str] = {}
        for key in ran:
            for text, why in skipped[key]:
                texts.setdefault(text, why)
        for text, why in texts.items():
            if all(any(t == text for t, _ in skipped[k]) for k in ran):
                self.skipped.append((text, why))

    # ---------- the run ----------

    def first_cfg(self):
        return next(iter(self.runs.values())).cfg

    async def run(self) -> None:
        if not self.runs:
            return
        progress = asyncio.create_task(self.progress_loop())
        apart = self.apart()
        try:
            async with contextlib.AsyncExitStack() as stack:
                for key, run in self.runs.items():
                    if key not in apart:
                        self.http[key] = await stack.enter_async_context(
                            engine.client(run.platform.actor, run.platform.concurrency * 2))
                if self.mode == 'check':
                    await self.check()
                else:
                    await self.search()
        finally:
            progress.cancel()

    async def gather_one(self, key: str) -> list:
        run = self.runs[key]
        if self.board_seconds is not None:
            run.deadline = time.monotonic() + self.board_seconds
        try:
            return await run.gather(self.http[key])
        except Exception as exc:  # noqa: BLE001  a platform whose whole search broke costs that platform, not the run
            run.board_error(run.platform.key, exc)
            run.boards_failed += 1
            return []
        finally:
            run.phase = 'gathered'
            run.deadline = None                      # the details of its chosen jobs answer to the run's time only
            self.took[key] = round(time.monotonic() - self.started, 1)

    def apart(self) -> list[str]:
        """The platforms that search in a process of their own: the APART ones, in a search with other boards too."""
        return [k for k in self.runs if k in APART] if self.mode == 'search' and len(self.runs) > 1 else []

    async def search(self) -> None:
        keys = list(self.runs)
        workers = {k: Worker(self, k) for k in self.apart()}
        try:
            gathered = await asyncio.gather(*(workers[k].gather() if k in workers else self.gather_one(k) for k in keys))
            # each platform's list is already newest first in its own order (engine.Run.gather): a stable sort by date
            # keeps that order, and the order of the platforms, among jobs of the same time
            items = [(posted, index, order, key, board, job) for key, chosen in zip(keys, gathered)
                     for index, (posted, order, board, job) in enumerate(chosen)]
            items.sort(key=lambda i: i[0], reverse=True)
            cfg = self.first_cfg()
            picked = self.once_each(items)
            if cfg.max_jobs is not None:
                picked = picked[:cfg.max_jobs]
            for key in keys:
                self.runs[key].details_wanted = sum(1 for i in picked if i[3] == key) if cfg.include_details else 0
            wanted = {k: [i[1] for i in picked if i[3] == k] for k in workers}
            replies = await asyncio.gather(*(workers[k].rows(wanted[k], cfg.include_details) for k in workers))
            ready = {(k, index): row for k, rows in zip(workers, replies) for index, row in zip(wanted[k], rows)}
            await self.deliver(picked, cfg.include_details, ready)
        finally:
            for worker in workers.values():
                worker.close()

    def once_each(self, items: list) -> list:
        """Newest first -> each job once: a job is left out when a platform before it in the list holds the same job
        link, or the same company, title and place. A link several titles share is a careers page, not a job's own."""
        titles: dict[str, set] = {}
        for _, _, _, _, board, job in items:
            for url in (job.get('jobUrl'), job.get('applyUrl')):
                key = link_key(url)
                if key:
                    titles.setdefault(key, set()).add(dedupe_key(job)[0])
        held: dict[tuple, set] = {}
        out = []
        for item in items:
            _, _, _, platform, board, job = item
            keys = same_job_keys(job, company_key(job.get('company') or board.name))
            for url in (job.get('jobUrl'), job.get('applyUrl')):
                key = link_key(url)
                if key and len(titles.get(key, ())) == 1:
                    keys.append(('link', key))
            if any(held.get(k, set()) - {platform} for k in keys):
                self.cross_duplicates[platform] = self.cross_duplicates.get(platform, 0) + 1
                continue
            for k in keys:
                held.setdefault(k, set()).add(platform)
            out.append(item)
        return out

    async def deliver(self, picked: list, include_details: bool, ready: dict | None = None) -> None:
        """The chosen jobs in order, a push chunk at a time. ready: (platform, index) -> (row, event), the rows a worker
        already made; the other platforms' details are read here, a chunk at a time."""
        ready = ready or {}
        event = DETAILS_EVENT if include_details else LINK_EVENT
        for start in range(0, len(picked), PUSH_CHUNK):
            if not self.delivery.can_pay(event):
                self.stop = 'limit'
                break
            part = picked[start:start + PUSH_CHUNK]
            if include_details:
                by_platform: dict[str, list] = {}
                for posted, _, order, key, board, job in part:
                    if key in self.http:
                        by_platform.setdefault(key, []).append((posted, order, board, job))
                await asyncio.gather(*(self.runs[k].read_details(self.http[k], chunk, self.runs[k].texts,
                                                                 self.runs[k].sessions)
                                       for k, chunk in by_platform.items()))
            rows = []
            for _, index, _, key, board, job in part:
                if key in self.http:
                    (row, ev), = self.runs[key].rows_of([(None, None, board, job)])
                elif (key, index) in ready:
                    row, ev = ready[(key, index)]
                else:
                    continue                             # its worker stopped before it sent the row
                rows.append((row, ev, key))
            await self.push(rows)
            for *_, job in part:
                job.pop('description', None)
                job.pop('descriptionHtml', None)
            if self.stop == 'limit':
                break

    async def push(self, rows: list[tuple[dict, str, str]]) -> None:
        """(row, event, platform) in order, a push per run of one event, until the spending limit."""
        for event, group in itertools.groupby(rows, key=lambda r: r[1]):
            batch = list(group)
            for i in range(0, len(batch), PUSH_CHUNK):
                chunk = batch[i:i + PUSH_CHUNK]
                if not self.delivery.can_pay(event):
                    self.stop = 'limit'
                    return
                done = await self.delivery.push([row for row, _, _ in chunk], event)
                for _, _, key in chunk[:done]:
                    self.tallies[key].rows += 1
                if self.delivery.limit:
                    self.stop = 'limit'
                    return

    async def check(self) -> None:
        keys = list(self.runs)

        async def one(key: str) -> list:
            run = self.runs[key]
            try:
                return await run.checked()
            except Exception as exc:  # noqa: BLE001
                run.board_error(run.platform.key, exc)
                return []

        found = await asyncio.gather(*(one(k) for k in keys))
        order = {_clean(t): i for i, t in enumerate(_items(self.raw.get('jobUrls')))}
        typed = [(order.get(_clean(url), len(order)), row, ev, key)
                 for key, checked in zip(keys, found) for row, ev, url in checked]
        typed.sort(key=lambda r: r[0])                   # stable: the order the links were typed in
        await self.push([(row, ev, key) for _, row, ev, key in typed])

    # ---------- status and summary ----------

    def matching(self) -> int:
        return sum(r.matching for r in self.runs.values())

    def progress_line(self) -> str:
        reading = [r for r in self.runs.values() if r.phase in ('', 'boards')]
        text = (f'Searching {plural(len(self.runs), "board")} at once, {plural(self.matching(), "matching job")} so far.'
                if self.mode == 'search' else f'Checking job links on {plural(len(self.runs), "board")}.')
        if self.mode == 'search' and reading and len(reading) < len(self.runs):
            text += ' Still reading ' + ', '.join(
                f'{r.platform.name} ({r.boards_read:,} of {r.boards_total:,})' if r.boards_total > 1 else r.platform.name
                for r in reading) + '.'
        elif any(r.phase == 'details' for r in self.runs.values()):
            text = 'Reading the details of the newest jobs.'
        return text

    async def progress_loop(self) -> None:
        while True:
            await asyncio.sleep(PROGRESS_EVERY_S)
            await engine.safe_status(self.progress_line())

    def ran(self) -> bool:
        return any(r.ran() for r in self.runs.values())

    def status(self) -> str:
        if not self.ran():
            return 'failed'
        if self.stop or any(r.status() != 'done' for r in self.runs.values()):
            return 'partial'
        return 'done'

    def message(self) -> str:
        seconds = round(time.monotonic() - self.started)
        runs = self.runs
        if not runs:
            return 'No board was searched: ' + '; '.join(
                f'{PLATFORMS[k].name}: {why}' for k, why in self.sat_out.items()) + '.'
        saved = sum(t.rows for t in self.tallies.values())
        by_board = ', '.join(f'{runs[k].platform.name} {self.tallies[k].rows:,}'
                             for k in sorted(runs, key=lambda k: -self.tallies[k].rows) if self.tallies[k].rows)
        if self.mode == 'check':
            links = {s: sum(r.links[s] for r in runs.values()) for s in ('open', 'closed', 'notFound', 'couldNotCheck')}
            text = (f'Checked {plural(sum(links.values()) - links["couldNotCheck"], "job link")} on '
                    f'{plural(len(runs), "board")} in {seconds} s: {links["open"]:,} open, {links["closed"]:,} closed, '
                    f'{links["notFound"]:,} not found.')
            if links['couldNotCheck']:
                text += f' {plural(links["couldNotCheck"], "link")} could not be checked (the board did not answer).'
        elif not self.ran():
            first = next((r.board_errors[0]['error'] for r in runs.values() if r.board_errors), 'no board answered')
            text = f'No board could be read: {first}.'
        else:
            matching = self.matching()
            floor = any(r.cut_short or r.platform.count_is_floor for r in runs.values())
            counted = ('at least ' if floor else '') + plural(matching, 'matching job')
            searched = plural(sum(1 for r in runs.values() if r.ran()), 'board')
            if saved and saved >= matching and not floor:
                text = f'Found and saved {plural(saved, "matching job")} on {searched} in {seconds} s: {by_board}.'
            elif saved:
                text = f'Found {counted} on {searched}, saved the {saved:,} newest in {seconds} s: {by_board}.'
            elif matching and self.first_cfg().max_jobs == 0:
                text = f'Found {counted} on {searched} in {seconds} s (maxJobs is 0).'
            else:
                text = f'No open job matched on {searched} in {seconds} s.'
                hints = [r.near_miss_hint().strip() for r in runs.values() if r.ran()]
                hint = max(hints, key=len) if any(hints) else ''
                if hint:
                    text += f' {hint}'
            empty = [r.platform.name for k, r in runs.items() if r.ran() and not r.matching]
            if saved and empty:
                text += f' No match on {", ".join(empty)}.'
            twice = sum(self.cross_duplicates.values())
            if twice:
                text += f' {plural(twice, "job")} listed on two boards {"was" if twice == 1 else "were"} saved once.'
        failed = [r for r in runs.values() if not r.ran()]
        if failed and self.ran():
            text += ' Could not read ' + '; '.join(
                f'{r.platform.name} ({r.board_errors[0]["error"] if r.board_errors else "no answer"})'
                for r in failed) + '.'
        partly = []
        for r in runs.values():
            if not r.ran():
                continue
            if r.stop == 'budget':
                partly.append(f'{r.platform.name} (stopped at {self.board_seconds:g} s, secondsPerBoard)')
            elif r.boards_failed or r.platform.incomplete or r.stop == 'time':
                partly.append(r.platform.name)
        if partly:
            text += f' Read in part: {", ".join(partly)}; boards in OUTPUT says why.'
        stopped = self.stop or next((r.stop for r in runs.values() if r.stop == 'time'), None)
        if stopped:
            text += f' Stopped early: {engine.STOP_REASONS[stopped]}'
        if self.sat_out and self.mode == 'search' and len(self.sat_out) < len(self.keys):
            text += (' Not searched: ' + '; '.join(f'{PLATFORMS[k].name}, {why}' for k, why in self.sat_out.items()) + '.'
                     if len(self.sat_out) <= 3 else
                     f' {plural(len(self.sat_out), "board")} were not searched (boards in OUTPUT says why).')
        for note in self.notes:
            text += f' {note}'
        if self.skipped:
            text += f' Skipped {plural(len(self.skipped), "entry")}: {self.skipped[0][1]}'
        return text.replace('entrys', 'entries').replace('companys', 'companies')

    def output(self, status: str) -> dict:
        runs = self.runs
        cfg = self.first_cfg() if runs else None
        boards = []
        for key in self.keys:
            if key in self.sat_out:
                boards.append({'platform': key, 'name': PLATFORMS[key].name, 'status': 'skipped',
                               'message': f'Not searched: {self.sat_out[key]}.'})
                continue
            run = runs[key]
            entry = run.output(run.status() if status != 'running' else 'running')
            entry = {'platform': key, 'name': run.platform.name, **{k: v for k, v in entry.items() if k not in (
                'platform', 'notes', 'skipped', 'traffic', 'finishedAt', 'mode', 'settings')}}
            entry['duplicatesOfOtherBoards'] = self.cross_duplicates.get(key, 0)
            entry['seconds'] = self.took.get(key)
            entry['settings'] = run.platform.settings() or None
            boards.append(entry)
        search = self.mode == 'search'
        return {
            'status': status,
            'message': (f'Searching {plural(len(runs), "board")} at once.' if status == 'running' else self.message()),
            'mode': self.mode,
            'platforms': [k for k in self.keys if k in runs],
            'jobsSaved': sum(t.rows for t in self.tallies.values()),
            'matchingJobs': self.matching() if search else None,
            'duplicatesAcrossBoards': sum(self.cross_duplicates.values()),
            'boards': boards,
            'notes': self.notes,
            'skipped': [{'input': text, 'reason': why} for text, why in self.skipped],
            'settings': {
                'platforms': list(self.keys), 'secondsPerBoard': self.board_seconds,
                'searchTerms': cfg.search_terms, 'excludeTerms': cfg.exclude_terms, 'location': cfg.locations,
                'remoteOnlyIn': cfg.remote_places, 'workType': cfg.work_types, 'employmentType': cfg.job_types,
                'postedWithinDays': cfg.posted_within_days, 'maxJobs': cfg.max_jobs,
                'includeDetails': cfg.include_details, 'searchDescriptions': cfg.search_descriptions,
                **({'experienceYears': [cfg.experience[0], None if math.isinf(cfg.experience[1]) else cfg.experience[1]]}
                   if cfg.experience is not None else {}),
            } if cfg else {'platforms': list(self.keys)},
            'traffic': {'calls': STATS['calls'], 'averageSeconds': round(STATS['seconds'] / (STATS['calls'] or 1), 2),
                        'retries': dict(STATS['retries'])},
            'finishedAt': now_iso() if status != 'running' else None,
        }

    async def write_output(self, final: bool) -> None:
        try:
            await Actor.set_value('OUTPUT', self.output(self.status() if final else 'running'))
        except Exception as exc:  # noqa: BLE001
            Actor.log.warning(f'Could not write the OUTPUT record: {exc}')


class _Unpaid:
    """A worker's run never delivers: rows go back to the run that charges for them."""
    rows, ppe, limit = 0, False, False

    def can_pay(self, event: str) -> bool:
        return True


def _worker_entry(conn, payload: dict) -> None:
    """One platform's search in a process of its own (Worker): its own event loop, so its calls never wait behind
    another platform's."""
    try:
        asyncio.run(_worker(conn, payload))
    except BaseException as exc:  # noqa: BLE001  whatever stops it is said to the run, which goes on without it
        try:
            conn.send(('error', f'{type(exc).__name__}: {exc}'[:200]))
        except Exception:  # noqa: BLE001
            pass
    finally:
        conn.close()


async def _worker(conn, payload: dict) -> None:
    module, name = payload['cls'].split(':')
    platform = getattr(importlib.import_module(module), name)()
    platform.actor = payload['actor']
    deadline = payload['deadline']
    if deadline is not None:                         # the run's time limit, as the run that started it reads it
        engine.seconds_left_in_run = lambda: deadline - time.time()
    boards, updated = platform.boards()
    run = Run(platform, parse_input(payload['raw'], platform, name_index(platform, boards)), boards, updated, _Unpaid())
    run.quiet = True

    async def report() -> None:
        while True:
            await asyncio.sleep(PROGRESS_EVERY_S / 2)
            conn.send(('progress', run.state()))

    async with engine.client(platform.actor, platform.concurrency * 2) as http:
        ticker = asyncio.create_task(report())
        if payload['seconds'] is not None:
            run.deadline = time.monotonic() + payload['seconds']
        try:
            chosen = await run.gather(http)
        except Exception as exc:  # noqa: BLE001  its whole search broke: the run hears it as a board error
            run.board_error(platform.key, exc)
            run.boards_failed += 1
            chosen = []
        finally:
            ticker.cancel()
            run.deadline = None
        conn.send(('gathered', (run.state(), chosen)))
        _, indices, details = await asyncio.to_thread(conn.recv)
        part = [chosen[i] for i in indices]
        if details and part:
            run.details_wanted = len(part)
            for start in range(0, len(part), PUSH_CHUNK):
                await run.read_details(http, part[start:start + PUSH_CHUNK], run.texts, run.sessions)
        conn.send(('rows', (run.state(), run.rows_of(part))))


class Worker:
    """A platform searched in a process of its own (APART), started with spawn so it holds nothing of this run's
    event loop or Apify client. It sends its run's state as it reads (the twin Run here takes it, so progress, message
    and OUTPUT read as for any platform), then its newest jobs; the rows of the ones chosen across every board come
    back on request, with their details when asked. Charging and delivery stay in this process."""

    SPARE_S = 90                     # past its time limit, a worker that has said nothing more is given up

    def __init__(self, many: Many, key: str):
        self.many, self.key, self.run = many, key, many.runs[key]
        ctx = multiprocessing.get_context('spawn')
        self.conn, child = ctx.Pipe()
        left = engine.seconds_left_in_run()
        cls = PLATFORMS[key]
        payload = {'cls': f'{cls.__module__}:{cls.__qualname__}', 'raw': many.raws[key], 'actor': many.actor,
                   'seconds': many.board_seconds, 'deadline': None if left is None else time.time() + left}
        self.process = ctx.Process(target=_worker_entry, args=(child, payload), daemon=True, name=f'jobs-{key}')
        self.process.start()
        child.close()
        limits = [x for x in (many.board_seconds, left) if x is not None]
        self.patience = min(limits) + self.SPARE_S if limits else None

    def failed(self, why: str) -> None:
        self.run.board_error(self.key, BoardError(f'its search stopped: {why}'))
        self.run.boards_failed += 1

    async def receive(self, until: str):
        """Messages until one of kind `until`; progress on the way is taken into the twin Run."""
        while True:
            kind, body = await asyncio.to_thread(self.conn.recv)
            if kind == 'progress':
                self.run.restore(body)
            elif kind == until:
                return body
            else:
                raise BoardError(str(body))

    async def gather(self) -> list:
        try:
            state, chosen = await asyncio.wait_for(self.receive('gathered'), self.patience)
            self.run.restore(state)
            return chosen
        except (TimeoutError, EOFError, OSError, BoardError) as exc:
            self.failed(str(exc) or type(exc).__name__)
            return []
        finally:
            self.run.phase = 'gathered'
            self.many.took[self.key] = round(time.monotonic() - self.many.started, 1)

    async def rows(self, indices: list[int], details: bool) -> list:
        try:
            self.conn.send(('rows', indices, details))
            state, rows = await asyncio.wait_for(self.receive('rows'), self.patience)
            self.run.restore(state)
            return rows
        except (TimeoutError, EOFError, OSError, BoardError):
            return []

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.conn.close()
        self.process.join(timeout=5)
        if self.process.is_alive():
            self.process.kill()


async def charge_boards(many: Many, delivery, first_event: str) -> bool:
    """One board event per board a search reads, when the pricing has the event, keeping room for one job in the run's
    maximum cost; boards past that sit the run out. False when not one board fits."""
    if many.mode != 'search' or not many.runs or not delivery.ppe:
        return True
    prices = delivery.cm.get_pricing_info().per_event_prices
    per_board = prices.get(BOARD_EVENT)
    if not per_board:
        return True
    left = delivery.cm.get_max_total_charge_usd() - delivery.cm.calculate_total_charged_amount()
    if not left.is_infinite():
        fits = int((left - prices.get(first_event, 0)) // per_board)
        if fits < 1:
            return False
        for key in list(many.runs)[fits:]:
            del many.runs[key], many.tallies[key]
            many.sat_out[key] = 'the maximum cost of the run did not cover one more board'
    await Actor.charge(event_name=BOARD_EVENT, count=len(many.runs))
    return True


async def main_many(actor: str) -> None:
    """The all-in-one job actor: every platform the input picks, searched at once."""
    async with Actor:
        raw = await Actor.get_input() or {}
        delivery = Delivery()
        many = Many(raw, actor, delivery)
        cfg = many.first_cfg() if many.runs else None
        names = ', '.join(PLATFORMS[k].name for k in many.runs) or 'none'
        Actor.log.info(
            f'Job boards: {names} | {"checking job links" if many.mode == "check" else "searching"}'
            + (f' | terms={cfg.search_terms or "any"} location={cfg.locations or "any"} workType={cfg.work_types or "any"}'
               f' employmentType={cfg.job_types or "any"} postedWithinDays={cfg.posted_within_days or "any"}'
               f' maxJobs={cfg.max_jobs if cfg.max_jobs is not None else "all"} details={cfg.include_details}'
               f' payPerEvent={delivery.ppe}' if cfg else ''))
        for note in many.notes:
            Actor.log.warning(note)
        for text, why in many.skipped:
            Actor.log.warning(f'Skipped "{text}": {why}')
        for key, why in many.sat_out.items():
            Actor.log.info(f'{PLATFORMS[key].name} sits this run out: {why}.')
        await many.write_output(final=False)
        first_event = DETAILS_EVENT if cfg and cfg.include_details and many.mode == 'search' else LINK_EVENT
        if not delivery.can_pay(first_event) or not await charge_boards(many, delivery, first_event):
            started = delivery.ppe and START_EVENT in delivery.cm.get_pricing_info().per_event_prices
            message = ('The maximum cost of this run is too low for one board and one job, so nothing was searched and '
                       f'nothing {"else " if started else ""}was charged. Raise the maximum cost per run and start again.')
            await Actor.set_value('OUTPUT', {'status': 'done', 'message': message, 'jobsSaved': 0})
            await engine.safe_status(message)
            return
        await many.run()
        message = many.message()
        Actor.log.info(message)
        await many.write_output(final=True)
        if many.ran():
            await engine.safe_status(message)
        else:
            await Actor.fail(status_message=message[:500])
