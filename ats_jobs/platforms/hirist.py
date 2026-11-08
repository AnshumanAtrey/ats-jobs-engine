"""
Hirist (hirist.tech), India's job board for tech roles, read through the public API its own pages call
(gladiator.hirist.tech, an AWS load balancer in Mumbai: no login, no key, no cookie, any user agent). Measured from this
laptop on 2026-11-08:

- GET /job/keyword/?pageNo=&size=: with no keywordId (a skill tag), every job on the board, newest first by the day
  Hirist shows on its job cards and in its JobPosting markup (createdTime, a day in India time): 28,770 jobs. Up to 1,000
  a page, about 1.5 KB a job, under a second. Filters it applies itself: loc (location ids; a comma list is any of them),
  posting (posted in the last N calendar days, India time: posting=3 is today and the two days before), minexp and maxexp
  (years: a job matches when its own range overlaps; 0 and 0 is no filter). Elasticsearch stops one query at 10,000 rows
  (page 10 of 1,000 answers empty with hasMore false), which reached back about 50 days. A job stays 150 days: posting=150
  equals the default (180 adds 321 jobs the site never shows), and the site's markup gives validThrough = posted + 5 months.
- Past 10,000 a query is split into queries that together hold every job: groups of location ids (every job names at
  least one, 1.5 on average), and a location over 10,000 (Bangalore, 11,394) by ranges of years of experience. On
  2026-11-08 the newest 10,000 and 8 such slices held 28,768 jobs of the 28,770 the total counts (it counts 2 jobs twice):
  232 calls, 89 MB, 22 s.
- GET /job/detail?jobcode=<id>: one job with its description (introText, HTML), the applicants (applicationCount:
  applyCount and the views, hits, come masked as '*****' to a visitor), Hirist's own job link and, when the recruiter gave
  one, an outside apply link. Open is status 1, active 1, not expired
  and not removed; anything else is closed (an id from January answered status 0 and permanentlyRemoved). An id that
  never existed answers 404 JOB_NOT_FOUND, text that is no id 400.
- GET /job/top-jobs?slug=<company page>&page=: one company's jobs, newest first, 15 a page with a count, each a full
  record. Complete where checked: Unisys 28 (all 26 of its jobs in the newest 10,000), Nagarro 18 (13). GET
  /job/company-id-slug?slug= says whether that company page exists. Only about half of the companies hiring have one
  under their own name (1,099 of 2,379 in the newest 10,000), so a company that has none is found by its name in the jobs.
- GET /job/search?query=: the site's own search box. Not used: it ranks by relevance and reads words loosely
  ("frontend developer": 1,168 jobs, 53 with both words in the title) or narrowly ("react": 40 jobs, while 295 titles in
  the newest 10,000 name React). Words are matched here, in titles, and with searchDescriptions in Hirist's skill tags.

Fields: experience as a range of years (min, max) on every job; skills on every job (tags, with a mandatory flag); pay
in lakhs of rupees a year (minSal, maxSal) shown on 7% (hideSal 0); work from home as a flag (workFromHome) or Remote
among the places: 10.7% of the 28,356 jobs a full read delivered on 2026-11-08. Of 20 work-from-home jobs read, 7
describe a hybrid setup and 8 remote work, and of 60 others 8 say hybrid and 11 an office, so remote here is Hirist's
work-from-home mark and onsite its absence. Hirist states no job type. Titles carry the company first ("GeBBS - Java
Developer"). The company's AmbitionBox rating and review count come with 57% of jobs. createdTimeMs, the moment the job
went live (98.5% between 09:00 and 20:00 India time), falls on the day Hirist shows for 95% of jobs; for the rest it is
a later day, so postedAt is that moment when it is on the shown day and the shown day alone otherwise. A job whose only
place is "Others" carries the place the recruiter typed in createdByAlias ("Bengaluru,Karnataka,India", "Mumbai (all
Areas)"): 451 of 28,770.

iimjobs.com (management jobs, the same company) runs the same API on gladiator.iimjobs.com with its own jobs; there the
keyword feed answers 404 and the newest-first lists are per category, so it is not read here.
"""
import asyncio
import math
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlparse

from ..http import BoardError, BoardMissing, send
from ..inputs import parse_experience
from ..rows import Place, Term, index, job_area, terms, words
from .base import Board, Platform
from .cornerstone import text_of

