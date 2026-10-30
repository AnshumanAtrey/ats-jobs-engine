"""
Rebuild ats_jobs/data/cornerstone.tsv: every Cornerstone career site we can find, each checked live.

The site:csod.com dork as an index query: the Wayback Machine lists every archived URL under *.csod.com, and each
/ux/ats/careersite/<site> in it is one candidate. The sites already in the file stay candidates, so a site stays
until Cornerstone stops serving it. A site is kept when its page gives a token and its job search answers. Names
already in the file are kept (the big employers are written by hand: Cornerstone publishes only a slug); a new
company gets its slug split into words where a dictionary allows it. Biggest first, so a run stopped by its time
limit has read the most jobs. On 2026-10-30: 1,754 candidates, 1,110 live sites of 605 companies, 61,090 open jobs,
about 4 minutes at 16 requests in parallel.

Run: python3 scripts/refresh_cornerstone.py   (needs httpx; writes the file, prints a summary)
"""
import asyncio
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
from ats_jobs.platforms.cornerstone import EN_US, HOME, SEARCH  # noqa: E402

OUT = ROOT / 'ats_jobs' / 'data' / 'cornerstone.tsv'
LINK = re.compile(r'^https?://([a-z0-9-]+)\.csod\.com/ux/ats/careersite/(\d+)', re.I)
CONTEXT = re.compile(r'csod\.context=(\{.*?\});</script>', re.S)
PARALLEL = 16
AGENT = 'Mozilla/5.0 (compatible; ats-jobs-engine/1.0; +https://github.com/AnshumanAtrey/ats-jobs-engine)'


def wayback() -> set[tuple[str, str]]:
    query = urllib.parse.urlencode({'url': 'csod.com', 'matchType': 'domain', 'fl': 'original', 'collapse': 'urlkey',
                                    'filter': 'urlkey:.*careersite.*', 'limit': 1_000_000})
    with httpx.Client(timeout=600, headers={'User-Agent': AGENT}) as http:
        lines = http.get(f'https://web.archive.org/cdx/search/cdx?{query}').text.splitlines()
    return {(m.group(1).lower(), m.group(2)) for m in map(LINK.match, lines) if m}


def known() -> dict[str, str]:
    """corp -> company name, from the file as it stands."""
    names = {}
    for line in OUT.read_text(encoding='utf-8').splitlines() if OUT.exists() else []:
        if line and not line.startswith('#'):
            key, _, name = line.partition('\t')
            names.setdefault(key.split('/')[0], name)
    return names


def dictionary() -> set[str]:
    path = Path('/usr/share/dict/words')
    return {w.strip().lower() for w in path.read_text().splitlines() if 4 <= len(w.strip()) <= 14} if path.exists() else set()


def pretty(corp: str, words: set[str]) -> str:
    """A slug -> a readable name: acme-group -> Acme Group; bourneleisure -> Bourne Leisure when both are words."""
    def split(word: str) -> list[str] | None:
        best = {0: []}
        for i in range(1, len(word) + 1):
            for j in range(max(0, i - 14), i):
                if j in best and i - j >= 5 and word[j:i] in words and (i not in best or len(best[j]) + 1 < len(best[i])):
                    best[i] = best[j] + [word[j:i]]
        return best.get(len(word))
    parts = []
    for part in re.split(r'[-_]+', corp):
        if len(part) <= 3:
            parts.append(part.upper())
        else:
            pieces = split(part) if len(part) >= 8 and part not in words else None
            parts += [p.capitalize() for p in (pieces if pieces and len(pieces) <= 3 else [part])]
    return ' '.join(parts)


async def check(http: httpx.AsyncClient, sem: asyncio.Semaphore, corp: str, site: str) -> dict | None:
    async with sem:
        try:
            page = await http.get(HOME.format(corp=corp, site=site))
            found = CONTEXT.search(page.text) if page.status_code == 200 else None
            if not found:
                return None
            context = json.loads(found.group(1))
            body = {'careerSiteId': int(site), 'careerSitePageId': int(site), 'pageNumber': 1, 'pageSize': 1, 'searchText': '',
                    'postingsWithinDays': None, 'states': [], 'countryCodes': [], 'cities': [], 'placeID': '', 'radius': None,
                    'customFieldCheckboxKeys': [], 'customFieldDropdowns': [], 'customFieldRadios': [], **EN_US}
            answer = await http.post(context['endpoints']['cloud'] + SEARCH, json=body,
                                     headers={'Authorization': f'Bearer {context["token"]}'})
            if answer.status_code != 200:
                return None
            return {'corp': corp, 'site': site, 'jobs': (answer.json().get('data') or {}).get('totalCount') or 0}
        except (httpx.HTTPError, ValueError, KeyError):
            return None


async def main() -> None:
    started = time.monotonic()
    names, words = known(), dictionary()
    candidates = wayback() | {tuple(line.split('\t')[0].split('/')) for line in
                              (OUT.read_text(encoding='utf-8').splitlines() if OUT.exists() else [])
                              if line and not line.startswith('#')}
    print(f'{len(candidates):,} candidates')
    sem = asyncio.Semaphore(PARALLEL)
    limits = httpx.Limits(max_connections=PARALLEL * 2, max_keepalive_connections=PARALLEL)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=15), limits=limits, follow_redirects=True,
                                 headers={'User-Agent': AGENT}) as http:
        found = await asyncio.gather(*[check(http, sem, corp, site) for corp, site in candidates])
    live = sorted((f for f in found if f), key=lambda f: (-f['jobs'], f['corp'], int(f['site'])))
    corps = {f['corp'] for f in live}
    lines = [f'# Cornerstone career sites, updated {date.today().isoformat()}: every site of *.csod.com/ux/ats/careersite in the '
             'Wayback Machine index that answers today, biggest first.',
             '# Columns: board key (corp/site), company name. Rebuilt by scripts/refresh_cornerstone.py;',
             f'# {len(live):,} sites of {len(corps):,} companies, {sum(f["jobs"] for f in live):,} open jobs before duplicates.']
    lines += [f'{f["corp"]}/{f["site"]}\t{names.get(f["corp"]) or pretty(f["corp"], words)}' for f in live]
    OUT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'{len(live):,} live sites of {len(corps):,} companies, {sum(f["jobs"] for f in live):,} jobs, '
          f'{round(time.monotonic() - started)} s')


if __name__ == '__main__':
    asyncio.run(main())
