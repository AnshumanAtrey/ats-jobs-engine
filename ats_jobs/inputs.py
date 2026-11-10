"""
What people type -> a clean run configuration (manager/rules/SEO.md section 11.5).

No field is required and no input stops a run (owner rule, 2026-10-01: type one keyword, press Start,
get the newest jobs). An empty form returns the newest jobs from every company. Accepted as typed:
several entries on one line split by commas, a pasted Google dork (site:<host> intext:frontend reads as
the keyword frontend), board links instead of company names, work types and job types in everyday words
("offline" is onsite, "internship" is intern), "remote" typed as a location, periods such as "24h",
"2 weeks" or a number of days, and a place, remote or fresher typed with the job title as a search box takes
them ("python developer in pune" is python developer in Pune). Anything that cannot be read falls back to the
field's default, and the OUTPUT record and the log say what was read and how.

Links belong to a platform: Platform.read_link says whether a text is one of its links and what it names.

Pure module: no Apify or network import, so the tests run offline.
"""
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from urllib.parse import parse_qs, urlparse

from . import places
from .rows import classify_place, country_code, plain

DEFAULT_MAX_JOBS = 100
MAX_JOBS_CAP = 100_000             # our guard: more than every open job on one platform we read
SPLIT = re.compile(r'[,;\n\r\t]+')
IS_URL = re.compile(r'^[a-z][a-z0-9+.-]*://', re.I)
DORK_OPERATOR = re.compile(r'\b(?:site|inurl|filetype|ext|before|after|cache|related):\S+', re.I)
DORK_TEXT = re.compile(r'\b(?:intext|intitle|allintext|allintitle):', re.I)

WORK_TYPES = ('remote', 'hybrid', 'onsite')
WORK_TYPE_ALIASES = {
    'remote': 'remote', 'fully remote': 'remote', 'remote only': 'remote', 'wfh': 'remote',
    'work from home': 'remote', 'anywhere': 'remote', 'distributed': 'remote',
    'hybrid': 'hybrid', 'flexible': 'hybrid',
    'onsite': 'onsite', 'on site': 'onsite', 'on-site': 'onsite', 'in office': 'onsite', 'in-office': 'onsite',
    'office': 'onsite', 'offline': 'onsite', 'in person': 'onsite', 'in-person': 'onsite',
}
JOB_TYPES = ('full-time', 'part-time', 'contract', 'intern', 'temporary')
JOB_TYPE_ALIASES = {
    'full time': 'full-time', 'fulltime': 'full-time', 'full-time': 'full-time', 'permanent': 'full-time',
    'part time': 'part-time', 'parttime': 'part-time', 'part-time': 'part-time', 'casual': 'part-time',
    'contract': 'contract', 'contractor': 'contract', 'consultant': 'contract', 'freelance': 'contract', 'freelancer': 'contract',
    'intern': 'intern', 'internship': 'intern', 'internships': 'intern', 'co-op': 'intern', 'coop': 'intern',
    'temporary': 'temporary', 'temp': 'temporary', 'seasonal': 'temporary',
}
PERIOD = re.compile(r'^(?:last|past)?\s*(\d+(?:\.\d+)?)?\s*(h|hr|hrs|hour|hours|d|day|days|w|wk|week|weeks|m|mo|month|months)?$')
PERIOD_DAYS = {'h': 1 / 24, 'hr': 1 / 24, 'hrs': 1 / 24, 'hour': 1 / 24, 'hours': 1 / 24, 'd': 1, 'day': 1,
               'days': 1, 'w': 7, 'wk': 7, 'week': 7, 'weeks': 7, 'm': 30, 'mo': 30, 'month': 30, 'months': 30}
ANY_TIME = {'', 'any', 'any time', 'anytime', 'all', 'all time', 'ever', 'none', '0'}
EVERY = {'any', 'all', 'every', 'both', 'either', 'any type', 'all types', 'no preference'}


@dataclass
class Config:
    search_terms: list[str] = field(default_factory=list)
    exclude_terms: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    remote_places: list[str] = field(default_factory=list)    # locations asked as remote jobs there ("Remote EMEA")
    work_types: list[str] = field(default_factory=list)       # empty: every work type
    job_types: list[str] = field(default_factory=list)        # empty: every job type
    posted_within_days: float | None = None                  # None: any time
    max_jobs: int | None = DEFAULT_MAX_JOBS                  # None: every match
    include_details: bool = False
    search_descriptions: bool = False
    companies: list[str] = field(default_factory=list)        # board keys; empty: every known company
    job_urls: list[tuple[str, str, str]] = field(default_factory=list)   # (board key, job id, as typed)
    experience: tuple[float, float] | None = None             # years asked for, (low, high); None: any (experienceYears)
    extra: dict = field(default_factory=dict)                 # a platform's own settings (Platform.read_input)
    match_skills: bool = False                                # words match a job's skill tags too (Instahyre)
    notes: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


