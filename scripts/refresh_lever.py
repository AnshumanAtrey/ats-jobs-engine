"""
Rebuild ats_jobs/data/lever.tsv: every Lever job site we can find, each checked live.

Lever lists no customers. Candidates: the sites already in the file; public lists on GitHub that name Lever sites
(job-board-aggregator, jobseek, SimplifyJobs, speedyapply and smaller catalogs); freehire's public job API, one country
at a time; with --deep also the Wayback Machine's index of jobs.lever.co and jobs.eu.lever.co, page by page, and Common
Crawl's raw URL index (both take hours: the Wayback Machine answers 503 under load); and site names given on the command
line or in a file (one per line, an EU site as name/eu). --quick reads only the file and GitHub. The sites already in
the file keep everything found once by any of these.

A site is kept when Lever's postings API answers for it (tried on the region a source names, then the other one) and its
links open: its job site page answers HEAD with 200, or, where a company hides that page, its first posting's page does
(veeva: the page 404, its 889 job pages 200). Sites whose job pages answer 404 as well are left out, because their links
would be dead: 467 sites with 1,412 postings in the API on 2026-11-03. Demo, training and test sites (leverdemo...,
salesdemo..., levertest, "LinkedIn Partner Sandbox", "Partner Experience XML Feed", a site whose company is "Acme", and
sites checked by hand whose every posting is a test, such as jobtwine) are left out: their postings are templates or
tests, not jobs. A company's real site with a few test postings among its jobs stays (MEGA: 6 of 12 on 2026-11-03).
Names are Lever's own: the job feed's employer for a site with postings, the
job site page's title for one without, and the site name where a company named itself only "career" or "jobs". A site
name keeps its case, because Lever's does, so archived links are read as written (the Wayback index key is lower case).
Biggest first, so a run stopped by its time limit has read the most jobs.

On 2026-11-02: 7,290 candidates from GitHub, freehire, Common Crawl and the first Wayback pages, then 1,630 more from
the rest of the Wayback index (419 pages for jobs.lever.co, 9 for jobs.eu.lever.co). On 2026-11-03 all of those names,
the file and GitHub (9,431 candidates, run with --quick --candidates) gave 2,397 live sites of 2,393 companies, 2,062
hiring, 76,589 open jobs before duplicates; leaving out JobTwine's test site the same day made it 2,396 sites of 2,392
companies, 2,061 hiring, 76,465 jobs. Of the live sites the Wayback Machine names 93% (82 no other source has), Common
Crawl 83% (6), GitHub lists 75% (11) and freehire 69% (125; it knows hiring sites only, and gave 23,584 of its 36,105
Lever jobs, because one query pages 10,000 rows and its US slice is bigger). Chao2 across the four gives at least 2,468
sites, a lower bound because the sources see the same web. Lever's vendor listings say over 5,000 companies
(GetApp, Y Combinator) and its home page says thousands of customers (2026-11-03); an account need not have a public
job site.

Run: python3 scripts/refresh_lever.py [--quick | --deep] [--candidates <file>] [site ...]   (needs httpx)
"""
import asyncio
import csv
import gzip
import io
import json
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
from ats_jobs.platforms.lever import (EMPLOYER, JOB_SITE, NOT_SITES, POSTINGS, SLUG, board_key, clean,  # noqa: E402
                                      site_of)

OUT = ROOT / 'ats_jobs' / 'data' / 'lever.tsv'
AGENT = 'Mozilla/5.0 (compatible; ats-jobs-engine/1.0; +https://github.com/AnshumanAtrey/ats-jobs-engine)'
LINK = re.compile(r'(?:https?://)?(?:jobs|api)\.(eu\.)?lever\.co(?::\d+)?/(?:v0/postings/)?([A-Za-z0-9][A-Za-z0-9_.-]*)', re.I)
JOB = re.compile(r'href="https://jobs\.(?:eu\.)?lever\.co/[^/"]+/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"')
TITLE = re.compile(r'<title>(.*?)</title>', re.S)
SANDBOX_KEY = re.compile(r'^(?:leverdemo|salesdemo|levertest)', re.I)
SANDBOX_NAME = re.compile(r'^(?:Lever (?:Demo|Education|Implementation|Test)|Sales Demo\b|Acme$)|\bPartner (?:Sandbox|Experience)\b',
                          re.I)    # "Acme": sigma-squared's 49 template postings (Army, Bartender, one About Us text)
