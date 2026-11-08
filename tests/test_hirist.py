"""Hirist: links, places, pay, dates, experience, skills, the 10,000-row window and its slices, companies and link checks,
on records recorded from gladiator.hirist.tech on 2026-11-08 and a fake of its feed."""
import asyncio
import copy
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from ats_jobs import engine
from ats_jobs.http import BoardMissing
from ats_jobs.inputs import parse_input
from ats_jobs.platforms import hirist
from ats_jobs.platforms.base import Board
from ats_jobs.platforms.hirist import (EVERYONE, LISTED, Hirist, experience_fits, pay, posted_at, read_experience, slug,
                                       typed_places)

IST = timezone(timedelta(hours=5, minutes=30))
# Recorded on 2026-11-08 (feed rows, trimmed to what the adapter reads; four tags each)
AZURE = {'id': 1682564, 'title': 'Happiest Minds - Azure Integration lead/Architect', 'min': 10, 'max': 17,
         'createdTime': 1793903400000, 'createdTimeMs': 1793939649591,
         'locations': [{'id': 3, 'name': 'Bangalore'}, {'id': 7, 'name': 'Pune'}], 'createdByAlias': 'Bangalore/Pune',
         'workFromHome': 0, 'minSal': 30, 'maxSal': 40, 'hideSal': 0,
         'tags': [{'id': 1239, 'name': 'Azure', 'isMandatory': True}, {'id': 175, 'name': 'Solution Design', 'isMandatory': True},
                  {'id': 29, 'name': 'Solution Architect', 'isMandatory': True},
                  {'id': 2709, 'name': 'Solution Integration', 'isMandatory': False}],
         'companyData': {'companyName': 'Happiest Minds Technologies',
                         'ambitionBoxInfo': {'aggregateRatingOneDecimal': 3.4, 'aggregateRating': 3.4, 'reviewsCount': 1694}},
         'confidential': 0}
ORACLE = {'id': 1682389, 'title': 'Oracle - Principal Member of Technical Staff - Backend Development', 'min': 8, 'max': 15,
          'createdTime': 1793817000000, 'createdTimeMs': 1793857513770, 'locations': [{'id': 132, 'name': 'Remote'}],
          'createdByAlias': 'Remote', 'workFromHome': 1, 'minSal': 65, 'maxSal': 100, 'hideSal': 0,
          'tags': [{'id': 5, 'name': 'Java', 'isMandatory': True}, {'id': 2267, 'name': 'Distributed Systems', 'isMandatory': True},
                   {'id': 2349, 'name': 'Microservices Architecture', 'isMandatory': True},
                   {'id': 2594, 'name': 'RESTful API', 'isMandatory': False}],
          'companyData': {'companyName': 'Oracle India',
                          'ambitionBoxInfo': {'aggregateRatingOneDecimal': 3.1, 'aggregateRating': 3.1, 'reviewsCount': 7450}},
          'confidential': 0}
OTHERS = {'id': 1682050, 'title': 'AU Small Finance Bank - Java Developer/Lead', 'min': 10, 'max': 14,
          'createdTime': 1793644200000, 'createdTimeMs': 1793707756147, 'locations': [{'id': 100000, 'name': 'Others'}],
          'createdByAlias': 'Jaipur,Rajasthan', 'workFromHome': 0, 'minSal': 30, 'maxSal': 42, 'hideSal': 0,
          'tags': [{'id': 5, 'name': 'Java', 'isMandatory': True}, {'id': 19, 'name': 'OOPS', 'isMandatory': True}],
          'companyData': {'companyName': 'AU SMALL FINANCE BANK Limited', 'ambitionBoxInfo': {}}, 'confidential': 0}
LATE = {'id': 1682504, 'title': 'NatWest - Principal Engineer - .Net/React.js', 'min': 12, 'max': 20,
        'createdTime': 1793817000000, 'createdTimeMs': 1793945315946, 'locations': [{'id': 37, 'name': 'Gurgaon/Gurugram'}],
        'createdByAlias': 'Gurgaon/Gurugram', 'workFromHome': 0, 'minSal': 35, 'maxSal': 50, 'hideSal': 1,
        'tags': [{'id': 1612, 'name': 'Full Stack', 'isMandatory': True}, {'id': 2043, 'name': 'React.js', 'isMandatory': True}],
        'companyData': {'companyName': 'Natwest Digital Services India Pvt Ltd'}, 'confidential': 0}