def _items(value) -> list[str]:
    """A list, or one string, -> the separate entries (commas, semicolons and new lines split)."""
    raw = value if isinstance(value, list) else [value] if isinstance(value, (str, int, float)) else []
    return [part.strip() for item in raw if isinstance(item, (str, int, float))
            for part in SPLIT.split(str(item)) if part.strip()]


def _clean(text: str) -> str:
    return text.strip().strip('"\'<>()[]{}').strip()


def read_search_term(token: str, platform=None) -> tuple[str | None, str | None, str | None, list[str]]:
    """One entry -> (search term, note, board link, excludes). A pasted Google dork keeps only its words:
    'site:newcold.csod.com intext:"frontend"' reads as frontend, a site: that names one board also limits the
    search to that company, and a -word, -"phrase" or -intitle:word is read as a term to exclude."""
    text = _clean(token)
    if DORK_OPERATOR.search(text) or DORK_TEXT.search(text):
        board, board_link = None, None
        for site in re.findall(r'\bsite:(\S+)', text, re.I):
            where_text = site.strip('"\'')
            link = platform.read_link(where_text) if platform else None
            if link and link[0]:
                board, board_link = link[0], where_text    # the link, not the key: read_company resolves it (EU keeps /eu)
        excludes: list[str] = []

        def _minus(m):
            excludes.append(m.group(1).strip())
            return ' '
        rest = re.sub(r'-"([^"]*)"?', _minus, DORK_TEXT.sub(' ', DORK_OPERATOR.sub(' ', text)))
        positives = []
        for w in rest.replace('"', ' ').replace("'", ' ').replace('(', ' ').replace(')', ' ').split():
            if w.upper() in ('OR', 'AND', '|'):
                continue
            core = w.lstrip('-')
            if not re.search(r'[A-Za-z0-9]', core):
                continue
            (excludes if w.startswith('-') else positives).append(core)
        excludes = [e for e in excludes if re.search(r'[A-Za-z0-9]', e)]
        words = ' '.join(positives)
        where = f' on the {board} board' if board else ''
        no = f', excluding {", ".join(excludes)}' if excludes else ''
        if not words:
            if board:
                return None, f'Read the Google dork "{text}" as every job{where}{no}.', board_link, excludes
            return None, f'"{text}" is a Google dork with no keyword in it, such as intext:frontend.', None, excludes
        return words, f'Read the Google dork "{text}" as the keyword "{words}"{where}{no}.', board_link, excludes
    return (text, None, None, []) if text else (None, None, None, [])


DORK_OR = re.compile(r'\s+(?:OR|\|)\s+')            # Google's OR: upper case, or |


def alternatives(token: str) -> list[str]:
    """A pasted dork with OR -> one dork per alternative, each keeping its operators:
    'site:x "data engineer" OR "ml engineer"' -> ['site:x "data engineer"', 'site:x "ml engineer"']."""
    text = _clean(token)
    if not (DORK_OPERATOR.search(text) or DORK_TEXT.search(text)) or not DORK_OR.search(text):
        return [token]
    operators = ' '.join(DORK_OPERATOR.findall(text))
    return [f'{operators} {part.strip()}'.strip() for part in DORK_OR.split(DORK_OPERATOR.sub(' ', text)) if part.strip()]


# What people type into a job site's search box beside the job title (owner, 2026-11-09: someone who wants a job types the
# position or the place and presses Start): a place ("python developer in pune", "java developer bangalore"), remote
# ("remote react developer", "data entry work from home"), fresher, and the words of a job ad ("jobs", "vacancies"). None
# is a word of the job's title, and a job matches only when its title has every word, so each is read into its own field
# (Cutshort's "python developer in pune" found 1 job, 2026-11-09). Only at the start or the end of an entry.
AD_WORDS = frozenset({'job', 'jobs', 'vacancy', 'vacancies', 'opening', 'openings', 'hiring', 'for'})
PLACE_WORDS = frozenset({'in', 'at', 'near', 'around'})
# The same words in the languages of the European boards, read only before a place a list knows: "développeur à Paris",
# "developpeur a Paris" (no accent), "chef de projet sur Lyon", "desarrollador en Madrid", "analista em Lisboa". Not
# dropped when they end an entry, as "in" is: "Class A" and a trailing "en" can be words of a title.
BEFORE_PLACE_WORDS = PLACE_WORDS | frozenset({'à', 'a', 'au', 'aux', 'en', 'dans', 'sur', 'em'})
TERM_REMOTE = frozenset({'remote', 'wfh', 'work from home'})      # no title starts with "remote" but a remote job's,
FRESHER_WORDS = frozenset({'fresher', 'freshers'})                  # 103,346 rows of five boards, 2026-11-09


