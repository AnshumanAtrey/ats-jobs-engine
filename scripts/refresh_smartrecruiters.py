"""
Rebuild ats_jobs/data/smartrecruiters.tsv: every SmartRecruiters career site with open jobs that we can find, each
checked live.

SmartRecruiters publishes no customer list, but its own job search holds one: the company box of
jobs.smartrecruiters.com (sr-jobs/company-lookup?q=) matches the starts of the words of company names and answers at
most 100 companies a query. Every one-letter start, every two-letter start under a full one, then the first four
letters of every word of every name found reach the companies in that search: three- and five-letter queries match
fuzzily and added nothing. On 2026-11-02 a crawl of 18,087 queries gave 11,361 companies (11,203 hiring); the version
here asks 8,075 and finds 11,312 (11,154 hiring), and a site found once stays listed. Of the 5,187 companies in the
job search that other sources saw, the box missed 6.

Companies that keep out of SmartRecruiters' job search (AbbVie, City of New York, Wabtec: 1,387 hiring sites, 36,440
jobs on 2026-11-02) are not in the box. The Wayback Machine's index of careers.smartrecruiters.com and
jobs.smartrecruiters.com finds some (one row per company with collapse=urlkey:38, one first character at a time), and
the sites already in the file stay candidates, so a site found once by other means (freehire, Common Crawl, jobseek)
stays until SmartRecruiters stops serving it. Slugs, links, or files of them given as @path, are added.

A site is kept when the Posting API lists an open job on it, with SmartRecruiters' own identifier and company name
(identifiers are read in any case, so case variants count once). Biggest first, so a run reads the busiest boards first.
A listed site that does not answer stays, at the end; when more than 1% do not answer, the file is left as it was.

A posting stays open until its company closes it, and many never do. A site with nothing posted in two years is written
as a #~ comment line after the others (key, name, newest posting, postings): runs leave it out, a run that names it
reads it, and the next refresh checks it again. On 2026-11-02, 9,650 of the 12,593 hiring sites had posted nothing
since 2024-11-02 and held 180,513 postings (129,639 on sites whose newest is from 2017), while 606 sites posted within
a day.

On 2026-11-02: 2,943 career sites of 2,931 companies with a job posted within two years, 242,790 open jobs before
duplicates. All hiring sites by source (alone in brackets): the company box 11,203 (6,022), the Wayback Machine 4,566 (530), freehire's public API 3,848 from
69,143 of its 172,042 rows (514), Common Crawl 1,518 (10), jobseek 142 (0). Of the sites outside the job search,
capture-recapture between freehire (744) and the Wayback Machine (857, 224 in both) puts their number near 2,846
(Chao1 over four sources 3,939) against the 1,387 found, and those seen by one source hold a median of 2 jobs: about
83 to 90% of hiring sites and 93 to 96% of open jobs. The sources are not independent, so the real share can be lower.

Run: python3 scripts/refresh_smartrecruiters.py [slug | link | @file ...]   (needs httpx; writes the file, prints a summary)
     --no-wayback skips the Wayback Machine (about an hour, one request at a time)
"""
import asyncio
import re
import string
import sys
import time
import urllib.parse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ats_jobs.platforms.smartrecruiters import API, COMPANY, DORMANT, SmartRecruiters  # noqa: E402

OUT = ROOT / 'ats_jobs' / 'data' / 'smartrecruiters.tsv'
LOOKUP = 'https://jobs.smartrecruiters.com/sr-jobs/company-lookup'
CDX = 'https://web.archive.org/cdx/search/cdx'
AGENT = 'Mozilla/5.0 (compatible; ats-jobs-engine/1.0; +https://github.com/AnshumanAtrey/ats-jobs-engine)'
ALPHA = string.ascii_lowercase + string.digits
CAP = 100                    # the company box answers at most this many companies
BOX_PARALLEL = 8             # the job search's own service: gentle
CHECK_PARALLEL = 48
ACTIVE_DAYS = 730            # a site with nothing posted in two years is written as a #~ comment line: runs leave it out
CAMEL = re.compile(r'[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+')
PLATFORM = SmartRecruiters()


def word_starts(text: str) -> set[str]:
    """'Bosch Group', 'BoschGroup' -> {'bosc', 'grou'}: the first four letters of each word and camel-case part."""
    starts = set()
    for word in re.split(r'[^\w]+', text or ''):
        for part in {word, *CAMEL.findall(word)}:
            part = part.lower()
            if len(part) >= 4 and part.isalnum():
                starts.add(part[:4])
    return starts


async def get_json(http: httpx.AsyncClient, url: str, params: dict):
    for attempt in range(5):
        try:
            response = await http.get(url, params=params)
            if response.status_code == 200:
                return response.json()
            if response.status_code in (400, 401, 404):    # 401: a company that locked its Posting API (CHECK24);
                return None                                  # 403 is a blocked address: retried, then a failure
        except (httpx.HTTPError, ValueError):
            pass
        await asyncio.sleep(1 + attempt * 3)
    raise RuntimeError(f'no answer from {url} {params}')


