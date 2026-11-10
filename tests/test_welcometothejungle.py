"""Welcome to the Jungle: links, jobs, places, the filters sent to Algolia, paging past the 1,000-hit window, the site's
key, details and checked links, on answers of the site's own search index and job API (recorded 2026-11-10, trimmed);
and the two engine hooks it brought (a platform's own job types and place names), which leave other platforms as they
were."""
import asyncio
import gzip
import json
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ats_jobs import engine
from ats_jobs.http import BoardError
from ats_jobs.inputs import parse_input, read_place_words
from ats_jobs.platforms import PLATFORMS
from ats_jobs.platforms import welcometothejungle as wt
from ats_jobs.platforms.base import Board
from ats_jobs.rows import Filters
from tests.stub import Stub

DATA = json.load(gzip.open(Path(__file__).parent / 'data' / 'welcometothejungle_2026-11-10.json.gz', 'rt'))
HITS = {h['slug']: h for h in DATA['hits']}
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
P = wt.WelcomeToTheJungle()
ENV_BODY = ('window.env = {"PUBLIC_NODE_ENV":"production","PUBLIC_ALGOLIA_APPLICATION_ID":"TESTAPP",'
            '"PUBLIC_ALGOLIA_API_KEY_CLIENT":"test-search-key","PUBLIC_WTTJ_API_URL":"https://api.welcometothejungle.com"};')


def hit(start: str) -> dict:
    return next(h for slug, h in HITS.items() if slug.startswith(start))


def routed(raw: dict) -> wt.WelcomeToTheJungle:
    platform = wt.WelcomeToTheJungle()
    cfg = parse_input(raw, platform)
    platform.search_all([], cfg)
    return platform


def passes(raw: dict, job: dict) -> bool:
    platform = routed(raw)
    return Filters(platform.cfg).passes(job, NOW) and platform.fits(job) is True


class Response:
    def __init__(self, status: int, body=None, text: str = ''):
        self.status_code, self.body, self.text = status, body, text or (json.dumps(body) if body is not None else '')

    def json(self):
        if self.body is None:
            raise ValueError('not JSON')
        return self.body


def answering(platform, answers: list):
    """platform.algolia answers from the list in turn (a function of the request may stand in for one); the requests
    are kept on platform.asked."""
    platform.asked = []

    async def fake(http, index, params, path='query'):
        platform.asked.append((index, path, params))
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        return answer(params) if callable(answer) else answer
    platform.algolia = fake
    return platform


def read_all(platform, session, term=None, days=None) -> list[list[dict]]:
    async def go():
        return [p async for p in platform.pages(None, session, term, days)]
    return asyncio.run(go())