@lru_cache(maxsize=1)
def _place_names() -> tuple[frozenset[str], frozenset[str]]:
    """(every place the job boards list, the places a bare word is read as). A bare word needs the short list, the cities
    Instahyre lists and the other names of places.py: the long one holds words of job titles and skills too (Industrial,
    Aurora, Palo, Bid, Rail: 103,226 rows of five boards, 2026-11-09). After "in" or "at" a name of the long one is a place."""
    def rows(name: str) -> list[list[str]]:
        text = resources.files('ats_jobs').joinpath(f'data/{name}').read_text(encoding='utf-8')
        return [line.split('\t') for line in text.splitlines() if line.strip() and not line.startswith('#')]
    every = {places.key(cols[0]) for name in ('naukri-cities.tsv', 'foundit_places.tsv') for cols in rows(name)}
    major = {places.key(n) for cols in rows('instahyre_places.tsv')
             for n in [cols[0]] + (cols[3].split(',') if len(cols) > 3 else []) if n.strip()}
    major |= {places.key(n) for name, others in places.OTHER_NAMES.items() for n in (name, *others)}
    major |= {places.key(cols[1]) for cols in rows('naukri-cities.tsv') if len(cols) > 1}     # the states
    major = (major | {'india'}) - {''}
    return frozenset(every | major), frozenset(major)


def _is_place(words: list[str], bare: bool, platform=None) -> bool:
    """A place, by the job boards' lists and the platform's own (Platform.place_names: a site outside India names its
    towns, such as Welcome to the Jungle's Paris, Lyon and London or eFinancialCareers' Zurich), whose names are
    also tried with their accents dropped (Zürich, Zurich)."""
    text = ' '.join(words)
    name = places.key(text)
    if not name:
        return False
    every, major = _place_names()
    own_every, own_major = platform.place_names() if platform is not None else (frozenset(), frozenset())
    own = own_major if bare else own_every
    return (name in (major if bare else every) or name in places.REGIONS
            or country_code(text, allow_codes=False) is not None
            or name in own or places.key(plain(text)) in own)


def _place_text(words: list[str]) -> str:
    """['-', 'Bangalore'] -> 'Bangalore', ['(pune)'] -> 'pune': the place as typed, without the marks around it."""
    return re.sub(r'^[\W_]+|[\W_]+$', '', ' '.join(words))


def read_place_words(term: str, platform=None) -> tuple[str, list[str], bool]:
    """One search entry -> (the job title words, the places in it, fresher asked). 'python developer in pune' ->
    ('python developer', ['pune'], False); 'remote react developer' -> ('react developer', ['Remote'], False);
    'fresher jobs in Bangalore' -> ('', ['Bangalore'], True); 'data analyst' -> ('data analyst', [], False). A platform
    adds the places of its own list (Platform.place_names)."""
    words, found, fresher = term.split(), [], False
    while words:
        keys = [places.key(w) for w in words]
        if not keys[-1] or keys[-1] in AD_WORDS or keys[-1] in PLACE_WORDS:     # a dash, "jobs", a hanging "in"
            words.pop()
            continue
        if not keys[0] or keys[0] in AD_WORDS:
            words.pop(0)
            continue
        if keys[-1] in FRESHER_WORDS or keys[0] in FRESHER_WORDS:
            words.pop(-1 if keys[-1] in FRESHER_WORDS else 0)
            fresher = True
            continue
        end = next((n for n in (3, 2, 1) if n <= len(words) and ' '.join(keys[-n:]) in TERM_REMOTE), 0)
        start = next((n for n in (3, 2, 1) if n <= len(words) and ' '.join(keys[:n]) in TERM_REMOTE), 0)
        if end or start:                                                        # "work-from-home" is one word
            if end:
                del words[-end:]
            else:
                del words[:start]
            found += [] if 'Remote' in found else ['Remote']
            continue
        cut = next((n for n in range(min(4, len(words) - 1), 0, -1)
                    if keys[-n - 1] in BEFORE_PLACE_WORDS and _is_place(words[-n:], False, platform)), 0)
        if cut:                                                                 # "... in <place>"
            found.append(_place_text(words[-cut:]))
            del words[-cut - 1:]
            continue
        if keys[0] in BEFORE_PLACE_WORDS and len(words) > 1 and _is_place(words[1:], False, platform):
            found.append(_place_text(words[1:]))                                   # "in <place>", its "jobs" gone
            words = []
            continue
        cut = next((n for n in range(min(3, len(words)), 0, -1) if _is_place(words[-n:], True, platform)), 0)
        if not cut:
            break
        found.append(_place_text(words[-cut:]))                                    # "... <city>", or the city alone
        del words[-cut:]
    return ' '.join(words), found, fresher


