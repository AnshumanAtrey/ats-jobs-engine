"""HiringCafe: links, the title query, the search state, places, jobs and rows, paging, its own filters, the connection
(Cloudflare's challenges, the proxy, the rate) and checked links, on answers recorded from hiringcafe.com on 2026-11-10
(tests/data/hiringcafe_2026-11-10.json.gz, trimmed to the fields the adapter reads)."""
import asyncio
import base64
import gzip
import json
import math
import unittest
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from ats_jobs import engine
from ats_jobs.http import STATS, BoardError
from ats_jobs.inputs import parse_input
from ats_jobs.platforms import PLATFORMS
from ats_jobs.platforms import hiringcafe as hc
from ats_jobs.rows import Filters, Term, index, job_row
from tests.test_engine import Delivered

DATA = json.loads(gzip.decompress((Path(__file__).parent / 'data' / 'hiringcafe_2026-11-10.json.gz').read_bytes()))
NOW = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)


def hits(name: str) -> list[dict]:
    return [dict(h) for h in DATA[name]['ssrHits']]


def made(raw: dict | None = None) -> hc.HiringCafe:
    """A platform with its input read, as the engine leaves it before the search."""
    platform = hc.HiringCafe()
    cfg = parse_input(raw or {}, platform)
    platform.search_all([], cfg)
    return platform


class Response:
    def __init__(self, status: int = 200, body=None, text: str | None = None, headers: dict | None = None):
        self.status_code, self.body = status, body
        self.text = text if text is not None else (json.dumps(body) if body is not None else '')
        self.headers = headers or {}

    def json(self):
        if self.body is None:
            raise ValueError('not JSON')
        return self.body


def challenge(status: int = 403) -> Response:
    return Response(status, text='<html>Just a moment...</html>', headers={'cf-mitigated': 'challenge'})


class Session:
    """curl_cffi's session, answering from a list in turn (an employer's own list, company=, from its own list, empty
    unless given); the calls made are kept with the proxy each went through."""

    def __init__(self, answers: list, employer: list | None = None):
        self.answers, self.employer, self.calls = list(answers), list(employer or []), []

    async def get(self, url, params=None, proxy=None):
        self.calls.append((url, dict(params or {}), proxy))
        if 'company' in (params or {}):
            answers = self.employer or [Response(200, {'pageProps': {'ssrHits': [], 'ssrIsLastPage': True}})]
        else:
            answers = self.answers
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def searches(self) -> list:
        return [c for c in self.calls if 'company' not in c[1]]


class Proxy:
    def __init__(self):
        self.sessions = []

    async def new_url(self, session_id=None):
        self.sessions.append(session_id)
        return f'http://proxy/{len(self.sessions)}'


def connected(platform: hc.HiringCafe, answers: list, proxy: Proxy | None = None, employer: list | None = None) -> Session:
    """The platform's connection on a fake session, no waiting, and the given proxy (none: no proxy to move to)."""
    session = Session(answers, employer)
    conn = platform.conn
    conn.session = session
    conn.build_id = 'BUILD'

    async def turn(gap):
        return None

    async def instant(seconds):
        return None
    conn.pace.turn = turn
    conn.sleep = instant
    conn.pace.hold = lambda seconds: None

    async def setup(automatic):
        conn.proxy_ready = True
        conn.proxy = proxy if (automatic or conn.proxy_input) else None
        return conn.proxy is not None
    conn.setup_proxy = setup
    return session


def run(coro):
    return asyncio.run(coro)


def read_pages(platform, term=None, days=None, company=None) -> list[list[dict]]:
    async def go():
        session = await platform.open(None, hc.EVERYONE if company is None else hc.Board(company, company))
        return [page async for page in platform.pages(None, session, term, days)]
    return run(go())


class Registry(unittest.TestCase):
    def test_registered_with_its_actor(self):
        self.assertIs(PLATFORMS['hiringcafe'], hc.HiringCafe)
        self.assertEqual(hc.HiringCafe.actor, 'hiringcafe-jobs-search')
        self.assertTrue(hc.HiringCafe.server_search and hc.HiringCafe.newest_first and hc.HiringCafe.server_filters)

    def test_no_company_list_one_feed(self):
        platform = hc.HiringCafe()
        self.assertEqual(platform.boards(), ([], None))
        self.assertEqual(platform.search_all([], parse_input({}, platform)), [hc.EVERYONE])