def stamped(n: int, at: int, prefix: str = 'j') -> list[dict]:
    """n hits of one second."""
    return [{'objectID': f'{prefix}{at}-{i}', 'slug': f'job-{prefix}{at}-{i}', 'name': 'Data Engineer',
             'organization': {'slug': 'acme', 'name': 'Acme'}, 'published_at_timestamp': at,
             'published_at': datetime.fromtimestamp(at, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
             'offices': [{'city': 'Paris', 'country': 'France', 'country_code': 'FR'}]} for i in range(n)]


class Links(unittest.TestCase):
    def test_a_job_page_names_the_company_and_the_job(self):
        link = 'https://www.welcometothejungle.com/en/companies/docaposte-1/jobs/ingenieur-devops-sre-f-h_sophia-antipolis'
        self.assertEqual(P.read_link(link), ('docaposte-1', 'docaposte-1/ingenieur-devops-sre-f-h_sophia-antipolis'))
        self.assertEqual(P.read_link('welcometothejungle.com/fr/companies/thales/jobs/ingenieur_paris?q=1'),
                         ('thales', 'thales/ingenieur_paris'))
        self.assertEqual(P.read_link('https://www.welcometothejungle.com/companies/monzo/jobs/backend-engineer_london'),
                         ('monzo', 'monzo/backend-engineer_london'))

    def test_a_company_page_names_the_company(self):
        self.assertEqual(P.read_link('https://www.welcometothejungle.com/en/companies/back-market'), ('back-market', None))
        self.assertEqual(P.read_link('https://www.welcometothejungle.com/en/companies/back-market/jobs'), ('back-market', None))

    def test_a_search_page_is_a_search_and_other_sites_are_no_link(self):
        self.assertEqual(P.read_link('https://www.welcometothejungle.com/en/jobs?query=data'), (None, None))
        for text in ('https://app.welcometothejungle.com/jobs/abc', 'https://otta.com/jobs', 'data engineer',
                     'https://www.linkedin.com/jobs/view/1'):
            self.assertIsNone(P.read_link(text))

    def test_a_pasted_search_link_gives_its_words_country_place_contract_and_remote(self):
        link = ('https://www.welcometothejungle.com/fr/jobs?query=data%20analyst&refinementList%5Boffices.country_code%5D'
                '%5B%5D=FR&refinementList%5Bcontract_type%5D%5B%5D=internship&refinementList%5Bremote%5D%5B%5D=fulltime'
                '&aroundQuery=Lyon%2C%20France')
        cfg = parse_input({'searchTerms': [link]}, wt.WelcomeToTheJungle())
        self.assertEqual(cfg.search_terms, ['data analyst'])
        self.assertEqual(cfg.locations, ['FR', 'Lyon'])
        self.assertEqual((cfg.job_types, cfg.work_types), (['intern'], ['remote']))

    def test_the_form_wins_over_a_pasted_link(self):
        link = 'https://www.welcometothejungle.com/en/jobs?query=sales&refinementList[contract_type][]=internship'
        cfg = parse_input({'searchTerms': [link], 'employmentType': ['full-time']}, wt.WelcomeToTheJungle())
        self.assertEqual(cfg.job_types, ['full-time'])


class Jobs(unittest.TestCase):
    def test_a_job_with_yearly_pay_hybrid_and_a_band_of_experience(self):
        job = P.job(hit('ingenieur-plateformes-conteneurisees'))
        self.assertEqual(job['id'], 'metanext/ingenieur-plateformes-conteneurisees-openshift-kubernetes-h-f_courbevoie')
        self.assertEqual((job['company'], job['companySlug'], job['location']),
                         ('Metanext', 'metanext', 'Courbevoie, Ile-de-France, France'))
        self.assertEqual((job['workType'], job['remotePolicy'], job['employmentType'], job['contractType']),
                         ('hybrid', 'partial', 'full-time', 'full_time'))
        self.assertEqual((job['salary'], job['salaryMin'], job['salaryMax'], job['salaryCurrency'], job['salaryPeriod']),
                         ('EUR 50,000 - 60,000 per year', 50000.0, 60000.0, 'EUR', 'year'))
        self.assertEqual((job['experienceMin'], job['experienceMax']), (3.0, 4.0))
        self.assertEqual(job['jobUrl'], 'https://www.welcometothejungle.com/en/companies/metanext/jobs/'
                                        'ingenieur-plateformes-conteneurisees-openshift-kubernetes-h-f_courbevoie')
        self.assertEqual(job['postedAt'], '2026-10-26T00:03:16Z')
        self.assertEqual((job['role'], job['companySize']), ('Software Engineer', 125))
        self.assertIn('SaaS / Cloud Services', job['industry'])

    def test_monthly_and_daily_pay_keep_their_period(self):
        intern = P.job(hit('charge-de-projet-gestion-de-campus'))
        self.assertEqual((intern['salary'], intern['salaryPeriod'], intern['employmentType'], intern['contractDurationMin']),
                         ('EUR 1,000 - 1,200 per month', 'month', 'intern', 6))
        freelance = P.job(hit('product-owner-senior-finance-growth'))
        self.assertEqual((freelance['salary'], freelance['employmentType']), ('EUR 450 - 550 per day', 'contract'))

    def test_offices_with_no_region_get_their_city_s_region(self):
        sf = P.job(hit('security-engineer-red-team'))
        self.assertEqual(sf['location'], 'San Francisco, California, United States')
        self.assertEqual((sf['workType'], sf['remotePolicy'], sf['salary']), ('remote', 'full', 'USD 210,000 - 290,000 per year'))
        london = P.job(hit('security-privacy-lead'))
        self.assertEqual(london['location'], 'London, England, United Kingdom')

    def test_every_office_is_a_place_and_its_own_language_names_find_it(self):
        job = P.job(hit('data-analyst-supply-chain'))
        self.assertEqual(job['location'], 'Lille, Hauts-de-France, France')
        self.assertEqual(job['otherLocations'], ['Casablanca, Casablanca - Settat, Morocco', 'Suresnes, Ile-de-France, France'])
        for place in ('Morocco', 'Nord', 'Hauts-de-Seine', 'Suresnes', 'Lille', 'EMEA'):
            self.assertTrue(passes({'location': [place]}, job), place)
        self.assertFalse(passes({'location': ['Germany']}, job))
        work_study = P.job(hit('alternant-juridique'))
        self.assertTrue(passes({'location': ['Bretagne']}, work_study))
        self.assertTrue(passes({'location': ['Brittany']}, work_study))

    def test_contracts_with_no_job_type_keep_theirs_and_a_title_of_the_site_stays_as_shown(self):
        vie = P.job(hit('vie-intellectual-property'))
        self.assertEqual((vie['employmentType'], vie['contractType'], vie['location']), (None, 'vie', 'New York, United States'))
        odd = P.job(hit('1_london'))
        self.assertEqual((odd['title'], odd['contractType'], odd['employmentType']), ('$1', 'other', None))

    def test_fifteen_years_or_more_has_no_ceiling(self):
        job = P.job(hit('responsable-securite-systeme-information'))
        self.assertEqual((job['experienceMin'], job['experienceMax'], job['education']), (15.0, None, "Bac+5 (master's)"))
        self.assertTrue(passes({'experienceYears': '20'}, job))
        self.assertFalse(passes({'experienceYears': '10'}, job))

    def test_rows_carry_the_site_s_fields_and_leave_out_internal_ones(self):
        job = P.job(hit('ingenieur-plateformes-conteneurisees'))
        row = engine.job_row('welcometothejungle', {}, job, 'now', False)
        row.update({k: job.get(k) for k in P.extra})
        self.assertNotIn('_slugPlace', row)
        self.assertNotIn('_sectors', row)
        self.assertEqual(row['companySlug'], 'metanext')
        self.assertTrue(row['summary'])
        self.assertTrue(row['keyMissions'])

    def test_one_title_at_one_office_is_one_job_unless_the_job_s_own_place_differs(self):
        clisson, vertou = P.job(hit('ambulancier-diplome-d-etat-f-h_clisson')), P.job(hit('ambulancier-diplome-d-etat-f-h_vertou'))
        self.assertEqual(clisson['location'], vertou['location'])
        self.assertNotEqual(P.copy_key(clisson), P.copy_key(vertou))
        repost = dict(clisson, id='keolis-/ambulancier-diplome-d-etat-f-h_clisson_KEO_x1')
        self.assertEqual(P.copy_key(clisson), P.copy_key(repost))
        self.assertEqual([wt.slug_place(s) for s in ('security-engineer-red-team_san-francisco_4hxziyk5',
                                                     '1_erfurt_ND_oMMWe3K', 'bi-engineer-h-f')],
                         ['san-francisco', 'erfurt', ''])


class Inputs(unittest.TestCase):
    def test_work_study_is_a_job_type_here_and_nowhere_else(self):
        for typed in ('apprenticeship', 'work-study', 'Alternance', 'work study'):
            self.assertEqual(parse_input({'employmentType': [typed]}, wt.WelcomeToTheJungle()).job_types, ['apprenticeship'])
        cfg = parse_input({'employmentType': ['apprenticeship']}, Stub())
        self.assertEqual(cfg.job_types, [])
        self.assertTrue(any('has no option apprenticeship' in n for n in cfg.notes))
        self.assertEqual(parse_input({'employmentType': ['CDD', 'stage']}, wt.WelcomeToTheJungle()).job_types,
                         ['temporary', 'intern'])

    def test_every_job_type_ticked_is_every_job(self):
        everything = ['full-time', 'part-time', 'contract', 'intern', 'temporary', 'apprenticeship']
        self.assertEqual(parse_input({'employmentType': everything}, wt.WelcomeToTheJungle()).job_types, [])

    def test_the_search_box_reads_the_site_s_towns_and_regions(self):
        cases = {'data analyst in Lyon': ('data analyst', ['Lyon']), 'comptable paris': ('comptable', ['paris']),
                 'stage marketing ile de france': ('stage marketing', ['ile de france']),
                 'software engineer london': ('software engineer', ['london']),
                 'chef de secteur nord': ('chef de secteur nord', [])}
        for text, (words, places) in cases.items():
            cfg = parse_input({'searchTerms': [text]}, wt.WelcomeToTheJungle())
            self.assertEqual((cfg.search_terms, cfg.locations), ([words], places), text)

    def test_other_platforms_keep_their_place_words(self):
        self.assertEqual(read_place_words('data analyst in Lyon'), ('data analyst in Lyon', [], False))
        self.assertEqual(read_place_words('data analyst in Lyon', Stub()), ('data analyst in Lyon', [], False))
        self.assertEqual(read_place_words('python developer in pune', Stub()), ('python developer', ['pune'], False))

    def test_the_site_s_own_fields(self):
        platform = wt.WelcomeToTheJungle()
        cfg = parse_input({'minSalary': '60k', 'companySize': ['50-250', '> 2000', 'huge'], 'industries': 'fintech, health',
                           'jobLanguage': ['English', 'fr', 'Klingon']}, platform)
        self.assertEqual(platform.min_salary, 60000)
        self.assertEqual(platform.sizes, ['50-250', 'over-2000'])
        self.assertEqual(platform.industries, ['fintech', 'health'])
        self.assertEqual(platform.languages, ['en', 'fr'])
        self.assertTrue(any('companySize has no option huge' in n for n in cfg.notes))
        self.assertTrue(any('jobLanguage has no language "Klingon"' in n for n in cfg.notes))
        self.assertEqual(platform.settings(), {'minSalary': 60000.0, 'companySize': ['50-250', 'over-2000'],
                                               'industries': ['fintech', 'health'], 'jobLanguage': ['en', 'fr']})

    def test_an_unreadable_amount_keeps_every_pay(self):
        platform = wt.WelcomeToTheJungle()
        cfg = parse_input({'minSalary': 'lots'}, platform)
        self.assertIsNone(platform.min_salary)
        self.assertTrue(any('Could not read minSalary' in n for n in cfg.notes))

    def test_worldwide_is_every_country_here(self):
        platform = routed({'location': ['Worldwide']})
        self.assertEqual(platform.cfg.locations, [])
        self.assertFalse(any('open worldwide: marked' in n for n in platform.cfg.notes))
        self.assertTrue(any('as every country' in n for n in platform.cfg.notes))
        self.assertEqual(platform.facets, [])


class Routing(unittest.TestCase):
    def test_countries_and_regions_go_by_country_code(self):
        self.assertEqual(routed({'location': ['United Kingdom']}).facets, [['offices.country_code:GB']])
        self.assertEqual(routed({'location': ['DACH']}).facets,
                         [['offices.country_code:AT', 'offices.country_code:CH', 'offices.country_code:DE']])

    def test_a_town_goes_by_every_spelling_the_index_holds(self):
        facets = routed({'location': ['Saint Denis']}).facets
        self.assertIn('offices.city:Saint-Denis', facets[0])
        paris = routed({'location': ['Paris']}).facets[0]
        self.assertIn('offices.city:Paris', paris)
        self.assertIn('offices.district:Paris', paris)

    def test_a_region_also_asks_for_its_towns_whose_offices_state_none(self):
        california = routed({'location': ['California']}).facets[0]
        self.assertIn('offices.state:California', california)
        self.assertIn('offices.city:San Francisco', california)
        self.assertIn('offices.city:Mountain View, CA', california)
        self.assertIn('offices.state:Brittany', routed({'location': ['Bretagne']}).facets[0])

    def test_a_place_not_on_the_list_is_checked_here_with_a_note(self):
        platform = routed({'location': ['Atlantis']})
        self.assertEqual(platform.facets, [])
        self.assertTrue(platform.places_local)
        self.assertTrue(any('Atlantis is not among the places' in n for n in platform.cfg.notes))
        mixed = routed({'location': ['Paris', 'Atlantis']})
        self.assertEqual(mixed.facets, [])

    def test_work_types_job_types_experience_and_the_site_s_filters(self):
        platform = routed({'workType': ['hybrid'], 'employmentType': ['intern', 'apprenticeship'], 'experienceYears': '3-5',
                           'companySize': ['under-15'], 'minSalary': 50000, 'jobLanguage': ['en'], 'industries': ['fintech']})
        self.assertIn(['remote:partial', 'remote:punctual'], platform.facets)
        self.assertIn(['contract_type:internship', 'contract_type:apprenticeship'], platform.facets)
        self.assertIn(['language:en'], platform.facets)
        self.assertIn(['sectors.reference:fintech-insurtech'], platform.facets)
        self.assertIn(['experience_level_minimum=2', 'experience_level_minimum=3', 'experience_level_minimum=4',
                       'experience_level_minimum=5'], platform.numbers)
        self.assertIn(['organization.nb_employees:1 TO 14'], platform.numbers)
        self.assertIn(['salary_minimum>0', 'salary_maximum>0'], platform.numbers)

    def test_remote_in_some_places_only_is_checked_here(self):
        platform = routed({'location': ['Remote Germany', 'Paris']})
        self.assertFalse(any(g[0].startswith('remote:') for g in platform.facets))

    def test_an_industry_group_finds_every_industry_in_it(self):
        group = routed({'industries': ['banking']}).facets[0]
        self.assertIn('sectors.reference:banking-1', group)
        self.assertIn('sectors.parent_reference:banking-insurance-finance', group)

    def test_an_industry_on_no_list_is_checked_here_with_a_note(self):
        platform = routed({'industries': ['underwater basket weaving']})
        self.assertEqual(platform.facets, [])
        self.assertTrue(any('is not among the industries' in n for n in platform.cfg.notes))

    def test_the_search_asks_titles_with_plurals_folded_and_prefixes(self):
        platform = routed({'searchTerms': ['Sales Managers'], 'postedWithin': '7 days'})
        with patch.object(wt.time, 'time', lambda: 1_794_300_000):
            params = platform.search_params({'orgs': ['acme']}, 'Sales Managers', 8)
        self.assertEqual((params['query'], params['queryType'], params['restrictSearchableAttributes']),
                         ('sale manager', 'prefixAll', ['name']))
        self.assertIn(['organization.slug:acme'], params['facetFilters'])
        self.assertIn(['published_at_timestamp>=1793608800'], params['numericFilters'])
        wider = routed({'searchTerms': ['kubernetes'], 'searchDescriptions': True})
        self.assertEqual(wider.search_params({}, 'kubernetes', None)['restrictSearchableAttributes'],
                         ['name', 'summary', 'profile'])


class Fits(unittest.TestCase):
    def test_pay_counts_a_year_from_its_own_period(self):
        yearly = P.job(hit('ingenieur-plateformes-conteneurisees'))          # EUR 50,000 - 60,000 a year
        monthly = P.job(hit('charge-de-projet-gestion-de-campus'))           # EUR 1,000 - 1,200 a month
        daily = P.job(hit('product-owner-senior-finance-growth'))            # EUR 450 - 550 a day
        none = P.job(hit('responsable-securite-systeme-information'))
        self.assertTrue(passes({'minSalary': 60000}, yearly))
        self.assertFalse(passes({'minSalary': 60001}, yearly))
        self.assertTrue(passes({'minSalary': 14000}, monthly))
        self.assertFalse(passes({'minSalary': 15000}, monthly))
        self.assertTrue(passes({'minSalary': 110000}, daily))                # 550 x 218 = 119,900
        self.assertFalse(passes({'minSalary': 50000}, none))
        platform = routed({'minSalary': 70000})
        self.assertEqual(platform.fits(yearly), 'minSalary')

    def test_company_size_industry_and_language_name_their_filter(self):
        job = P.job(hit('ingenieur-plateformes-conteneurisees'))            # 125 people, SaaS, French
        self.assertEqual(routed({'companySize': ['under-15']}).fits(job), 'companySize')
        self.assertIs(routed({'companySize': ['50-250']}).fits(job), True)
        self.assertEqual(routed({'industries': ['retail']}).fits(job), 'industries')
        self.assertIs(routed({'industries': ['cloud']}).fits(job), True)
        self.assertEqual(routed({'jobLanguage': ['en']}).fits(job), 'jobLanguage')
        self.assertIs(routed({'jobLanguage': ['French']}).fits(job), True)


class Paging(unittest.TestCase):
    def test_one_page_of_fewer_than_a_thousand_is_all(self):
        platform = answering(wt.WelcomeToTheJungle(), [{'hits': stamped(3, 100)}])
        platform.search_all([], parse_input({}, platform))
        pages = read_all(platform, {'orgs': None})
        self.assertEqual([len(p) for p in pages], [3])
        self.assertEqual(len(platform.asked), 1)

    def test_past_the_window_by_posting_time_without_repeating_its_last_second(self):
        first = stamped(998, 500, 'a') + stamped(2, 400, 'b')
        second = stamped(2, 400, 'b') + stamped(5, 300, 'c')
        platform = answering(wt.WelcomeToTheJungle(), [{'hits': first}, {'hits': second}])
        platform.search_all([], parse_input({}, platform))
        pages = read_all(platform, {'orgs': None})
        self.assertEqual([len(p) for p in pages], [1000, 5])
        self.assertEqual(platform.asked[1][2]['numericFilters'][-1], ['published_at_timestamp<=400'])
        ids = [j['id'] for p in pages for j in p]
        self.assertEqual(len(ids), len(set(ids)))

    def test_a_whole_page_of_one_second_is_read_whole_by_browse(self):
        crowded = stamped(1000, 700, 'x')
        rest = stamped(1200, 700, 'x')                       # the second holds 1,200 jobs
        older = stamped(4, 600, 'y')

        def browse(params):
            return {'hits': rest[600:], 'cursor': None} if 'cursor' in params else {'hits': rest[:600], 'cursor': 'next'}
        platform = answering(wt.WelcomeToTheJungle(), [{'hits': crowded}, {'hits': crowded}, browse, browse, {'hits': older}])
        platform.search_all([], parse_input({}, platform))
        pages = read_all(platform, {'orgs': None})
        self.assertEqual([len(p) for p in pages], [1000, 200, 4])
        self.assertEqual([a[1] for a in platform.asked], ['query', 'query', 'browse', 'browse', 'query'])
        self.assertIn('published_at_timestamp%3D700', platform.asked[2][2]['params'])
        self.assertEqual(platform.asked[4][2]['numericFilters'][-1], ['published_at_timestamp<=699'])

    def test_an_empty_answer_ends_the_search(self):
        platform = answering(wt.WelcomeToTheJungle(), [{'hits': []}])
        platform.search_all([], parse_input({}, platform))
        self.assertEqual(read_all(platform, {'orgs': None}), [])


class Keys(unittest.TestCase):
    def test_the_env_gives_the_site_s_application_and_key(self):
        self.assertEqual(wt.read_env(ENV_BODY), ('TESTAPP', 'test-search-key'))
        self.assertIsNone(wt.read_env('<html>challenge</html>'))
        headers = wt.algolia_headers('TESTAPP', 'test-search-key')
        self.assertEqual((headers['Referer'], headers['X-Algolia-Application-Id']), ('https://www.welcometothejungle.com/', 'TESTAPP'))

    def test_a_refused_key_is_read_again_once(self):
        platform = wt.WelcomeToTheJungle()
        calls = []

        async def fake_send(http, method, url, **kwargs):
            calls.append((method, url))
            if url == wt.ENV:
                return Response(200, text=ENV_BODY)
            return Response(403, {'message': 'Invalid Application-ID or API key'}) if len(calls) < 3 else Response(200, {'hits': []})
        with patch.object(wt, 'send', fake_send):
            answer = asyncio.run(platform.algolia(None, wt.NEWEST, {'query': ''}))
        self.assertEqual(answer, {'hits': []})
        self.assertEqual([c[1] == wt.ENV for c in calls], [True, False, True, False])
        self.assertTrue(calls[1][1].startswith('https://testapp-dsn.algolia.net/1/indexes/wttj_jobs_production_en'))

    def test_no_key_from_the_site_fails_the_search_with_a_reason(self):
        async def fake_send(http, method, url, **kwargs):
            return Response(202, text='')
        with patch.object(wt, 'send', fake_send):
            with self.assertRaises(BoardError) as caught:
                asyncio.run(wt.WelcomeToTheJungle().algolia(None, wt.NEWEST, {'query': ''}))
        self.assertIn('did not hand out its search key (HTTP 202)', str(caught.exception))


class Companies(unittest.TestCase):
    def test_a_company_by_name_is_its_slugs_with_open_jobs_or_its_named_pages(self):
        platform = answering(wt.WelcomeToTheJungle(), [DATA['companies_qonto'], DATA['counted_qonto']])
        platform.cfg = parse_input({}, platform)
        self.assertEqual(asyncio.run(platform.find_company(None, 'Qonto')), ['qonto-1', 'qonto'])
        hiring = answering(wt.WelcomeToTheJungle(), [{'hits': [{'name': 'Back Market', 'slug': 'back-market'}]},
                                                     {'facets': {'organization.slug': {'back-market': 31}}}])
        hiring.cfg = parse_input({}, hiring)
        self.assertEqual(asyncio.run(hiring.find_company(None, 'back market')), ['back-market'])

    def test_no_such_company_is_none(self):
        platform = answering(wt.WelcomeToTheJungle(), [{'hits': []}])
        platform.cfg = parse_input({}, platform)
        self.assertEqual(asyncio.run(platform.find_company(None, 'Nowhere Inc')), [])
        self.assertIsNone(asyncio.run(platform.open(None, Board('Nowhere Inc', 'Nowhere Inc'))))

    def test_a_checked_link_s_company_is_not_looked_up(self):
        platform = wt.WelcomeToTheJungle()
        platform.cfg = parse_input({'jobUrls': ['https://www.welcometothejungle.com/en/companies/batch/jobs/x_lyon']}, platform)
        self.assertEqual(asyncio.run(platform.open(None, Board('batch', 'batch'))), {'orgs': ['batch']})


class Details(unittest.TestCase):
    def detail(self, job_id):
        record = DATA['detail'][job_id]

        async def fake(http, wanted):
            return record['status'], record['job']
        return fake

    def test_details_add_the_description_skills_tools_apply_link_and_website(self):
        platform = wt.WelcomeToTheJungle()
        platform.detail = self.detail('docaposte-1/ingenieur-devops-sre-f-h_sophia-antipolis')
        job = {'id': 'docaposte-1/ingenieur-devops-sre-f-h_sophia-antipolis', 'title': 'Ingénieur DevOps / SRE F/H'}
        out = asyncio.run(platform.details(None, None, job))
        self.assertIn('<h2>Preferred experience</h2>', out['descriptionHtml'])
        self.assertTrue(out['description'].startswith("Au poste d'ingénieur DevOps"))
        self.assertEqual(out['skills'][0], 'Teamwork')
        self.assertEqual(out['tools'][:2], ['Grafana', 'Rundeck'])
        self.assertTrue(out['applyUrl'].startswith('https://docaposte-recrute.profils.org/'))
        self.assertEqual(out['companyWebsite'], 'https://www.docaposte.com/')

    def test_a_link_is_open_closed_or_not_found(self):
        platform = wt.WelcomeToTheJungle()
        platform.detail = self.detail('docaposte-1/ingenieur-devops-sre-f-h_sophia-antipolis')
        status, job = asyncio.run(platform.lookup(None, None, 'docaposte-1/ingenieur-devops-sre-f-h_sophia-antipolis'))
        self.assertEqual(status, 'open')
        self.assertEqual((job['id'], job['company'], job['experienceMin'], job['experienceMax'], job['employmentType']),
                         ('docaposte-1/ingenieur-devops-sre-f-h_sophia-antipolis', 'Docaposte', 3.0, 4.0, 'full-time'))
        self.assertEqual(job['location'], "Sophia-Antipolis, Provence-Alpes-Cote d'Azur, France")
        self.assertEqual(job['postedAt'], '2026-11-04T22:02:41Z')
        platform.detail = self.detail('batch/business-developer-germany-bilingual-in-german_lyon')
        status, who = asyncio.run(platform.lookup(None, None, 'batch/business-developer-germany-bilingual-in-german_lyon'))
        self.assertEqual((status, who['companySlug']), ('closed', 'batch'))
        platform.detail = self.detail('agence-france-presse/not-a-job')
        self.assertEqual(asyncio.run(platform.lookup(None, None, 'agence-france-presse/not-a-job')), ('not found', None))


class Run(unittest.TestCase):
    """The engine over this platform, its search answered by recorded hits."""

    def search(self, raw: dict, hits: list[dict]):
        platform = answering(wt.WelcomeToTheJungle(), [{'hits': hits}])

        @asynccontextmanager
        async def no_client(actor, connections=64):
            yield None
        run = engine.Run(platform, parse_input(raw, platform), [], None, Delivered())
        with patch.object(engine, 'client', no_client), patch.object(engine, 'safe_status', _quiet), \
                patch.object(engine, 'seconds_left_in_run', lambda: None):
            asyncio.run(run.search())
        return run

    def test_a_search_delivers_title_matches_newest_first_with_the_site_s_fields(self):
        # Responsable Sécurité ... is in French: security is not a word of its title
        run = self.search({'searchTerms': ['security']}, DATA['hits'])
        rows = run.delivery.pushed
        self.assertEqual([r['title'] for r in rows], ['Security & Privacy Lead', 'Security Engineer, Red Team'])
        self.assertEqual(rows[0]['platform'], 'welcometothejungle')
        self.assertEqual(rows[0]['salaryCurrency'], 'GBP')
        self.assertIn('companyLogo', rows[0])
        self.assertNotIn('team', rows[0])
        self.assertIn('Found and saved 2 matching jobs at 2 companies on Welcome to the Jungle', run.message())

    def test_an_empty_search_names_the_site_s_filter_that_left_jobs_out(self):
        run = self.search({'minSalary': 1_000_000}, DATA['hits'])
        self.assertEqual(run.delivery.pushed, [])
        self.assertIn('pay filter left out the most jobs', run.message())

    def test_the_platform_is_registered(self):
        self.assertIs(PLATFORMS['welcometothejungle'], wt.WelcomeToTheJungle)


class Delivered:
    def __init__(self):
        self.pushed, self.rows, self.limit = [], 0, False

    def can_pay(self, event):
        return True

    async def push(self, rows, event):
        self.pushed += rows
        self.rows += len(rows)
        return len(rows)


async def _quiet(message):
    return None


if __name__ == '__main__':
    unittest.main()