def parse_terms(value, key: str, notes: list[str], skipped: list[tuple[str, str]],
                boards: list[str] | None = None, platform=None, excludes: list[str] | None = None,
                places_found: list[str] | None = None, fresher: list[bool] | None = None) -> list[str]:
    """Search words -> terms. Boards named by a pasted dork are added to boards, a dork's -word to excludes. Given
    places_found, the places typed with the words go there (read_place_words) and a fresher typed with them to fresher."""
    terms, seen = [], set()
    for token in [alternative for item in _items(value) for alternative in alternatives(item)]:
        term, note, board, dork_excludes = read_search_term(token, platform)
        if excludes is not None:
            excludes.extend(dork_excludes)
        if board is not None and boards is not None and board.lower() not in (b.lower() for b in boards):
            boards.append(board)
        if term is None:
            if note and board is None:
                skipped.append((token, note))
            elif note:
                notes.append(note)
            continue
        if not re.search(r'[A-Za-z0-9]', term):
            skipped.append((token, f'"{term}" in {key} has no letters or digits to match.'))
            continue
        if note:
            notes.append(note)
        elif places_found is not None:
            words, found, is_fresher = read_place_words(term, platform)
            if words != term:
                read = ([f'the job title "{words}"'] if words else [])
                read += [('remote jobs' if place == 'Remote' else f'jobs in {place}') for place in found]
                read += ['fresher'] if is_fresher else []
                notes.append(f'Read "{_clean(token)}" in {key} as ' + (', '.join(read) or 'every job') + '.')
                places_found += [p for p in found if places.key(p) not in {places.key(q) for q in places_found}]
                if is_fresher and fresher is not None:
                    fresher.append(True)
                term = words
            if not term:
                continue
        if term.lower() not in seen:
            seen.add(term.lower())
            terms.append(term)
    return terms


def _links_whole(value) -> list[str]:
    """_items, except that a link with a query keeps its commas: a site's own search link can list values with them
    (Instahyre: ?skills=Python,Django&location=Pune). A comma before another link still separates the two."""
    raw = value if isinstance(value, list) else [value] if isinstance(value, (str, int, float)) else []
    out = []
    for item in raw:
        text = str(item).strip() if isinstance(item, (str, int, float)) else ''
        if IS_URL.match(text) and '?' in text:
            out += [part.strip() for part in re.split(r'[,;\s]+(?=[a-z][a-z0-9+.-]*://)|[;\n\r\t]+', text, flags=re.I)
                    if part.strip()]
        else:
            out += _items([item])
    return out


def route_links(value, platform, notes: list[str],
                skipped: list[tuple[str, str]]) -> tuple[list[str], list[str], list[str], list[str]]:
    """Main search box -> (words, job links, board links, places). A link of this platform pasted there is read
    for what it is: a job link is checked, a board link searches that company, a search link
    (?query=engineer&location=Germany) searches its words and place, and a link that names none of these is
    skipped with the note the companies field gives, instead of being searched as words."""
    words, jobs, boards, where = [], [], [], []
    for token in _links_whole(value):
        text = _clean(token)
        link = None if DORK_OPERATOR.search(text) or DORK_TEXT.search(text) else platform.read_link(text)
        if link and link[1]:
            jobs.append(text)
            notes.append(f'Read {text} in searchTerms as a job link to check.')
            continue
        if link and link[0]:
            boards.append(text)
            notes.append(f'Read {text} in searchTerms as the {link[0]} board.')
            continue
        if link is not None:
            query = parse_qs(urlparse(text if IS_URL.match(text) else f'https://{text}').query)
            # Instahyre's search links carry skills=python,java: one word list, split at the commas
            asked = [part.strip() for key in ('query', 'q', 'keywords', 'skills') for value in query.get(key, [])
                     for part in (value.split(',') if key == 'skills' else [value]) if part.strip()]
            place = [v.strip() for key in ('location', 'locations') for v in query.get(key, []) if v.strip()]
            more_words, more_places = platform.search_link(text)
            asked += [w for w in more_words if w not in asked]
            place += [p for p in more_places if p not in place]
            if asked or place:
                words += asked
                where += place
                notes.append(f'Read {text} in searchTerms as a search for {" and ".join(asked) or "every job"}'
                             f'{" in " + " and ".join(place) if place else ""}.')
            else:
                skipped.append((token, f'"{text}" is a {platform.name} link with no company in it. Paste a link '
                                       f'such as {platform.board_example}.'))
            continue
        words.append(token)
    return words, jobs, boards, where


