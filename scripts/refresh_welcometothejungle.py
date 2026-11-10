"""
Rebuild data/welcometothejungle_places.tsv and data/welcometothejungle_industries.tsv from Welcome to the Jungle's own
search index: every office place and every industry of the open jobs, with how many jobs name each. The adapter
(ats_jobs/platforms/welcometothejungle.py) sends a typed place or industry to Algolia's filters only when it is on these
lists, by the exact spelling the index stores (filters ignore case and accents, not punctuation: "saint denis" finds
nothing, "Saint-Denis" 270 jobs, 2026-11-10); anything else is checked job by job.

A city row also says the one region the index gives that city in that country, and how many of its offices state no
region: most offices of the UK and US jobs that came from Otta name only the city (2,758 of 2,795 in San Francisco, 3,170
of 3,878 in London), so the adapter gives them that region and a search for California or England finds them. A city
name given two regions in one country (a Portland in Oregon and in Maine) gets none. A city written with its state code
("Mountain View, CA": 591 jobs, "San Mateo, CA" 348) gets the state that code names in its country (pycountry).

One browse of the index (about 85 calls of 1,000 jobs, 40 s and 90 MB from a laptop). Run from the repo root:
    python -m scripts.refresh_welcometothejungle
"""
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import httpx

from ats_jobs.platforms.welcometothejungle import ENV, INDEX, REFERER, algolia_headers, read_env

DATA = Path(__file__).resolve().parent.parent / 'ats_jobs' / 'data'
STATE_CODE = re.compile(r'^.+,\s*([A-Z]{2,3})$')                   # "Mountain View, CA"
FIELDS = ('city', 'district', 'state', 'country')                 # facets of offices, in this order
LOCAL = {'city': 'local_city', 'district': 'local_district', 'state': 'local_state'}   # the place's name in its language


AGENT = 'Mozilla/5.0 (compatible; ats-jobs-engine refresh; +https://github.com/AnshumanAtrey/ats-jobs-engine)'


def credentials() -> tuple[str, str]:
    """The site's own Algolia application and search-only key, as /api/env hands them to every visitor."""
    response = httpx.get(ENV, headers={'User-Agent': AGENT, 'Referer': REFERER}, timeout=30)
    found = read_env(response.text) if response.status_code == 200 else None
    if not found:
        sys.exit(f'{ENV} answered HTTP {response.status_code} without the search key')
    return found


def browse():
    """Every job of the index, a page of 1,000 at a time (browse ignores the 1,000-hit window of a search)."""
    app, key = credentials()
    url = f'https://{app.lower()}-dsn.algolia.net/1/indexes/{INDEX}/browse'
    body = {'attributesToRetrieve': ['offices', 'sectors'], 'hitsPerPage': 1000, 'attributesToHighlight': []}
    with httpx.Client(timeout=60, headers={**algolia_headers(app, key), 'User-Agent': AGENT}) as http:
        cursor = None
        while True:
            response = http.post(url, json=body if cursor is None else {'cursor': cursor})
            response.raise_for_status()
            data = response.json()
            yield from data.get('hits') or []
            cursor = data.get('cursor')
            if not cursor:
                return


def coded_state(city: str, code: str) -> str:
    """'Mountain View, CA', 'US' -> 'California': the state a trailing code names in the city's country, else ''."""
    found = STATE_CODE.match(city)
    if not found or not code:
        return ''
    try:
        import pycountry
        state = pycountry.subdivisions.get(code=f'{code}-{found.group(1)}')
    except (ImportError, KeyError, LookupError):
        return ''
    return state.name if state else ''


def main() -> None:
    places: Counter = Counter()                  # (field, value, country code) -> jobs
    others: dict[tuple, set] = {}                # the same place's local names
    industries: Counter = Counter()              # (reference, name, parent reference, parent name) -> jobs
    regions_of: dict[tuple, set] = {}            # (city, country code) -> the regions its offices state
    stateless: Counter = Counter()               # (city, country code) -> its offices that state no region
    jobs = 0
    for hit in browse():
        jobs += 1
        seen = set()
        for office in hit.get('offices') or []:
            code = (office.get('country_code') or '').upper()
            for field in FIELDS:
                value = (office.get(field) or '').strip()
                if not value or (field, value, code) in seen:
                    continue
                seen.add((field, value, code))
                places[(field, value, code)] += 1
                local = (office.get(LOCAL.get(field, '')) or '').strip()
                if local and local != value:
                    others.setdefault((field, value, code), set()).add(local)
            city, state = (office.get('city') or '').strip(), (office.get('state') or '').strip()
            if city and state:
                regions_of.setdefault((city, code), set()).add(state)
            elif city:
                stateless[(city, code)] += 1
        for sector in {(s.get('reference'), s.get('name'), s.get('parent_reference'), s.get('parent_name'))
                       for s in hit.get('sectors') or [] if s.get('reference')}:
            industries[sector] += 1
    if jobs < 10_000:
        sys.exit(f'only {jobs} jobs read: the index did not answer in full, nothing written')
    today = date.today().isoformat()
    lines = [f'# Welcome to the Jungle office places, updated {today}: {jobs:,} open jobs read by '
             f'scripts/refresh_welcometothejungle.py',
             '# value\tfield (city, district, state, country)\tcountry code\tjobs\tother names\t'
             'region of the city (when one)\toffices of the city with no region']
    for (field, value, code), count in sorted(places.items(), key=lambda kv: (FIELDS.index(kv[0][0]), -kv[1], kv[0][1])):
        stated = regions_of.get((value, code), set()) if field == 'city' else set()
        region = next(iter(stated)) if len(stated) == 1 else ''
        region = region or (coded_state(value, code) if field == 'city' else '')
        bare = str(stateless[(value, code)]) if field == 'city' and stateless[(value, code)] else ''
        lines.append('\t'.join((value, field, code, str(count), ','.join(sorted(others.get((field, value, code), ()))),
                                region, bare)).rstrip('\t'))
    (DATA / 'welcometothejungle_places.tsv').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    lines = [f'# Welcome to the Jungle industries, updated {today}: {jobs:,} open jobs read',
             '# reference\tname\tparent reference\tparent name\tjobs']
    for (ref, name, parent_ref, parent), count in sorted(industries.items(), key=lambda kv: (kv[0][3] or '', -kv[1])):
        lines.append('\t'.join((ref, name or '', parent_ref or '', parent or '', str(count))))
    (DATA / 'welcometothejungle_industries.tsv').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'jobs': jobs, 'places': len(places), 'industries': len(industries)}))


if __name__ == '__main__':
    main()
