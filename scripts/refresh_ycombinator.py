"""
Rebuild ats_jobs/data/ycombinator_places.tsv: the places YC's jobs name, with how many open jobs name each.

The list is read from the board itself, the way a search reads it (platforms/ycombinator.py): every hiring company in
YC's directory, then each company's job list. A place is the first part of each place in a job's location text ("San
Francisco" of "San Francisco, CA, US", "London" of "Remote (London, England, GB)"); two- and three-letter codes (US, CA,
NY) are countries and states, which the engine reads already, so they are not listed. A name stays when at least
MIN_JOBS open jobs name it, and when its shape is a place's (NAME, NOT_PLACE): an office label such as "NYC Office" or
"Chariot HQ" is not one.

The adapter reads a place typed with a job title against this list ("product manager in new york"), so a word of a job
title that is not a place where YC companies hire ("engineer in residence") stays in the title.

Run: python3 scripts/refresh_ycombinator.py   (needs httpx; about 30 s; writes the file and prints a summary)
"""
import asyncio
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ats_jobs.http import client  # noqa: E402
from ats_jobs.platforms.ycombinator import YCombinator  # noqa: E402

PLACES = ROOT / 'ats_jobs' / 'data' / 'ycombinator_places.tsv'
MIN_JOBS = 3
# the shape of a place name: letters, spaces, hyphens, dots and apostrophes, and none of the words of an office label
# ("NYC Office", "Chariot HQ") or a list ("New York or Remote")
NAME = re.compile(r"^[^\W\d_]+(?:[ .'-]+[^\W\d_]+)*\.?$")
NOT_PLACE = {'office', 'offices', 'hq', 'hub', 'headquarters', 'remote', 'or', 'and', 'campus', 'onsite', 'hybrid'}


def place_names(location: str | None) -> set[str]:
    """'San Francisco, CA, US / Remote (US; London, England, GB)' -> {'San Francisco', 'London'}."""
    names = set()
    for part in str(location or '').split(' / '):
        part = part.strip()
        inner = re.fullmatch(r'remote\s*\((.*)\)', part, re.I | re.S)
        for item in [i.strip() for i in inner.group(1).split(';')] if inner else [part]:
            if not item or re.match(r'remote\b', item, re.I):
                continue
            first = ' '.join(item.split(',')[0].split())
            if first and not re.fullmatch(r'[A-Z]{2,3}', first) and NAME.match(first) and \
                    not NOT_PLACE & {w.lower() for w in re.split(r'[ .-]+', first)}:
                names.add(first)
    return names


async def main() -> None:
    p = YCombinator()
    async with client(p.actor, 64) as http:
        companies = await p.hiring(http)
        lists = await p.read_lists(http, companies)
    counts: Counter = Counter()
    jobs = 0
    for _, listed in lists:
        for job in listed:
            jobs += 1
            counts.update(place_names(job.get('location')))
    kept = sorted(((n, c) for n, c in counts.items() if c >= MIN_JOBS), key=lambda nc: (-nc[1], nc[0]))
    lines = [f'# Places named by the open jobs of YC companies on Work at a Startup, updated {date.today().isoformat()}: '
             f'{len(kept)} of {len(counts)} names, each on {MIN_JOBS} or more of {jobs:,} jobs. '
             f'Built by scripts/refresh_ycombinator.py.',
             '# name\topen jobs naming it']
    lines += [f'{name}\t{count}' for name, count in kept]
    PLACES.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'{len(companies):,} companies, {jobs:,} jobs, {len(counts)} place names, {len(kept)} kept in {PLACES.name}')


if __name__ == '__main__':
    asyncio.run(main())