def _alias(text: str, aliases: dict[str, str]) -> str | None:
    key = ' '.join(text.lower().replace('_', ' ').split())
    return aliases.get(key) or aliases.get(key.replace(' ', '')) or aliases.get(key.replace(' ', '-'))


def parse_choices(value, key: str, allowed: tuple[str, ...], aliases: dict[str, str], notes: list[str]) -> list[str]:
    """Work types or job types in everyday words -> the fixed values, empty for every one. Unknown words
    are dropped with a note; when none is known, every one is kept."""
    chosen, unknown = [], []
    for token in _items(value):
        if ' '.join(token.lower().split()) in EVERY:
            return []
        name = _alias(token, aliases)
        if name is None:
            unknown.append(token)
            continue
        if token.strip().lower() not in allowed:
            notes.append(f'Read "{token}" in {key} as {name}.')
        if name not in chosen:
            chosen.append(name)
    if unknown and chosen:
        notes.append(f'{key} has no option {", ".join(unknown)}, so it was left out.')
    if unknown and not chosen:
        notes.append(f'{key} has no option {", ".join(unknown)}, so every one is kept. The options are '
                     f'{", ".join(allowed)}.')
    return chosen if len(chosen) < len(allowed) else []


REMOTE_WORDS = re.compile(r'\b(?:fully\s+)?remote(?:ly)?\b|\bwfh\b', re.I)


def location_entries(value) -> list[list[str]]:
    """The location field -> its entries, each split into its parts. An entry is one choice; its parts (split by
    commas, semicolons or new lines) are places named together: "Remote, United States" typed as one entry is
    remote jobs in the United States, while ["Spain", "Remote"] is two choices."""
    raw = value if isinstance(value, list) else [value] if isinstance(value, (str, int, float)) else []
    entries = []
    for item in raw:
        if isinstance(item, (str, int, float)):
            parts = [part.strip() for part in SPLIT.split(str(item)) if part.strip()]
            if parts:
                entries.append(parts)
    return entries


def _read_part(token: str, notes: list[str], skipped: list[tuple[str, str]]) -> tuple[str, str | None, str | None]:
    """One part of a location entry -> (what it is, place, work type): ('skip', None, None), ('place', text, None),
    ('remote', place or None, 'remote') for "Remote EMEA" or a bare "Remote", or ('work', None, kind) for another
    work type typed as a place ("Hybrid")."""
    text = _clean(token)
    if not re.search(r'[^\W_]', text):
        skipped.append((token, f'"{token}" in location has no letters or digits to match.'))
        return 'skip', None, None
    if places.world_query(places.tokens(text)):
        notes.append(f'Read "{text}" in location as jobs open worldwide: marked worldwide, global or anywhere, '
                     f'or just "Remote" with no country.')
        return 'place', text, None
    kind = _alias(text, WORK_TYPE_ALIASES)
    if kind == 'remote':
        return 'remote', None, 'remote'
    if kind:
        return 'work', None, kind
    rest = REMOTE_WORDS.sub(' ', text)
    if rest != text:                                         # "Remote EMEA", "Remote - US", "Remote 100%"
        rest = re.sub(r'^(?:in|within|from)\s+', '', rest.strip(' \t-–—:,()%'), flags=re.I).strip(' \t-–—:,()%')
        if not re.search(r'[^\W\d_]', rest):
            return 'remote', None, 'remote'
        _region_note(rest, notes)
        return 'remote', rest, 'remote'
    _region_note(text, notes)
    return 'place', text, None