class Response:
    def __init__(self, status_code, data=None):
        self.status_code, self.data, self.headers, self.text = status_code, data, {}, ''

    def json(self):
        if self.data is None:
            raise ValueError('not JSON')
        return self.data


def day(n: int) -> int:
    """Midnight in India n days ago, as createdTime."""
    today = datetime.now(IST).date() - timedelta(days=n)
    return int(datetime(today.year, today.month, today.day, tzinfo=IST).timestamp() * 1000)


def record(n: int, days_ago: int, place: int = 3, low: int = 2, high: int = 5, title: str = 'Engineer', company: str = 'Acme'):
    return {'id': n, 'title': f'{title} {n}', 'min': low, 'max': high, 'createdTime': day(days_ago),
            'createdTimeMs': day(days_ago) + 4 * 3600_000 + n, 'locations': [{'id': place, 'name': 'x'}],
            'createdByAlias': '', 'workFromHome': 0, 'hideSal': 1, 'tags': [{'id': 1, 'name': 'Python', 'isMandatory': True}],
            'companyData': {'companyName': company}}


class FakeHirist:
    """gladiator.hirist.tech's feed as measured: filters loc (any of), posting (days), minexp/maxexp (overlap; 0 and 0 is
    none), newest first by createdTime, and nothing past a result window of WINDOW rows."""

    def __init__(self, records, window):
        self.records, self.window, self.calls = records, window, []

    async def request(self, method, url, params=None, headers=None):
        params = params or {}
        self.calls.append((url, dict(params)))
        if url == hirist.FEED:
            return Response(200, self.feed(params))
        if url == hirist.DETAIL:
            found = [r for r in self.records if str(r['id']) == str(params['jobcode'])]
            return Response(200, {'data': {**found[0], 'status': 1, 'active': 1}}) if found else Response(404, {})
        raise AssertionError(url)

    def feed(self, params):
        rows = self.records
        if params.get('loc'):
            wanted = {int(i) for i in str(params['loc']).split(',')}
            rows = [r for r in rows if wanted & {loc['id'] for loc in r['locations']}]
        low, high = int(params.get('minexp') or 0), int(params.get('maxexp') or 0)
        if low or high:
            rows = [r for r in rows if r['min'] <= high and r['max'] >= low]
        if params.get('posting'):
            rows = [r for r in rows if r['createdTime'] > day(int(params['posting']))]
        rows = sorted(rows, key=lambda r: -r['createdTime'])
        start, size = int(params['pageNo']) * int(params['size']), int(params['size'])
        if start >= self.window:
            return {'data': [], 'totalJobs': len(rows), 'hasMore': False}
        page = rows[start:min(start + size, self.window)]
        return {'data': copy.deepcopy(page), 'totalJobs': len(rows), 'hasMore': start + size < len(rows)}


def read(platform, http, params):
    async def go():
        out = []
        async for records in platform.stream(http, params):
            out += records
        return out
    return asyncio.run(go())


def small_window():
    return patch.multiple(hirist, WINDOW=20, PAGE=5, PACK=16)