API = 'https://gladiator.hirist.tech/job'
FEED = API + '/keyword/'
DETAIL = API + '/detail'
COMPANY_JOBS = API + '/top-jobs'
COMPANY_PAGE = API + '/company-id-slug'
SITE = 'https://www.hirist.tech'
JOB_LINK = SITE + '/j/{slug}-{id}'
HOSTS = ('www.hirist.tech', 'hirist.tech', 'www.hirist.com', 'hirist.com', 'm.hirist.tech')
EVERYONE = Board('*', 'Hirist')                 # the whole board, every company at once
LISTED = Board('hirist.tech', 'Hirist')         # what boards() lists: one job board
HEADERS = {'version': '2'}                      # what Hirist's own pages send
PAGE = 1000                                     # rows a call
WINDOW = 10_000                                 # rows one query reaches (Elasticsearch's result window)
PACK = 9_000                                    # rows a slice is planned for: under WINDOW with room for new jobs
DEEPEST = 150                                   # days a job stays on Hirist
MANY_PLACES = 40                                # more location ids than this and the place filter stays here only
COMPANY_PAGE_SIZE = 15
IST = timezone(timedelta(hours=5, minutes=30))
LAKH = 100_000
JOB_ID = re.compile(r'^\d{1,9}$')
JOB_PATH = re.compile(r'^(?:.*-)?(\d{4,9})(?:\.html)?$')
COMPANY_PATH = re.compile(r'^(.+?)-careers$')
SLUG = re.compile(r'^[a-z0-9][a-z0-9-]*$')
OTHERS, ANYWHERE = 100000, 0                    # "Others" and "Any Location": the recruiter's own text says where
REMOTE_ID = 132

