"""
Rebuild ats_jobs/data/greenhouse.tsv: every Greenhouse job board we can find, each checked live.

Greenhouse publishes no list of boards, and its job seeker search (my.greenhouse.io) needs a login. The
site:boards.greenhouse.io dork as index queries, each source read once:
- the boards already in the file, so a board found once stays until Greenhouse stops serving it;
- public lists on GitHub: Feashliaa/job-board-aggregator's greenhouse_companies.json, colophon-group/jobseek's
  boards.csv and the job lists of SimplifyJobs and speedyapply;
- Common Crawl's raw URL index on data.commoncrawl.org (sorted by SURT, read with range requests) for every
  Greenhouse board host and the API hosts, in the newest crawls;
- with --wayback, the Wayback Machine's index of every board host, board-root URLs only (a filter on its side): about
  8 minutes for job-boards.greenhouse.io and longer for boards.greenhouse.io;
- with --freehire, freehire's public job API (freehire.me), whose external_id is <token>:<job id>. A query pages at
  most 10,000 rows, so it is read per country and the US per category: about 1,500 calls, one a second, of a quota
  of 2,000 calls a day for one IP address;
- tokens given on the command line.
A board is kept when its job list answers; when any check fails (Greenhouse refuses an address that made too many
calls), the file is left as it was. Greenhouse reads tokens in any case, so they are kept in lower case.
Names are Greenhouse's own (the board's name); the website column is the company's own career site when every job
of the board links there (stripe.com), which also reads links such as stripe.com/jobs/search?gh_jid=<id>. Biggest
first, so a run stopped by its time limit has read the most jobs.

Run: python3 scripts/refresh_greenhouse.py [--wayback] [--freehire] [token ...]   (needs the engine's dependencies,
pip install .; writes the file, prints a summary)
"""
import asyncio
import csv
import gzip
import io
import json
import math
import re
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ats_jobs.http import BoardError  # noqa: E402
from ats_jobs.platforms.greenhouse import TOKEN, Greenhouse, clean  # noqa: E402

OUT = ROOT / 'ats_jobs' / 'data' / 'greenhouse.tsv'
AGENT = 'Mozilla/5.0 (compatible; ats-jobs-engine/1.0; +https://github.com/AnshumanAtrey/ats-jobs-engine)'
RAW = 'https://raw.githubusercontent.com/'
LISTS = ('Feashliaa/job-board-aggregator/main/data/greenhouse_companies.json',
         'colophon-group/jobseek/main/apps/crawler/data/boards.csv',
         'SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json',
         'SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json',
         'speedyapply/2026-SWE-College-Jobs/main/README.md',
         'speedyapply/2026-SWE-College-Jobs/main/NEW_GRAD_USA.md')
CC_INDEX = 'https://data.commoncrawl.org/cc-index/collections/{}/indexes/'
CRAWLS = ('CC-MAIN-2026-43', 'CC-MAIN-2026-39', 'CC-MAIN-2026-30', 'CC-MAIN-2026-21', 'CC-MAIN-2026-17')
HOSTS = ('io,greenhouse,job-boards)/', 'io,greenhouse,boards)/', 'io,greenhouse,eu,job-boards)/',
         'io,greenhouse,eu,boards)/', 'io,greenhouse,boards-api)/', 'io,greenhouse,api)/')
FREEHIRE = 'https://freehire.me/api/v1'
WAYBACK = 'https://web.archive.org/cdx/search/cdx'
ROOTS = {'job-boards.greenhouse.io': 'io,greenhouse,job-boards', 'boards.greenhouse.io': 'io,greenhouse,boards',
         'job-boards.eu.greenhouse.io': 'io,greenhouse,eu,job-boards', 'boards.eu.greenhouse.io': 'io,greenhouse,eu,boards'}
URL = re.compile(r'https?://[^\s"\'<>()\]\\]*greenhouse\.io[^\s"\'<>()\]\\]*', re.I)
PARALLEL = 16                  # a scan of 16,000 mostly dead names at about 90 calls a second got this IP blocked
P = Greenhouse()


def token_of(url: str) -> str | None:
    link = P.read_link(url)
    return link[0] if link else None