class Links(unittest.TestCase):
    P = Hirist()

    def test_job_links(self):
        for text in ('https://www.hirist.tech/j/gebbs-java-developer-1682327', 'hirist.tech/j/1682327',
                     'https://www.hirist.com/j/gebbs-java-developer-1682327.html', 'https://www.hirist.tech/j/x-1682327?ref=kp'):
            self.assertEqual(self.P.read_link(text), (EVERYONE.key, '1682327'), text)
        self.assertEqual(self.P.read_link('https://www.hirist.tech/j/x'), (None, None))

    def test_company_links_and_other_pages(self):
        self.assertEqual(self.P.read_link('https://www.hirist.tech/company/nagarro-software-careers'), ('nagarro-software', None))
        self.assertEqual(self.P.read_link('https://www.hirist.tech/search/python-jobs?loc=3'), (None, None))
        self.assertEqual(self.P.read_link('https://www.hirist.tech/'), (None, None))
        for text in ('https://www.naukri.com/job-listings-1682327', 'python developer', 'https://example.com/j/1682327'):
            self.assertIsNone(self.P.read_link(text))

    def test_search_links_name_words_and_places(self):
        self.assertEqual(self.P.search_link('https://www.hirist.tech/search/python-developer-jobs?loc=3,7&minexp=2'),
                         (['python developer'], ['Bangalore', 'Pune']))
        self.assertEqual(self.P.search_link('https://www.hirist.tech/k/golang-jobs'), (['golang'], []))
        self.assertEqual(self.P.search_link('https://www.hirist.tech/it-jobs-in-hyderabad'), ([], ['hyderabad']))

    def test_a_pasted_search_link_sets_the_run(self):
        platform = Hirist()
        cfg = parse_input({'searchTerms': ['https://www.hirist.tech/search/python-jobs?loc=3&minexp=2&maxexp=3&posting=7'],
                           'postedWithin': 'any time'}, platform, {})
        self.assertEqual((cfg.search_terms, cfg.locations, cfg.posted_within_days, platform.experience),
                         (['python'], ['Bangalore'], 7.0, (2, 3)))
        self.assertEqual(platform.loc, [0, 3, 100000])

    def test_a_link_with_several_places_stays_whole(self):
        platform = Hirist()
        cfg = parse_input({'searchTerms': ['https://www.hirist.tech/search/python-jobs?loc=3,7&minexp=0&maxexp=0']}, platform, {})
        self.assertEqual((cfg.search_terms, cfg.locations), (['python'], ['Bangalore', 'Pune']))
        self.assertEqual(platform.loc, [0, 3, 7, 100000])

    def test_a_typed_company_is_tried_by_name(self):
        cfg = parse_input({'companies': ['Happiest Minds', 'https://www.hirist.tech/company/unisys-careers']}, Hirist(), {})
        self.assertEqual(cfg.companies, ['Happiest Minds', 'unisys'])

    def test_job_links_come_from_titles(self):
        self.assertEqual(slug('Unisys - Java Technical Architect - Spring Boot/Hibernate'),
                         'unisys-java-technical-architect-spring-boothibernate')
        self.assertEqual(slug('EPAM - Senior Java - React.js/Microservices'), 'epam-senior-java-react-jsmicroservices')


class Jobs(unittest.TestCase):
    P = Hirist()

    def test_a_record_becomes_a_job(self):
        job = self.P.job(AZURE)
        self.assertEqual((job['id'], job['company'], job['location'], job['otherLocations'], job['workType']),
                         ('1682564', 'Happiest Minds Technologies', 'Bangalore', ['Pune'], 'onsite'))
        self.assertEqual((job['salary'], job['salaryMin'], job['salaryMax'], job['salaryCurrency'], job['salaryPeriod']),
                         ('INR 30 - 40 lakh a year', 3_000_000, 4_000_000, 'INR', 'year'))
        self.assertEqual((job['experienceMin'], job['experienceMax'], job['companyRating'], job['companyReviews']),
                         (10, 17, 3.4, 1694))
        self.assertEqual(job['skills'][:2], ['Azure', 'Solution Design'])
        self.assertEqual(job['mandatorySkills'], ['Azure', 'Solution Design', 'Solution Architect'])
        self.assertEqual(job['jobUrl'], 'https://www.hirist.tech/j/happiest-minds-azure-integration-leadarchitect-1682564')
        self.assertEqual(job['places'][0], {'name': 'Bangalore', 'city': 'Bengaluru', 'region': 'Karnataka', 'country': 'India'})

    def test_work_from_home_or_a_remote_place_is_remote(self):
        self.assertEqual(self.P.job(ORACLE)['workType'], 'remote')
        self.assertEqual(self.P.job({**AZURE, 'workFromHome': 1})['workType'], 'remote')

    def test_others_gives_way_to_what_the_recruiter_typed(self):
        self.assertEqual(self.P.job(OTHERS)['location'], 'Jaipur, Rajasthan')
        both = {**OTHERS, 'locations': [{'id': 3, 'name': 'Bangalore'}, {'id': 100000, 'name': 'Others'}],
                'createdByAlias': 'Bangalore/Bengaluru,Karnataka,India'}
        self.assertEqual([p['name'] for p in typed_places(both)], ['Bangalore', 'Bengaluru, Karnataka, India'])
        self.assertEqual([p['name'] for p in typed_places({**OTHERS, 'createdByAlias': ''})], ['Others'])

    def test_the_posting_date(self):
        self.assertEqual(posted_at(AZURE['createdTime'], AZURE['createdTimeMs']), ('2026-11-06T04:34:09Z', False))
        self.assertEqual(posted_at(LATE['createdTime'], LATE['createdTimeMs']), ('2026-11-05', True))   # live a day later
        self.assertEqual(posted_at(None, None), (None, True))

    def test_hidden_pay_is_no_pay(self):
        self.assertIsNone(pay(LATE)['salary'])
        self.assertEqual(pay({'hideSal': 0, 'minSal': 12, 'maxSal': 12})['salary'], 'INR 12 lakh a year')
        self.assertEqual(pay({'hideSal': 0, 'minSal': 7.5, 'maxSal': 0})['salaryMin'], 750_000)


