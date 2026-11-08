"""
Rebuild ats_jobs/data/instahyre.tsv (the companies with open jobs on Instahyre) and ats_jobs/data/instahyre_places.tsv
(every place Instahyre knows, with its state and other names).

Places: GET /api/v1/candidate_misc/profile/candidate_locations/location_data/, the list Instahyre's own forms use. Each
place sits in a group named after its state (a place in "top metropolitan cities" also sits in its state's group), and
"alter" holds the other names people type (Bengaluru for Bangalore, Gurugram for Gurgaon). International places get
their country when the name is one. On 2026-11-08: 307 places in 31 states and union territories, 50 abroad.

Companies: Instahyre's search, read whole, split by company size so each part stays under its 10,000-job window. Each
job names its employer: id, name, headcount band (1, 10, 50, 200, 500 or 1000, the low end of the band), founding year,
tagline. A walk (platforms/instahyre.py) reads jobs one by one and those answers name only the company, so this list
gives the headcount and founding year to the companies on it; a company new to Instahyre gets them from its company page
at run time. The search allows about 250 calls an hour per address, so a full read (about 400 calls) takes two hours at
its pace; --from <jsonl> builds the list from records already read (one search record per line).

Run: python3 scripts/refresh_instahyre.py [--from records.jsonl]   (needs httpx; writes both files, prints a summary)
"""
import json
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ats_jobs.platforms.instahyre import HOST, PAGE_SIZE, SEARCH, SEARCH_GAP, WINDOW  # noqa: E402
from ats_jobs.rows import country_code  # noqa: E402

COMPANIES = ROOT / 'ats_jobs' / 'data' / 'instahyre.tsv'
PLACES = ROOT / 'ats_jobs' / 'data' / 'instahyre_places.tsv'
LOCATIONS = HOST + '/api/v1/candidate_misc/profile/candidate_locations/location_data/'
AGENT = 'Mozilla/5.0 (compatible; ats-jobs-engine/1.0; +https://github.com/AnshumanAtrey/ats-jobs-engine)'
ABROAD = 'international locations'
NOT_STATES = ('regions', 'top metropolitan cities', 'remote', ABROAD)


def clean(text) -> str:
    return ' '.join(str(text or '').replace('\t', ' ').split())


def places(http: httpx.Client) -> list[tuple]:
    data = http.get(LOCATIONS).json()['data']
    rows: dict[str, list] = {}
    for item in data.get('cities_preferred', []) + data.get('cities_current', []):
        name, group = clean(item.get('name')), (item.get('type') or '').strip().lower()
        if not name or group == 'regions':
            continue
        other = item.get('alter') or []
        other = other if isinstance(other, list) else str(other).split(',')
        row = rows.setdefault(name, [name, '', '', set()])
        if group not in NOT_STATES:
            row[1] = row[1] or group.title().replace(' & ', ' and ').replace('Union Territories', 'Union Territory')
            row[2] = 'India'
        elif group == 'top metropolitan cities':
            row[2] = 'India'
        elif group == ABROAD and not row[2]:
            row[2] = country_name(item.get('iso_alpha_code')) or (name if country_code(name, allow_codes=False) else '')
        row[3].update(clean(o) for o in other if clean(o) and clean(o).lower() != name.lower())
    return [(name, state, country, ', '.join(sorted(other)))
            for name, state, country, other in sorted(rows.values(), key=lambda r: r[0].lower())]


def country_name(code: str | None) -> str:
    """'AE' -> 'United Arab Emirates': Instahyre gives each place abroad its ISO country code."""
    if not code:
        return ''
    try:
        import pycountry
        found = pycountry.countries.get(alpha_2=code.strip().upper())
    except (ImportError, LookupError, KeyError):
        return ''
    return getattr(found, 'common_name', None) or (found.name if found else '')


def read_all(http: httpx.Client) -> list[dict]:
    """Every search record, by company size (1 small, 3 medium, 2 large), one call every SEARCH_GAP seconds; a 429
    waits its Retry-After, the hourly one included."""
    records, last = [], 0.0
    for size in (1, 3, 2):
        offset, total = 0, None
        while total is None or (offset < total and offset + PAGE_SIZE <= WINDOW):
            time.sleep(max(0.0, SEARCH_GAP - (time.monotonic() - last)))
            last = time.monotonic()
            answer = http.get(SEARCH, params={'company_size': size, 'job_type': 0, 'offset': offset})
            if answer.status_code == 429:
                wait = float(answer.headers.get('retry-after') or 60)
                print(f'429: waiting {wait:.0f} s', flush=True)
                time.sleep(wait + 1)
                continue
            answer.raise_for_status()
            page = answer.json()
            total = page['meta']['total_count']
            got = page.get('objects') or []
            records += [dict(r, _size=size) for r in got]
            print(f'size {size}: {offset + len(got):,} of {total:,}', flush=True)
            if not got:
                break
            offset += PAGE_SIZE
    return records


def companies(records: list[dict]) -> tuple[list[tuple], int]:
    jobs: Counter = Counter()
    found: dict[str, tuple] = {}
    ids = set()
    for record in records:
        employer = record.get('employer') or {}
        key = str(employer.get('id') or '')
        if not key or record.get('id') in ids:
            continue
        ids.add(record.get('id'))
        jobs[key] += 1
        found[key] = (key, clean(employer.get('company_name')), '', str(employer.get('employee_count') or ''),
                      str(employer.get('company_founded') or ''), clean(employer.get('company_tagline')))
    return sorted(found.values(), key=lambda row: (-jobs[row[0]], row[1].lower())), len(ids)


def main() -> None:
    with httpx.Client(timeout=60, headers={'User-Agent': AGENT}) as http:
        place_rows = places(http)
        if '--from' in sys.argv:
            source = Path(sys.argv[sys.argv.index('--from') + 1])
            records = [json.loads(line) for line in source.read_text(encoding='utf-8').splitlines() if line.strip()]
        else:
            records = read_all(http)
    today = date.today().isoformat()
    states = len({r[1] for r in place_rows if r[1]})
    PLACES.write_text(
        f'# Instahyre places, updated {today}: {len(place_rows)} places in {states} states and union territories, '
        f'from {LOCATIONS}\n# name\tstate\tcountry\tother names\n'
        + ''.join('\t'.join(row) + '\n' for row in place_rows), encoding='utf-8')
    rows, jobs = companies(records)
    COMPANIES.write_text(
        f'# Instahyre companies with open jobs, updated {today}: {len(rows):,} companies, {jobs:,} open jobs, from '
        f'Instahyre\'s search read whole\n# employer id\tname\twebsite\theadcount band\tfounded\ttagline\n'
        + ''.join('\t'.join(row) + '\n' for row in rows), encoding='utf-8')
    print(f'{len(place_rows)} places, {len(rows):,} companies, {jobs:,} open jobs')


if __name__ == '__main__':
    main()