# Hirist's places as its filter lists them (www.hirist.tech, 2026-11-08), plus 'Any Location' (id 0) seen only on jobs:
# id (a group's members after ':'), Hirist's label, the city's name as written today (for a city), the state, the country,
# other names of a group. Every name here finds the place; the row's city, region and country come from the first place.
_PLACES = """
1:36,40,41,37,38,39	Delhi NCR		Delhi	India	NCR, National Capital Region
87:1,36,41,40,37,38,2,5,3,7,4,6,54,78,39	Metros			India
88	Anywhere in India/Multiple Locations			India
89	Overseas/International
0	Any Location
100000	Others
132	Remote			India
3	Bangalore	Bengaluru	Karnataka	India
4	Hyderabad	Hyderabad	Telangana	India
7	Pune	Pune	Maharashtra	India
6	Chennai	Chennai	Tamil Nadu	India
2	Mumbai	Mumbai	Maharashtra	India
5	Kolkata	Kolkata	West Bengal	India
36	Delhi	New Delhi	Delhi	India
37	Gurgaon/Gurugram	Gurugram	Haryana	India
38	Noida	Noida	Uttar Pradesh	India
39	Greater Noida	Greater Noida	Uttar Pradesh	India
40	Faridabad	Faridabad	Haryana	India
41	Ghaziabad	Ghaziabad	Uttar Pradesh	India
53	Ahmedabad	Ahmedabad	Gujarat	India
45	Amritsar	Amritsar	Punjab	India
34	Andhra Pradesh		Andhra Pradesh	India
79	Aurangabad	Aurangabad	Maharashtra	India
65	Bhubaneshwar	Bhubaneswar	Odisha	India
19	Bihar		Bihar	India
14	Chandigarh	Chandigarh	Chandigarh	India
64	Chhattisgarh		Chhattisgarh	India
70	Cochin/Kochi	Kochi	Kerala	India
84	Coimbatore	Coimbatore	Tamil Nadu	India
86	Cuttack	Cuttack	Odisha	India
58	Dehradun	Dehradun	Uttarakhand	India
55	Gandhinagar	Gandhinagar	Gujarat	India
13	Goa		Goa	India
8	Gujarat		Gujarat	India
77	Guntur	Guntur	Andhra Pradesh	India
12	Guwahati	Guwahati	Assam	India
57	Haridwar	Haridwar	Uttarakhand	India
16	Haryana		Haryana	India
71	Hosur	Hosur	Tamil Nadu	India
72	Hubli	Hubballi	Karnataka	India
11	Jaipur	Jaipur	Rajasthan	India
46	Jalandhar	Jalandhar	Punjab	India
43	Jammu	Jammu	Jammu and Kashmir	India
42	Jammu & Kashmir		Jammu and Kashmir	India
63	Jamshedpur	Jamshedpur	Jharkhand	India
20	Jharkhand		Jharkhand	India
52	Jodhpur	Jodhpur	Rajasthan	India
31	Karnataka		Karnataka	India
17	Kerala		Kerala	India
60	Lucknow	Lucknow	Uttar Pradesh	India
48	Ludhiana	Ludhiana	Punjab	India
83	Madurai	Madurai	Tamil Nadu	India
9	Maharashtra		Maharashtra	India
10	MP		Madhya Pradesh	India
73	Mysore	Mysuru	Karnataka	India
66	Nagpur	Nagpur	Maharashtra	India
67	Nasik	Nashik	Maharashtra	India
68	Navi Mumbai	Navi Mumbai	Maharashtra	India
18	Odisha		Odisha	India
50	Panipat	Panipat	Haryana	India
47	Patiala	Patiala	Punjab	India
61	Patna	Patna	Bihar	India
85	Pondicherry	Puducherry	Puducherry	India
15	Punjab		Punjab	India
74	Raipur	Raipur	Chhattisgarh	India
33	Rajasthan		Rajasthan	India
80	Rajkot	Rajkot	Gujarat	India
62	Ranchi	Ranchi	Jharkhand	India
54	Surat	Surat	Gujarat	India
44	Srinagar	Srinagar	Jammu and Kashmir	India
49	Sonipat	Sonipat	Haryana	India
32	Tamil Nadu		Tamil Nadu	India
35	Telangana		Telangana	India
69	Thane	Thane	Maharashtra	India
75	Trivandrum/Thiruvananthapuram	Thiruvananthapuram	Kerala	India
51	Udaipur	Udaipur	Rajasthan	India
21	UP		Uttar Pradesh	India
59	Uttarakhand		Uttarakhand	India
56	Vadodara/Baroda	Vadodara	Gujarat	India
81	Varanasi/Banaras	Varanasi	Uttar Pradesh	India
76	Vijayawada	Vijayawada	Andhra Pradesh	India
78	Vishakhapatnam/Vizag	Visakhapatnam	Andhra Pradesh	India
82	Warangal	Warangal	Telangana	India
127	Arunachal Pradesh		Arunachal Pradesh	India
123	Assam		Assam	India
121	Bhopal	Bhopal	Madhya Pradesh	India
130	Indore	Indore	Madhya Pradesh	India
124	Manipur		Manipur	India
125	Meghalaya		Meghalaya	India
122	Mizoram		Mizoram	India
128	Nagaland		Nagaland	India
126	Tripura		Tripura	India
129	West Bengal		West Bengal	India
131	Mohali	Mohali	Punjab	India
133	Kanpur	Kanpur	Uttar Pradesh	India
134	Mangalore	Mangaluru	Karnataka	India
135	Silvassa	Silvassa	Dadra and Nagar Haveli and Daman and Diu	India
136	Agra	Agra	Uttar Pradesh	India
137	Imphal	Imphal	Manipur	India
138	Manesar	Manesar	Haryana	India
139	Shillong	Shillong	Meghalaya	India
140	Baddi	Baddi	Himachal Pradesh	India
141	Gwalior	Gwalior	Madhya Pradesh	India
142	Panchkula	Panchkula	Haryana	India
143	Siliguri	Siliguri	West Bengal	India
144	Secunderabad	Secunderabad	Telangana	India
145	Vapi	Vapi	Gujarat	India
146	Kozhikode	Kozhikode	Kerala	India
147	Jamnagar	Jamnagar	Gujarat	India
148	Tiruchirappalli	Tiruchirappalli	Tamil Nadu	India
149	Salem	Salem	Tamil Nadu	India
150	Tirupati	Tirupati	Andhra Pradesh	India
151	Vellore	Vellore	Tamil Nadu	India
152	Shimla	Shimla	Himachal Pradesh	India
153	Himachal Pradesh		Himachal Pradesh	India
158	Sikkim		Sikkim	India
160	Daman and Diu		Dadra and Nagar Haveli and Daman and Diu	India
161	Dadra and Nagar Haveli		Dadra and Nagar Haveli and Daman and Diu	India
162	Andaman and Nicobar Island		Andaman and Nicobar Islands	India
163	Lakshadweep		Lakshadweep	India
22	US			United States
23	UK			United Kingdom
24	Singapore			Singapore
25	Middle East
26	Africa
27	Malaysia			Malaysia
28	EU
30	Hong Kong			Hong Kong
90	Bahrain			Bahrain
91	Dubai	Dubai		United Arab Emirates
92	Kabul	Kabul		Afghanistan
93	Kuwait			Kuwait
94	Nigeria			Nigeria
95	London	London		United Kingdom
96	Oman			Oman
97	Muscat	Muscat		Oman
98	Doha	Doha		Qatar
99	Qatar			Qatar
100	Abu Dhabi	Abu Dhabi		United Arab Emirates
101	Saudi Arabia			Saudi Arabia
102	Riyadh	Riyadh		Saudi Arabia
103	Indonesia			Indonesia
104	Nepal			Nepal
105	Bhutan			Bhutan
106	Dhaka	Dhaka		Bangladesh
107	Bangladesh			Bangladesh
108	China			China
109	Afghanistan			Afghanistan
110	Pakistan			Pakistan
111	Sri Lanka			Sri Lanka
112	Ethiopia			Ethiopia
113	Egypt			Egypt
114	Kenya			Kenya
115	Nairobi	Nairobi		Kenya
116	Tanzania			Tanzania
117	South Africa			South Africa
118	Zimbabwe			Zimbabwe
119	Zambia			Zambia
120	Philippines			Philippines
165	Australia			Australia
166	Japan			Japan
167	Canada			Canada
168	Germany			Germany
169	Switzerland			Switzerland
170	England			United Kingdom
"""


