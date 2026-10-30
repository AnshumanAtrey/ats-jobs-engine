"""
Rebuild ats_jobs/data/workday.tsv: every Workday career site we can find, each checked live.

Candidates: the sites already in the file; the Wayback Machine index of myworkdayjobs.com, one query per pod, collapsed
to the start of each URL so one site is one row; and the public job lists on GitHub that link Workday (SimplifyJobs and
others). A site is kept when its job search answers. An answer of 422 means the company moved to another pod (Walmart's
wd5 address fails, wd504 answers), so the other pods are tried. Workday reads site names in any case, so one site is
kept once, and sites named internal, private or employee are left out. Names already in the file are kept (the big
employers are written by hand: Workday publishes only a tenant name and legal entities); a new company gets its tenant
name split into words where a dictionary allows it. Biggest first, so a run stopped by its time limit has read the most
jobs.

Run: python3 scripts/refresh_workday.py   (needs httpx; writes the file, prints a summary)
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
from scripts.refresh_cornerstone import AGENT, dictionary, pretty  # noqa: E402

OUT = ROOT / 'ats_jobs' / 'data' / 'workday.tsv'
LINK = re.compile(r'https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com(?::\d+)?/(?:[a-z]{2}-[A-Za-z]{2}/)?([A-Za-z0-9_.-]+)', re.I)
SKIP = {'wday', 'en-us', 'api', 'cdn-cgi', 'robots.txt', 'sitemap.xml', 'favicon.ico', 'login', 'assets', 'static', 'job'}
PODS = ('wd1', 'wd2', 'wd3', 'wd5', 'wd10', 'wd12', 'wd100', 'wd101', 'wd102', 'wd103', 'wd104', 'wd105', 'wd108',
        'wd501', 'wd502', 'wd503', 'wd504')
LISTS = ('SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json',
         'SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json',
         'speedyapply/2026-SWE-College-Jobs/main/README.md')
MOVED_TO = ('wd504', 'wd501', 'wd503', 'wd502', 'wd505', 'wd5', 'wd1', 'wd3', 'wd12', 'wd10', 'wd103', 'wd105', 'wd108',
            'wd102', 'wd101', 'wd2')
INTERNAL = re.compile(r'private|internal|employee|alumni|staging|sandbox|^test|_test|-test|demo', re.I)
PARALLEL = 16


def sites_in(text: str) -> set[tuple[str, str, str]]:
    return {(t.lower(), p.lower(), s) for t, p, s in LINK.findall(text) if s.lower() not in SKIP}


def candidates() -> set[tuple[str, str, str]]:
    found: set[tuple[str, str, str]] = set()
    with httpx.Client(timeout=300, headers={'User-Agent': AGENT}, follow_redirects=True) as http:
        for path in LISTS:
            try:
                found |= sites_in(http.get(f'https://raw.githubusercontent.com/{path}').text)
            except httpx.HTTPError as exc:
                print(f'list {path}: {exc}')
        for pod in PODS:
            query = urllib.parse.urlencode({'url': f'{pod}.myworkdayjobs.com', 'matchType': 'domain', 'fl': 'original',
                                            'collapse': 'urlkey:64', 'filter': 'statuscode:200', 'limit': 300_000})
            try:
                answer = http.get(f'https://web.archive.org/cdx/search/cdx?{query}')
                found |= sites_in(answer.text)
                print(f'{pod}: {answer.status_code}, {len(found):,} sites so far')
            except httpx.HTTPError as exc:
                print(f'{pod}: {exc}')
    return found


def rows() -> list[tuple[str, str]]:
    lines = OUT.read_text(encoding='utf-8').splitlines() if OUT.exists() else []
    return [tuple(line.split('\t')[:2]) for line in lines if line and not line.startswith('#')]


async def check(http: httpx.AsyncClient, sem: asyncio.Semaphore, tenant: str, pod: str, site: str) -> dict | None:
    async with sem:
        for where in (pod, *(p for p in MOVED_TO if p != pod)):
            url = f'https://{tenant}.{where}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs'
            try:
                answer = await http.post(url, json={'appliedFacets': {}, 'limit': 1, 'offset': 0, 'searchText': ''})
                if answer.status_code == 200:
                    return {'tenant': tenant, 'pod': where, 'site': site, 'jobs': answer.json().get('total') or 0}
                if answer.status_code != 422 and where == pod:
                    return None
            except (httpx.HTTPError, ValueError):
                if where == pod:
                    return None
        return None


async def main() -> None:
    started = time.monotonic()
    known = rows()
    names = {key.split('/')[0]: name for key, name in known}
    words = dictionary()
    found = {site for site in candidates() | {tuple(key.split('/', 2)) for key, _ in known} if not INTERNAL.search(site[2])}
    print(f'{len(found):,} candidates')
    sem = asyncio.Semaphore(PARALLEL)
    limits = httpx.Limits(max_connections=PARALLEL * 2, max_keepalive_connections=PARALLEL)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=15), limits=limits, follow_redirects=True,
                                 headers={'User-Agent': AGENT}) as http:
        checked = await asyncio.gather(*[check(http, sem, *site) for site in found])
    one_each: dict[str, dict] = {}
    for c in (c for c in checked if c):
        key = f'{c["tenant"]}/{c["site"].lower()}'
        # the spelling with capitals is how the company wrote it
        if key not in one_each or c['site'] != c['site'].lower():
            one_each[key] = c
    live = sorted(one_each.values(), key=lambda c: (-c['jobs'], c['tenant'], c['site']))
    tenants = {c['tenant'] for c in live}
    lines = [f'# Workday career sites, updated {date.today().isoformat()}: every site found on myworkdayjobs.com that answers '
             'today, biggest first.',
             '# Columns: board key (tenant/pod/site), company name. Rebuilt by scripts/refresh_workday.py;',
             f'# {len(live):,} sites of {len(tenants):,} companies, {sum(c["jobs"] for c in live):,} open jobs before duplicates.']
    lines += [f'{c["tenant"]}/{c["pod"]}/{c["site"]}\t{names.get(c["tenant"]) or pretty(c["tenant"], words)}' for c in live]
    OUT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'{len(live):,} live sites of {len(tenants):,} companies, {sum(c["jobs"] for c in live):,} jobs, '
          f'{round(time.monotonic() - started)} s')


if __name__ == '__main__':
    asyncio.run(main())