def _region_note(text: str, notes: list[str]) -> None:
    place, countries = classify_place(text)
    if place == 'region':
        notes.append(f'Read "{text}" in location as a region of {len(countries)} countries: it finds jobs in '
                     f'them, jobs posted for the region and jobs open worldwide.')


def parse_locations(entries: list[list[str]], work_types: list[str], notes: list[str],
                    skipped: list[tuple[str, str]]) -> tuple[list[str], list[str], list[str]]:
    """Location entries (location_entries) -> (places, work types, remote places). Each entry is one choice: a job
    matches when it matches any of them.

    - "Remote" inside an entry asks for remote jobs in that entry's places: "Remote EMEA", "Remote - US", or
      "Remote, United States" typed as one entry.
    - When every entry asks for remote jobs, remote moves to the work type, as if Work type were Remote: "Remote"
      alone is every remote job, "Remote EMEA" every remote job in EMEA.
    - Next to an entry that names a place with no "Remote", every entry keeps its own meaning: the plain places keep
      every work type, and "Remote EMEA" stays remote jobs in EMEA (a remote place). "Remote" as an entry of its own
      narrows nothing: the places already hold their remote jobs ("Spain - Remote", "Remote, UK") and the jobs marked
      worldwide. ["Barcelona", "Spain", "Remote"] is every job in Barcelona or Spain. Until 2026-11-08 it was remote
      jobs only, so a Deel user's search for Barcelona, Spain, EMEA, Europe or Remote found none of the 4 jobs there
      (run UxZ0dOfitMrBnt6TF). Reading a lone "Remote" as every job that says just "Remote" was tried and measured
      the same day: on that user's Greenhouse search it added 9 such jobs to 60, mostly US-only roles ("Provider
      Partnerships Regional Manager (Austin / San Antonio)"), so "Worldwide" stays the way to ask for them.
    - "Hybrid" or "On-site" typed as a place moves to the work type, and so does every remote word next to it.
    "Worldwide", "Anywhere" and "Global" stay places: they find the jobs open everywhere. An entry with no letter
    or digit ("!!!") would match no job, so it is skipped with a reason."""
    read = []                                    # per entry: (its text, its places, whether it asks remote)
    other_kinds = []                             # hybrid or onsite typed as a place
    for parts in entries:
        found, remote = [], False
        for token in parts:
            what, place, kind = _read_part(token, notes, skipped)
            if what == 'work':
                other_kinds.append(kind)
                notes.append(f'Read "{_clean(token)}" in location as the {kind} work type.')
            elif what == 'remote':
                remote = True
                if place:
                    found.append(place)
            elif what == 'place':
                found.append(place)
        if found or remote:
            read.append((', '.join(_clean(t) for t in parts), found, remote))
    plain = any(found and not remote for _, found, remote in read)
    each_its_own = plain and not other_kinds and any(remote for *_, remote in read)
    moved, chosen = list(work_types), {}        # chosen: lower text -> [text, asks remote]

    def add(text: str, remote: bool) -> None:
        item = chosen.setdefault(text.lower(), [text, remote])
        item[1] = item[1] and remote             # a place also asked plainly keeps every work type

    for text, found, remote in read:
        if not remote:
            for place in found:
                add(place, False)
        elif each_its_own and found:
            for place in found:
                add(place, True)
            notes.append(f'Read "{text}" in location as remote jobs in {", ".join(found)}; the other places keep '
                         f'every work type.')
        elif each_its_own:
            notes.append(f'Read "{text}" next to other places as their remote jobs, which they already hold with '
                         f'every other work type there. To get remote jobs only, set Work type to Remote; to add remote '
                         f'jobs elsewhere, add their country or region, or Worldwide for jobs open to anyone.')
        else:
            if 'remote' not in moved:
                moved.append('remote')
            notes.append(f'Read "{text}" in location as the remote work type' +
                         (f' in {", ".join(found)}.' if found else '.'))
            for place in found:
                add(place, False)
    for kind in other_kinds:
        if kind not in moved:
            moved.append(kind)
    if len(moved) == len(WORK_TYPES):
        moved = []
    return [t for t, _ in chosen.values()], moved, [t for t, r in chosen.values() if r]