def _table() -> tuple[dict[int, dict], dict[int, list[int]]]:
    entries, groups = {}, {}
    for line in _PLACES.strip('\n').splitlines():
        key, label, city, state, country, aliases = ((line.split('\t') + [''] * 6)[:6])
        number, _, members = key.partition(':')
        entries[int(number)] = {'name': label, 'city': city or None, 'region': state or None, 'country': country or None,
                                'aliases': [a.strip() for a in aliases.split(',') if a.strip()]}
        if members:
            groups[int(number)] = [int(m) for m in members.split(',')]
    return entries, groups


PLACES, GROUPS = _table()


def place(location: dict) -> dict:
    """A Hirist location -> one place: its label, another name it goes by, the state and the country."""
    entry = PLACES.get(location.get('id'))
    if entry is None:
        return {'name': ' '.join((location.get('name') or '').split()) or None}
    return {k: entry[k] for k in ('name', 'city', 'region', 'country')}


def typed_places(record: dict) -> list[dict]:
    """Every place of a job. "Others" and "Any Location" give way to what the recruiter typed (createdByAlias), with the
    parts the other places already name left out."""
    locations = record.get('locations') or record.get('location') or []
    known = [place(loc) for loc in locations if loc.get('id') not in (OTHERS, ANYWHERE)]
    vague = [loc for loc in locations if loc.get('id') in (OTHERS, ANYWHERE)]
    if vague:
        labels = {' '.join(words(p['name'])) for p in known if p.get('name')}
        alias = [re.sub(r'\s*,\s*', ', ', part.strip()).strip(', ')
                 for part in re.split(r'[/|]', record.get('createdByAlias') or '') if part.strip()]
        alias = [part for part in alias if ' '.join(words(part)) not in labels
                 and ' '.join(words(part)) not in ('others', 'any location')]
        text = ', '.join(dict.fromkeys(alias))
        known += [{'name': text}] if text else [place(loc) for loc in vague]
    return [p for p in known if p.get('name')]


def posted_at(created: int | None, moment: int | None) -> tuple[str | None, bool]:
    """(postedAt, dateOnly): the moment the job went live when it falls on the day Hirist shows, else that day."""
    if not created:
        return None, True
    day = datetime.fromtimestamp(created / 1000, IST).date()
    if moment:
        at = datetime.fromtimestamp(moment / 1000, timezone.utc)
        if at.astimezone(IST).date() == day:
            return at.isoformat(timespec='seconds').replace('+00:00', 'Z'), False
    return day.isoformat(), True


KEEP = ('id', 'title', 'min', 'max', 'createdTime', 'createdTimeMs', 'locations', 'createdByAlias', 'workFromHome',
        'minSal', 'maxSal', 'hideSal', 'tags', 'companyData', 'applyUrl', 'jobDetailUrl', 'applyCount', 'applicationCount',
        'introText', 'confidential')


def trim(record: dict) -> dict:
    """A record with only what job() reads: the rest of a feed page held in memory is about a third smaller."""
    out = {k: record[k] for k in KEEP if k in record}
    company = out.get('companyData') or {}
    rating = company.get('ambitionBoxInfo') or {}
    out['companyData'] = {'companyName': company.get('companyName'),
                          'ambitionBoxInfo': {k: rating.get(k) for k in ('aggregateRatingOneDecimal', 'aggregateRating',
                                                                          'reviewsCount') if k in rating}}
    return out


def lakhs(value) -> str:
    return f'{value:g}'


def pay(record: dict) -> dict:
    """Pay in lakhs of rupees a year, when Hirist shows it (hideSal 0)."""
    low, high = record.get('minSal'), record.get('maxSal')
    if record.get('hideSal') != 0 or not (low or high):
        return {'salary': None, 'salaryMin': None, 'salaryMax': None, 'salaryCurrency': None, 'salaryPeriod': None}
    amounts = ' - '.join(lakhs(v) for v in dict.fromkeys(v for v in (low, high) if v))
    return {'salary': f'INR {amounts} lakh a year', 'salaryMin': round(low * LAKH) if low else None,
            'salaryMax': round(high * LAKH) if high else None, 'salaryCurrency': 'INR', 'salaryPeriod': 'year'}


def slug(title: str) -> str:
    """Hirist's job link rule, nearly: a slash goes, other runs of punctuation become one hyphen. A link opens its job by
    the number at its end whatever comes before it."""
    return re.sub(r'[^a-z0-9]+', '-', (title or '').lower().replace('/', '')).strip('-') or 'job'


