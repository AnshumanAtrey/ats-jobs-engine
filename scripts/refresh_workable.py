"""
Rebuild ats_jobs/data/workable.tsv: every Workable account we can find, each checked live.

Workable lists no accounts. Candidates come from four places. The accounts already in the file, so an account found
once stays until Workable stops answering for it. The site:apply.workable.com dork as an index query: Common Crawl's raw
index of every workable.com host (apply.workable.com/<account> and <account>.workable.com) in CRAWLS. Accounts given on
the command line or in a file of one account a line (--accounts). And Workable's own public feed of its job board
(www.workable.com/boards/workable.xml), which names each listed job's company and links the job as
apply.workable.com/j/<shortcode>, a link that redirects to /<account>/j/<shortcode>. Each candidate account is checked
with its career page's JSON (the widget): its name and its open jobs (one per shortcode); 404 drops it. A feed company
whose name is the name of no live account is then resolved with one redirect and checked the same way, so the feed costs
calls only for what the archives missed. The website is the one the feed gives the company. Workable allows about 3
calls a second to apply.workable.com from one address and a burst can ban an address for a day, so calls start PACE
apart (8 waiting for answers at once), and a run that loses its answers stops without writing. Biggest first, so a run
for the newest jobs reaches the jobs posted today soonest.

On 2026-11-02: FILLED_BY_THE_RUN

Run: python3 scripts/refresh_workable.py [account ...] [--accounts <file>] [--feed <saved copy of the feed: .xml,
.xml.gz or .xml.br>]   (needs httpx and brotli; writes the file, prints a summary; about 1.5 hours). From a slow
line the feed download is cut at about 60 s and the part that arrived is read; Workable refuses an address for an hour
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
CRAWLS = ('CC-MAIN-2026-43', 'CC-MAIN-2026-39', 'CC-MAIN-2026-30', 'CC-MAIN-2026-21', 'CC-MAIN-2026-17',
          'CC-MAIN-2026-12', 'CC-MAIN-2026-08', 'CC-MAIN-2025-51', 'CC-MAIN-2025-47')
PREFIX = 'com,workable,'        # every workable.com host: apply.workable.com and <account>.workable.com
CC = 'https://data.commoncrawl.org/cc-index/collections/{crawl}/indexes/'
CODES_PER_COMPANY = 3           # a company's first job can close between the feed and the redirect
IN_FLIGHT = 8                   # calls waiting for their answer at once; their starts stay PACE apart
MOST_UNANSWERED = 0.02          # more calls than this share without an answer: Workable refused us, nothing is written


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
        block = get_range(CC.format(crawl=crawl) + name, offset, offset + length - 1)
        text = gzip.decompress(block).decode('utf-8', 'ignore')
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
        try:
            urls = common_crawl(crawl, PREFIX)
        except (OSError, RuntimeError, KeyError) as exc:
            print(f'{crawl} {PREFIX}: {exc}')
            continue
        for url in urls:
            link = platform.read_link(url)
            if link and link[0] and link[0] != '*':
                found.add(link[0])
        print(f'{crawl} {PREFIX}: {len(urls):,} urls, {len(found):,} accounts so far', flush=True)
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
                    entry = self.by_name.setdefault(name.casefold(), {
                        'name': name, 'website': clean(fields.get('website')) or None, 'jobs': set(), 'codes': []})
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
    """The account a feed company's jobs redirect to; {'error'} when Workable answered none of them."""
    unanswered = 0
    for code in codes:
        try:
            response = await turns.send(http, SHORT_LINK.format(code=code), follow_redirects=False)
        except BoardError:
            unanswered += 1
            continue
        found = re.match(r'^(?:https://apply\.workable\.com)?/([a-z0-9-]+)/j/', response.headers.get('location') or '')
        if found and found.group(1) not in NOT_ACCOUNTS:
            return found.group(1)
    return {'error': 'no answer'} if unanswered == len(codes) else None


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
    gate, done = asyncio.Semaphore(IN_FLIGHT), [0]

    async def one(item):
        async with gate:
            result = await work(item)
            done[0] += 1
            if done[0] % 1000 == 0:
                print(f'{done[0]:,} of {len(items):,} {label}', flush=True)
            return result
    return list(await asyncio.gather(*[one(item) for item in items]))


def unanswered(results: list) -> int:
    return sum(1 for r in results if isinstance(r, dict) and 'error' in r)


