"""
Rebuild ats_jobs/data/workable.tsv: every Workable account we can find, each checked live.

Workable lists no accounts. Candidates come from three places. Workable's own public feed of its job board
(www.workable.com/boards/workable.xml) names each listed job's company and links the job as
apply.workable.com/j/<shortcode>, which redirects to /<account>/j/<shortcode>: one redirect per company gives its
account. The feed is 861 MB with the oldest listings first, and from a slow line the download is cut at about 60 s, so
the part that arrives is read. The site:apply.workable.com dork as an index query: Common Crawl's raw index of
apply.workable.com and <account>.workable.com in the latest crawls. And the accounts already in the file, so an account
found once stays until Workable stops answering for it; accounts given on the command line are added. Each account is
checked with its career page's JSON (the widget): its name and its open jobs; 404 drops it. An account the feed names
takes the feed's company name, website and open jobs. Workable allows about 3 calls a second to apply.workable.com
from one address, so calls start PACE apart (8 waiting for answers at once). Biggest first, so a run for the newest
jobs reaches the jobs posted today soonest.

On 2026-11-02: FILLED_BY_THE_RUN

Run: python3 scripts/refresh_workable.py [account ...] [--feed <saved copy of the feed, .xml, .xml.gz or .xml.br>]
(needs httpx and brotli; writes the file, prints a summary; one to two hours). Workable refuses an address for an hour
after a few downloads of its feed, so --feed reads a copy saved earlier.
"""
import asyncio
import gzip
import json
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from collections import Counter
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ats_jobs.http import BoardError, send  # noqa: E402
from ats_jobs.platforms.workable import ACCOUNT, NOT_ACCOUNTS, SHORT_LINK, WIDGET, Workable, clean  # noqa: E402

OUT = ROOT / 'ats_jobs' / 'data' / 'workable.tsv'
FEED_URL = 'https://www.workable.com/boards/workable.xml'
AGENT = 'Mozilla/5.0 (compatible; ats-jobs-engine/1.0; +https://github.com/AnshumanAtrey/ats-jobs-engine)'
PACE = 0.45                     # seconds between calls to apply.workable.com
CRAWLS = ('CC-MAIN-2026-43', 'CC-MAIN-2026-39')
PREFIXES = ('com,workable,apply)/', 'com,workable,')
CC = 'https://data.commoncrawl.org/cc-index/collections/{crawl}/indexes/'
CODES_PER_COMPANY = 3           # a company's first job can close between the feed and the redirect
IN_FLIGHT = 8                   # calls waiting for their answer at once; their starts stay PACE apart


def known() -> dict[str, tuple[str, str | None]]:
    """account -> (name, website), from the file as it stands."""
    rows = {}
    for line in OUT.read_text(encoding='utf-8').splitlines() if OUT.exists() else []:
        if line and not line.startswith('#'):
            parts = line.split('\t')
            rows[parts[0]] = (parts[1] if len(parts) > 1 else parts[0], (parts[2] or None) if len(parts) > 2 else None)
    return rows


# ---------- Common Crawl ----------

def get_range(url: str, start: int, end: int) -> bytes:
    for attempt in range(6):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': AGENT, 'Range': f'bytes={start}-{end}'})
            return urllib.request.urlopen(request, timeout=90).read()
        except OSError:
            time.sleep(2 + 4 * attempt)
    raise RuntimeError(f'range request failed: {url}')


def common_crawl(crawl: str, prefix: str) -> set[str]:
    """The URLs Common Crawl holds under one SURT prefix: a binary search of cluster.idx, then the blocks."""
    index = CC.format(crawl=crawl) + 'cluster.idx'
    size = int(urllib.request.urlopen(urllib.request.Request(index, headers={'User-Agent': AGENT}, method='HEAD'),
                                      timeout=60).headers['Content-Length'])
    end_key, low, high = prefix + '~', 0, size
    while high - low > 300_000:
        middle = (low + high) // 2
        line = get_range(index, middle, middle + 8000).decode('utf-8', 'ignore').split('\n')[1]
        low, high = (middle, high) if line.split(' ')[0] < prefix else (low, middle)
    blocks, position = [], low
    while True:
        lines = get_range(index, position, position + 600_000).decode('utf-8', 'ignore').split('\n')
        body = lines[1:-1] if position else lines[:-1]
        for i, line in enumerate(body):
            following = body[i + 1].split(' ')[0] if i + 1 < len(body) else '~'
            if following >= prefix and line.split(' ')[0] <= end_key:
                fields = line.split('\t')
                blocks.append((fields[1], int(fields[2]), int(fields[3])))
        if not body or body[-1].split(' ')[0] > end_key:
            break
        position += 600_000 - 2000
    urls = set()
    for name, offset, length in blocks:
        text = gzip.decompress(get_range(CC.format(crawl=crawl) + name, offset, offset + length - 1)).decode('utf-8', 'ignore')
        for line in text.split('\n'):
            if line.startswith(prefix):
                try:
                    urls.add(json.loads(line.split(' ', 2)[2])['url'])
                except (ValueError, KeyError, IndexError):
                    pass
    return urls