async def company_box(http: httpx.AsyncClient) -> dict[str, str]:
    """Every company in SmartRecruiters' job search: identifier -> name."""
    found: dict[str, str] = {}
    asked: set[str] = set()
    sem = asyncio.Semaphore(BOX_PARALLEL)

    async def ask(q: str) -> int:
        async with sem:
            try:
                data = await get_json(http, LOOKUP, {'q': q}) or {}
            except RuntimeError as exc:
                print(f'company box: {exc}, left out', flush=True)
                return 0
        results = [r for r in data.get('results') or [] if r.get('identifier')]
        for r in results:
            found.setdefault(r['identifier'], ' '.join((r.get('name') or r['identifier']).split()))
        return len(results)

    async def wave(queries: list[str]) -> dict[str, int]:
        queries = [q for q in dict.fromkeys(queries) if q not in asked]
        asked.update(queries)
        return dict(zip(queries, await asyncio.gather(*[ask(q) for q in queries])))

    full = [q for q, n in (await wave(list(ALPHA))).items() if n >= CAP]
    await wave([q + c for q in full for c in ALPHA])
    while True:                                   # the four-letter starts of every name found, until none is new
        starts = set()
        for ident, name in found.items():
            starts |= word_starts(name) | word_starts(ident)
        todo = sorted(starts - asked)
        if not todo:
            break
        await wave(todo)
        print(f'company box: {len(asked):,} queries, {len(found):,} companies', flush=True)
    return found


def cdx_rows(http: httpx.Client, query: dict, limit: int) -> list[str] | None:
    """Every row of one CDX query, walked by resume key so that no request scans too much; None when it never answers."""
    rows, resume = [], None
    while True:
        params = {**query, 'limit': limit, 'showResumeKey': 'true', **({'resumeKey': resume} if resume else {})}
        lines = None
        for attempt in range(5):
            try:
                response = http.get(CDX, params=params)
                if response.status_code == 200:
                    lines = response.text.splitlines()
                    break
            except httpx.HTTPError:
                pass
            time.sleep(15 + attempt * 30)          # the Wayback Machine answers 503 and 504 under load
        if lines is None:
            return None
        resume = lines[-1] if len(lines) >= 2 and lines[-2] == '' else None
        rows += lines[:-2] if resume else lines
        time.sleep(2)
        if not resume:
            return rows


def wayback() -> set[str]:
    """Company slugs in the Wayback Machine's index of careers.smartrecruiters.com and jobs.smartrecruiters.com: every
    careers URL, and one row per distinct 12-character path start of the job URLs (collapse=urlkey:38)."""
    slugs: set[str] = set()
    # careers: about 17,500 URLs in one answer (53 s on 2026-11-02); job links: a whole letter in one answer times
    # out, while 1,000 collapsed rows at a time cover most letters in one or two steps of under a minute
    queries = [('https://careers.smartrecruiters.com/', {'url': 'careers.smartrecruiters.com', 'collapse': 'urlkey'}, 100_000)]
    queries += [('https://jobs.smartrecruiters.com/', {'url': f'jobs.smartrecruiters.com/{c}', 'collapse': 'urlkey:38'}, 1000)
                for c in ALPHA]
    with httpx.Client(timeout=300, headers={'User-Agent': AGENT}) as http:
        for base, query, limit in queries:
            rows = cdx_rows(http, {**query, 'matchType': 'prefix', 'fl': 'urlkey'}, limit)
            if rows is None:
                print(f'wayback: no answer for {query["url"]}, left out', flush=True)
                continue
            for row in rows:                          # com,smartrecruiters,jobs)/boschgroup/744... -> boschgroup
                path = row.split(')', 1)[-1].lstrip('/')
                link = PLATFORM.read_link(base + path)
                if link and link[0]:
                    slugs.add(urllib.parse.unquote(link[0]))
            print(f'wayback: {query["url"]} {len(rows):,} rows, {len(slugs):,} slugs', flush=True)
    return slugs


def known() -> dict[str, str]:
    """The sites in the file now, read in runs or left out as dormant: identifier -> company name."""
    lines = OUT.read_text(encoding='utf-8').splitlines() if OUT.exists() else []
    rows = [line.removeprefix(DORMANT).split('\t') for line in lines
            if line and (not line.startswith('#') or line.startswith(DORMANT))]
    return {row[0]: (row[1] if len(row) > 1 else row[0]) for row in rows}


def given(args: list[str]) -> set[str]:
    """Slugs, links, and files of them (@path), from the command line."""
    items = []
    for arg in args:
        items += Path(arg[1:]).read_text(encoding='utf-8').splitlines() if arg.startswith('@') else [arg]
    slugs = set()
    for item in (i.strip() for i in items if i.strip()):
        link = PLATFORM.read_link(item)
        slugs.add(link[0] if link and link[0] else item)
    return slugs