TEST_SITES = {   # checked by hand: every posting is a test, under a real company's name
    'jobtwine',  # JobTwine's Lever integration tests: 124 postings ("Test Lever Job", "kombo job 2", "Meesho mock test"),
}                # many titled with its staff's first names (2026-11-03)
GENERIC = {'career', 'careers', 'job', 'jobs', 'hiring', 'recruiting', 'recruitment', 'job board', 'jobs board', 'join us',
           'work with us', 'opportunities', 'openings'}
RAW = 'https://raw.githubusercontent.com/'
GITHUB = ('Feashliaa/job-board-aggregator/main/data/lever_companies.json',
          'colophon-group/jobseek/main/apps/crawler/data/boards.csv',
          'SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json',
          'SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json',
          'speedyapply/2026-SWE-College-Jobs/main/README.md',
          'speedyapply/2026-AI-College-Jobs/main/README.md',
          'kalebconfer-sys/job-board-directory/main/lever.csv',
          'mikeym88/job-board-scraper/master/csv_files/jobs.lever.co_companies.csv',
          'Cla-Code-Community/candidate/master/scraper-go/internal/interfaces/leverCompanies.json',
          'vishal-yadav111/Job-Finder-main/master/backend/app/config/providers/lever_companies.py',
          'crypto-jobs-fyi/crawler/main/fin_companies.json')
FREEHIRE = 'https://freehire.me/api/v1'
CDX = 'https://web.archive.org/cdx/search/cdx'
CRAWL = 'https://data.commoncrawl.org/cc-index/collections/{}/indexes/'
CRAWLS = ('CC-MAIN-2025-30', 'CC-MAIN-2025-26', 'CC-MAIN-2025-21', 'CC-MAIN-2025-18', 'CC-MAIN-2025-13', 'CC-MAIN-2025-08',
          'CC-MAIN-2025-05', 'CC-MAIN-2024-51', 'CC-MAIN-2024-46', 'CC-MAIN-2024-42', 'CC-MAIN-2024-38', 'CC-MAIN-2024-33',
          'CC-MAIN-2024-30', 'CC-MAIN-2024-26', 'CC-MAIN-2024-22', 'CC-MAIN-2024-18', 'CC-MAIN-2024-10', 'CC-MAIN-2026-43')
PARALLEL = 12


def add(found: dict, site: str, region: str) -> None:
    """A candidate site and the region its source names (eu wins: a source that saw the EU host knows)."""
    site = urllib.parse.unquote(site).strip()
    if SLUG.match(site) and site not in NOT_SITES:
        found[site] = 'eu' if region == 'eu' or found.get(site) == 'eu' else 'global'


def links_in(text: str, found: dict) -> None:
    for eu, site in LINK.findall(text):
        add(found, site, 'eu' if eu else 'global')