def archive_accounts(platform: Workable) -> set[str]:
    found = set()
    for crawl in CRAWLS:
        for prefix in PREFIXES:
            try:
                urls = common_crawl(crawl, prefix)
            except (OSError, RuntimeError, KeyError) as exc:
                print(f'{crawl} {prefix}: {exc}')
                continue
            for url in urls:
                link = platform.read_link(url)
                if link and link[0] and link[0] != '*':
                    found.add(link[0])
            print(f'{crawl} {prefix}: {len(urls):,} urls, {len(found):,} accounts so far', flush=True)
    return found


# ---------- the feed ----------

def decoder(encoding: str):
    if encoding == 'br':
        import brotli
        return brotli.Decompressor().process
    if encoding == 'gzip':
        return zlib.decompressobj(16 + zlib.MAX_WBITS).decompress
    return lambda chunk: chunk


class Companies:
    """Feed entries as they arrive -> company name -> {name, website, jobs, codes}."""
    def __init__(self):
        self.parser = ET.XMLPullParser(events=('start', 'end'))
        self.root, self.by_name, self.entries = None, {}, 0

    def feed(self, chunk: bytes) -> None:
        try:
            self.parser.feed(chunk)
            events = list(self.parser.read_events())
        except ET.ParseError:
            return                         # a cut download: what arrived before it counts
        for event, elem in events:
            if event == 'start':
                self.root = self.root if self.root is not None else elem
            elif elem.tag == 'job':
                fields = {child.tag: (child.text or '').strip() for child in elem}
                code, name = fields.get('referencenumber', '').upper(), clean(fields.get('company'))
                if re.match(r'^[0-9A-F]{10}$', code) and name:
                    self.entries += 1
                    entry = self.by_name.setdefault(name.casefold(), {'name': name, 'website': clean(fields.get('website')) or None,
                                                                      'jobs': set(), 'codes': []})
                    entry['jobs'].add(code)
                    if code not in entry['codes'] and len(entry['codes']) < CODES_PER_COMPANY:
                        entry['codes'].append(code)
        if self.root is not None:
            self.root.clear()


async def read_feed(http, path: str | None) -> Companies:
    companies = Companies()
    if path:
        opener = gzip.open if path.endswith('.gz') else open
        decode = decoder('br' if path.endswith('.br') else 'identity')
        with opener(path, 'rb') as f:
            while chunk := f.read(1 << 20):
                try:
                    companies.feed(decode(chunk))
                except Exception as exc:        # noqa: BLE001  a copy that was cut mid-stream
                    print(f'saved feed ends early: {type(exc).__name__}')
                    break
        return companies
    try:
        async with http.stream('GET', FEED_URL, timeout=httpx.Timeout(120, connect=15),
                               headers={'Accept-Encoding': 'br'}) as response:
            print(f'feed: HTTP {response.status_code}', flush=True)
            if response.status_code == 200:
                decode = decoder(response.headers.get('content-encoding', 'identity'))
                async for chunk in response.aiter_raw(1 << 18):
                    companies.feed(decode(chunk))
    except (httpx.HTTPError, zlib.error) as exc:
        print(f'feed download ended early: {type(exc).__name__}: {exc}'[:200])
    return companies


# ---------- apply.workable.com ----------

class Turns:
    """One call to apply.workable.com at a time, PACE apart."""
    def __init__(self):
        self.lock, self.last = asyncio.Lock(), 0.0

    async def send(self, http, url: str, **kwargs):
        async with self.lock:
            wait = self.last + PACE - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self.last = time.monotonic()
        return await send(http, 'GET', url, **kwargs)


async def account_of(http, turns: Turns, codes: list[str]) -> str | None:
    for code in codes:
        try:
            response = await turns.send(http, SHORT_LINK.format(code=code), follow_redirects=False)
        except BoardError:
            continue
        found = re.match(r'^(?:https://apply\.workable\.com)?/([a-z0-9-]+)/j/', response.headers.get('location') or '')
        if found and found.group(1) not in NOT_ACCOUNTS:
            return found.group(1)
    return None


