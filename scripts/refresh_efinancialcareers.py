"""
Rebuild ats_jobs/data/efinancialcareers.tsv: the values eFinancialCareers' own search filters take for sectors and
places, with their names on the site and the open jobs behind each.

The search page asks the same API for facets (facets=sectors|locationPath), and their values are what its filters send
back: filters.sectors=QUANTITATIVE_ANALYTICS (a sector) or QUANTITATIVE_ANALYTICS/EQUITY (one of its sub-sectors), and
filters.locationPath=Europe/United Kingdom (a country under eFinancialCareers' own region; its regions are Africa, Asia,
Australasia, Europe, Gulf, North America and South America). The adapter sends a typed sector or country as one of these
values, names a row's sectors by them, and gives each place its two-letter country code for the engine's place matching.
Cities come from the same API's locationCity facet, asked once per country (it lists 150 at most a call): the places
the site has jobs in, which a search box entry reads as a place ("risk manager in Zurich"). On 2026-11-10: 36 sectors
with 259 sub-sectors, 66 countries in 7 regions.

Run: python3 scripts/refresh_efinancialcareers.py   (needs httpx and pycountry; writes the file, prints a summary)
"""
import re
import sys
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ats_jobs.platforms.efinancialcareers import SEARCH, search_params  # noqa: E402
from ats_jobs.rows import country_code  # noqa: E402

OUT = ROOT / 'ats_jobs' / 'data' / 'efinancialcareers.tsv'
# names eFinancialCareers writes that pycountry reads otherwise
COUNTRY_NAMES = {'Türkiye': 'TR', 'Macao': 'MO', 'Réunion': 'RE', 'South Korea': 'KR', 'British Virgin Islands': 'VG',
                 'Czechia': 'CZ', 'Vietnam': 'VN', 'Taiwan': 'TW', 'Russia': 'RU', 'Laos': 'LA'}


def slug(name: str) -> str:
    """'Investment Banking/M&A' -> 'investment-banking-m-a': the sector's value in the actor's form."""
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


def city_facet(http: httpx.Client, path: str) -> list[tuple[str, int]]:
    """The cities the site has jobs in, in one country: ('Zurich, Switzerland', 755)."""
    response = http.get(SEARCH, params={**search_params(), 'pageSize': 1, 'facets': 'locationCity',
                                        'filters.locationPath': path})
    response.raise_for_status()
    found = {f['facetName']: f['facetResults'] for f in response.json()['meta']['facetQueryResults']}
    return [(r['value'], r['count']) for r in found.get('locationCity', []) if r.get('value') and r.get('count')]


def main() -> None:
    http = httpx.Client(timeout=60)
    response = http.get(SEARCH, params={**search_params(), 'pageSize': 1, 'facets': 'sectors|locationPath'})
    response.raise_for_status()
    facets = {f['facetName']: f['facetResults'] for f in response.json()['meta']['facetQueryResults']}
    lines, sectors, subsectors, places, missing, cities = [], 0, 0, 0, [], 0
    for sector in sorted(facets['sectors'], key=lambda s: -s['count']):
        lines.append(f'SECTOR\t{sector["value"]}\t{sector["displayValue"]}\t{slug(sector["displayValue"])}\t{sector["count"]}')
        sectors += 1
        for child in sorted(sector.get('children') or [], key=lambda c: -c['count']):
            lines.append(f'SUBSECTOR\t{child["value"]}\t{child["displayValue"]}\t\t{child["count"]}')
            subsectors += 1
    for region in sorted(facets['locationPath'], key=lambda r: -r['count']):
        for country in sorted(region.get('children') or [], key=lambda c: -c['count']):
            name = country['displayValue']
            code = COUNTRY_NAMES.get(name) or country_code(name, allow_codes=False)
            if not code:
                missing.append(name)
                continue
            lines.append(f'PLACE\t{country["value"]}\t{name}\t{code}\t{country["count"]}')
            places += 1
            found = city_facet(http, country['value'])
            for city, count in found:
                lines.append(f'CITY\t{city}\t{city.split(",")[0].strip()}\t{code}\t{count}')
                cities += 1
    header = [f'# eFinancialCareers sectors and places, updated {date.today().isoformat()}: the values its search filters take.',
              '# Columns: kind (SECTOR, SUBSECTOR or PLACE), the value eFinancialCareers takes (filters.sectors or '
              'filters.locationPath), its name on the site, the form value (a sector) or the country code (a place), open jobs.',
              f'# Rebuilt by scripts/refresh_efinancialcareers.py; {sectors} sectors, {subsectors} sub-sectors, {places} countries, '
              f'{cities} cities (CITY rows: the site\'s name, the city alone, its country code, open jobs).']
    OUT.write_text('\n'.join(header + lines) + '\n', encoding='utf-8')
    print(f'{sectors} sectors, {subsectors} sub-sectors, {places} countries, {cities} cities -> {OUT}')
    if missing:
        print('No country code for:', ', '.join(missing))


if __name__ == '__main__':
    main()