async def check(http: httpx.AsyncClient, sem: asyncio.Semaphore, slug: str) -> dict | None:
    async with sem:
        try:
            data = await get_json(http, API.format(company=slug), {'limit': 1})
        except RuntimeError as exc:
            print(exc, flush=True)
            return {'error': str(exc)}
    newest = ((data or {}).get('content') or [None])[0]
    if not data or not data.get('totalFound') or not newest:
        return None
    company = newest.get('company') or {}
    ident = company.get('identifier') or slug
    return {'slug': ident, 'name': ' '.join((company.get('name') or ident).split()), 'jobs': data['totalFound'],
            'newest': str(newest.get('releasedDate') or '')}


async def main(args: list[str]) -> None:
    started = time.monotonic()
    limits = httpx.Limits(max_connections=CHECK_PARALLEL * 2, max_keepalive_connections=CHECK_PARALLEL)
    async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=15), limits=limits, headers={'User-Agent': AGENT}) as http:
        listed = known()
        sources = {'company box': set(await company_box(http)), 'already listed': set(listed),
                   'given': given([a for a in args if a != '--no-wayback'])}
        if '--no-wayback' not in args:
            sources['wayback'] = wayback()
        candidates = {}
        for slugs in sources.values():
            for slug in slugs:
                if COMPANY.match(slug):
                    candidates.setdefault(slug.lower(), slug)
        print(f'{len(candidates):,} candidates', flush=True)
        sem = asyncio.Semaphore(CHECK_PARALLEL)
        checked = await asyncio.gather(*[check(http, sem, slug) for slug in candidates.values()])
    failed = [slug for slug, site in zip(candidates.values(), checked) if site and 'error' in site]
    if len(failed) > max(10, len(candidates) // 100):
        sys.exit(f'{len(failed):,} of {len(candidates):,} sites did not answer, so {OUT.name} was left as it was '
                 f'(first: {", ".join(failed[:5])}). Try again later.')
    sites: dict[str, dict] = {}
    found_by = {}
    for slug, site in zip(candidates.values(), checked):
        if site and 'error' not in site:
            key = site['slug'].lower()
            if key not in sites or site['jobs'] > sites[key]['jobs']:
                sites[key] = site
            found_by[slug.lower()] = key
    folded = {slug.lower(): (slug, name) for slug, name in listed.items()}
    kept = [folded[s.lower()] for s in failed if s.lower() in folded and s.lower() not in sites]   # no answer: not "no jobs"
    for slug, name in kept:
        sites[slug.lower()] = {'slug': slug, 'name': name, 'jobs': 0}
    live = sorted(sites.values(), key=lambda s: (-s['jobs'], s['name'].lower(), s['slug']))
    since = (datetime.now(timezone.utc) - timedelta(days=ACTIVE_DAYS)).strftime('%Y-%m-%dT%H:%M:%S')
    active = [s for s in live if not s.get('newest') or s['newest'] >= since]      # no answer: stays where runs read it
    dormant = [s for s in live if s.get('newest') and s['newest'] < since]
    companies = {s['name'].lower() for s in active}
    jobs, old_jobs = sum(s['jobs'] for s in active), sum(s['jobs'] for s in dormant)
    lines = [f'# SmartRecruiters career sites, updated {date.today().isoformat()}: every career site found with a job posted '
             f'since {since[:10]}, biggest first.',
             '# Columns: board key (the company identifier, read in any case), company name. Rebuilt by '
             'scripts/refresh_smartrecruiters.py;',
             f'# {len(active):,} career sites of {len(companies):,} companies, {jobs:,} open jobs before duplicates.']
    lines += [f'{s["slug"]}\t{s["name"]}' for s in active]
    lines += [f'# Left out of runs, read when named: {len(dormant):,} career sites with nothing posted since {since[:10]} '
              f'and {old_jobs:,} postings SmartRecruiters still lists as open. #~ key, name, newest posting, postings:']
    # the list's date is read from a comment line holding "updated", so no other comment line may hold the word
    lines += [f'{DORMANT}{s["slug"]}\t{s["name"]}\t{s["newest"][:10]}\t{s["jobs"]}'.replace('updated', 'Updated')
              for s in dormant]
    OUT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    per_source = Counter()
    for name, slugs in sources.items():
        per_source[name] = len({found_by[s.lower()] for s in slugs if s.lower() in found_by})
    print(f'{len(active):,} career sites of {len(companies):,} companies, {jobs:,} jobs; left out with nothing since '
          f'{since[:10]}: {len(dormant):,} sites, {old_jobs:,} postings; {round(time.monotonic() - started)} s; '
          f'hiring sites by source: {dict(per_source)}; kept without an answer: {len(kept)}')


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1:]))