async def career_page(http, turns: Turns, account: str) -> dict | None:
    """{name, jobs} of a live account, None for 404, {'error'} when Workable did not answer."""
    try:
        response = await turns.send(http, WIDGET.format(account=account))
    except BoardError as exc:
        return {'error': str(exc)}
    if response.status_code == 404:
        return None
    try:
        data = response.json()
        return {'name': clean(data.get('name')) or account,
                'jobs': len({j.get('shortcode') for j in data.get('jobs') or [] if j.get('shortcode')})}
    except (ValueError, AttributeError):
        return {'error': f'HTTP {response.status_code}'}


async def paced(items, work, label: str) -> list:
    """work(item) for every item, IN_FLIGHT at a time; Turns spaces their starts, so slow answers overlap."""
    gate, done, results = asyncio.Semaphore(IN_FLIGHT), [0], []

    async def one(item):
        async with gate:
            result = await work(item)
            done[0] += 1
            if done[0] % 1000 == 0:
                print(f'{done[0]:,} of {len(items):,} {label}', flush=True)
            return result
    results = await asyncio.gather(*[one(item) for item in items])
    return list(results)


async def main(extra: list[str], feed_file: str | None) -> None:
    started = time.monotonic()
    platform = Workable()
    platform._known = {}
    file_rows = known()
    archive = archive_accounts(platform)
    turns = Turns()
    limits = httpx.Limits(max_connections=IN_FLIGHT * 2, max_keepalive_connections=IN_FLIGHT * 2)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=15), limits=limits, follow_redirects=True,
                                 headers={'User-Agent': AGENT, 'Accept-Encoding': 'gzip'}) as http:
        companies = await read_feed(http, feed_file)
        print(f'feed: {companies.entries:,} entries of {len(companies.by_name):,} companies read', flush=True)
        ranked = sorted(companies.by_name.values(), key=lambda c: -len(c['jobs']))
        found = await paced(ranked, lambda c: account_of(http, turns, c['codes']), 'feed companies resolved')
        accounts: dict[str, dict] = {}
        for company, account in zip(ranked, found):
            if account and account not in accounts:   # the feed's own name, open jobs and website for its accounts
                accounts[account] = {'name': company['name'], 'website': company['website'], 'jobs': len(company['jobs'])}
        from_feed = set(accounts)
        websites = {c['name'].casefold(): c['website'] for c in companies.by_name.values()}
        candidates = sorted(a for a in (archive | set(file_rows) | set(extra)) - from_feed if ACCOUNT.match(a))
        print(f'{len(from_feed):,} accounts from the feed; {len(candidates):,} more to check', flush=True)
        pages = await paced(candidates, lambda a: career_page(http, turns, a), 'accounts checked')
        errors = 0
        for account, page in zip(candidates, pages):
            if page is not None and 'error' in page:
                errors += 1
                if account in file_rows:          # Workable did not answer: keep what the file knew
                    name, website = file_rows[account]
                    accounts[account] = {'name': name, 'website': website, 'jobs': 0}
            elif page is not None:
                website = websites.get(page['name'].casefold()) or file_rows.get(account, (None, None))[1]
                accounts[account] = {'name': page['name'], 'website': website, 'jobs': page['jobs']}
    live = sorted(accounts.items(), key=lambda kv: (-kv[1]['jobs'], kv[1]['name'].lower(), kv[0]))
    hiring = sum(1 for _, a in live if a['jobs'])
    jobs = sum(a['jobs'] for _, a in live)
    sources = Counter()
    for account, a in live:
        if a['jobs']:
            sources['feed' if account in from_feed else 'archive' if account in archive else 'file or given'] += 1
    lines = [f'# Workable accounts, updated {date.today().isoformat()}: every account found through Workable\'s feed or '
             'the archives that answers today, biggest first.',
             '# Columns: board key (the account, lower case), company name, website. Rebuilt by scripts/refresh_workable.py;',
             f'# {len(live):,} accounts, {hiring:,} hiring, {jobs:,} open jobs before duplicates (counted in the part of the feed '
             f'read for the {len(from_feed):,} accounts it names, on the career page for the others).']
    lines += [f'{account}\t{a["name"]}\t{a["website"] or ""}' for account, a in live]
    OUT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'{len(live):,} live accounts, {hiring:,} hiring, {jobs:,} jobs; hiring accounts by source {dict(sources)}; '
          f'{errors} not answered; {round(time.monotonic() - started)} s')


if __name__ == '__main__':
    args = sys.argv[1:]
    saved = args[args.index('--feed') + 1] if '--feed' in args else None
    asyncio.run(main([a for a in args if a not in ('--feed', saved)], saved))
