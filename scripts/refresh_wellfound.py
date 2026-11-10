"""
Rebuild ats_jobs/data/wellfound_roles.tsv and ats_jobs/data/wellfound_places.tsv: the roles and places Wellfound has
search pages for (platforms/wellfound.py reads a typed title through its roles and a typed place through its places).

Roles: wellfound.com/browse/tech-jobs, the site's own list (SeoRoleKeyword in the page's __NEXT_DATA__), each with its
group. On 2026-11-10: 518 roles in 13 groups. Some roles have a page without being on the list (role/marketing,
role/sales, role/finance, role/recruiter); the adapter tries a typed title's own slug when no listed role fits.

Places: wellfound.com/browse/tech-job-locations (NewTag records: slug, name, US state code) in its three groups (US
cities, US states, other places), then every country pycountry knows whose slug has a page (location/united-states,
location/india...) and the continents that do (north-america, europe, asia...). A place's kind is country when
pycountry knows its name, region when it is a continent or a region of the engine's list, state for a US state, else
city. On 2026-11-10: 306 listed places.

Run: python3 scripts/refresh_wellfound.py   (needs httpx and pycountry; about 260 calls, under 2 minutes)
"""
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ats_jobs import places as regions  # noqa: E402
from ats_jobs.platforms.wellfound import CONTINENTS, ORIGIN, apollo, slugify  # noqa: E402
from ats_jobs.rows import country_code  # noqa: E402

ROLES = ROOT / 'ats_jobs' / 'data' / 'wellfound_roles.tsv'
PLACES = ROOT / 'ats_jobs' / 'data' / 'wellfound_places.tsv'
AGENT = 'Mozilla/5.0 (compatible; ats-jobs-engine/1.0; +https://github.com/AnshumanAtrey/ats-jobs-engine)'


def clean(text) -> str:
    return ' '.join(str(text or '').replace('\t', ' ').split())


def roles(http: httpx.Client) -> list[tuple[str, str, str]]:
    data = apollo(http.get(f'{ORIGIN}/browse/tech-jobs').text)
    out = []
    for ref in data['ROOT_QUERY']['talent']['seoBrowseJobs']['categories']:
        group = data[ref['__ref']]
        for keyword in group.get('roleKeywords') or []:
            role = data[keyword['__ref']]
            out.append((role['slug'], clean(role['displayName']), clean(group.get('displayName'))))
    return sorted(dict.fromkeys(out), key=lambda r: r[0])


def kind_of(name: str, state: str, group: str) -> str:
    """The list's own group first: Georgia among the US states is the state, not the country."""
    if 'States' in group:
        return 'state'
    if 'Cities' in group or state:
        return 'city'
    if country_code(name, allow_codes=False):
        return 'country'
    return 'region' if regions.region_query(regions.tokens(name)) is not None else 'city'


def page_exists(http: httpx.Client, path: str) -> str | None:
    """The name Wellfound gives a place's page, or None when it has none."""
    response = http.get(f'{ORIGIN}/{path}')
    time.sleep(0.3)
    if response.status_code != 200:
        return None
    data = apollo(response.text)
    return next((v.get('displayName') for k, v in data.items() if k.startswith('NewTag:')), None)


def places(http: httpx.Client) -> list[tuple[str, str, str, str]]:
    data = apollo(http.get(f'{ORIGIN}/browse/tech-job-locations').text)
    out = {}
    for group in data['ROOT_QUERY']['talent']['seoBrowseLocations']['categories']:
        for ref in group.get('locations') or []:
            tag = data[ref['__ref']]
            name, state = clean(tag['displayName']), clean(tag.get('stateShortName'))
            out[tag['slug']] = (tag['slug'], name, state, kind_of(name, state, group.get('displayName') or ''))
    for slug, name in CONTINENTS.items():
        if slug not in out and page_exists(http, f'location/{slug}'):
            out[slug] = (slug, name, '', 'region')
    import pycountry
    listed = {row[1].lower() for row in out.values()}
    for country in pycountry.countries:
        name = getattr(country, 'common_name', None) or country.name
        if name.lower() in listed:
            continue
        for slug in dict.fromkeys((slugify(name), slugify(country.name))):
            if slug in out:
                break
            shown = page_exists(http, f'location/{slug}')
            if shown:
                out[slug] = (slug, clean(shown), '', 'country')
                break
    return sorted(out.values(), key=lambda r: r[0])


def main() -> None:
    today = date.today().isoformat()
    with httpx.Client(timeout=30, headers={'User-Agent': AGENT, 'Accept-Encoding': 'gzip'}) as http:
        found_roles = roles(http)
        found_places = places(http)
    groups = sorted({r[2] for r in found_roles})
    ROLES.write_text(
        f'# Wellfound roles, updated {today}: {len(found_roles)} roles in {len(groups)} groups, from {ORIGIN}/browse/tech-jobs.\n'
        f'# Columns: slug (wellfound.com/role/<slug>), name, group. Rebuilt by scripts/refresh_wellfound.py.\n'
        + ''.join('\t'.join(r) + '\n' for r in found_roles), encoding='utf-8')
    kinds = {k: sum(1 for p in found_places if p[3] == k) for k in ('city', 'state', 'region', 'country')}
    PLACES.write_text(
        f'# Wellfound places, updated {today}: {len(found_places)} places with a page ({", ".join(f"{n} {k}" for k, n in kinds.items())}),'
        f' from {ORIGIN}/browse/tech-job-locations, the continents and the countries whose slug has a page.\n'
        f'# Columns: slug (wellfound.com/location/<slug>), name, US state code, kind. Rebuilt by scripts/refresh_wellfound.py.\n'
        + ''.join('\t'.join(p) + '\n' for p in found_places), encoding='utf-8')
    print(json.dumps({'roles': len(found_roles), 'groups': len(groups), 'places': len(found_places), **kinds}))


if __name__ == '__main__':
    main()