def lists(http: httpx.Client) -> set[str]:
    found = set()
    for path in LISTS:
        try:
            text = http.get(RAW + path).text
        except httpx.HTTPError as exc:
            print(f'{path}: {exc}')
            continue
        if path.endswith('greenhouse_companies.json'):
            found.update(t.lower() for t in json.loads(text) if isinstance(t, str))
        elif path.endswith('boards.csv'):
            for row in csv.DictReader(io.StringIO(text)):
                if row.get('monitor_type') == 'greenhouse':
                    found.add((json.loads(row.get('monitor_config') or '{}').get('token') or '').lower())
        found.update(filter(None, map(token_of, URL.findall(text))))
    return {t for t in found if TOKEN.match(t)}


def ranged(http: httpx.Client, url: str, start: int, end: int) -> bytes:
    for attempt in range(6):
        try:
            response = http.get(url, headers={'Range': f'bytes={start}-{end}'})
            if response.status_code in (200, 206):
                return response.content
        except httpx.HTTPError:
            pass
        time.sleep(2 + attempt * 4)
    raise RuntimeError(f'range request failed: {url}')


def blocks(http: httpx.Client, index: str, size: int, prefix: str) -> list[tuple[str, int, int]]:
    """The index blocks that can hold URLs starting with prefix: a binary search over cluster.idx, then a forward read."""
    end = prefix + '~'
    low, high = 0, size
    while high - low > 300_000:
        middle = (low + high) // 2
        line = ranged(http, index + 'cluster.idx', middle, middle + 8_000).decode('utf-8', 'ignore').split('\n')[1]
        low, high = (middle, high) if line.split(' ')[0] < prefix else (low, middle)
    found, position = [], low
    while True:
        lines = ranged(http, index + 'cluster.idx', position, position + 600_000).decode('utf-8', 'ignore').split('\n')
        body = lines[1:-1] if position else lines[:-1]
        for i, line in enumerate(body):
            following = body[i + 1].split(' ')[0] if i + 1 < len(body) else '~'
            if following >= prefix and line.split(' ')[0] <= end:
                fields = line.split('\t')
                found.append((fields[1], int(fields[2]), int(fields[3])))
        if not body or body[-1].split(' ')[0] > end:
            return found
        position += 598_000


def common_crawl(http: httpx.Client) -> set[str]:
    found = set()
    for crawl in CRAWLS:
        index = CC_INDEX.format(crawl)
        try:
            size = int(http.head(index + 'cluster.idx').headers['content-length'])
            for prefix in HOSTS:
                for name, offset, length in dict.fromkeys(blocks(http, index, size, prefix)):
                    text = gzip.decompress(ranged(http, index + name, offset, offset + length - 1)).decode('utf-8', 'ignore')
                    for line in text.splitlines():
                        if line.startswith(prefix):
                            found.add(token_of(json.loads(line.split(' ', 2)[2]).get('url') or ''))
        except (RuntimeError, httpx.HTTPError, KeyError, ValueError) as exc:
            print(f'{crawl}: {exc}')
        print(f'{crawl}: {len(found - {None}):,} tokens so far')
    return {t for t in found if t}


def wayback(http: httpx.Client) -> set[str]:
    found = set()
    for host, surt in ROOTS.items():
        query = {'url': f'{host}/', 'matchType': 'prefix', 'fl': 'original', 'collapse': 'urlkey', 'limit': 300_000,
                 'filter': f'urlkey:{surt}\\)/[^/]+/?$'}
        try:
            found.update(filter(None, map(token_of, http.get(WAYBACK, params=query, timeout=1_800).text.split())))
        except httpx.HTTPError as exc:
            print(f'{host}: {exc}')
        print(f'Wayback {host}: {len(found):,} tokens so far')
    return found


def freehire(http: httpx.Client) -> set[str]:
    def get(path: str) -> dict:
        for attempt in range(5):
            try:
                return http.get(FREEHIRE + path).json()
            except (httpx.HTTPError, ValueError):
                time.sleep(3 + attempt * 5)
        return {}
    facets = (get('/jobs/facets?source=greenhouse').get('data') or {}).get('facets') or {}
    us = (get('/jobs/facets?source=greenhouse&countries=us').get('data') or {}).get('facets') or {}
    slices = [''] + [f'&countries={c}' for c in facets.get('countries') or {}]
    for category, count in (us.get('category') or {}).items():
        types = ('full_time', 'part_time', 'contract', 'internship', 'fellowship') if count > 10_000 else ('',)
        slices += [f'&countries=us&category={category}' + (f'&employment_type={t}' if t else '') for t in types]
    found = set()
    for where in slices:
        for offset in range(0, 10_000, 100):
            rows = get(f'/jobs/search?source=greenhouse&limit=100&offset={offset}{where}').get('data') or []
            found.update((r.get('external_id') or '').split(':')[0].lower() for r in rows)
            found.update(filter(None, (token_of(r.get('url') or '') for r in rows)))
            time.sleep(1)
            if len(rows) < 100:
                break
    print(f'freehire: {len(found):,} tokens from {len(slices)} slices')
    return {t for t in found if TOKEN.match(t)}