class Links(unittest.TestCase):
    P = hc.HiringCafe()

    def test_job_links_in_every_form(self):
        for link in ('https://hiringcafe.com/job/python-developer-stafio-ai-noida-uttar-pradesh-bm70sqnccerep4uq',
                     'hiringcafe.com/job/bm70sqnccerep4uq', 'https://hiring.cafe/job/bm70sqnccerep4uq/',
                     'https://hiring.cafe/viewjob/bm70sqnccerep4uq', 'https://www.hiringcafe.com/req/bm70sqnccerep4uq',
                     'https://hiringcafe.com/classic?openJob=bm70sqnccerep4uq', 'bm70sqnccerep4uq'):
            self.assertEqual(self.P.read_link(link), ('hiringcafe', 'bm70sqnccerep4uq'), link)

    def test_company_pages_and_a_search_link_company(self):
        self.assertEqual(self.P.read_link('https://hiring.cafe/org/google.com'), ('google.com', None))
        # company= is base64 of source___board___name___website, as the site's own "View all jobs" link writes it
        self.assertEqual(self.P.read_link('https://hiring.cafe/?company=Z3JuaHNlX19fZ2Vuc3luX19fR2Vuc3luX19fZ2Vuc3luLmFp'
                                          '&searchState=%7B%22sortBy%22%3A%22date%22%7D'), ('Gensyn', None))

    def test_search_links_name_no_job_and_others_are_not_hiringcafe(self):
        self.assertEqual(self.P.read_link('https://hiringcafe.com/classic?searchState=%7B%22searchQuery%22%3A%22nurse%22%7D'),
                         (None, None))
        self.assertIsNone(self.P.read_link('https://example.com/job/bm70sqnccerep4uq'))
        self.assertIsNone(self.P.read_link('python developer'))
        self.assertIsNone(self.P.read_link('developer'))      # 9 letters, no digit: a word, not a job id

    def test_search_link_words_and_places_json_or_base64(self):
        self.assertEqual(self.P.search_link('https://hiringcafe.com/?searchState=eyJzZWFyY2hRdWVyeSI6InNvZnR3YXJlIGVuZ2luZWVyIn0%3D'),
                         (['software engineer'], []))
        state = {'jobTitleQuery': '"data" AND "analyst"', 'locations': [
            {'formatted_address': 'London, England, GB', 'types': ['locality']},
            {'formatted_address': 'India', 'id': 'user_country', 'types': ['country']}]}
        link = 'https://hiring.cafe/?searchState=' + json.dumps(state)
        self.assertEqual(self.P.search_link(link), (['data analyst'], ['London, England, GB']))
        self.assertEqual(self.P.search_link('https://hiring.cafe/?searchState=not-json'), ([], []))

    def test_pasted_search_link_searches_its_words_and_filters(self):
        state = {'searchQuery': 'nurse', 'workplaceTypes': ['Remote'], 'commitmentTypes': ['Part Time'],
                 'dateFetchedPastNDays': 14, 'seniorityLevel': ['Entry Level'], 'roleYoeRange': [0, 3]}
        decoded = made({'searchTerms': ['https://hiringcafe.com/?searchState=' + json.dumps(state)]})
        self.assertEqual(decoded.cfg.search_terms, ['nurse'])
        platform = made({'searchTerms': ['https://hiringcafe.com/?searchState=' + quote(json.dumps(state))]})
        cfg = platform.cfg
        self.assertEqual(cfg.search_terms, ['nurse'])
        self.assertEqual(cfg.work_types, ['remote'])
        self.assertEqual(cfg.job_types, ['part-time'])
        self.assertEqual(cfg.posted_within_days, 7)
        self.assertEqual(cfg.experience, (0.0, 3.0))
        self.assertEqual(platform.options['seniority'], ['entry'])

    def test_form_fields_win_over_a_pasted_link(self):
        state = {'searchQuery': 'nurse', 'workplaceTypes': ['Remote'], 'dateFetchedPastNDays': 14}
        platform = made({'searchTerms': ['https://hiringcafe.com/?searchState=' + json.dumps(state)],
                         'workType': ['onsite'], 'postedWithin': '24 hours'})
        self.assertEqual(platform.cfg.work_types, ['onsite'])
        self.assertEqual(platform.cfg.posted_within_days, 1)


class TitleQuery(unittest.TestCase):
    def test_every_word_in_any_order_with_plurals(self):
        query = hc.HiringCafe.title_query('python developer')
        self.assertTrue(query.startswith('(("python" OR "pythons") AND ("developer" OR "developers"))'))
        self.assertIn('"pythondeveloper"', query)               # the two words joined, as the engine also matches
        self.assertEqual(hc.HiringCafe.title_query('nurses'), '("nurses" OR "nurse")')

    def test_split_words_find_hyphenated_titles(self):
        query = hc.HiringCafe.title_query('frontend')
        self.assertIn('("front" OR "fronts") AND ("end" OR "ends")', query)
        self.assertNotIn('"j" AND', hc.HiringCafe.title_query('java'))               # short words are not split
        long = hc.HiringCafe.title_query('senior principal frontend fullstack software engineer manager director')
        self.assertLessEqual(len(long), hc.MAX_QUERY + 2000)
        self.assertNotIn('"front" OR', long)                                          # too long: no splits

    def test_accents_signs_and_nothing_to_search(self):
        query = hc.HiringCafe.title_query('Développeur')
        self.assertIn('"développeur"', query)
        self.assertIn('"developpeur"', query)
        self.assertTrue(hc.HiringCafe.title_query('c++ developer').startswith('("c++" AND'))
        self.assertEqual(hc.HiringCafe.title_query('!!!'), '')

    def test_every_title_the_engine_accepts_is_one_of_the_readings(self):
        """The server is asked for a superset of the engine's own title match: each title below matches the term in
        the engine, and holds every word of one reading of the query."""
        for term, title in (('frontend developer', 'Front-End Developer'), ('front end developer', 'Frontend Developer'),
                            ('nurses', 'Registered Nurse'), ('developpeur', 'Développeur Python'),
                            ('data analyst', 'Senior Analyst, Data'), ('ecommerce manager', 'E-Commerce Manager')):
            idx = index(title)
            self.assertTrue(Term(term).in_title(idx), (term, title))
            title_words = set(hc.words(title)) | set(hc.WORD.findall(title.casefold()))
            readings = [[part.strip('"() ') for part in clause.split(' AND ')]
                        for clause in hc.HiringCafe.title_query(term).split(') OR (')]
            found = any(all(any(form.strip('"() ') in title_words for form in word.split(' OR ')) for word in reading)
                        for reading in readings)
            self.assertTrue(found, (term, title))