def parse_period(value, notes: list[str]) -> float | None:
    """'24 hours', '24h', '7 days', '2 weeks', 'month', 3, 'any time' -> days, or None for any time."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    text = ' '.join(str(value).strip().lower().replace('_', ' ').split())
    if text in ANY_TIME:
        return None
    if text in ('today', '24 hours', 'day', 'past day', 'last day'):
        return 1.0
    m = PERIOD.match(text)
    if m and (m.group(1) or m.group(2)):
        number = float(m.group(1)) if m.group(1) else 1.0
        days = number * PERIOD_DAYS[m.group(2) or 'd']
        if days > 0:
            return days
    notes.append(f'Could not read "{value}" as a period such as 24 hours, 7 days or 30 days, so any posting '
                 f'date is kept.')
    return None


def parse_max_jobs(value, notes: list[str]) -> int | None:
    """Missing -> 100. Empty, 0 or "all" -> every match. A number -> that many, newest first."""
    if value is None:
        return DEFAULT_MAX_JOBS
    if str(value).strip().lower() in ('', 'all', '0', 'every', 'unlimited'):
        return None
    try:
        n = int(float(str(value).strip().replace(',', '')))
    except ValueError:
        notes.append(f'Could not read maxJobs "{value}" as a number, so it is {DEFAULT_MAX_JOBS}.')
        return DEFAULT_MAX_JOBS
    if n < 0:
        notes.append(f'maxJobs {n} is below 0, so it is {DEFAULT_MAX_JOBS}.')
        return DEFAULT_MAX_JOBS
    if n > MAX_JOBS_CAP:
        notes.append(f'maxJobs {n:,} is above {MAX_JOBS_CAP:,}, so every match is returned.')
        return None
    return n


FRESHER = {'fresher', 'freshers', 'fresh', 'fresh graduate', 'entry', 'entry level', 'entry-level', 'no experience',
           'graduate', 'new grad'}
YEARS = re.compile(r'^(\d+(?:\.\d+)?)\s*(?:(?:-|to|\u2013|\u2014)\s*(\d+(?:\.\d+)?)|(\+|or more|and above|plus))?$')


def parse_experience(value, notes: list[str]) -> tuple[float, float] | None:
    """Years of experience, as a job board asks for them: 3 keeps the jobs whose range holds 3 years, 2-5 the jobs whose
    range overlaps 2 to 5, 5+ five years or more, fresher 0. Empty: any. Unreadable: any, with a note."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return float(value), float(value)
    text = ' '.join(str(value).strip().lower().replace('years', '').replace('year', '').replace('yrs', '').split())
    if text in ('', 'any', 'all', 'any experience'):
        return None
    if text in FRESHER:
        return 0.0, 0.0
    m = YEARS.match(text)
    if m:
        low = float(m.group(1))
        high = float('inf') if m.group(3) else float(m.group(2)) if m.group(2) else low
        return (min(low, high), max(low, high))
    notes.append(f'Could not read "{value}" as years of experience, such as 3, 2-5 or 5+, so any experience is kept.')
    return None


def parse_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ('true', 'yes', 'on', '1'):
        return True
    if isinstance(value, str) and value.strip().lower() in ('false', 'no', 'off', '0'):
        return False
    return default


def read_company(token: str, platform, names: dict[str, list[str]] | None = None) -> tuple[list[str], str | None]:
    """One entry -> (board keys, note). Accepts a board link, a company name we know, or a board name. One
    company can have several boards, and a name finds all of them."""
    text = _clean(token)
    names = names or {}
    link = platform.read_link(text)
    if link is not None:
        if link[0]:
            return names.get(link[0].lower()) or [link[0]], None
        return [], f'"{text}" is a {platform.name} link with no company in it. Paste a link such as {platform.board_example}.'
    if IS_URL.match(text) or '/' in text:
        return [], f'"{text}" is not a {platform.name} link. Type the company name, or paste a link such as {platform.board_example}.'
    found = names.get(text.lower()) or names.get(re.sub(r'[^a-z0-9]', '', text.lower()))
    if found:
        quiet = any(key.lower() == text.lower() for key in found)
        return found, None if quiet else f'Read the company "{text}" as {", ".join(found[:5])}.'
    start = f'{" ".join(text.lower().split())} '          # "world bank" finds World Bank Group
    longer = [key for name, keys in names.items() if name.startswith(start) for key in keys]
    if longer:
        return list(dict.fromkeys(longer)), f'Read the company "{text}" as {", ".join(longer[:5])}.'
    guess = platform.guess_board(' '.join(text.split()))
    if guess:
        return [guess], None
    return [], f'"{text}" is not a company name or board link.'


def parse_companies(value, platform, names, notes, skipped) -> list[str]:
    companies, seen = [], set()
    for token in _items(value):
        keys, note = read_company(token, platform, names)
        if not keys:
            skipped.append((token, note))
            continue
        if note:
            notes.append(note)
        for key in keys:
            if key.lower() not in seen:
                seen.add(key.lower())
                companies.append(key)
    return companies