def github(http: httpx.Client) -> dict:
    found: dict = {}
    for path in GITHUB:
        try:
            answer = http.get(RAW + path)
        except httpx.HTTPError as exc:
            print(f'github {path}: {exc}')
            continue
        if answer.status_code != 200:
            print(f'github {path}: HTTP {answer.status_code}')
            continue
        text = answer.text
        links_in(text, found)
        if path.endswith('lever_companies.json'):           # a list of bare site names
            for site in json.loads(text):
                add(found, str(site), 'global')
        elif path.endswith('leverCompanies.json'):          # [{"site": ..., "region": "eu"}]
            for entry in json.loads(text):
                add(found, entry.get('site') or '', entry.get('region') or 'global')
        elif path.endswith('boards.csv'):                   # jobseek: monitor_type lever, {"token": ...}
            for row in csv.DictReader(io.StringIO(text)):
                if row.get('monitor_type') == 'lever':
                    config = json.loads(row.get('monitor_config') or '{}')
                    add(found, config.get('token') or '', 'eu' if config.get('region') == 'eu' else 'global')
        elif path.endswith('lever_companies.py'):
            for site in re.findall(r'"token":\s*"([^"]+)"', text):
                add(found, site, 'global')
    print(f'GitHub lists: {len(found):,} candidates')
    return found


class Refused(Exception):
    """freehire gave no page: its daily quota is used up, or a request failed five times."""


def freehire(http: httpx.Client) -> dict:
    """Every Lever job URL freehire holds. A query pages at most 10,000 rows, so it reads one slice per country.
    freehire allows 2,000 requests a day per address with no key, shared by everything on the machine: a refused
    request stops this source at once with what it read, and is never taken for an empty page."""
    def get(path):
        for attempt in range(5):
            try:
                answer = http.get(FREEHIRE + path, timeout=60)
            except httpx.HTTPError:
                answer = None
            if answer is not None and answer.status_code == 429 and answer.headers.get('x-ratelimit-remaining') == '0':
                raise Refused('its quota for today is used up')
            if answer is not None and answer.status_code == 200:
                try:
                    data = answer.json().get('data')
                except (ValueError, AttributeError):
                    data = None
                if data is not None:
                    return data
            time.sleep(3 + attempt * 5)
        raise Refused(f'{path} failed 5 times')
    found: dict = {}
    try:
        facets = (get('/jobs/facets?source=lever').get('facets') or {}).get('countries') or {}
        for where in [''] + [f'&countries={urllib.parse.quote(c)}' for c in sorted(facets, key=lambda c: -facets[c])]:
            for offset in range(0, 10_000, 100):
                rows = get(f'/jobs/search?source=lever&limit=100&offset={offset}{where}')
                for row in rows:
                    links_in(row.get('url') or '', found)
                time.sleep(1)
                if len(rows) < 100:
                    break
    except Refused as exc:
        print(f'freehire stopped: {exc}. Kept {len(found):,} candidates; run again another day for the rest.')
    print(f'freehire: {len(found):,} candidates')
    return found


def wayback(http: httpx.Client) -> dict:
    """The CDX index of both hosts, one page at a time, with patience for 503 and 504. The original URLs, because the
    index key is lower case and Lever's site names are not."""
    found: dict = {}
    for host in ('jobs.eu.lever.co', 'jobs.lever.co'):
        try:
            pages = int(http.get(CDX, params={'url': f'{host}/*', 'showNumPages': 'true'}, timeout=120).text.strip())
        except (httpx.HTTPError, ValueError) as exc:
            print(f'wayback {host}: no page count ({exc})')
            continue
        for page in range(pages):
            for attempt in range(8):
                try:
                    answer = http.get(CDX, params={'url': f'{host}/*', 'page': page, 'fl': 'original'}, timeout=300)
                    if answer.status_code == 200 and '<html' not in answer.text[:200].lower():
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(10 + attempt * 20)
            else:
                print(f'wayback {host} page {page}: no answer')
                continue
            links_in(answer.text, found)
            time.sleep(1)
        print(f'wayback {host}: {len(found):,} candidates so far')
    return found


