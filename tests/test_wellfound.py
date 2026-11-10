"""Wellfound: links, pay and equity, remote settings, roles and places, reading every page of a search, job pages and
link checks, on a search page and job pages recorded from wellfound.com on 2026-11-10 and a fake of its search pages."""
import asyncio
import gzip
import json
import unittest
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path
from unittest.mock import patch

from ats_jobs import engine
from ats_jobs.http import BoardMissing
from ats_jobs.inputs import parse_input
from ats_jobs.platforms import wellfound
from ats_jobs.platforms.wellfound import (EVERYONE, EVERYWHERE, Wellfound, compensation, facts, funding, policy, read_page,
                                          work_type)

DATA = Path(__file__).parent / 'data'
NOW = 1794300000                     # 2026-11-10T09:20:00Z


def recorded_search() -> dict:
    """location/india?page=2, recorded 2026-11-10 (descriptions cut to 300 characters): 20 startups, 43 jobs."""
    with gzip.open(DATA / 'wellfound_india_p2_2026-11-10.json.gz', 'rt', encoding='utf-8') as f:
        return json.load(f)


def recorded_job(job_id: str) -> str:
    """A job page recorded 2026-11-10, cut to the markup, header, fact grid (no hiring contact) and funding box."""
    with gzip.open(DATA / f'wellfound_job_{job_id}_2026-11-10.html.gz', 'rt', encoding='utf-8') as f:
        return f.read()


class Response:
    def __init__(self, status_code, text='', headers=None):
        self.status_code, self.text, self.headers = status_code, text, headers or {}


def html_of(data: dict) -> str:
    return ('<html><script id="__NEXT_DATA__" type="application/json" crossorigin="anonymous">'
            + json.dumps({'props': {'pageProps': {'apolloState': {'data': data}}}}) + '</script></html>')


def listing(n: int, hours_ago: float, title: str | None = None, places=('San Francisco',), remote=False,
            accepted=(), kind=None, flexible=None, pay='$150k – $200k • 0.1% – 0.5%', years=None, job_type='full-time'):
    """One search record as the site's search pages carry it; its title is Software Engineer <n> unless given, so jobs of
    one startup are not copies of each other."""
    title = title or f'Software Engineer {n}'
    return {'id': str(n), 'slug': title.lower().replace(' ', '-'), 'title': title, 'liveStartAt': NOW - int(hours_ago * 3600),
            'locationNames': list(places), 'remote': remote, 'acceptedRemoteLocationNames': list(accepted),
            'remoteConfig': {'kind': kind, 'wfhFlexible': flexible} if kind else None, 'compensation': pay,
            'jobType': job_type, 'yearsExperienceMin': years, 'yearsExperienceMax': None,
            'primaryRoleTitle': 'Software Engineer', 'description': f'About the job {n}: Python and SQL.'}