def company_slug(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


class Hirist(Platform):
    key = 'hirist'
    name = 'Hirist'
    site_word = 'job board'
    actor = 'hirist-jobs-search'
    board_example = 'https://www.hirist.tech/company/nagarro-software-careers'
    job_example = 'https://www.hirist.tech/j/gebbs-java-developer-1682327'
    concurrency = 16
    server_search = True          # pages() gets each word: Hirist's own search is not used, the words are matched here
    newest_first = True
    server_filters = True         # places, period and experience go to Hirist's own search (see params)
    never = ('employmentType', 'department', 'team', 'expiresAt', 'reference', 'offersEquity', 'companySlug',
             'companyWebsite')
    extra = ('salaryMin', 'salaryMax', 'salaryCurrency', 'salaryPeriod', 'experienceMin', 'experienceMax', 'skills',
             'mandatorySkills', 'companyRating', 'companyReviews', 'applicants')

    def __init__(self):
        self.cfg = None
        self.experience: tuple[int, int] | None = None
        self.loc: list[int] = []           # location ids Hirist is asked for; empty: every place, checked here
        self._board: list[dict] | None = None   # every job of the run's query, once a company name had to be found in it
        self._board_lock = asyncio.Lock()
        self._asked = []

    # ---------- links ----------

    def boards(self):
        return [LISTED], None

    def read_link(self, text):
        text = text.strip().rstrip('.,;:!?')
        link = text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}'
        parsed = urlparse(link)
        host = (parsed.hostname or '').lower() if ' ' not in text else ''
        if host not in HOSTS and not host.endswith('.hirist.tech'):
            return None
        parts = [unquote(p) for p in parsed.path.split('/') if p]
        if len(parts) >= 2 and parts[0].lower() == 'j':
            found = JOB_PATH.match(parts[1])
            return (EVERYONE.key, found.group(1)) if found else (None, None)
        if len(parts) >= 2 and parts[0].lower() == 'company':
            found = COMPANY_PATH.match(parts[1].lower())
            return (found.group(1) if found else parts[1].lower()), None
        found = parse_qs(parsed.query).get('jobcode') or parse_qs(parsed.query).get('jobId')
        if found and JOB_ID.match(found[0]):
            return EVERYONE.key, found[0]
        return None, None

    def search_link(self, text):
        """A search, skill, category or city page of Hirist -> (words, places) it asks for."""
        link = text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}'
        parsed = urlparse(link)
        parts = [unquote(p) for p in parsed.path.split('/') if p]
        query = parse_qs(parsed.query)
        asked, where = [], []
        if len(parts) >= 2 and parts[0].lower() in ('search', 'k'):
            asked.append(re.sub(r'-jobs$', '', parts[1], flags=re.I).replace('-', ' ').strip())
        if parts and parts[0].lower().startswith('it-jobs-in-'):
            where.append(parts[0][len('it-jobs-in-'):].replace('-', ' '))
        for number in ','.join(query.get('loc', [])).split(','):
            if number.strip().isdigit() and int(number) in PLACES:
                where.append(PLACES[int(number)]['name'])
        return [a for a in asked if a], where

    def guess_board(self, name):
        return ' '.join(name.split()) or None

    # ---------- the run's settings ----------

    def read_input(self, raw, cfg):
        """Years of experience, Hirist's places, and what Hirist does not state, read once the shared fields are."""
        self.cfg = cfg
        self._mend_links(raw, cfg)
        link = self._link_settings(raw)
        if cfg.experience is None and link.get('experience'):      # experienceYears is the engine's (inputs.py)
            cfg.experience = parse_experience(link['experience'], cfg.notes)
            if cfg.experience is not None:
                cfg.notes.append(f'Read the experience of the pasted Hirist link: {link["experience"]} years.')
        # Hirist's own search takes whole years, 99 for no ceiling ("10+")
        self.experience = None if cfg.experience is None else (
            int(cfg.experience[0]), 99 if math.isinf(cfg.experience[1]) else math.ceil(cfg.experience[1]))
        if cfg.posted_within_days is None and link.get('posting'):
            cfg.posted_within_days = float(link['posting'])
            cfg.notes.append(f'Read the posting period of the pasted Hirist link: the last {link["posting"]} days.')
        if cfg.posted_within_days is not None and cfg.posted_within_days > DEEPEST:
            cfg.notes.append(f'Hirist keeps a job for {DEEPEST} days, so every job on it was posted in that period.')
        if 'hybrid' in cfg.work_types:
            cfg.work_types = [w for w in cfg.work_types if w != 'hybrid']
            if 'remote' not in cfg.work_types:
                cfg.work_types.append('remote')
            cfg.notes.append('Hirist marks a job only as work from home or not, and its work-from-home jobs include the '
                             'hybrid ones, so hybrid was read as remote.')
            if set(cfg.work_types) == {'remote', 'onsite'}:
                cfg.work_types = []
        if cfg.job_types:
            cfg.notes.append(f'Hirist states no job type, so employmentType {", ".join(cfg.job_types)} was left out.')
            cfg.job_types = []
        cfg.locations = self._expand_groups(cfg.locations, cfg.notes)
        self.loc = self._location_ids(cfg.locations)
        self._asked = terms(cfg.search_terms)

    def _mend_links(self, raw, cfg) -> None:
        """A pasted Hirist link with several places (loc=3,7) was split at its commas like a list of entries: the parts after
        the first are no search words, and the places they name are added."""
        values = raw.get('searchTerms')
        for item in values if isinstance(values, list) else [values]:
            for line in re.split(r'[;\n\r\t]+', item) if isinstance(item, str) else []:
                head, _, tail = line.strip().partition(',')
                if not tail or self.read_link(head.strip()) is None:
                    continue
                strays = {part.strip().lower() for part in tail.split(',') if part.strip()}
                cfg.search_terms = [t for t in cfg.search_terms if t.lower() not in strays]
                cfg.skipped = [(text, why) for text, why in cfg.skipped if text.strip().lower() not in strays]
                added = [place for place in self.search_link(line.strip())[1] if place not in cfg.locations]
                cfg.locations += added
                if added:
                    cfg.notes.append(f'Read the places of the pasted Hirist link: {", ".join(added)} too.')

    def _link_settings(self, raw) -> dict:
        """Experience and posting period of a Hirist search link pasted in searchTerms."""
        values = raw.get('searchTerms')
        out = {}
        for item in values if isinstance(values, list) else [values] if isinstance(values, str) else []:
            if not isinstance(item, str) or self.read_link(item.strip()) is None:
                continue
            query = parse_qs(urlparse(item.strip() if '://' in item else f'https://{item.strip()}').query)
            low, high = (query.get('minexp') or [''])[0], (query.get('maxexp') or [''])[0]
            if low.isdigit() and high.isdigit() and (int(low) or int(high)):
                out['experience'] = f'{low}-{high}'
            posting = (query.get('posting') or [''])[0].split(',')[0]
            if posting.isdigit() and int(posting) > 0:
                out['posting'] = int(posting)
        return out

    @staticmethod
    def _expand_groups(locations: list[str], notes: list[str]) -> list[str]:
        """Delhi NCR and Metros stand for their cities, as on Hirist's own filter."""
        out = []
        for text in locations:
            key = ' '.join(words(text))
            group = next((g for g, members in GROUPS.items()
                          if key in [' '.join(words(n)) for n in (PLACES[g]['name'], *PLACES[g]['aliases'])]), None)
            if group is None:
                out.append(text)
                continue
            members = [PLACES[group]['name']] + [PLACES[m]['name'] for m in GROUPS[group]]
            notes.append(f'Read "{text}" in location as {", ".join(members)}.')
            out += [m for m in members if m.lower() not in (o.lower() for o in out)]
        return out

    @staticmethod
    def _location_ids(locations: list[str]) -> list[int]:
        """The Hirist location ids every typed place can be in, when each names some and they are few; else none, and
        the places are matched here alone. "Others" and "Any Location" always come too: their jobs say where in text."""
        found: set[int] = set()
        for text in locations:
            wanted = Place(text)
            ids = {i for i, entry in PLACES.items() if i not in (OTHERS, ANYWHERE)
                   and wanted.matches(job_area({'places': [{k: entry[k] for k in ('name', 'city', 'region', 'country')}]}))}
            if not ids:
                return []
            found |= ids
        return sorted(found | {OTHERS, ANYWHERE}) if found and len(found) <= MANY_PLACES else []

    def search_all(self, boards, cfg):
        return [EVERYONE]

    def settings(self):
        return {'hiristLocationIds': self.loc or None}

    # ---------- reading ----------

    def query(self, days: int | None) -> dict:
        """The filters Hirist applies itself; every job is still checked here."""
        params = {}
        if self.loc:
            params['loc'] = ','.join(map(str, self.loc))
        if days and days < DEEPEST:
            params['posting'] = days
        if self.experience:
            low, high = self.experience
            params['minexp'], params['maxexp'] = (low, max(high, 1)) if (low, high) == (0, 0) else (low, min(high, 100))
        return params

    async def _get(self, http, url: str, params: dict) -> dict:
        response = await send(http, 'GET', url, params=params, headers=HEADERS)
        if response.status_code != 200:
            raise BoardError(f'HTTP {response.status_code}')
        try:
            return response.json()
        except ValueError as exc:
            raise BoardError('the answer was not JSON') from exc

    async def _page(self, http, params: dict, number: int, size: int | None = None) -> dict:
        return await self._get(http, FEED, {**params, 'pageNo': number, 'size': size or PAGE})

    async def _total(self, http, params: dict) -> int:
        return int((await self._page(http, params, 0, 1)).get('totalJobs') or 0)

    async def stream(self, http, params: dict):
        """Pages of records of one query, newest first, every one: the newest WINDOW by paging, then the rest through
        slices (slices()), sorted by day."""
        seen: set = set()
        places: set = set()
        total = 0
        for number in range(WINDOW // PAGE):
            data = await self._page(http, params, number)
            rows = [trim(r) for r in data.get('data') or [] if r.get('id') is not None and r['id'] not in seen]
            total = int(data.get('totalJobs') or 0) if number == 0 else total
            seen.update(r['id'] for r in rows)
            places.update(loc.get('id') for r in rows for loc in r.get('locations') or [])
            if rows:
                yield rows
            if not data.get('hasMore') or not data.get('data'):
                return
        if total <= len(seen):
            return
        rest: dict = {}
        sem = asyncio.Semaphore(6)

        async def read(query: dict) -> None:
            for number in range(WINDOW // PAGE):
                async with sem:
                    data = await self._page(http, query, number)
                for record in data.get('data') or []:
                    if record.get('id') is not None and record['id'] not in seen:
                        rest[record['id']] = trim(record)
                if not data.get('hasMore') or not data.get('data'):
                    return

        plan, short = await self.slices(http, params, places)
        await asyncio.gather(*[read(query) for query in plan])
        read_jobs = len(seen) + len(rest)
        if short or read_jobs < total - max(5, total // 1000):
            self.incomplete = True
            if self.cfg is not None:
                self.cfg.notes.append(f'Hirist listed {total:,} jobs for these settings and {read_jobs:,} could be '
                                      'read: one search on Hirist shows 10,000 at most.')
        ordered = sorted(rest.values(), key=lambda r: (r.get('createdTime') or 0, r.get('createdTimeMs') or 0),
                         reverse=True)
        for i in range(0, len(ordered), PAGE):
            yield ordered[i:i + PAGE]

    async def slices(self, http, params: dict, seen_places: set) -> tuple[list[dict], bool]:
        """Queries under WINDOW that together hold every job of params: groups of location ids (Hirist's list and any
        other id the newest jobs named), and a location over WINDOW by ranges of years of experience. short: one could
        not be cut under WINDOW."""
        sem = asyncio.Semaphore(8)

        async def total(query: dict) -> int:
            async with sem:
                return await self._total(http, query)

        ids = ([int(i) for i in params['loc'].split(',')] if params.get('loc')
               else sorted((set(PLACES) | {i for i in seen_places if isinstance(i, int)})))
        counts = dict(zip(ids, await asyncio.gather(*[total({**params, 'loc': str(i)}) for i in ids])))
        plan, group, size, short = [], [], 0, False
        for place_id, count in sorted(counts.items(), key=lambda item: -item[1]):
            if not count:
                continue
            if count > PACK:
                cut, cut_short = await self.by_experience(http, {**params, 'loc': str(place_id)}, sem)
                plan += cut
                short = short or cut_short
                continue
            if size + count > PACK and group:
                plan.append({**params, 'loc': ','.join(map(str, group))})
                group, size = [], 0
            group.append(place_id)
            size += count
        if group:
            plan.append({**params, 'loc': ','.join(map(str, group))})
        return plan, short

    async def by_experience(self, http, params: dict, sem) -> tuple[list[dict], bool]:
        """One query split by ranges of years: a job is in every range its own overlaps, so together they hold all."""
        low, high = int(params.get('minexp') or 0), int(params.get('maxexp') or 100)

        async def split(a: int, b: int) -> tuple[list[dict], bool]:
            query = {**params, 'minexp': a, 'maxexp': b if (a, b) != (0, 0) else 1}    # 0 and 0 is no filter on Hirist
            async with sem:
                count = await self._total(http, query)
            if count <= PACK or a == b:
                return [query], count > WINDOW
            middle = (a + b) // 2 if b < 30 else min(12, (a + b) // 2)
            (left, short_left), (right, short_right) = await asyncio.gather(split(a, middle), split(middle + 1, b))
            return left + right, short_left or short_right

        return await split(low, high)

    def job(self, record: dict) -> dict:
        places = typed_places(record)
        posted, date_only = posted_at(record.get('createdTime'), record.get('createdTimeMs'))
        company = record.get('companyData') or {}
        rating = company.get('ambitionBoxInfo') or {}
        remote = record.get('workFromHome') == 1 or any(
            (loc or {}).get('id') == REMOTE_ID for loc in record.get('locations') or [])
        title = ' '.join((record.get('title') or '').split())
        link = record.get('jobDetailUrl') or JOB_LINK.format(slug=slug(title), id=record.get('id'))
        skills = [t.get('name') for t in record.get('tags') or [] if t.get('name')]
        html = record.get('introText') or None
        return {
            'id': str(record.get('id')),
            'company': ' '.join((company.get('companyName') or '').split()) or None,
            'title': title,
            'location': places[0]['name'] if places else None,
            'otherLocations': [p['name'] for p in places[1:]],
            'places': places,
            'workType': 'remote' if remote else 'onsite',
            **pay(record),
            'postedAt': posted,
            'dateOnly': date_only,
            'jobUrl': link,
            'applyUrl': record.get('applyUrl') or link,
            'experienceMin': record.get('min'),
            'experienceMax': record.get('max'),
            'skills': skills,
            'mandatorySkills': [t.get('name') for t in record.get('tags') or [] if t.get('name') and t.get('isMandatory')],
            'companyRating': rating.get('aggregateRatingOneDecimal') or rating.get('aggregateRating'),
            'companyReviews': rating.get('reviewsCount'),
            'applicants': next((n for n in (record.get('applyCount'), record.get('applicationCount'))
                                if isinstance(n, int) and not isinstance(n, bool)), None),
            'description': text_of(html),
            'descriptionHtml': html,
        }

    def fits(self, job: dict) -> bool:
        """What the engine does not check itself: years of experience, and with searchDescriptions the words in the
        title or the skills (the engine takes the words as found, as from a platform's own search)."""
        if self.cfg is not None and self.cfg.search_descriptions and self.cfg.search_terms:
            if not any(t.in_title(skill_index(job)) for t in self._asked):
                return False
        return True

    async def open(self, http, board):
        if board.key in (EVERYONE.key, LISTED.key):
            return {'everyone': True}
        key = board.key.strip()
        candidate = key.lower() if SLUG.match(key.lower()) else company_slug(key)
        if candidate:
            data = await self._get(http, COMPANY_PAGE, {'slug': candidate})
            found = data.get('data') or {}
            if found.get('id'):
                return {'slug': candidate, 'name': board.name}
        if SLUG.match(key) and '-' in key and not re.search(r'[A-Z ]', key):
            return None                     # a company page link that Hirist does not know
        return {'scan': key}

    async def pages(self, http, session, term, days):
        if session.get('read'):             # every word was matched on the first call
            return
        session['read'] = True
        if session.get('everyone'):
            async for records in self.stream(http, self.query(days)):
                yield [self.job(r) for r in records]
        elif session.get('slug'):
            number = 0
            while True:
                data = await self._get(http, COMPANY_JOBS, {'slug': session['slug'], 'page': number})
                found = data.get('data') or {}
                yield [self.job(r) for r in found.get('jobs') or []]
                if not found.get('hasMore') or not found.get('jobs'):
                    return
                number += 1
        else:
            name = Term(session['scan'])
            board = await self.whole_board(http, self.query(days))
            found = [j for j in board if name.in_title(index(j.get('company')))]
            if not found:
                raise BoardMissing(session['scan'])     # no job on Hirist names this company
            yield found

    async def whole_board(self, http, params: dict) -> list[dict]:
        """Every job of the run's query, read once and shared by the companies that had to be found by name."""
        async with self._board_lock:
            if self._board is None:
                board = []
                async for records in self.stream(http, params):
                    board += [self.job(r) for r in records]
                self._board = board
        return self._board

    async def _detail(self, http, job_id: str) -> tuple[int, dict]:
        response = await send(http, 'GET', DETAIL, params={'jobcode': job_id}, headers=HEADERS)
        if response.status_code in (400, 404):
            return response.status_code, {}
        if response.status_code != 200:
            raise BoardError(f'HTTP {response.status_code}')
        try:
            return 200, response.json().get('data') or {}
        except ValueError as exc:
            raise BoardError('the job details were not JSON') from exc

    async def details(self, http, session, job):
        if job.get('description'):          # a company page's record carries its description
            return job
        status, record = await self._detail(http, job['id'])
        if status != 200 or not record:
            return job
        found = self.job(record)
        return {**job, **{k: v for k, v in found.items() if v not in (None, [], '') and k not in ('postedAt', 'dateOnly')}}

    async def lookup(self, http, session, job_id):
        nobody = {'company': None, 'companySlug': None, 'companyWebsite': None}
        if not JOB_ID.match(job_id):
            return 'not found', nobody
        status, record = await self._detail(http, job_id)
        if status in (400, 404) or not record:
            return 'not found', nobody
        if not is_open(record):
            return 'closed', {'company': ((record.get('companyData') or {}).get('companyName')), 'companySlug': None,
                              'companyWebsite': None}
        return 'open', self.job(record)


def is_open(record: dict) -> bool:
    return (record.get('status') == 1 and record.get('active') == 1 and not record.get('hasExpired')
            and not record.get('permanentlyRemoved'))


def skill_index(job: dict) -> set[str]:
    """The words of the title and of every skill, for a search that reads skills too."""
    out = index(job.get('title'))
    for skill in job.get('skills') or []:
        out |= index(skill)
    return out