class Settings(unittest.TestCase):
    def test_years_of_experience(self):
        notes = []
        self.assertEqual([read_experience(v, notes) for v in (5, '5', '3-6', '3 to 6 years', '10+', 'fresher', 0, '', None)],
                         [(5, 5), (5, 5), (3, 6), (3, 6), (10, 99), (0, 0), (0, 0), None, None])
        self.assertIsNone(read_experience('senior', notes))
        self.assertIn('Could not read experience "senior"', notes[0])

    def test_experience_overlaps_as_on_hirist(self):
        job = {'experienceMin': 4, 'experienceMax': 8}
        self.assertTrue(all(experience_fits(job, w) for w in [(5, 5), (2, 4), (8, 12), None]))
        self.assertFalse(any(experience_fits(job, w) for w in [(0, 3), (9, 12)]))

    def test_places_go_to_hirist_when_each_names_its_ids(self):
        platform = Hirist()
        parse_input({'location': ['Bengaluru', 'Karnataka']}, platform, {})
        self.assertEqual(platform.loc, [0, 3, 31, 72, 73, 134, 100000])
        for places in (['India'], ['Whitefield'], ['Bangalore', 'Atlantis']):
            platform = Hirist()
            parse_input({'location': places}, platform, {})
            self.assertEqual(platform.loc, [], places)

    def test_delhi_ncr_stands_for_its_cities(self):
        platform = Hirist()
        cfg = parse_input({'location': ['NCR']}, platform, {})
        self.assertEqual(cfg.locations, ['Delhi NCR', 'Delhi', 'Faridabad', 'Ghaziabad', 'Gurgaon/Gurugram', 'Noida',
                                         'Greater Noida'])
        self.assertEqual(platform.loc, [0, 1, 36, 37, 38, 39, 40, 41, 100000])

    def test_what_hirist_does_not_state(self):
        cfg = parse_input({'workType': ['hybrid'], 'employmentType': ['contract']}, Hirist(), {})
        self.assertEqual((cfg.work_types, cfg.job_types), (['remote'], []))
        self.assertTrue(any('hybrid was read as remote' in n for n in cfg.notes))
        self.assertTrue(any('no job type' in n for n in cfg.notes))
        self.assertEqual(parse_input({'workType': ['hybrid', 'onsite']}, Hirist(), {}).work_types, [])

    def test_hirist_is_asked_for_what_it_filters(self):
        platform = Hirist()
        parse_input({'location': ['Pune'], 'experience': '0'}, platform, {})
        self.assertEqual(platform.query(2), {'loc': '0,7,100000', 'posting': 2, 'minexp': 0, 'maxexp': 1})
        self.assertEqual(Hirist().query(200), {})

    def test_skills_count_when_descriptions_are_searched(self):
        platform = Hirist()
        parse_input({'searchTerms': ['react'], 'searchDescriptions': True}, platform, {})
        self.assertTrue(platform.fits(platform.job(LATE)))       # React.js is a skill, and in the title
        self.assertFalse(platform.fits(platform.job(ORACLE)))
        plain = Hirist()
        parse_input({'searchTerms': ['react']}, plain, {})
        self.assertTrue(plain.fits(plain.job(ORACLE)))           # titles are the engine's to match