class FakeSite:
    """wellfound.com's search pages as measured: a search path paged with ?page=N, 20 startups a page and at most 3
    jobs of each, 303 past the last page, 404 for a search it does not have; job pages by their key."""

    def __init__(self, searches: dict, job_pages: dict | None = None, refuse_after: int | None = None, place=None):
        self.searches, self.job_pages, self.refuse_after, self.place = searches, job_pages or {}, refuse_after, place
        self.calls = []

    async def request(self, method, url, follow_redirects=False, headers=None, params=None):
        path = url.split('wellfound.com/', 1)[1]
        self.calls.append(path)
        if self.refuse_after is not None and len(self.calls) > self.refuse_after:
            return Response(403, 'Security Check', {'cf-mitigated': 'challenge'})
        if path.startswith('jobs/'):
            status, page = self.job_pages.get(path[5:], (404, 'Page not found'))
            return Response(status, page)
        base, _, number = path.partition('?page=')
        if base not in self.searches:
            return Response(404, 'Page not found')
        return self.page(base, int(number or 1))

    def page(self, base: str, number: int):
        startups = self.searches[base]               # [(startup name, [listing, ...]), ...]
        pages = max(1, -(-len(startups) // 20))
        if number > pages:
            return Response(303, '', {'location': f'https://wellfound.com/{base}'})
        data, refs = {}, []
        for i, (name, jobs) in enumerate(startups[(number - 1) * 20:number * 20]):
            sid = f'{abs(hash(name)) % 10**7}'
            shown = []
            for job in jobs[:3]:
                record = dict(job)
                if record.get('remoteConfig'):
                    data[f'JobListingRemoteConfig:{record["id"]}'] = {'__typename': 'JobListingRemoteConfig',
                                                                     **record['remoteConfig']}
                    record['remoteConfig'] = {'__ref': f'JobListingRemoteConfig:{record["id"]}'}
                data[f'JobListingSearchResult:{record["id"]}'] = record
                shown.append({'__ref': f'JobListingSearchResult:{record["id"]}'})
            data[f'Badge:COMPANY_STAGE-{sid}'] = {'name': 'COMPANY_STAGE_BADGE', 'label': 'Early Stage', 'data': 'early_stage'}
            data[f'Badge:YC-{sid}'] = {'name': 'YC_BADGE', 'label': 'YC Funded'}
            data[f'StartupResult:{sid}'] = {'id': sid, 'name': name, 'slug': name.lower().replace(' ', '-'),
                                            'companySize': 'SIZE_11_50', 'highConcept': f'{name} does things',
                                            'logoUrl': None, 'highlightedJobListings': shown,
                                            'badges': [{'__ref': f'Badge:COMPANY_STAGE-{sid}'}, {'__ref': f'Badge:YC-{sid}'}]}
            refs.append({'__ref': f'StartupResult:{sid}'})
        total = sum(len(jobs) for _, jobs in startups)
        data['ROOT_QUERY'] = {'talent': {f'seoLandingPageJobSearchResults({{"page":{number}}})': {
            'totalJobCount': total, 'totalStartupCount': len(startups), 'perPage': 20, 'pageCount': pages,
            'startups': refs}}}
        if self.place:
            data['NewTag:1'] = {'displayName': self.place}
        return Response(200, html_of(data))


class Delivered:
    def __init__(self):
        self.rows_out, self.rows, self.limit = [], 0, False

    def can_pay(self, event):
        return True

    async def push(self, rows, event):
        self.rows_out += rows
        self.rows += len(rows)
        return len(rows)


def run_of(platform, http, raw, links=False):
    @asynccontextmanager
    async def fake_client(actor, connections=64):
        yield http
    boards, updated = platform.boards()
    run = engine.Run(platform, parse_input(raw, platform, engine.name_index(platform, boards)), boards, updated, Delivered())

    async def quiet(message):
        return None
    with patch.object(engine, 'client', fake_client), patch.object(engine, 'safe_status', quiet), \
            patch.object(engine, 'seconds_left_in_run', lambda: None), patch.object(wellfound, 'RATE', 10_000.0), \
            patch.object(wellfound, 'BURST', 10_000):
        platform.pace = wellfound.Pace()
        asyncio.run(run.check_links() if links else run.search())
    return run


def plan_of(raw, site):
    platform = Wellfound()
    boards, _ = platform.boards()
    cfg = parse_input(raw, platform, engine.name_index(platform, boards))
    platform.search_all(boards, cfg)
    with patch.object(wellfound, 'RATE', 10_000.0), patch.object(wellfound, 'BURST', 10_000):
        platform.pace = wellfound.Pace()
        queries = asyncio.run(platform.plan(site))
    return platform, cfg, queries


def site_with(*paths, place=None) -> FakeSite:
    return FakeSite({p: [('Acme', [listing(1, 1)])] for p in paths}, place=place)


class Links(unittest.TestCase):
    platform = Wellfound()

    def test_a_job_link_gives_the_key_its_page_needs(self):
        read = self.platform.read_link
        self.assertEqual(read('https://wellfound.com/jobs/4965854-software-engineer'), ('*', '4965854-software-engineer'))
        self.assertEqual(read('wellfound.com/jobs/4965854-Software-Engineer?utm=x.'), ('*', '4965854-software-engineer'))
        self.assertEqual(read('https://angel.co/company/calm/jobs/4889905-sdet'), ('*', '4889905-sdet'))
        self.assertEqual(read('https://www.wellfound.com/company/methodfi/jobs/4965854-software-engineer'),
                         ('*', '4965854-software-engineer'))

    def test_company_and_search_pages(self):
        read = self.platform.read_link
        self.assertEqual(read('https://wellfound.com/company/calm'), ('calm', None))
        self.assertEqual(read('wellfound.com/company/calm/jobs'), ('calm', None))
        self.assertEqual(read('https://wellfound.com/role/l/software-engineer/san-francisco'), (None, None))
        self.assertIsNone(read('https://example.com/jobs/1-x'))
        self.assertIsNone(read('python developer'))

    def test_search_links_name_words_and_places(self):
        link = self.platform.search_link
        self.assertEqual(link('https://wellfound.com/role/software-engineer'), (['Software Engineer'], []))
        self.assertEqual(link('https://wellfound.com/role/l/python-developer/bangalore'), (['Python Developer'], ['Bengaluru']))
        self.assertEqual(link('wellfound.com/role/r/data-scientist'), (['Data Scientist'], ['Remote']))
        self.assertEqual(link('https://wellfound.com/location/india'), ([], ['India']))
        self.assertEqual(link('https://wellfound.com/remote'), ([], ['Remote']))
        self.assertEqual(link('https://wellfound.com/role/ui-designer'), (['UI Designer'], []))

    def test_a_pasted_search_link_sets_the_run(self):
        cfg = parse_input({'searchTerms': ['https://wellfound.com/role/l/software-engineer/san-francisco']}, self.platform, {})
        self.assertEqual((cfg.search_terms, cfg.locations), (['Software Engineer'], ['San Francisco']))
        cfg = parse_input({'searchTerms': ['https://wellfound.com/jobs/4965854-software-engineer']}, self.platform, {})
        self.assertEqual(cfg.job_urls, [('*', '4965854-software-engineer', 'https://wellfound.com/jobs/4965854-software-engineer')])


class Pay(unittest.TestCase):
    def test_the_texts_wellfound_shows(self):
        def pay(text):
            out = compensation(text)
            return (out['salaryMin'], out['salaryMax'], out['salaryCurrency'], out['offersEquity'], out['equityMin'],
                    out['equityMax'])
        self.assertEqual(pay('$175k – $215k • 0.5% – 1%'), (175000, 215000, 'USD', True, 0.5, 1.0))
        self.assertEqual(pay('₹6L – ₹9L • No equity'), (600000, 900000, 'INR', False, None, None))
        self.assertEqual(pay('₹80,000 – ₹1.2L'), (80000, 120000, 'INR', None, None, None))
        self.assertEqual(pay('₹5L – ₹1 cr'), (500000, 10000000, 'INR', None, None, None))
        self.assertEqual(pay('$90k – $120k CAD • No equity'), (90000, 120000, 'CAD', False, None, None))
        self.assertEqual(pay('Up to $90k'), (None, 90000, 'USD', None, None, None))
        self.assertEqual(pay('د.إ120k – د.إ240k'), (120000, 240000, 'AED', None, None, None))
        self.assertEqual(pay('ج.م900k – ج.م1.2M'), (900000, 1200000, 'EGP', None, None, None))   # 2026-11-10 board
        self.assertEqual(pay('0.1% – 0.8%'), (None, None, None, True, 0.1, 0.8))
        self.assertEqual(pay('No equity'), (None, None, None, False, None, None))
        self.assertEqual(pay(None), (None, None, None, None, None, None))
        self.assertEqual(compensation('$175k – $215k')['salary'], 'USD 175,000 - 215,000 per year')

    def test_a_sign_several_currencies_share_names_none(self):
        out = compensation('¥100k – ¥300k • No equity')
        self.assertEqual((out['salaryMin'], out['salaryCurrency'], out['salary']), (100000, None, '¥ 100,000 - 300,000 per year'))

    def test_every_pay_text_of_the_recorded_page_reads(self):
        data = recorded_search()
        texts = [v['compensation'] for k, v in data.items() if k.startswith('JobListingSearchResult:') and v.get('compensation')]
        self.assertGreater(len(texts), 20)
        for text in texts:
            pay_part = text.split('•')[0]
            if any(c.isdigit() for c in pay_part) and '%' not in pay_part:
                self.assertIsNotNone(compensation(text)['salaryMax'], text)


class Jobs(unittest.TestCase):
    def test_the_remote_settings_as_the_job_pages_word_them(self):
        # measured on job pages 2026-11-10: kind, work-from-home flexibility -> the page's Remote Work Policy
        self.assertEqual((work_type(True, 'REMOTE', False), policy('REMOTE', False)), ('remote', 'Remote only'))
        self.assertEqual((work_type(True, 'ONSITE_OR_REMOTE', False), policy('ONSITE_OR_REMOTE', False)),
                         ('remote', 'Onsite or remote'))
        self.assertEqual((work_type(False, 'ONSITE', True), policy('ONSITE', True)), ('hybrid', 'In office - WFH flexibility'))
        self.assertEqual((work_type(True, 'ONSITE', True), policy('ONSITE', True)), ('hybrid', 'In office - WFH flexibility'))
        self.assertEqual((work_type(False, 'ONSITE', False), policy('ONSITE', False)), ('onsite', 'In office'))
        self.assertEqual((work_type(True, None, None), policy(None, None)), ('remote', None))
        self.assertEqual((work_type(False, None, None), policy(None, None)), ('onsite', None))

    def test_a_recorded_record_becomes_a_job(self):
        data = recorded_search()
        platform = Wellfound()
        query = wellfound.Query('location/india', [{'name': 'India'}, {'name': 'India', 'country': 'India'}], data)
        jobs = platform.jobs_of(query, data)
        self.assertEqual(len(jobs), 43)
        self.assertTrue(all(j['jobUrl'].startswith('https://wellfound.com/jobs/' + j['id'] + '-') for j in jobs))
        self.assertTrue(all(j['postedAt'].endswith('Z') and j['company'] and j['title'] for j in jobs))
        self.assertTrue(all({'name': 'India', 'country': 'India'} in j['places'] for j in jobs))
        self.assertTrue(all(j['companySize'] in (None, '1-10', '11-50', '51-200', '201-500', '501-1000', '1001-5000', '5000+')
                            for j in jobs))
        self.assertTrue(all(j['workType'] in ('remote', 'hybrid', 'onsite') for j in jobs))
        remote = [j for j in jobs if j['workType'] == 'remote']
        self.assertTrue(remote and all(j['remoteIn'] for j in remote))
        self.assertTrue(all(j[k] is None for j in jobs for k in Wellfound.DETAILS_ONLY))
        self.assertEqual(platform.jobs_of(query, data), [])        # each job once

    def test_a_remote_job_naming_no_place_hires_everywhere(self):
        platform = Wellfound()
        data = {'S:1': {'name': 'Acme', 'slug': 'acme'}}
        job = platform.job(listing(1, 2, places=(), remote=True), data['S:1'], data, [])
        self.assertEqual((job['location'], job['remoteIn'], job['workType']), ('Remote', ['Everywhere'], 'remote'))
        from ats_jobs.rows import Place, job_area
        self.assertTrue(Place('India').matches(job_area(job)))


class Filters(unittest.TestCase):
    def test_minimum_pay_as_people_type_it(self):
        notes = []
        read = lambda value: wellfound.read_pay(value, notes.append)
        self.assertEqual(read('120k USD'), (120000, 'USD'))
        self.assertEqual(read('20L INR'), (2000000, 'INR'))
        self.assertEqual(read('€60k'), (60000, 'EUR'))
        self.assertEqual(read('$150,000'), (150000, 'USD'))
        self.assertEqual(notes, [])
        self.assertEqual(read(120000), (120000, None))
        self.assertEqual(read('120000'), (120000, 'USD'))
        self.assertIn('US dollars', notes[-1])
        self.assertIsNone(read('lots'))
        self.assertIn('Could not read minSalary', notes[-1])
        self.assertIsNone(read(''))

    def test_company_stages_as_people_type_them(self):
        notes = []
        self.assertEqual(wellfound.read_stages(['Early Stage', 'seed', 'growth'], notes.append), ['early', 'growth'])
        self.assertEqual(wellfound.read_stages('late, IPO', notes.append), ['scale', 'public'])
        self.assertEqual(wellfound.read_stages(['early', 'growth', 'scale', 'public'], notes.append), [])
        self.assertEqual(wellfound.read_stages(['unicorn'], notes.append), [])
        self.assertIn('companyStage has no option unicorn', notes[-1])

    def test_pay_and_stage_are_checked_on_every_job_and_named_when_they_leave_all_out(self):
        jobs = [listing(1, 2, pay='$150k – $200k'), listing(2, 3, pay='₹30L – ₹40L'), listing(3, 4, pay='No equity'),
                listing(4, 5, pay='$90k – $110k')]
        startups = [('Acme', jobs[:2]), ('Bolt', jobs[2:])]
        site = FakeSite({'role/software-engineer': startups})
        run = run_of(Wellfound(), site, {'searchTerms': ['software engineer'], 'minSalary': '120k USD'})
        self.assertEqual([r['jobId'] for r in run.delivery.rows_out], ['1'])
        self.assertEqual(run.output('done')['settings']['minSalary'], {'amount': 120000.0, 'currency': 'USD'})
        site = FakeSite({'role/software-engineer': startups})
        run = run_of(Wellfound(), site, {'searchTerms': ['software engineer'], 'minSalary': '500k USD'})
        self.assertEqual(run.delivery.rows, 0)
        self.assertIn('the pay alone left out 4', run.message())
        site = FakeSite({'role/software-engineer': startups})
        run = run_of(Wellfound(), site, {'searchTerms': ['software engineer'], 'companyStage': ['growth']})
        self.assertEqual(run.delivery.rows, 0)
        self.assertIn('company stage', run.message())
        site = FakeSite({'role/software-engineer': startups})
        run = run_of(Wellfound(), site, {'searchTerms': ['software engineer'], 'companyStage': ['early']})
        self.assertEqual(run.delivery.rows, 4)


class SearchBox(unittest.TestCase):
    """The engine's shared hooks (0.6.x): Platform.place_names for places typed with a job title, Platform.job_types for
    Wellfound's cofounder type."""

    def read(self, raw):
        return parse_input(raw, Wellfound(), {})

    def test_a_place_typed_with_the_title_is_read_as_a_place(self):
        cfg = self.read({'searchTerms': ['software engineer in San Francisco']})
        self.assertEqual((cfg.search_terms, cfg.locations), (['software engineer'], ['San Francisco']))
        cfg = self.read({'searchTerms': ['product manager in Kochi']})      # a small town, read after "in"
        self.assertEqual((cfg.search_terms, cfg.locations), (['product manager'], ['Kochi']))
        cfg = self.read({'searchTerms': ['backend engineer at Düsseldorf']})   # accents or none
        self.assertEqual(cfg.locations, ['Düsseldorf'])
        cfg = self.read({'searchTerms': ['data scientist NYC']})            # a short name of a big one, read alone
        self.assertEqual((cfg.search_terms, cfg.locations), (['data scientist'], ['NYC']))

    def test_only_big_towns_are_read_alone_at_the_end(self):
        every, bare = wellfound.place_names()
        self.assertIn('san francisco', bare)
        self.assertIn('lehi', every)
        self.assertNotIn('lehi', bare)
        self.assertNotIn('madrid colombia', every)          # a page's odd slug is no name people type
        cfg = self.read({'searchTerms': ['data scientist san francisco']})
        self.assertEqual((cfg.search_terms, cfg.locations), (['data scientist'], ['san francisco']))
        cfg = self.read({'searchTerms': ['engineer lehi']})                 # Lehi, Utah: too few jobs to read alone
        self.assertEqual((cfg.search_terms, cfg.locations), (['engineer lehi'], []))
        cfg = self.read({'searchTerms': ['engineer in Lehi']})              # only Wellfound's list has it: read after in
        self.assertEqual((cfg.search_terms, cfg.locations), (['engineer'], ['Lehi']))
        from tests.stub import Stub                                          # a platform with no list of its own
        self.assertEqual(parse_input({'searchTerms': ['engineer in Lehi']}, Stub(), {}).locations, [])

    def test_cofounder_is_a_job_type(self):
        self.assertEqual(self.read({'employmentType': ['cofounder']}).job_types, ['cofounder'])
        self.assertEqual(self.read({'employmentType': ['Co-founder', 'intern']}).job_types, ['cofounder', 'intern'])
        cfg = self.read({'employmentType': ['part-time', 'contract']})
        self.assertEqual(cfg.job_types, ['contract'])
        self.assertIn('Wellfound has no part-time job type', ' '.join(cfg.notes))

    def test_a_cofounder_search_keeps_cofounder_roles(self):
        jobs = [listing(1, 2, title='Technical Cofounder', job_type='cofounder'),
                listing(2, 3, title='Cofounder and CTO', job_type='full-time')]
        site = FakeSite({'role/cofounder': [('Acme', jobs[:1]), ('Bolt', jobs[1:])]})
        run = run_of(Wellfound(), site, {'employmentType': ['cofounder'], 'searchTerms': ['cofounder']})
        self.assertEqual([(r['jobId'], r['employmentType']) for r in run.delivery.rows_out], [('1', 'cofounder')])
        self.assertEqual(run.missed_only, {'employmentType': 1})


class Plans(unittest.TestCase):
    def test_a_title_reads_its_role_and_a_longer_one_the_role_inside_it(self):
        _, _, queries = plan_of({'searchTerms': ['data scientist']}, site_with('role/data-scientist'))
        self.assertEqual([q.path for q in queries], ['role/data-scientist'])
        _, cfg, queries = plan_of({'searchTerms': ['senior python developer']}, site_with('role/python-developer'))
        self.assertEqual([q.path for q in queries], ['role/python-developer'])
        self.assertIn('Python Developer', ' '.join(cfg.notes))

    def test_a_word_reads_its_own_page_else_the_roles_that_hold_it(self):
        _, _, queries = plan_of({'searchTerms': ['marketing']}, site_with('role/marketing'))
        self.assertEqual([q.path for q in queries], ['role/marketing'])
        _, _, queries = plan_of({'searchTerms': ['react']}, site_with('role/react-developer', 'role/react-native'))
        self.assertEqual([q.path for q in queries], ['role/react-developer', 'role/react-native'])

    def test_a_word_in_no_role_or_in_too_many_reads_every_job(self):
        for word in ('llm', 'data'):
            _, cfg, queries = plan_of({'searchTerms': [word]}, site_with(*EVERYWHERE))
            self.assertEqual([q.path for q in queries], list(EVERYWHERE), word)
            self.assertIn('every job on Wellfound was read', ' '.join(cfg.notes))

    def test_nothing_typed_reads_every_job(self):
        _, _, queries = plan_of({}, site_with(*EVERYWHERE))
        self.assertEqual([q.path for q in queries], list(EVERYWHERE))

    def test_places_go_to_their_pages_with_the_place_for_their_jobs(self):
        _, cfg, queries = plan_of({'searchTerms': ['python developer'], 'location': ['Bangalore']},
                                  site_with('role/l/python-developer/bangalore'))
        self.assertEqual([q.path for q in queries], ['role/l/python-developer/bangalore'])
        self.assertEqual(queries[0].tag, [{'name': 'Bangalore'}, {'name': 'Bengaluru', 'country': None}])
        _, _, queries = plan_of({'location': ['USA']}, site_with('location/united-states'))
        self.assertEqual(([q.path for q in queries], queries[0].tag[1]),
                         (['location/united-states'], {'name': 'United States', 'country': 'United States'}))
        _, _, queries = plan_of({'location': ['NYC']}, site_with('location/new-york-city-ny'))
        self.assertEqual([q.path for q in queries], ['location/new-york-city-ny'])

    def test_a_business_region_reads_the_pages_wellfound_has_for_it(self):
        pages = [f'location/{s}' for s in wellfound.REGION_PAGES['apac']]
        _, _, queries = plan_of({'location': ['APAC']}, site_with(*pages))
        self.assertEqual([q.path for q in queries], pages)
        self.assertEqual(queries[0].tag[1], {'name': 'Asia'})

    def test_a_town_wellfound_has_no_page_for_reads_its_state(self):
        _, cfg, queries = plan_of({'location': ['Kozhikode']}, site_with('location/kerala'))
        self.assertEqual([q.path for q in queries], ['location/kerala'])
        self.assertIn('Kerala', ' '.join(cfg.notes))

    def test_remote_jobs_only_read_the_remote_searches(self):
        _, _, queries = plan_of({'searchTerms': ['data scientist'], 'workType': ['remote']}, site_with('role/r/data-scientist'))
        self.assertEqual([q.path for q in queries], ['role/r/data-scientist'])
        _, _, queries = plan_of({'location': ['Remote India']}, site_with('remote'))
        self.assertEqual([q.path for q in queries], ['remote'])
        _, _, queries = plan_of({'location': ['Worldwide']}, site_with('remote'))
        self.assertEqual([q.path for q in queries], ['remote'])


class Reading(unittest.TestCase):
    def test_every_page_is_read_and_the_newest_come_first_once(self):
        startups = [(f'Startup {i}', [listing(i * 10 + k, hours_ago=(i * 7 + k) % 50 + 1) for k in range(2)]) for i in range(45)]
        site = FakeSite({'role/software-engineer': startups})
        run = run_of(Wellfound(), site, {'searchTerms': ['software engineer'], 'maxJobs': 10})
        rows = run.delivery.rows_out
        self.assertEqual(sorted(site.calls), sorted(['role/software-engineer', 'role/software-engineer?page=2',
                                                     'role/software-engineer?page=3']))
        self.assertEqual(len(rows), 10)
        self.assertEqual([r['postedAt'] for r in rows], sorted((r['postedAt'] for r in rows), reverse=True))
        every = [j for _, jobs in startups for j in jobs]
        newest = sorted(every, key=lambda j: -j['liveStartAt'])[:10]
        self.assertEqual({r['jobId'] for r in rows}, {j['id'] for j in newest})
        self.assertEqual(run.matching, 90)

    def test_a_page_of_jobs_already_read_does_not_end_the_search(self):
        # Wellfound's order shifts while a search is read, so a startup can come again on the next page; a page whose
        # jobs all came before must not read as the end of the search (the empty form read 421 of 578 pages, 2026-11-10)
        first = [(f'Startup {i}', [listing(i, hours_ago=i + 5)]) for i in range(20)]
        second = first[:20]
        third = [(f'Late {i}', [listing(100 + i, hours_ago=0.5)]) for i in range(5)]

        class Shifting(FakeSite):
            def page(self, base, number):
                self.searches[base] = {1: first, 2: second, 3: third}[number] if number <= 3 else []
                found = super().page(base, 1)
                found.text = found.text.replace('"pageCount": 1', '"pageCount": 3')
                return found
        site = Shifting({'role/software-engineer': first})
        run = run_of(Wellfound(), site, {'searchTerms': ['software engineer'], 'maxJobs': 5})
        self.assertEqual(len(site.calls), 3)
        self.assertEqual({r['jobId'] for r in run.delivery.rows_out}, {str(100 + i) for i in range(5)})

    def test_a_place_page_gives_its_jobs_the_place(self):
        site = FakeSite({'location/india': [('Acme', [listing(1, 3, title='Backend Engineer', places=('Sonipat',))])]},
                        place='India')
        run = run_of(Wellfound(), site, {'location': ['India'], 'maxJobs': 10})
        self.assertEqual([r['location'] for r in run.delivery.rows_out], ['Sonipat'])

    def test_the_run_says_how_many_wellfound_lists_and_shows(self):
        startups = [('Big', [listing(n, n + 1) for n in range(6)])]
        run = run_of(Wellfound(), FakeSite({'role/software-engineer': startups}), {'searchTerms': ['software engineer']})
        self.assertEqual(run.delivery.rows, 3)
        self.assertIn('Wellfound lists 6 jobs for this search and shows visitors at most 3 of each startup: the run '
                      'read the 3 it shows.', run.message())

    def test_a_refusal_keeps_what_was_read(self):
        startups = [(f'Startup {i}', [listing(i, i + 1)]) for i in range(60)]
        site = FakeSite({'role/software-engineer': startups}, refuse_after=2)
        with patch.object(wellfound, 'REFUSAL_WAITS', (0, 0)):
            run = run_of(Wellfound(), site, {'searchTerms': ['software engineer'], 'maxJobs': 100})
        self.assertGreater(run.delivery.rows, 0)
        self.assertEqual(run.status(), 'partial')
        self.assertIn('Cloudflare check refused this address', run.message())

    def test_a_refusal_before_any_page_fails_the_run_and_says_so(self):
        # live on 2026-11-10 (run 13_remote_india): refused at planning, nothing read, a failed run with this message
        site = FakeSite({'role/software-engineer': [('Acme', [listing(1, 1)])]}, refuse_after=0)
        with patch.object(wellfound, 'REFUSAL_WAITS', (0, 0)):
            run = run_of(Wellfound(), site, {'searchTerms': ['software engineer']})
        self.assertEqual((run.delivery.rows, run.ran(), run.status()), (0, False, 'failed'))
        self.assertIn('refused this address (HTTP 403 with a challenge) before any page was read: run again in 10 '
                      'minutes, or turn on Apify Proxy', run.message())

    def test_cloudflares_429_challenge_is_a_refusal_too(self):
        # send() retries a 429 by itself and then raises; the run must still wait, slow down and keep what it read
        from ats_jobs import http as http_module
        startups = [(f'Startup {i}', [listing(i, i + 1)]) for i in range(60)]

        class Limited(FakeSite):
            async def request(self, method, url, follow_redirects=False, headers=None, params=None):
                if len(self.calls) >= 2:
                    self.calls.append('limited')
                    return Response(429, 'Security Check', {'cf-mitigated': 'challenge'})
                return await super().request(method, url, follow_redirects, headers, params)
        platform = Wellfound()
        with patch.object(wellfound, 'REFUSAL_WAITS', (0, 0)), patch.object(http_module, 'BACKOFF_S', (0, 0, 0)):
            run = run_of(platform, Limited({'role/software-engineer': startups}), {'searchTerms': ['software engineer']})
        self.assertGreater(run.delivery.rows, 0)
        self.assertEqual(run.status(), 'partial')
        self.assertIn('refused this address (HTTP 429)', run.message())
        self.assertLess(platform.pace.rate, 10_000.0)

    def test_a_page_that_cannot_be_read_costs_only_its_jobs(self):
        startups = [(f'Startup {i}', [listing(i, i + 1)]) for i in range(60)]

        class Broken(FakeSite):
            async def request(self, method, url, follow_redirects=False, headers=None, params=None):
                if url.endswith('?page=2'):
                    self.calls.append('broken')
                    return Response(200, '<html>no search data</html>')
                return await super().request(method, url, follow_redirects, headers, params)
        run = run_of(Wellfound(), Broken({'role/software-engineer': startups}), {'searchTerms': ['software engineer'],
                                                                                   'maxJobs': 100})
        self.assertEqual(run.delivery.rows, 40)
        self.assertEqual(run.status(), 'partial')
        self.assertIn("1 of Wellfound's search pages could not be read", run.message())

    def test_a_company_is_found_in_the_jobs_read(self):
        startups = [('Acme Robotics', [listing(1, 2), listing(2, 3)]), ('Other', [listing(3, 1)])]
        site = FakeSite({'role/software-engineer': startups})
        run = run_of(Wellfound(), site, {'searchTerms': ['software engineer'], 'companies': ['Acme', 'Nobody Inc']})
        self.assertEqual({r['company'] for r in run.delivery.rows_out}, {'Acme Robotics'})
        self.assertEqual(run.boards_missing, ['Nobody Inc'])
        self.assertEqual(site.calls.count('role/software-engineer'), 1)

    def test_rows_carry_wellfounds_fields(self):
        job = listing(7, 2, places=('Austin',), remote=True, accepted=('United States',), kind='REMOTE', flexible=False,
                      years=3)
        run = run_of(Wellfound(), FakeSite({'role/software-engineer': [('Acme', [job])]}), {'searchTerms': ['software engineer']})
        row = run.delivery.rows_out[0]
        self.assertEqual((row['workType'], row['remotePolicy'], row['remoteIn'], row['experienceMin']),
                         ('remote', 'Remote only', ['United States'], 3))
        self.assertEqual((row['salaryMin'], row['salaryCurrency'], row['equityMin'], row['offersEquity']), (150000, 'USD', 0.1, True))
        self.assertEqual((row['companySize'], row['companyStage'], row['companyBadges']), ('11-50', 'Early Stage', ['YC Funded']))
        for key in ('department', 'team', 'expiresAt', 'reference'):
            self.assertNotIn(key, row)


class Pages(unittest.TestCase):
    def test_details_add_what_the_job_page_says(self):
        job = {'remoteIn': ['United States'], 'places': [{'name': 'Remote'}], 'description': 'list text', 'salaryCurrency': 'USD'}
        out = read_page(recorded_job('4889905'), job)
        self.assertTrue(out['description'].startswith('What We Do'))
        self.assertTrue(out['descriptionHtml'].startswith('<'))
        self.assertEqual((out['remotePolicy'], out['remoteIn'], out['visaSponsorship'], out['relocation']),
                         ('Remote only', ['United States'], False, False))
        self.assertEqual((out['companyFunding'], out['fundingRounds'], out['latestRound'], out['latestRoundDate']),
                         ('$115M', 4, 'Series B', '2019-02'))
        self.assertEqual((out['salaryMin'], out['salaryMax'], out['salaryCurrency'], out['salaryPeriod']),
                         (111000.0, 182000.0, 'USD', 'year'))
        self.assertEqual((out['companyLocation'], out['reposted']), ('San Francisco', True))
        self.assertIn('Health and Wellness', out['industries'])

    def test_time_zones_skills_and_every_place_with_its_country(self):
        page = recorded_job('927086')
        grid = facts(page)
        self.assertEqual(grid['Remote Work Policy'], ['Onsite or remote'])
        out = read_page(page, {'places': [], 'remoteIn': []})
        self.assertEqual(out['timezones'], ['Pacific Time', 'Mountain Time', 'Central Time', 'Eastern Time'])
        self.assertEqual(out['skills'][:3], ['Embedded Systems', 'C', 'Operating Systems'])
        self.assertEqual((out['relocation'], out['remoteIn']), (True, ['United States']))
        self.assertTrue(all(p.get('country') == 'United States' for p in out['places']))

    def test_the_funding_box_shows_the_newest_round(self):
        self.assertEqual(funding(recorded_job('4965854')),
                         {'companyFunding': '$60.5M', 'fundingRounds': 3, 'latestRound': 'Series B', 'latestRoundDate': '2025-01'})
        brex = ('<div class="mt-4 text-lg font-semibold">Rounds</div><div class="flex"><div class="x"><div class="y">D2</div>'
                '<span>Undisclosed amount</span></div><div class="z"><span>Series D2<!-- --> - <!-- -->Jan 2022'
                '<span>+<!-- -->4</span></span></div></div>')
        self.assertEqual(funding(brex)['latestRound'], 'Series D2')

    def test_links_are_open_closed_or_not_found(self):
        pages = {'4889905-software-development-engineer-in-test': (200, recorded_job('4889905')),
                 '5008727-gtm-engineer-fresh-grad-india': (410, recorded_job('5008727'))}
        links = ['https://wellfound.com/jobs/4889905-software-development-engineer-in-test',
                 'https://wellfound.com/jobs/5008727-gtm-engineer-fresh-grad-india',
                 'https://wellfound.com/jobs/4889905-a-wrong-slug']
        run = run_of(Wellfound(), FakeSite({}, pages), {'jobUrls': links}, links=True)
        opened, closed, missing = run.delivery.rows_out
        self.assertEqual((opened['status'], opened['title'], opened['company'], opened['workType'], opened['postedAt']),
                         ('open', 'Software Development Engineer in Test', 'Calm', 'remote', '2026-11-09T21:12:08Z'))
        self.assertEqual((closed['status'], closed['company'], closed['companySlug']), ('closed', 'Secfix', 'secfix'))
        self.assertEqual((missing['status'], missing['company']), ('not found', None))
        details = {'description', 'descriptionHtml', 'city', 'region', 'country', 'countryCode'}
        self.assertEqual(set(opened) - details, set(closed))
        self.assertEqual(set(closed), set(missing))

    def test_a_job_taken_down_after_the_search_keeps_its_row(self):
        platform = Wellfound()
        site = FakeSite({}, {'5008727-gtm-engineer-fresh-grad-india': (410, recorded_job('5008727'))})
        job = {'id': '5008727', 'jobUrl': 'https://wellfound.com/jobs/5008727-gtm-engineer-fresh-grad-india', 'title': 'GTM'}
        with patch.object(wellfound, 'RATE', 10_000.0):
            platform.pace = wellfound.Pace()
            self.assertEqual(asyncio.run(platform.details(site, None, job)), job)

    def test_an_open_job_whose_page_has_no_markup_is_open_and_read_from_its_html(self):
        # 2026-11-10: Wellfound marks some open jobs' pages noindex and leaves their JobPosting markup out (2 of 139)
        page = recorded_job('4985575')
        self.assertEqual(wellfound.markup(page), {})
        link = 'https://wellfound.com/jobs/4985575-senior-software-engineer'
        pages = {'4985575-senior-software-engineer': (200, page)}
        run = run_of(Wellfound(), FakeSite({}, pages), {'jobUrls': [link]}, links=True)
        row, = run.delivery.rows_out
        self.assertEqual((row['status'], row['title'], row['company'], row['companySlug'], row['workType'],
                          row['employmentType'], row['experienceMin']),
                         ('open', 'Senior Software Engineer', 'CELSTARTAB', 'celstartab', 'remote', 'full-time', 8))
        self.assertEqual((row['salaryMin'], row['salaryMax'], row['salaryCurrency'], row['offersEquity']),
                         (115000, 125000, 'USD', False))
        self.assertEqual((row['location'], row['otherLocations'], row['remoteIn'], row['postedAt']),
                         ('California', ['Texas', 'Florida', 'Montana'], ['Everywhere'], None))
        job = {'id': '4985575', 'jobUrl': link, 'title': 'Senior Software Engineer', 'description': 'list text',
               'remoteIn': ['Everywhere'], 'places': []}
        platform = Wellfound()
        with patch.object(wellfound, 'RATE', 10_000.0):
            platform.pace = wellfound.Pace()
            out = asyncio.run(platform.details(FakeSite({}, pages), None, job))
        self.assertTrue(out['descriptionHtml'].startswith('<p><strong>About Us'))
        self.assertTrue(out['description'].startswith('About Us'))
        self.assertEqual((out['visaSponsorship'], out['relocation'], out['skills'][:2]), (False, False, ['.NET', 'SCRUM']))

    def test_the_header_names_a_cofounder_role_which_the_markup_calls_full_time(self):
        page = ('<h1 class="inline text-xl font-semibold text-black">Cofounder &amp; CTO</h1><ul class="flex"><li class="md:'
                'flex-none"></li><li class="flex">Remote (<div><a href="/remote">Egypt</a></div>)</li><li class="md:flex-none">'
                '<span class="mx-2 inline-block">|</span>3 years of exp</li><li class="md:flex-none"><span class="mx-2 '
                'inline-block">|</span>Cofounder</li></ul>')
        self.assertEqual(wellfound.header(page), {'title': 'Cofounder & CTO', 'pay': '', 'type': 'cofounder'})
        self.assertEqual(wellfound.header(recorded_job('4965854')),
                         {'title': 'Software Engineer', 'pay': '$175k – $215k', 'type': 'full-time'})


class Lists(unittest.TestCase):
    def test_the_role_and_place_lists_load(self):
        roles, by_name = wellfound.role_list()
        self.assertGreater(len(roles), 500)
        self.assertEqual(by_name['ui designer'].slug, 'ui-designer')
        self.assertEqual(by_name['cto'].slug, 'chief-technical-officer')
        listed, updated = wellfound.place_list()
        self.assertEqual((listed['bangalore'].slug, listed['bengaluru'].slug, listed['chennai'].slug), ('bangalore',) * 2 + ('madras',))
        self.assertEqual((listed['georgia'].country, listed['india'].country), (None, 'India'))
        self.assertTrue(updated)
        self.assertEqual(resources.files('ats_jobs').joinpath('data/wellfound_roles.tsv').is_file(), True)


if __name__ == '__main__':
    unittest.main()