class SearchState(unittest.TestCase):
    def state(self, raw, term=None, days=None, company=None):
        platform = made(raw)
        connected(platform, [Response(200, [])])
        platform.base = run(platform.state_base())
        return platform.state({'company': company}, term, days)

    def test_the_empty_form_newest_everywhere_every_date(self):
        self.assertEqual(self.state({}), {'sortBy': 'date', 'defaultToUserLocation': False, 'dateFetchedPastNDays': -1})

    def test_period_is_a_range_of_utc_days(self):
        with patch.object(hc, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = NOW
            state = self.state({}, days=2)
        self.assertEqual(state['dateFetchedPastNDays'], {'from': '2026-11-08'})

    def test_words_go_to_the_title_or_with_wider_match_to_the_search_box(self):
        self.assertIn('"nurse"', self.state({}, term='nurse')['jobTitleQuery'])
        wide = self.state({'searchTerms': ['nurse'], 'searchDescriptions': True}, term='nurse')
        self.assertIn('"nurse"', wide['searchQuery'])
        self.assertNotIn('jobTitleQuery', wide)

    def test_filters_go_to_hiringcafe(self):
        state = self.state({'workType': ['onsite', 'remote'], 'employmentType': ['intern', 'temporary'],
                            'experienceYears': '2-4', 'seniority': ['entry', 'Mid Level'], 'companySize': ['11-200'],
                            'visaSponsorship': True, 'minSalary': '90k', 'salaryCurrency': 'gbp'})
        self.assertEqual(state['workplaceTypes'], ['Onsite', 'Field', 'Remote'])
        self.assertEqual(state['commitmentTypes'], ['Internship', 'Temporary', 'Seasonal'])
        self.assertEqual(state['roleYoeRange'], [0, 4])
        self.assertTrue(state['excludeIfRoleYoeIsNotSpecified'])
        self.assertEqual(state['seniorityLevel'], ['Entry Level', 'Mid Level'])
        self.assertEqual(state['companySizeRanges'], [[11, 50], [51, 200]])
        self.assertEqual(state['benefitsAndPerks'], ['visa_sponsorship'])
        self.assertEqual((state['maxCompensationLowEnd'], state['currency']['value'], state['frequency']['value']),
                         (90000.0, 'GBP', 'Yearly'))
        self.assertTrue(state['restrictJobsToTransparentSalaries'])

    def test_five_or_more_years_and_twenty_plus(self):
        self.assertEqual(self.state({'experienceYears': '5+'})['roleYoeRange'], [0, 20])
        self.assertEqual(self.state({'experienceYears': '30'})['roleYoeRange'], [0, 20])
        self.assertEqual(self.state({'experienceYears': 'fresher'})['roleYoeRange'], [0, 0])

    def test_a_company_searches_its_name(self):
        self.assertEqual(self.state({}, company='OpenAI')['companyNames'], ['OpenAI'])


class Places(unittest.TestCase):
    def locations(self, typed, answers=None):
        platform = made({'location': typed})
        session = connected(platform, answers or [Response(200, [])])
        found = run(platform.locations(platform.cfg.locations))
        return platform, found, session

    def test_countries_and_whole_continents_need_no_lookup(self):
        platform, found, session = self.locations(['Germany', 'Europe'])
        self.assertEqual(session.calls, [])
        names = [p['formatted_address'] for p in found]
        self.assertEqual(names, ['Germany', 'Europe'])
        self.assertEqual(found[0]['address_components'][0]['short_name'], 'DE')

    def test_a_region_is_its_continents_and_the_rest_by_country(self):
        found = hc.HiringCafe.country_objects(hc.regions.REGIONS['emea'])
        kinds = [(p['types'][0], p['formatted_address']) for p in found]
        self.assertIn(('continent', 'Europe'), kinds)
        self.assertIn(('continent', 'Africa'), kinds)
        self.assertIn(('country', 'United Arab Emirates'), kinds)
        self.assertNotIn(('continent', 'Asia'), kinds)
        oceania = hc.HiringCafe.country_objects(hc.regions.REGIONS['oceania'])
        self.assertEqual(oceania[0]['address_components'][0]['long_name'], 'Australia')     # its label: Australia / Oceania
        dach = [p['address_components'][0]['short_name'] for p in hc.HiringCafe.country_objects(hc.regions.REGIONS['dach'])]
        self.assertEqual(sorted(dach), ['AT', 'CH', 'DE'])

    def test_a_city_by_hiringcafes_own_list(self):
        platform, found, session = self.locations(['London'], [Response(200, DATA['places_london'])])
        self.assertEqual(found[0]['id'], 'xRg1yZQBoEtHp_8UXQ1z')
        self.assertEqual(found[0]['geometry']['location']['lat'], 51.50853)
        self.assertEqual(session.calls[0][1], {'query': 'London'})

    def test_bangalore_is_looked_up_as_bengaluru_too(self):
        bengaluru = [{'label': 'Bengaluru, Karnataka, IN', 'placeDetail': {
            'id': 'b1', 'types': ['locality'], 'formatted_address': 'Bengaluru, Karnataka, IN',
            'address_components': [{'long_name': 'Bengaluru', 'short_name': 'Bengaluru', 'types': ['locality']}],
            'geometry': {'location': {'lat': 12.97, 'lon': 77.59}}}}]
        platform, found, session = self.locations(['Bangalore'], [Response(200, []), Response(200, bengaluru)])
        self.assertEqual([c[1]['query'] for c in session.calls], ['Bangalore', 'Bengaluru'])
        self.assertEqual(found[0]['id'], 'b1')

    def test_a_place_it_does_not_know_is_checked_by_name_on_the_newest_pages(self):
        platform, found, session = self.locations(['Atlantis'])
        self.assertEqual(found, [])
        self.assertTrue(platform.scan)
        self.assertTrue(any('Atlantis' in n for n in platform.cfg.notes))

    def test_worldwide_or_remote_sends_no_place(self):
        for typed in (['Worldwide'], ['Remote']):
            platform, found, session = self.locations(typed)
            self.assertEqual(found, [], typed)


class Jobs(unittest.TestCase):
    def test_a_page_keeps_hiringcafes_order_and_lists_each_job_at_its_employers_newest(self):
        jobs = made().page_jobs(hits('london_swe'))
        self.assertEqual([j['id'] for j in jobs[:3]], ['zh9bygb8rwbzwggw', 'ebyx3hzfrtb5kuj8', 'sw9o89i2sh5l7dtv'])
        openai = [j for j in jobs if j['company'] == 'OpenAI']
        self.assertEqual({j['listedAt'] for j in openai}, {'2026-11-09T22:58:22.319Z'})
        self.assertTrue(all(j['listedAt'] >= j['postedAt'] for j in jobs))

    def test_a_job_with_its_employers_apply_link_and_hiringcafes_reading(self):
        job = next(j for j in made().page_jobs(hits('london_swe')) if j['id'] == 'ebyx3hzfrtb5kuj8')
        self.assertEqual(job['applyUrl'], 'https://jobs.ashbyhq.com/openai/8ddb799f-f0c7-4df8-9665-5016a1f39da3')
        self.assertEqual(job['jobUrl'], 'https://hiringcafe.com/job/ebyx3hzfrtb5kuj8')
        self.assertEqual((job['ats'], job['atsJobId']), ('ashby', '8ddb799f-f0c7-4df8-9665-5016a1f39da3'))
        self.assertEqual((job['company'], job['companyWebsite'], job['companySlug']),
                         ('OpenAI', 'https://openai.com', 'openai.com'))
        self.assertEqual((job['workType'], job['employmentType'], job['seniority']), ('hybrid', 'full-time', 'Senior Level'))
        self.assertEqual((job['salaryMin'], job['salaryMax'], job['salaryCurrency'], job['salaryPeriod']),
                         (266000.0, 445000.0, 'USD', 'year'))
        self.assertEqual(job['salary'], 'USD 266,000 - 445,000 per year')
        self.assertEqual(job['experienceMin'], 7)
        self.assertNotIn('experienceMax', job)
        self.assertEqual(job['places'][0], {'name': 'London, England, United Kingdom', 'country': 'United Kingdom',
                                            'city': 'London', 'region': 'England'})
        self.assertEqual(job['companyHeadquarters'], 'United States')

    def test_pay_only_when_the_employer_published_it(self):
        jobs = made().page_jobs(hits('world_newest'))
        taboola = next(j for j in jobs if j['id'] == '8c6kz7l4kpgwvs77')
        self.assertIsNone(taboola['salary'])
        self.assertIsNone(taboola['salaryCurrency'])          # EUR is only HiringCafe's guess of the currency
        bernstein = next(j for j in jobs if j['id'] == 'dqxxhfz04ljybaem')
        self.assertEqual(bernstein['salaryMin'], 105000.0)
        self.assertEqual(bernstein['employmentType'], 'intern')

    def test_day_only_dates_and_several_places(self):
        jobs = made().page_jobs(hits('python_india'))
        innefu = next(j for j in jobs if j['id'] == '0mkvp7rkln3c64dh')
        self.assertTrue(innefu['dateOnly'])                    # 2026-08-04T00:00:00.000Z: a day
        stafio = next(j for j in jobs if j['id'] == 'bm70sqnccerep4uq')
        self.assertFalse(stafio['dateOnly'])
        nucleus = next(j for j in jobs if j['id'] == '9u06jskp4esuq7mg')
        self.assertEqual(nucleus['otherLocations'], ['Indore, Madhya Pradesh, India', 'Raipur, Chhattisgarh, India',
                                                     'Gurugram, Haryana, India', 'Bangalore, Karnataka, India'])
        nameless = next(j for j in jobs if j['id'] == '9l5ck43mfg3alhu5')
        self.assertTrue(nameless['company'])                   # no AI name: the enriched company's

    def test_a_country_only_job_and_a_folded_duplicates_places(self):
        optum = next(j for j in made().page_jobs(hits('world_newest')) if j['id'] == 'hydj51kidxaoib2m')
        self.assertEqual(optum['places'], [{'name': 'United States', 'country': 'United States'}])
        wtw = next(j for j in made().page_jobs(hits('london_swe')) if j['id'] == 'xawnkzuut6ekwbq7')
        self.assertIn('Cambridge, Cambridgeshire, United Kingdom', wtw['otherLocations'])

    def test_the_type_asked_is_the_one_a_job_of_several_shows(self):
        hit = next(h for h in hits('london_swe') if h['requisition_id'] == 'yzcl0azsxan2sm8t')   # Full Time + Internship
        self.assertEqual(made().job(hit)['employmentType'], 'intern')
        self.assertEqual(made({'employmentType': ['full-time']}).job(hit)['employmentType'], 'full-time')
        self.assertEqual(made().job(hit)['employmentTypes'], ['full-time', 'intern'])

    def test_rows_carry_hiringcafes_fields(self):
        platform = made()
        job = next(j for j in platform.page_jobs(hits('london_swe')) if j['id'] == 'ebyx3hzfrtb5kuj8')
        row = job_row('hiringcafe', {'name': 'HiringCafe', 'slug': 'hiringcafe', 'website': hc.ORIGIN}, dict(job),
                      NOW.isoformat(), False)
        row.update({k: job.get(k) for k in platform.extra})
        for key in ('seniority', 'skills', 'requirementsSummary', 'visaSponsorship', 'companySize', 'ats', 'atsJobId',
                    'salaryMin', 'companyIndustries'):
            self.assertIn(key, row)
        self.assertEqual(row['companySlug'], 'openai.com')
        self.assertEqual(row['company'], 'OpenAI')


class Filtering(unittest.TestCase):
    def test_words_places_and_period_on_recorded_jobs(self):
        platform = made({'searchTerms': ['software engineer'], 'location': ['London'], 'postedWithin': '7 days'})
        f = Filters(platform.cfg)
        kept = [j['title'] for j in platform.page_jobs(hits('london_swe')) if f.passes(j, NOW)]
        self.assertIn('Software Engineer, Guardian', kept)
        self.assertNotIn('Principal Software Engineer - Salesforce', kept)     # September: outside 7 days
        self.assertNotIn('Head of Implementation', kept)                        # no such words

    def test_its_own_filters_name_themselves(self):
        jobs = {j['id']: j for j in made().page_jobs(hits('london_swe'))}
        openai, guardian = jobs['ebyx3hzfrtb5kuj8'], jobs['sw9o89i2sh5l7dtv']
        self.assertEqual(made({'seniority': ['entry']}).fits(openai), 'seniority')
        self.assertIs(made({'seniority': ['senior']}).fits(openai), True)
        self.assertEqual(made({'companySize': ['1-50']}).fits(openai), 'companySize')
        self.assertIs(made({'companySize': ['5001-10000']}).fits(openai), True)
        self.assertEqual(made({'visaSponsorship': True}).fits(openai), 'visaSponsorship')
        self.assertIs(made({'minSalary': 200000}).fits(openai), True)
        self.assertEqual(made({'minSalary': 500000}).fits(openai), 'minSalary')
        self.assertEqual(made({'minSalary': 100000}).fits(guardian), 'minSalary')       # no published pay
        self.assertEqual(made({'minSalary': 100000, 'salaryCurrency': 'GBP'}).fits(openai), 'minSalary')
        nurse = {**guardian, 'salaryCurrency': 'USD', 'salaryPeriod': 'hour', 'payPerYear': (110240.0, 152880.0)}
        self.assertIs(made({'minSalary': 150000}).fits(nurse), True)       # hourly pay compared as a year's

    def test_experience_reads_the_least_years_asked(self):
        jobs = {j['id']: j for j in made().page_jobs(hits('london_swe'))}
        self.assertTrue(Filters(made({'experienceYears': '8'}).cfg).passes(jobs['ebyx3hzfrtb5kuj8'], NOW))
        self.assertFalse(Filters(made({'experienceYears': '3'}).cfg).passes(jobs['ebyx3hzfrtb5kuj8'], NOW))
        self.assertFalse(Filters(made({'experienceYears': '3'}).cfg).passes(jobs['sw9o89i2sh5l7dtv'], NOW))   # none stated

    def test_typed_options_in_everyday_words(self):
        platform = made({'seniority': ['Junior', 'senior level', 'boss'], 'companySize': ['10001+', 'big'],
                         'minSalary': 'lots'})
        self.assertEqual(platform.options['seniority'], ['entry', 'senior'])
        self.assertEqual(platform.options['companySize'], [(10001, None)])
        self.assertNotIn('minSalary', platform.options)
        notes = ' '.join(platform.cfg.notes)
        self.assertIn('boss', notes)
        self.assertIn('big', notes)
        self.assertIn('lots', notes)
        self.assertEqual(made({'seniority': list(hc.SENIORITY)}).options.get('seniority'), None)   # all: no filter


class Paging(unittest.TestCase):
    def page(self, name, total=1000, last=False):
        return Response(200, {'pageProps': {**DATA[name], 'ssrTotalCount': total, 'ssrIsLastPage': last}})

    def test_pages_until_hiringcafe_says_last(self):
        platform = made({'searchTerms': ['engineer']})
        session = connected(platform, [self.page('world_newest'), self.page('london_swe', last=True)])
        pages = read_pages(platform, term='engineer', days=31)
        self.assertEqual([len(p) for p in pages], [20, 28])        # the employers' own lists answered nothing more
        url, params, proxy = session.calls[0]
        self.assertEqual(url, 'https://hiringcafe.com/_next/data/BUILD/classic.json')
        state = json.loads(params['searchState'])
        self.assertEqual((state['sortBy'], state['defaultToUserLocation']), ('date', False))
        self.assertIn('"engineer"', state['jobTitleQuery'])
        self.assertNotIn('page', params)
        self.assertEqual(session.searches()[1][1]['page'], 1)

    def test_an_empty_page_ends_the_search(self):
        platform = made()
        connected(platform, [Response(200, {'pageProps': {'ssrHits': [], 'ssrIsLastPage': True}})])
        self.assertEqual(read_pages(platform), [[]])

    def test_the_250th_page_is_the_last_and_says_so(self):
        platform = made()
        session = connected(platform, [self.page('python_india', total=60000)])
        with patch.object(hc, 'LAST_PAGE', 2):
            pages = read_pages(platform)
        self.assertEqual(len(pages), 3)
        self.assertTrue(platform.incomplete)
        self.assertTrue(any('60,000' in n for n in platform.cfg.notes))
        self.assertEqual(len(session.searches()), 3)

    def test_an_unknown_place_reads_only_the_newest_pages(self):
        platform = made({'location': ['Atlantis']})
        connected(platform, [Response(200, []), self.page('world_newest')])
        pages = read_pages(platform)
        self.assertEqual(len(pages), hc.SCAN_PAGES)
        self.assertTrue(platform.incomplete)

    def test_a_search_error_is_a_board_error(self):
        platform = made()
        connected(platform, [Response(200, {'pageProps': {'ssrError': 'Elasticsearch timed out', 'ssrHits': []}})])
        with self.assertRaises(BoardError):
            read_pages(platform)

    def test_a_new_deployment_is_read_once(self):
        platform = made()
        about = Response(200, text='<script id="__NEXT_DATA__">{"buildId":"NEW"}</script>')
        session = connected(platform, [Response(404, text='<html>404</html>'), about, self.page('world_newest', last=True)])
        pages = read_pages(platform)
        self.assertEqual(len(pages), 1)
        self.assertEqual(session.calls[2][0], 'https://hiringcafe.com/_next/data/NEW/classic.json')


class Employers(unittest.TestCase):
    """HiringCafe shows an employer's 4 newest matches per search; its own list (company=) holds the rest."""

    def employer_page(self, jobs_of: str, extra_ids: list[str], last=True):
        base = next(h for h in hits('world_newest') if h['requisition_id'] == jobs_of)
        made_hits = [{**base, 'requisition_id': i, 'id': f'x_{i}'} for i in extra_ids]
        return Response(200, {'pageProps': {'ssrHits': made_hits, 'ssrIsLastPage': last}})

    def test_the_key_is_the_sites_own_view_all_jobs_link(self):
        hit = next(h for h in hits('london_swe') if h['requisition_id'] == 'ebyx3hzfrtb5kuj8')
        self.assertEqual(base64.b64decode(hc.employer_key(hit)).decode(), 'ashby___openai___OpenAI___openai.com')

    def test_every_match_reads_each_full_employers_list(self):
        platform = made({'maxJobs': 0})
        employer = [self.employer_page('8c6kz7l4kpgwvs77', ['8c6kz7l4kpgwvs77', 'taboolanew000001'])]
        session = connected(platform, [Response(200, {'pageProps': {**DATA['world_newest'], 'ssrIsLastPage': True}})],
                            employer=employer)
        pages = read_pages(platform)
        lists = [c for c in session.calls if 'company' in c[1]]
        self.assertEqual(len(lists), 3)          # Taboola, Bernstein with Societe Generale, Optum: 4 shown each
        ids = [j['id'] for j in pages[0]]
        self.assertEqual(ids.count('8c6kz7l4kpgwvs77'), 1)              # shown once though its list repeats it
        self.assertIn('taboolanew000001', ids)
        added = next(j for j in pages[0] if j['id'] == 'taboolanew000001')
        self.assertEqual(added['listedAt'], '2026-11-10T09:25:22.000Z')   # listed at its employer's newest
        self.assertEqual(ids.index('taboolanew000001'), 4)                 # right after Taboola's 4

    def test_a_list_is_read_only_when_its_hidden_jobs_can_make_the_cut(self):
        platform = made({'maxJobs': 3})
        session = connected(platform, [Response(200, {'pageProps': {**DATA['world_newest'], 'ssrIsLastPage': True}})])
        read_pages(platform)
        self.assertEqual([c for c in session.calls if 'company' in c[1]], [])   # each 4th job is older than the 3rd newest
        platform = made({'maxJobs': 50})
        session = connected(platform, [Response(200, {'pageProps': {**DATA['world_newest'], 'ssrIsLastPage': True}})])
        read_pages(platform)
        self.assertEqual(len([c for c in session.calls if 'company' in c[1]]), 3)   # 20 jobs read: fewer than 50

    def test_a_list_page_with_nothing_new_ends_the_list(self):
        platform = made({'maxJobs': 0})
        repeat = self.employer_page('8c6kz7l4kpgwvs77', ['8c6kz7l4kpgwvs77'], last=False)
        session = connected(platform, [Response(200, {'pageProps': {**DATA['world_newest'], 'ssrIsLastPage': True}})],
                            employer=[repeat])
        read_pages(platform)
        self.assertEqual(len([c for c in session.calls if 'company' in c[1]]), 3)   # one page each, not 25

    def test_a_named_company_keeps_its_own_jobs_and_names_the_related_ones(self):
        platform = made({'companies': ['OpenAI']})
        jobs = [{'company': 'OpenAI', 'companySlug': 'openai.com'}, {'company': 'OpenAI Foundation', 'companySlug': 'openai.org'},
                {'company': 'Paystack', 'companySlug': 'paystack.com'}, {'company': 'Lumanity', 'companySlug': 'lumanity.com'}]
        kept = platform.of_company(jobs, 'OpenAI')
        self.assertEqual([j['company'] for j in kept], ['OpenAI', 'OpenAI Foundation'])
        note = ' '.join(platform.cfg.notes)
        self.assertIn('Paystack', note)
        self.assertIn('Lumanity', note)
        self.assertEqual([j['company'] for j in platform.of_company(jobs, 'paystack.com')], ['Paystack'])

    def test_a_company_page_link_searches_the_name_before_the_dot(self):
        platform = made({'companies': ['https://hiring.cafe/org/stripe.com']})
        self.assertEqual(platform.cfg.companies, ['stripe.com'])
        connected(platform, [Response(200, [])])
        platform.base = run(platform.state_base())
        self.assertEqual(platform.state({'company': 'stripe.com'}, None, None)['companyNames'], ['stripe'])


class Connection(unittest.TestCase):
    def test_python_tls_challenge_is_recognised(self):
        self.assertTrue(hc.challenged(challenge(403)))
        self.assertTrue(hc.challenged(challenge(429)))
        self.assertFalse(hc.challenged(Response(200, {})))
        self.assertFalse(hc.challenged(Response(404, {'notFound': True})))

    def test_a_challenge_moves_the_run_to_a_proxy_once_and_says_so(self):
        platform = made()
        proxy = Proxy()
        session = connected(platform, [challenge(), Response(200, {'ok': 1})], proxy)
        response = run(platform.conn.get('https://hiringcafe.com/x'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([c[2] for c in session.calls], [None, 'http://proxy/1'])
        self.assertTrue(any('residential proxy' in n for n in platform.cfg.notes))

    def test_on_a_proxy_each_challenge_takes_a_fresh_address_until_none_is_left(self):
        platform = made()
        proxy = Proxy()
        session = connected(platform, [challenge()], proxy)
        with self.assertRaises(hc.Refused):
            run(platform.conn.get('https://hiringcafe.com/x'))
        self.assertEqual(len(proxy.sessions), hc.ROTATIONS + 1)
        self.assertEqual(len(set(proxy.sessions)), len(proxy.sessions))

    def test_a_proxy_address_rests_after_its_share_of_calls(self):
        platform = made()
        proxy = Proxy()
        connected(platform, [challenge(), Response(200, {})], proxy)

        async def many():
            for _ in range(hc.CALLS_PER_ADDRESS + 2):
                await platform.conn.get('https://hiringcafe.com/x')
        run(many())
        self.assertEqual(len(proxy.sessions), 2)

    def test_without_a_proxy_a_429_is_waited_out_and_a_403_ends_reading(self):
        platform = made()
        session = connected(platform, [challenge(429), challenge(429), Response(200, {})])
        self.assertEqual(run(platform.conn.get('https://hiringcafe.com/x')).status_code, 200)
        self.assertTrue(any('slow down' in n for n in platform.cfg.notes))
        platform = made()
        connected(platform, [challenge(403)])
        with self.assertRaises(hc.Refused) as caught:
            run(platform.conn.get('https://hiringcafe.com/x'))
        self.assertIn('proxyConfiguration', str(caught.exception))

    def test_a_proxy_the_user_set_is_used_from_the_first_call(self):
        platform = made({'proxyConfiguration': {'useApifyProxy': True, 'apifyProxyGroups': ['RESIDENTIAL']}})
        proxy = Proxy()
        session = connected(platform, [Response(200, {})], proxy)
        run(platform.conn.get('https://hiringcafe.com/x'))
        self.assertEqual(session.calls[0][2], 'http://proxy/1')
        self.assertNotIn('proxyConfiguration', platform.settings())

    def test_network_errors_are_tried_again_then_a_board_error(self):
        platform = made()
        connected(platform, [OSError('reset'), Response(200, {})])
        self.assertEqual(run(platform.conn.get('https://hiringcafe.com/x')).status_code, 200)
        platform = made()
        connected(platform, [OSError('reset')])
        with self.assertRaises(BoardError):
            run(platform.conn.get('https://hiringcafe.com/x'))


class DetailsAndLinks(unittest.TestCase):
    def test_details_add_the_description(self):
        platform = made({'includeDetails': True})
        session = connected(platform, [Response(200, DATA['description'])])
        job = made().job(hits('python_india')[0])
        out = run(platform.details(None, None, job))
        self.assertIn('Python Developer', out['description'])
        self.assertTrue(out['descriptionHtml'].startswith('<p>'))
        self.assertEqual(session.calls[0][1], {'requisitionId': 'bm70sqnccerep4uq'})

    def test_a_job_gone_since_the_search_keeps_its_row(self):
        platform = made({'includeDetails': True})
        connected(platform, [Response(404, {'error': 'not found'})])
        job = made().job(hits('python_india')[0])
        self.assertEqual(run(platform.details(None, None, job)), job)

    def test_checked_links_open_closed_not_found(self):
        platform = made()
        moved = {'pageProps': {'__N_REDIRECT': '/job/python-developer-stafio-ai-noida-uttar-pradesh-bm70sqnccerep4uq',
                               '__N_REDIRECT_STATUS': 308}, '__N_SSP': True}
        session = connected(platform, [Response(200, moved), Response(200, {'pageProps': DATA['job_page']})])
        status, job = run(platform.lookup(None, None, 'bm70sqnccerep4uq'))
        self.assertEqual(session.calls[1][0], 'https://hiringcafe.com/_next/data/BUILD/job/'
                                              'python-developer-stafio-ai-noida-uttar-pradesh-bm70sqnccerep4uq.json')
        self.assertEqual(status, 'open')
        self.assertEqual(job['title'], 'Python Developer')
        self.assertIn('artificial intelligence', job['description'])
        closed = {'job': {**DATA['job_page']['job'], 'is_expired': True}}
        connected(platform, [Response(200, {'pageProps': closed})])
        self.assertEqual(run(platform.lookup(None, None, 'bm70sqnccerep4uq'))[0], 'closed')
        connected(platform, [Response(404, {'notFound': True})])
        self.assertEqual(run(platform.lookup(None, None, 'zzzzzzzzzzzzzzz1'))[0], 'not found')


class EngineRun(unittest.TestCase):
    """A whole search through the engine on recorded pages: newest first, filters, rows and events."""

    def go(self, raw, answers):
        platform = hc.HiringCafe()
        cfg = parse_input(raw, platform)
        connected(platform, answers)
        delivery = Delivered()
        run_ = engine.Run(platform, cfg, [], None, delivery)

        async def no_client(*a, **k):
            return None

        class NoClient:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *exc):
                return False

        async def quiet(message):
            return None
        with patch.object(engine, 'client', lambda *a, **k: NoClient()), patch.object(engine, 'safe_status', quiet), \
                patch.object(engine, 'seconds_left_in_run', lambda: None), \
                patch.object(engine, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = NOW
            asyncio.run(run_.search())
        return run_, delivery

    def page(self, name, last=False):
        return Response(200, {'pageProps': {**DATA[name], 'ssrIsLastPage': last}})

    def test_newest_first_one_feed_and_a_message(self):
        run_, delivery = self.go({'maxJobs': 5, 'postedWithin': 'any time'}, [self.page('world_newest', last=True)])
        self.assertEqual(delivery.rows, 5)
        ids = [i for i, _ in delivery.pushed]
        self.assertEqual(ids[0], '8c6kz7l4kpgwvs77')          # 09:25:22, the newest on the page
        self.assertEqual({e for _, e in delivery.pushed}, {'job'})
        self.assertIn('on HiringCafe', run_.message())

    def test_a_search_keeps_only_what_matches(self):
        run_, delivery = self.go({'searchTerms': ['software engineer'], 'location': ['United Kingdom'],
                                  'postedWithin': 'any time', 'maxJobs': 50},
                                 [self.page('london_swe', last=True)])
        titles = [j for j, _ in delivery.pushed]
        self.assertGreater(len(titles), 5)
        self.assertNotIn('1tdzuchkykh7flsg', titles)            # Head of Implementation: not the words

    def test_stops_paging_once_older_than_the_cut(self):
        """listedAt is an employer's newest job: a page whose last employer is older than the newest maxJobs held
        ends the search, even when its jobs go back weeks."""
        run_, delivery = self.go({'maxJobs': 3, 'postedWithin': 'any time'},
                                 [self.page('world_newest'), self.page('london_swe')])
        self.assertEqual(delivery.rows, 3)
        self.assertEqual(len(run_.platform.conn.session.searches()), 1)  # its last employer (09:19) is past the cut
        run_, delivery = self.go({'maxJobs': 30, 'postedWithin': 'any time'},
                                 [self.page('world_newest'), self.page('london_swe', last=True)])
        self.assertEqual(len(run_.platform.conn.session.searches()), 2)  # 20 jobs on the first page: one more page


if __name__ == '__main__':
    unittest.main()