def common_crawl(http: httpx.Client) -> dict:
    """Common Crawl's raw URL index, read with range requests: it is sorted by SURT, so a binary search finds a host."""
    found: dict = {}
    for prefix, region in (('co,lever,eu,jobs)/', 'eu'), ('co,lever,jobs)/', 'global')):
        end = prefix[:-1] + '~'
        for crawl in CRAWLS:
            base = CRAWL.format(crawl)

            def part(url, start, stop):
                for attempt in range(6):
                    try:
                        return http.get(url, headers={'Range': f'bytes={start}-{stop}'}, timeout=90).content
                    except httpx.HTTPError:
                        time.sleep(2 + attempt * 4)
                return b''
            try:
                size = int(http.head(base + 'cluster.idx', timeout=60).headers['content-length'])
            except (httpx.HTTPError, KeyError, ValueError):
                continue
            low, high = 0, size
            while high - low > 300_000:
                middle = (low + high) // 2
                line = part(base + 'cluster.idx', middle, middle + 8000).decode('utf-8', 'ignore').split('\n')[1]
                low, high = (middle, high) if line.split(' ')[0] < prefix else (low, middle)
            blocks, position = [], low
            while True:
                lines = part(base + 'cluster.idx', position, position + 600_000).decode('utf-8', 'ignore').split('\n')
                body = lines[1:-1] if position else lines[:-1]
                for i, line in enumerate(body):
                    following = body[i + 1].split(' ')[0] if i + 1 < len(body) else '~'
                    if following >= prefix and line.split(' ')[0] <= end:
                        name, offset, length = line.split('\t')[1:4]
                        blocks.append((name, int(offset), int(length)))
                if not body or body[-1].split(' ')[0] > end:
                    break
                position += 598_000
            for name, offset, length in blocks:
                try:
                    text = gzip.decompress(part(base + name, offset, offset + length - 1)).decode('utf-8', 'ignore')
                except (OSError, EOFError):
                    continue
                for line in text.split('\n'):
                    if line.startswith(prefix):
                        add(found, line[len(prefix):].split('/')[0].split('?')[0].split(' ')[0], region)
        print(f'Common Crawl {prefix}: {len(found):,} candidates so far')
    return found


def sandbox(site: str, name: str | None) -> bool:
    """A demo, training or test site: its postings are templates or tests, not jobs."""
    return bool(SANDBOX_KEY.match(site) or SANDBOX_NAME.search(name or '') or site.lower() in TEST_SITES)


def rows() -> dict[str, str]:
    lines = OUT.read_text(encoding='utf-8').splitlines() if OUT.exists() else []
    return {line.split('\t')[0]: (line.split('\t') + [''])[1] for line in lines if line and not line.startswith('#')}


async def check(http: httpx.AsyncClient, sem: asyncio.Semaphore, site: str, hint: str) -> dict | None:
    """The API's answer (on the region named, then the other) and whether the site's links open: its job site page,
    or, where a company hides that page, its first posting's page (veeva: page 404, 889 job pages 200)."""
    async with sem:
        try:
            for region in (hint, 'eu' if hint == 'global' else 'global'):
                listed = await send(http, 'GET', POSTINGS[region][0] + urllib.parse.quote(site), params={'mode': 'html'})
                if listed.status_code == 404:
                    continue
                if listed.status_code != 200:
                    print(f'{site}: HTTP {listed.status_code}')
                    return None
                jobs = list(dict.fromkeys(JOB.findall(listed.text)))
                home = JOB_SITE[region] + urllib.parse.quote(site)
                alive = (await send(http, 'HEAD', home, follow_redirects=True)).status_code == 200
                if not alive and jobs:
                    alive = (await send(http, 'HEAD', f'{home}/{jobs[0]}', follow_redirects=True)).status_code == 200
                return {'site': site, 'region': region, 'jobs': len(jobs), 'alive': alive}
        except BoardError as exc:
            print(f'{site}: {exc}')
        return None


