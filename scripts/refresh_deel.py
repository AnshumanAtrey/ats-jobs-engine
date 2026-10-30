"""
Rebuild ats_jobs/data/deel.tsv: every Deel career page we can find, each checked live.

Deel lists no boards: its sitemaps are per company and its API has no list. The site:jobs.deel.com dork as an index
query: the Wayback Machine lists the archived URLs of jobs.deel.com, and the first part of each path is a candidate.
The pages already in the file stay candidates, so a page found once by other means stays until Deel stops serving it;
slugs given on the command line are added. A page is kept when its settings answer. Names are Deel's own, with the
"(copy)" of a copied page left out, so its jobs dedupe with the original's. Biggest first, so a run stopped by its time
limit has read the most jobs. On 2026-10-30: 184 candidates from the Wayback Machine, Common Crawl's raw index, search
engines, GitHub and 31,728 guessed company names (those found 9 pages), 161 live pages of 159 companies, 145 hiring, 1,385
open jobs. No source saw them all: slugless pages have a UUID for an address, so only a crawl finds them.

Run: python3 scripts/refresh_deel.py [slug ...]   (needs httpx; writes the file, prints a summary)
"""
import asyncio
import re
import sys
import time
import urllib.parse
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ats_jobs.http import BoardError, send  # noqa: E402
from ats_jobs.platforms.deel import COPY, POSTINGS, SETTINGS, SLUG  # noqa: E402

OUT = ROOT / 'ats_jobs' / 'data' / 'deel.tsv'
LINK = re.compile(r'^https?://jobs\.deel\.com(?::\d+)?/(?:job-boards/)?([^/?#]+)', re.I)
PARALLEL = 16
AGENT = 'Mozilla/5.0 (compatible; ats-jobs-engine/1.0; +https://github.com/AnshumanAtrey/ats-jobs-engine)'


def wayback() -> set[str]:
    query = urllib.parse.urlencode({'url': 'jobs.deel.com/*', 'fl': 'original', 'collapse': 'urlkey', 'limit': 100_000})
    with httpx.Client(timeout=600, headers={'User-Agent': AGENT}) as http:
        lines = http.get(f'https://web.archive.org/cdx/search/cdx?{query}').text.splitlines()
    return {m.group(1) for m in map(LINK.match, lines) if m}


def known() -> set[str]:
    lines = OUT.read_text(encoding='utf-8').splitlines() if OUT.exists() else []
    return {line.split('\t')[0] for line in lines if line and not line.startswith('#')}


async def check(http: httpx.AsyncClient, sem: asyncio.Semaphore, slug: str) -> dict | None:
    async with sem:
        try:
            page = await send(http, 'GET', SETTINGS.format(slug=slug))
            if page.status_code != 200:
                return None
            settings = page.json()
            org, board = settings.get('organizationId'), (settings.get('jobBoard') or {}).get('id')
            if not org or not board:
                return None
            listed = await send(http, 'GET', POSTINGS.format(org=org, board=board))
            jobs = len(listed.json()) if listed.status_code == 200 else 0
        except (BoardError, ValueError) as exc:
            print(f'{slug}: {exc}')
            return None
    name = COPY.sub('', settings.get('preferredOrganizationName') or '').strip()
    return {'slug': settings.get('urlSlug') or slug, 'name': name or slug, 'jobs': jobs}


async def main(extra: list[str]) -> None:
    started = time.monotonic()
    candidates = sorted(slug for slug in wayback() | known() | set(extra) if SLUG.match(slug))
    print(f'{len(candidates):,} candidates')
    sem = asyncio.Semaphore(PARALLEL)
    limits = httpx.Limits(max_connections=PARALLEL * 2, max_keepalive_connections=PARALLEL)
    async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=15), limits=limits,
                                 headers={'User-Agent': AGENT}) as http:
        found = await asyncio.gather(*[check(http, sem, slug) for slug in candidates])
    pages = {f['slug']: f for f in found if f}
    live = sorted(pages.values(), key=lambda f: (-f['jobs'], f['name'].lower(), f['slug']))
    hiring = [f for f in live if f['jobs']]
    companies = {f['name'].lower() for f in live}
    jobs = sum(f['jobs'] for f in live)
    lines = [f'# Deel career pages, updated {date.today().isoformat()}: every jobs.deel.com page we could find that answers '
             'today, biggest first.',
             '# Columns: board key (the slug, its case matters), company name. Rebuilt by scripts/refresh_deel.py;',
             f'# {len(live):,} pages of {len(companies):,} companies, {len(hiring):,} hiring, {jobs:,} open jobs before duplicates.']
    lines += [f'{f["slug"]}\t{f["name"]}' for f in live]
    OUT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'{len(live):,} live pages of {len(companies):,} companies, {len(hiring):,} hiring, {jobs:,} jobs, '
          f'{round(time.monotonic() - started)} s')


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1:]))