async def main(extra: list[str], feed_file: str | None) -> None:
    started = time.monotonic()
    platform = Workable()
    platform._known = {}
    file_rows = known()
    archive = archive_accounts(platform)
    given = {a.strip().lower() for a in extra if a.strip()}
    turns = Turns()
    limits = httpx.Limits(max_connections=IN_FLIGHT * 2, max_keepalive_connections=IN_FLIGHT * 2)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=15), limits=limits, follow_redirects=True,
                                 headers={'User-Agent': AGENT, 'Accept-Encoding': 'gzip'}) as http:
        companies = await read_feed(http, feed_file)
        print(f'feed: {companies.entries:,} entries of {len(companies.by_name):,} companies read', flush=True)
        first = sorted(a for a in archive | set(file_rows) | given if ACCOUNT.match(a))
        print(f'{len(first):,} accounts from the file, the archives and the command line to check', flush=True)
        results = await paced(first, lambda a: career_page(http, turns, a), 'accounts checked')
        pages = dict(zip(first, results))
        names = {p['name'].casefold() for p in results if p and 'error' not in p}
        ranked = sorted((c for c in companies.by_name.values() if c['name'].casefold() not in names),
                        key=lambda c: -len(c['jobs']))
        print(f'{len(companies.by_name) - len(ranked):,} feed companies are live accounts already; '
              f'{len(ranked):,} to resolve', flush=True)
        found = await paced(ranked, lambda c: account_of(http, turns, c['codes']), 'feed companies resolved')
        named = {}                                   # account -> the feed's company, for its website
        for company, account in zip(ranked, found):
            if isinstance(account, str):
                named.setdefault(account, company)
        new = sorted(a for a in named if a not in pages and ACCOUNT.match(a))
        results += (more := await paced(new, lambda a: career_page(http, turns, a), 'feed accounts checked'))
        pages.update(zip(new, more))
    calls = len(results) + len(found)
    lost = unanswered(results) + unanswered(found)
    if lost > MOST_UNANSWERED * calls:
        sys.exit(f'{lost:,} of {calls:,} calls got no answer: Workable refused this address. Nothing was written; '
                 'run again later.')
    websites = {c['name'].casefold(): c['website'] for c in companies.by_name.values()}
    accounts: dict[str, dict] = {}
    for account, page in pages.items():
        if page is not None and 'error' in page:
            if account in file_rows:          # Workable did not answer: keep what the file knew
                name, website = file_rows[account]
                accounts[account] = {'name': name, 'website': website, 'jobs': 0}
        elif page is not None:
            website = websites.get(page['name'].casefold()) or (named.get(account) or {}).get('website') \
                or file_rows.get(account, (None, None))[1]
            accounts[account] = {'name': page['name'], 'website': website, 'jobs': page['jobs']}
    live = sorted(accounts.items(), key=lambda kv: (-kv[1]['jobs'], kv[1]['name'].lower(), kv[0]))
    hiring = {account for account, a in live if a['jobs']}
    jobs = sum(a['jobs'] for _, a in live)
    feed_names = set(companies.by_name)
    seen = {'the feed': {a for a in hiring if a in named or accounts[a]['name'].casefold() in feed_names},
            'Common Crawl': hiring & archive, 'the list before': hiring & set(file_rows), 'given': hiring & given}
    by_source = ', '.join(f'{k} {len(v):,} ({len(v - set().union(*(w for j, w in seen.items() if j != k))):,} only '
                          'there)' for k, v in seen.items() if v)
    lines = [f'# Workable accounts, updated {date.today().isoformat()}: every account found through Workable\'s feed, '
             'Common Crawl or the list before that answers today, biggest first.',
             '# Columns: board key (the account, lower case), company name, website. Rebuilt by '
             'scripts/refresh_workable.py;',
             f'# {len(live):,} accounts, {len(hiring):,} hiring, {jobs:,} open jobs on their career pages before '
             'duplicates.',
             f'# Hiring accounts by source: {by_source}.']
    lines += [f'{account}\t{a["name"]}\t{a["website"] or ""}' for account, a in live]
    OUT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'{len(live):,} live accounts, {len(hiring):,} hiring, {jobs:,} jobs; hiring accounts by source: '
          f'{by_source}; {lost} of {calls:,} calls not answered; {round(time.monotonic() - started)} s')


if __name__ == '__main__':
    args = sys.argv[1:]
    options = {flag: args[args.index(flag) + 1] for flag in ('--feed', '--accounts') if flag in args}
    listed = Path(options['--accounts']).read_text(encoding='utf-8').split() if '--accounts' in options \
        else []
    given = [a for a in args if a not in options and a not in options.values()]
    asyncio.run(main(given + listed, options.get('--feed')))
