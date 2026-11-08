"""
Rebuild ats_jobs/data/foundit_places.tsv: every place foundit's own location list knows, with its country.

foundit's search reads a place by this list: a name on it finds its jobs (Bangalore finds the jobs written
"Bengaluru, India", because the list holds "Bengaluru / Bangalore"), and a name not on it is answered with the site's
remote jobs. The adapter sends a place only to a site whose list knows it, adds a known name's other spellings to the
jobs it matches, and takes a city's country from here when a job's location names none.

The list is the one the site's search page asks for (raven/api/public/search/v1/master-data?type=location), per site
context, 500 at a time with start. On 2026-11-08: rexmonster (foundit.in) 4,078 places, monstergulf (founditgulf.com)
336, and one list of 1,731 shared by the seven Southeast and East Asian sites.

Run: python3 scripts/refresh_foundit.py   (needs httpx; writes the file, prints a summary)
"""
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ats_jobs.platforms.foundit import MASTER, SITES, headers  # noqa: E402

OUT = ROOT / 'ats_jobs' / 'data' / 'foundit_places.tsv'
PAGE = 500
PAUSE_S = 1.0           # foundit's CDN refuses an address that makes about 150 calls in a short window


def places_of(http: httpx.Client, site) -> list[tuple[str, str, str]]:
    found, start = [], 0
    while True:
        response = http.get(MASTER, params={'appName': 'srp-ui', 'type': 'location', 'limit': PAGE, 'start': start,
                                            'siteContext': site.context}, headers=headers(site))
        response.raise_for_status()
        data = response.json()
        records = data.get('data') or []
        for record in records:
            name, code = ' '.join((record.get('name') or '').split()), (record.get('countryIsoCode') or '').upper()
            if name and record.get('enabled', True) and len(code) == 2:
                found.append((name, code, (record.get('locationType') or 'CITY').upper()))
        total = ((data.get('meta') or {}).get('paging') or {}).get('total') or 0
        start += PAGE
        time.sleep(PAUSE_S)
        if not records or start >= total:
            return found


def main() -> None:
    listed: dict[tuple[str, str, str], list[str]] = {}     # (name, country, kind) -> the sites whose list holds it
    per_site = {}
    with httpx.Client(timeout=60) as http:
        for site in SITES:
            found = set(places_of(http, site))
            per_site[site.host] = len(found)
            for row in found:
                listed.setdefault(row, []).append(site.code)
    lines = sorted(listed, key=lambda row: (row[1], row[0].casefold()))
    kinds = Counter(kind for _, _, kind in lines)
    header = [
        f'# foundit places, updated {date.today().isoformat()}: every name on the location lists of foundit\'s nine sites.',
        '# Columns: name, country code, kind (CITY or COUNTRY), the sites whose list holds it (their X-Source-Country).',
        f'# Rebuilt by scripts/refresh_foundit.py; {len(lines):,} places '
        f'({", ".join(f"{n:,} {k.lower()}" for k, n in kinds.most_common())}).',
    ]
    body = ['\t'.join((*row, ','.join(listed[row]))) for row in lines]
    OUT.write_text('\n'.join(header + body) + '\n', encoding='utf-8')
    print({'places': len(lines), 'per site': per_site, 'countries': len({c for _, c, _ in lines})})


if __name__ == '__main__':
    main()
