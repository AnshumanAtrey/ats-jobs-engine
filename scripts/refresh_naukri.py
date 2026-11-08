"""
Rebuild ats_jobs/data/naukri-cities.tsv: Naukri's own list of Indian cities, each with its state.

Naukri's pages fill their location boxes from one service, GET https://apis.naukri.com/central_services/dropdowns/v0/
<CITY|STATE>?appid=1&languageId=1&prefix=a&formatType=nested&srcAppId=121 with the headers appid 1 and systemid Entity
(the search page's own call, its JS module 78450). CITY names each city's state by id, STATE names the states. A city
written two ways ("Bengaluru / Bangalore", "Vadodara / Baroda") gets one line per name. On 2026-11-08: 5,810 cities in
36 states and union territories; no city abroad (Dubai, London) is on it.

The adapter reads the file to give a job's city its state and India, so "Karnataka" finds jobs in Bengaluru and "India"
leaves out a job in Dubai.

Run: python3 scripts/refresh_naukri.py   (needs httpx; writes the file, prints a summary)
"""
import sys
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ats_jobs.platforms.naukri import AGENT  # noqa: E402

OUT = ROOT / 'ats_jobs' / 'data' / 'naukri-cities.tsv'
DROPDOWN = 'https://apis.naukri.com/central_services/dropdowns/v0/{name}'
QUERY = {'appid': 1, 'languageId': 1, 'prefix': 'a', 'formatType': 'nested', 'srcAppId': 121}
HEADERS = {'User-Agent': AGENT, 'appid': '1', 'systemid': 'Entity', 'Accept': 'application/json'}


def dropdown(http: httpx.Client, name: str) -> dict:
    response = http.get(DROPDOWN.format(name=name.lower()), params=QUERY, headers=HEADERS)
    response.raise_for_status()
    return response.json()[name]


def main() -> None:
    with httpx.Client(timeout=120) as http:
        states = {entry['id']: ' '.join(entry['label'].split()) for entry in dropdown(http, 'STATE').values()}
        cities = dropdown(http, 'CITY')
    lines, missing = set(), 0
    for entry in cities.values():
        state = states.get(entry.get('parentId'))
        if not state:
            missing += 1
            continue
        for name in entry['label'].split('/'):
            name = ' '.join(name.split())
            if name:
                lines.add((name, state))
    head = (f'# Naukri\'s Indian cities and their states, updated {date.today().isoformat()}: the CITY and STATE lists of '
            f'apis.naukri.com/central_services/dropdowns.\n# Columns: city, state. Rebuilt by scripts/refresh_naukri.py; '
            f'{len(cities):,} cities in {len(states)} states and union territories, a city written two ways once per '
            f'name.\n')
    OUT.write_text(head + ''.join(f'{name}\t{state}\n' for name, state in sorted(lines)), encoding='utf-8')
    print(f'{len(cities):,} cities, {len(states)} states, {len(lines):,} lines, {missing} cities with no state -> {OUT}')


if __name__ == '__main__':
    main()