def known() -> set[str]:
    lines = OUT.read_text(encoding='utf-8').splitlines() if OUT.exists() else []
    return {line.split('\t')[0] for line in lines if line and not line.startswith('#')}


async def check(http: httpx.AsyncClient, sem: asyncio.Semaphore, token: str) -> dict | None:
    async with sem:
        try:
            listed = await P.get(http, f'{token}/jobs')
            if listed.status_code != 200:
                return None
            jobs = listed.json().get('jobs') or []
            board = await P.get(http, token)
            name = clean(board.json().get('name')) if board.status_code == 200 else ''
        except (BoardError, ValueError, AttributeError) as exc:
            print(f'{token}: {exc}')
            return {'token': token, 'error': str(exc)}
    hosts = {(urlparse(j.get('absolute_url') or '').hostname or '').lower() for j in jobs}
    site = next(iter(hosts)) if len(hosts) == 1 and not next(iter(hosts)).endswith('greenhouse.io') else ''
    company = clean(next((j.get('company_name') for j in jobs if j.get('company_name')), ''))
    return {'token': token, 'name': name or company or token, 'jobs': len(jobs), 'website': f'https://{site}' if site else ''}


async def main(args: list[str]) -> None:
    started = time.monotonic()
    sources: dict[str, set[str]] = {'file': known(), 'command line': {a.lower() for a in args if not a.startswith('--')}}
    with httpx.Client(timeout=120, headers={'User-Agent': AGENT}, follow_redirects=True) as http:
        sources['GitHub lists'] = lists(http)
        sources['Common Crawl'] = common_crawl(http)
        if '--wayback' in args:
            sources['Wayback Machine'] = wayback(http)
        if '--freehire' in args:
            sources['freehire'] = freehire(http)
    candidates = sorted({t for found in sources.values() for t in found if TOKEN.match(t)})
    print(f'{len(candidates):,} candidates: ' + ', '.join(f'{k} {len(v):,}' for k, v in sources.items()))
    sem = asyncio.Semaphore(PARALLEL)
    limits = httpx.Limits(max_connections=PARALLEL * 2, max_keepalive_connections=PARALLEL)
    async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=15), limits=limits, follow_redirects=True,
                                 headers={'User-Agent': AGENT, 'Accept-Encoding': 'gzip'}) as http:
        checked = await asyncio.gather(*[check(http, sem, t) for t in candidates])
    failed = [c for c in checked if c and 'error' in c]
    if failed:                     # a refused call is not a dead board: keep the list as it is
        print(f'{len(failed):,} boards could not be checked ({failed[0]["error"]}); the file was not rewritten. Run again later.')
        return
    live = sorted((c for c in checked if c), key=lambda c: (-c['jobs'], c['name'].lower(), c['token']))
    hiring = {c['token'] for c in live if c['jobs']}
    jobs = sum(c['jobs'] for c in live)
    for name, found in sources.items():
        alone = found - set().union(*(v for k, v in sources.items() if k != name))
        print(f'{name}: {len(found & hiring):,} hiring boards, {len(alone & hiring):,} found by no other source')
    seen = Counter(sum(t in found for k, found in sources.items() if k != 'file') for t in hiring)
    chao1 = len(hiring) + seen[1] ** 2 / (2 * seen[2]) if seen[2] else math.nan
    lines = [f'# Greenhouse job boards, updated {date.today().isoformat()}: every board found that answers today, biggest first.',
             '# Columns: board token (any case reads the same board), company name, the company\'s own career site when',
             '# its jobs link there. Rebuilt by scripts/refresh_greenhouse.py;',
             f'# {len(live):,} boards of {len({c["name"].lower() for c in live}):,} companies, {len(hiring):,} hiring, '
             f'{jobs:,} open jobs before duplicates.']
    lines += [f'{c["token"]}\t{c["name"]}\t{c["website"]}'.rstrip('\t') for c in live]
    OUT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'{len(live):,} live boards, {len(hiring):,} hiring, {jobs:,} jobs; Chao1 over the sources {chao1:,.0f} hiring '
          f'boards (a lower bound: the sources are not independent); {round(time.monotonic() - started)} s')


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1:]))