async def name_of(http: httpx.AsyncClient, sem: asyncio.Semaphore, found: dict) -> str | None:
    """The job feed's employer for a site with postings, the job site page's title for one without."""
    site, region = found['site'], found['region']
    async with sem:
        try:
            if found['jobs']:
                feed = await send(http, 'GET', POSTINGS[region][0] + urllib.parse.quote(site),
                                  params={'mode': 'xml', 'limit': 1})
                match = EMPLOYER.search(feed.text) if feed.status_code == 200 else None
            else:
                page = await send(http, 'GET', JOB_SITE[region] + urllib.parse.quote(site))
                match = TITLE.search(page.text) if page.status_code == 200 else None
        except BoardError:
            return None
    return clean(match.group(1)) or None if match else None


async def main(args: list[str]) -> None:
    started = time.monotonic()
    deep, quick = '--deep' in args, '--quick' in args
    given = list(args)
    if '--candidates' in given:
        at = given.index('--candidates')
        given[at:at + 2] = Path(given[at + 1]).read_text(encoding='utf-8').split()
    known = rows()
    candidates: dict = {}
    for key in [*known, *(g for g in given if not g.startswith('--'))]:
        site, region = site_of(key)
        add(candidates, site, region or 'global')
    with httpx.Client(timeout=300, headers={'User-Agent': AGENT}, follow_redirects=True) as http:
        sources = [github] + ([] if quick else [freehire]) + ([wayback, common_crawl] if deep else [])
        for source in sources:
            for site, region in source(http).items():
                add(candidates, site, region)
    print(f'{len(candidates):,} candidates')
    sem = asyncio.Semaphore(PARALLEL)
    limits = httpx.Limits(max_connections=PARALLEL * 2, max_keepalive_connections=PARALLEL * 2)
    async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15), limits=limits,
                                 headers={'User-Agent': AGENT, 'Accept-Encoding': 'gzip'}) as http:
        checked = [c for c in await asyncio.gather(*[check(http, sem, s, r) for s, r in candidates.items()]) if c]
        live = [c for c in checked if c['alive']]
        names = {board_key(c['site'], c['region']): known.get(board_key(c['site'], c['region'])) for c in live}
        missing = [c for c in live if not names[board_key(c['site'], c['region'])] or c['jobs']]
        for c, name in zip(missing, await asyncio.gather(*[name_of(http, sem, c) for c in missing])):
            key = board_key(c['site'], c['region'])
            names[key] = name or names[key] or c['site']
    for key, name in names.items():
        if name.strip().casefold() in GENERIC:
            names[key] = site_of(key)[0]
    sandboxes = [c for c in live if sandbox(c['site'], names[board_key(c['site'], c['region'])])]
    live = [c for c in live if c not in sandboxes]
    live.sort(key=lambda c: (-c['jobs'], names[board_key(c['site'], c['region'])].lower(), c['site']))
    hiring = [c for c in live if c['jobs']]
    companies = {names[board_key(c['site'], c['region'])].casefold() for c in live}
    jobs = sum(c['jobs'] for c in live)
    dead = [c for c in checked if not c['alive']]
    lines = [f'# Lever job sites, updated {date.today().isoformat()}: every jobs.lever.co and jobs.eu.lever.co site we could '
             'find whose API and job site answer today, biggest first.',
             '# Columns: board key (the site name, its case matters; an EU site ends in /eu), company name. Rebuilt by '
             'scripts/refresh_lever.py;',
             f'# {len(live):,} sites of {len(companies):,} companies, {len(hiring):,} hiring, {jobs:,} open jobs before '
             f'duplicates. Left out: {len(dead):,} sites the API answers for whose job site and job pages answer 404 '
             f'({sum(c["jobs"] for c in dead):,} postings with dead links) and {len(sandboxes):,} demo and test sites with template postings.']
    lines += [f'{board_key(c["site"], c["region"])}\t{names[board_key(c["site"], c["region"])]}' for c in live]
    OUT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'{len(live):,} live sites of {len(companies):,} companies, {len(hiring):,} hiring, {jobs:,} jobs; '
          f'{len(dead):,} left out with dead job sites, {len(sandboxes):,} demo and test sites; '
          f'{round(time.monotonic() - started)} s')


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1:]))
