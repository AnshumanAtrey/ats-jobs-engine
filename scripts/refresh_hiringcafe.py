"""
Rebuild ats_jobs/data/hiringcafe_places.tsv: the cities and states HiringCafe's jobs name, with how many jobs name each.

The list is read the way a search reads HiringCafe (platforms/hiringcafe.py): the first PAGES pages of its newest jobs
of every kind (40 employers a page, up to 4 jobs each: 14,134 jobs on 2026-11-10, the site's newest few hours; a search
shows 250 pages). A job names its places as "Amsterdam, North Holland, NL" (workplace_cities) and "North Holland, NL"
(workplace_states). Countries are not listed: the engine reads them already. A name stays when at least MIN_JOBS jobs
name it.

The adapter hands these names to the engine (Platform.place_names), so a place typed with a job title reads as a place
("nurse in California", "data analyst in London"), and a word that is no place where HiringCafe's jobs are stays in
the title ("engineer in training").

Run from the repo root: python3 scripts/refresh_hiringcafe.py [pages]   (needs curl_cffi; PAGES by default. The calls go
about 10 seconds apart and a block is waited out: Cloudflare blocked one address for a minute or more after about 80
calls 2 seconds apart, and after about 70 calls 5 seconds apart on an address that had been busy, while 100 calls 10
seconds apart met no block (2026-11-10, 17 minutes). The file is written every 25 pages, so a stopped census keeps what
it read; a summary is printed at the end. The adapter's BARE_PLACE_JOBS is set for a census of this size)
"""
import asyncio
import json
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ats_jobs.platforms.hiringcafe import LAST_PAGE, SEARCH, Connection, Refused  # noqa: E402

PLACES = ROOT / 'ats_jobs' / 'data' / 'hiringcafe_places.tsv'
MIN_JOBS = 3
PAGES = 100
STATE = {'sortBy': 'date', 'defaultToUserLocation': False}      # every job, newest first, anywhere
PAUSE_S = 8.0          # after each page, besides the answer's own time: about 10 seconds between calls
BLOCK_S = 360          # a Cloudflare block of one address lasted 5 to 6 minutes


class Notes:
    """What the connection would tell a run's user, printed instead."""

    def note(self, text: str, key: str | None = None) -> None:
        print(f'{time.strftime("%H:%M:%S")} note: {text}', file=sys.stderr)


async def page(conn: Connection, params: dict) -> dict | None:
    """One search page, a block waited out twice; None when the address stays blocked."""
    for _ in range(3):
        try:
            return await conn.data(SEARCH, params)
        except Refused as exc:
            print(f'{time.strftime("%H:%M:%S")} blocked ({exc}), waiting {BLOCK_S} s', file=sys.stderr)
            await asyncio.sleep(BLOCK_S)
            conn.waited = 0
    return None


def parts_of(text) -> list[str]:
    return [p.strip() for p in str(text or '').split(',') if p.strip()]


def write(cities: Counter, states: Counter, jobs: int) -> None:
    rows = [(name, 'city', code, n, state) for (name, state, code), n in cities.items() if n >= MIN_JOBS]
    rows += [(name, 'state', code, n, '') for (name, code), n in states.items() if n >= MIN_JOBS]
    rows.sort(key=lambda r: (-r[3], r[1], r[0], r[2]))
    head = [f'# HiringCafe workplace places, updated {date.today().isoformat()}: {jobs:,} jobs, the newest of every kind '
            f'its search shows, read by scripts/refresh_hiringcafe.py; a name kept when {MIN_JOBS} or more jobs name it',
            '# name\tkind (city or state)\tcountry code\tjobs\tstate of the city']
    PLACES.write_text('\n'.join(head + ['\t'.join(map(str, r)) for r in rows]) + '\n', encoding='utf-8')


async def census(pages: int) -> tuple[Counter, Counter, int]:
    """(cities: (name, state, country code) -> jobs, states: (name, country code) -> jobs, jobs read)."""
    cities, states, seen = Counter(), Counter(), set()
    conn = Connection(Notes())
    searched = json.dumps(STATE, separators=(',', ':'))
    try:
        for n in range(min(pages, LAST_PAGE + 1)):
            props = await page(conn, {'searchState': searched, **({'page': n} if n else {})})
            if props is None:
                print(f'stopped at page {n}: the list is written from the pages read', file=sys.stderr)
                break
            hits = [h for h in props.get('ssrHits') or [] if isinstance(h, dict)]
            for hit in hits:
                if hit.get('id') in seen:
                    continue
                seen.add(hit.get('id'))
                v = hit.get('v5_processed_job_data') or {}
                for name, state, code in {tuple(p) for p in map(parts_of, v.get('workplace_cities') or []) if len(p) == 3}:
                    cities[(name, state, code)] += 1
                for name, code in {tuple(p) for p in map(parts_of, v.get('workplace_states') or []) if len(p) == 2}:
                    states[(name, code)] += 1
            if n % 25 == 24:
                write(cities, states, len(seen))
                print(f'{time.strftime("%H:%M:%S")} page {n}: {len(seen):,} jobs, written', file=sys.stderr)
            if not hits or props.get('ssrIsLastPage'):
                break
            await asyncio.sleep(PAUSE_S)
    finally:
        await conn.close()
    return cities, states, len(seen)


def main() -> None:
    cities, states, jobs = asyncio.run(census(int(sys.argv[1]) if len(sys.argv) > 1 else PAGES))
    write(cities, states, jobs)
    kept = Counter(line.split('\t')[1] for line in PLACES.read_text(encoding='utf-8').splitlines() if not line.startswith('#'))
    print(f'{jobs:,} jobs: {kept["city"]:,} cities and {kept["state"]:,} states kept of {len(cities):,} and '
          f'{len(states):,} -> {PLACES.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