def read_job_url(token: str, platform) -> tuple[tuple[str, str] | None, str | None]:
    """A job link -> ((board key, job id), None), or (None, reason)."""
    text = _clean(token)
    link = platform.read_link(text)
    if link is None:
        return None, f'"{text}" is not a {platform.name} job link. Paste links such as {platform.job_example}.'
    if link[0] and link[1]:
        return (link[0], link[1]), None
    return None, f'"{text}" is a {platform.name} link without a job in it. Paste the link of one job.'


def parse_job_urls(value, platform, skipped) -> list[tuple[str, str, str]]:
    urls, seen = [], set()
    for token in _items(value):
        found, why = read_job_url(token, platform)
        if found is None:
            skipped.append((token, why))
            continue
        if found not in seen:
            seen.add(found)
            urls.append((found[0], found[1], _clean(token)))
    return urls


def parse_input(raw: dict | None, platform, company_names: dict[str, list[str]] | None = None) -> Config:
    raw = raw or {}
    notes: list[str] = []
    skipped: list[tuple[str, str]] = []
    words, pasted_jobs, pasted_boards, pasted_places = route_links(raw.get('searchTerms'), platform, notes, skipped)
    job_urls = parse_job_urls(_items(raw.get('jobUrls')) + pasted_jobs, platform, skipped)
    if _items(raw.get('jobUrls')) and not job_urls:
        notes.append('None of the job links could be read, so the run searched instead. ' + skipped[-1][1])
    work_types = parse_choices(raw.get('workType'), 'workType', WORK_TYPES, WORK_TYPE_ALIASES, notes)
    from_dork: list[str] = []
    dork_excludes: list[str] = []
    term_places: list[str] = []
    term_fresher: list[bool] = []
    search_terms = parse_terms(words, 'searchTerms', notes, skipped, from_dork, platform, dork_excludes,
                               term_places, term_fresher)
    typed = location_entries(raw.get('location'))
    have = {places.key(part) for entry in typed for part in entry} | {places.key(p) for p in pasted_places}
    locations, work_types, remote_places = parse_locations(
        typed + [[place] for place in pasted_places] + [[p] for p in term_places if places.key(p) not in have],
        work_types, notes, skipped)
    companies = parse_companies(_items(raw.get('companies')) + pasted_boards, platform, company_names, notes, skipped)
    if _items(raw.get('companies')) and not companies:
        notes.append('None of the companies could be read, so every company was searched. ' + skipped[-1][1])
    for key in parse_companies(from_dork, platform, company_names, notes, skipped):
        if key.lower() not in (c.lower() for c in companies):
            companies.append(key)
    exclude_terms = parse_terms(raw.get('excludeTerms'), 'excludeTerms', notes, skipped, platform=platform)
    for term in dork_excludes:
        if term.lower() not in (e.lower() for e in exclude_terms):
            exclude_terms.append(term)
    cfg = Config(
        search_terms=search_terms,
        exclude_terms=exclude_terms,
        locations=locations,
        remote_places=remote_places,
        work_types=work_types,
        job_types=parse_choices(raw.get('employmentType'), 'employmentType',
                                JOB_TYPES + tuple(t for t in platform.job_types if t not in JOB_TYPES),
                                {**JOB_TYPE_ALIASES, **platform.job_type_aliases}, notes),
        posted_within_days=parse_period(raw.get('postedWithin'), notes),
        max_jobs=parse_max_jobs(raw.get('maxJobs'), notes),
        include_details=parse_bool(raw.get('includeDetails'), False),
        search_descriptions=parse_bool(raw.get('searchDescriptions'), False),
        companies=companies,
        job_urls=job_urls,
        experience=parse_experience(raw.get('experienceYears'), notes) or (
            (0.0, 0.0) if term_fresher and raw.get('experienceYears') in (None, '') else None),
        notes=notes,
        skipped=skipped,
    )
    if term_fresher and raw.get('experienceYears') not in (None, ''):
        cfg.notes.append(f'experienceYears {raw["experienceYears"]} was kept over the fresher typed in searchTerms.')
    if cfg.job_urls and (cfg.search_terms or cfg.locations or cfg.companies):
        cfg.notes.append('Job links were given, so the run checks those links and does not search. '
                         'Run again without job links to search.')
    if cfg.search_descriptions and not cfg.search_terms:
        cfg.search_descriptions = False
    platform.read_input(raw, cfg)
    return cfg