class Window(unittest.TestCase):
    def test_a_query_under_the_window_is_read_page_by_page(self):
        with small_window():
            http = FakeHirist([record(n, n % 9) for n in range(1, 13)], window=20)
            got = read(Hirist(), http, {})
        self.assertEqual(len(got), 12)
        self.assertEqual(len(http.calls), 3)
        self.assertEqual([r['createdTime'] for r in got], sorted((r['createdTime'] for r in got), reverse=True))

    def test_past_the_window_every_job_comes_once_newest_first(self):
        records = [record(n, n // 2, place=(3 if n % 3 else 4), low=n % 7, high=n % 7 + 3) for n in range(1, 41)]
        with small_window():
            http = FakeHirist(records, window=20)
            platform = Hirist()
            got = read(platform, http, {})
        ids = [r['id'] for r in got]
        self.assertEqual(sorted(ids), list(range(1, 41)))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual([r['createdTime'] for r in got[20:]], sorted((r['createdTime'] for r in got[20:]), reverse=True))
        self.assertFalse(platform.incomplete)
        # Bangalore (26 jobs) is over the window, so it was cut by years of experience
        self.assertTrue(any(c[1].get('loc') == '3' and c[1].get('minexp') is not None for c in http.calls))

    def test_a_slice_that_cannot_be_cut_says_so(self):
        records = [record(n, n // 3, place=3, low=5, high=5) for n in range(1, 31)]
        with small_window():
            http = FakeHirist(records, window=20)
            platform = Hirist()
            platform.cfg = parse_input({}, platform, {})
            got = read(platform, http, {})
        self.assertTrue(platform.incomplete)
        self.assertLess(len(got), 30)
        self.assertTrue(any('could be read' in n for n in platform.cfg.notes))

    def test_a_search_stops_once_nothing_older_can_make_the_cut(self):
        records = [record(n, n // 4) for n in range(1, 61)]
        with small_window():
            http = FakeHirist(records, window=20)
            run = search_run(Hirist(), http, {'maxJobs': 3})
        self.assertEqual(len(run.delivery.rows_out), 3)
        self.assertEqual(len([c for c in http.calls if c[0] == hirist.FEED]), 1)


class Companies(unittest.TestCase):
    def test_a_company_page_or_a_scan(self):
        class Http:
            async def request(self, method, url, params=None, headers=None):
                if url == hirist.COMPANY_PAGE:
                    return Response(200, {'data': {'id': 5505} if params['slug'] == 'unisys' else {}})
                raise AssertionError(url)
        platform = Hirist()
        self.assertEqual(asyncio.run(platform.open(Http(), Board('Unisys', 'Unisys'))), {'slug': 'unisys', 'name': 'Unisys'})
        self.assertEqual(asyncio.run(platform.open(Http(), Board('Happiest Minds', 'Happiest Minds'))), {'scan': 'Happiest Minds'})
        self.assertIsNone(asyncio.run(platform.open(Http(), Board('gone-company', 'gone-company'))))
        self.assertEqual(asyncio.run(platform.open(None, EVERYONE)), {'everyone': True})
        self.assertEqual(asyncio.run(platform.open(None, LISTED)), {'everyone': True})

    def test_a_scanned_name_finds_its_jobs_or_none(self):
        records = [record(1, 0, company='Happiest Minds Technologies'), record(2, 1, company='Acme')]
        with small_window():
            http = FakeHirist(records, window=20)
            platform = Hirist()

            async def pages(name):
                out = []
                async for jobs in platform.pages(http, {'scan': name}, None, None):
                    out += jobs
                return out
            self.assertEqual([j['id'] for j in asyncio.run(pages('happiest minds'))], ['1'])
            with self.assertRaises(BoardMissing):
                asyncio.run(pages('Nobody Ltd'))
        self.assertEqual(len([c for c in http.calls if c[0] == hirist.FEED]), 1)     # the board is read once a run


class Lookup(unittest.TestCase):
    def status(self, answer):
        async def fake(http, job_id):
            return answer
        platform = Hirist()
        platform._detail = fake
        return asyncio.run(platform.lookup(None, None, '1682564'))

    def test_open_closed_and_never_existed(self):
        self.assertEqual(self.status((200, {**AZURE, 'status': 1, 'active': 1}))[0], 'open')
        for gone in ({'status': 0, 'active': 0}, {'status': 1, 'active': 1, 'hasExpired': True},
                     {'status': 1, 'active': 1, 'permanentlyRemoved': True}):
            state, who = self.status((200, {**AZURE, **gone}))
            self.assertEqual((state, who['company']), ('closed', 'Happiest Minds Technologies'))
        self.assertEqual(self.status((404, {})), ('not found', {'company': None, 'companySlug': None, 'companyWebsite': None}))

    def test_details_add_the_description_and_applicants(self):
        async def fake(http, job_id):
            return 200, {**AZURE, 'introText': '<p>Lead <b>Azure</b></p>', 'applyCount': '*****', 'applicationCount': 12,
                         'jobDetailUrl': 'https://www.hirist.tech/j/happiest-minds-azure-1682564'}
        platform = Hirist()
        platform._detail = fake
        job = asyncio.run(platform.details(None, None, platform.job(AZURE)))
        self.assertEqual((job['description'], job['applicants'], job['jobUrl']),
                         ('Lead Azure', 12, 'https://www.hirist.tech/j/happiest-minds-azure-1682564'))


class Delivered:
    def __init__(self):
        self.rows_out, self.rows, self.limit = [], 0, False

    def can_pay(self, event):
        return True

    async def push(self, rows, event):
        self.rows_out += rows
        self.rows += len(rows)
        return len(rows)


def search_run(platform, http, raw):
    @asynccontextmanager
    async def fake_client(actor, connections=64):
        yield http
    boards, updated = platform.boards()
    run = engine.Run(platform, parse_input(raw, platform, engine.name_index(platform, boards)), boards, updated, Delivered())

    async def quiet(message):
        return None
    with patch.object(engine, 'client', fake_client), patch.object(engine, 'safe_status', quiet), \
            patch.object(engine, 'seconds_left_in_run', lambda: None):
        asyncio.run(run.search())
    return run


class Rows(unittest.TestCase):
    def test_rows_carry_hirists_own_fields_and_the_run_its_settings(self):
        records = [record(n, n, low=n, high=n + 2) for n in range(1, 6)]
        with small_window():
            http = FakeHirist(records, window=20)
            run = search_run(Hirist(), http, {'experience': '4', 'maxJobs': 10})
        rows = run.delivery.rows_out
        self.assertEqual([r['jobId'] for r in rows], ['2', '3', '4'])
        self.assertEqual((rows[0]['experienceMin'], rows[0]['experienceMax'], rows[0]['skills']), (2, 4, ['Python']))
        for key in ('employmentType', 'department', 'companySlug', 'companyWebsite', 'expiresAt'):
            self.assertNotIn(key, rows[0])
        self.assertEqual(run.output('done')['settings']['experience'], [4, 4])
        self.assertIn('minexp', http.calls[0][1])
        self.assertTrue(run.message().startswith('Found and saved 3 matching jobs at 1 company on Hirist'))


class CheckedLinks(unittest.TestCase):
    def test_link_rows_have_every_field_of_a_hirist_row(self):
        @asynccontextmanager
        async def fake_client(actor, connections=64):
            yield FakeHirist([AZURE], window=20)

        async def quiet(message):
            return None
        platform = Hirist()
        boards, _ = platform.boards()
        cfg = parse_input({'jobUrls': ['https://www.hirist.tech/j/x-1682564', 'https://www.hirist.tech/j/gone-9999999']},
                          platform, {})
        run = engine.Run(platform, cfg, boards, None, Delivered())
        with patch.object(engine, 'client', fake_client), patch.object(engine, 'safe_status', quiet):
            asyncio.run(run.check_links())
        opened, gone = run.delivery.rows_out
        self.assertEqual((opened['status'], opened['experienceMin'], gone['status'], gone['company']),
                         ('open', 10, 'not found', None))
        self.assertEqual(set(opened) - {'description', 'descriptionHtml', 'city', 'region', 'country', 'countryCode'},
                         set(gone))


if __name__ == '__main__':
    unittest.main()
