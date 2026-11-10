"""Y Combinator (Work at a Startup): YC's words for dates and pay, its places, links, its own filters, and whole searches
on answers recorded from ycombinator.com on 2026-11-10 at 10:24 UTC (data/ycombinator_2026-11-10.json.gz: the job lists
of five companies trimmed to the fields the adapter reads, and each job's JobPosting markup with its description cut)."""
import asyncio
import copy
import gzip
import html
import json
import os
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from ats_jobs import engine
from ats_jobs.inputs import parse_input
from ats_jobs.platforms import PLATFORMS
from ats_jobs.platforms import ycombinator as yc
from ats_jobs.platforms.ycombinator import YCombinator
from ats_jobs.rows import Filters, job_area, job_row

FIXTURE = os.path.join(os.path.dirname(__file__), 'data', 'ycombinator_2026-11-10.json.gz')
with gzip.open(FIXTURE, 'rt', encoding='utf-8') as _f:
    FX = json.load(_f)
RECORDED = datetime(2026, 11, 10, 10, 24, tzinfo=timezone.utc)


class FixedNow(datetime):
    """The clock at the recording, a minute later each time it is read: the engine reads it before the platform."""
    calls = 0

    @classmethod
    def now(cls, tz=None):
        cls.calls += 1
        return RECORDED + timedelta(minutes=cls.calls)


class Response:
    def __init__(self, status_code, text='', body=None, url='https://www.ycombinator.com/', headers=None):
        self.status_code, self.text, self.body, self.url = status_code, text, body, url
        self.headers = headers or {}

    def json(self):
        if self.body is None:
            raise ValueError('not JSON')
        return self.body


def inertia_html(component, props, version=None):
    page = {'component': component, 'props': props, 'url': '/', 'version': version or FX['version']}
    return f'<html><body><div id="app" data-page="{html.escape(json.dumps(page))}"></div></body></html>'


def job_html(posting, props=None):
    ld = f'<script type="application/ld+json">{json.dumps(posting)}</script>'
    return f'<html><head>{ld}</head><body>{inertia_html("WaasShowJobPage", props or {})}</body></html>'


def directory_html():
    return (f'<html><script>window.AlgoliaOpts = {json.dumps(FX["algoliaOpts"])};</script>'
            f'{inertia_html("ycdc_new/pages/Companies/IndexPage", {"env": "production"})}</html>')


def lists_routes(lists=None, postings=None, companies=None):
    """URL -> answer for the directory, its search, the job lists and the job pages of the fixture."""
    lists = copy.deepcopy(FX['lists']) if lists is None else lists
    postings = copy.deepcopy(FX['postings']) if postings is None else postings
    companies = copy.deepcopy(FX['companies']) if companies is None else companies
    routes = {yc.DIRECTORY: Response(200, directory_html())}
    for slug, jobs in lists.items():
        routes[yc.COMPANY_JOBS.format(slug=slug)] = Response(200, inertia_html('WaasShowJobsPage', {'jobPostings': jobs}))
        for job in jobs:
            posting = postings.get(str(job['id']))
            routes[yc.SITE + job['url']] = Response(200, job_html(posting)) if posting else Response(404, 'gone')
    batches = {}
    for c in companies.values():
        batches[c['batch']] = batches.get(c['batch'], 0) + 1
    routes['algolia'] = (batches, list(companies.values()))
    return routes


class FakeHttp:
    """Answers by URL; the directory search answers from the companies given. Records every call."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def request(self, method, url, params=None, headers=None, json=None, **kwargs):
        self.calls.append((method, url, dict(headers or {})))
        if 'algolia.net' in url:
            batches, hits = self.routes['algolia']
            results = []
            for q in json['requests']:
                params = q['params']
                if 'facets=' in params:
                    results.append({'nbHits': len(hits), 'hits': [], 'facets': {'batch': batches}})
                elif 'isHiring' in params:
                    results.append({'nbHits': len(hits), 'hits': [h for h in hits if h['batch'] in params.replace('+', ' ')
                                                                  or __import__('urllib.parse').parse.unquote_plus(params)
                                                                  .count(h['batch'])]})
                else:                                         # a name search
                    typed = __import__('urllib.parse').parse.parse_qs(params)['query'][0].lower()
                    results.append({'hits': [h for h in hits if typed in h['name'].lower() or typed == h['slug']]})
            return Response(200, body={'results': results})
        found = self.routes.get(url)
        if isinstance(found, list):
            return found.pop(0) if len(found) > 1 else found[0]
        return found or Response(404, 'not here')


async def _quiet(message):
    return None


class Delivered:
    def __init__(self):
        self.pushed, self.rows, self.limit = [], 0, False

    def can_pay(self, event):
        return True

    async def push(self, rows, event):
        self.pushed += [dict(r, _event=event) for r in rows]
        self.rows += len(rows)
        return len(rows)


def search(raw, routes=None):
    """One search through the engine, offline, at the recording's clock."""
    p = PLATFORMS['ycombinator']()
    cfg = parse_input(raw, p, {})
    http = FakeHttp(routes or lists_routes())

    @asynccontextmanager
    async def fake_client(actor, connections=64):
        yield http

    run = engine.Run(p, cfg, [], None, Delivered())
    FixedNow.calls = 0
    with patch.object(engine, 'client', fake_client), patch.object(engine, 'safe_status', _quiet), \
            patch.object(engine, 'seconds_left_in_run', lambda: None), patch.object(engine, 'datetime', FixedNow), \
            patch.object(yc, 'datetime', FixedNow):
        asyncio.run(run.check_links() if cfg.job_urls else run.search())
    return run, http


def platform_for(raw):
    p = YCombinator()
    cfg = parse_input(raw, p, {})
    return p, cfg


class Dates(unittest.TestCase):
    def test_rails_words_become_the_ages_they_can_mean(self):
        day = yc.DAY
        self.assertEqual(yc.age_range('29 days'), (28.5 * day, 29.5 * day))
        self.assertEqual(yc.age_range('about 1 month'), (30 * day, 45 * day))
        self.assertEqual(yc.age_range('about 2 months'), (45 * day, 60 * day))
        self.assertEqual(yc.age_range('about 7 hours'), (6.5 * 3600, 7.5 * 3600))
        self.assertEqual(yc.age_range('about 1 hour'), (44.5 * 60, 89.5 * 60))
        self.assertEqual(yc.age_range('1 day'), (1439.5 * 60, 2519.5 * 60))
        self.assertEqual(yc.age_range('32 minutes'), (31.5 * 60, 32.5 * 60))
        low, high = yc.age_range('over 1 year')
        self.assertLess(low, 1.25 * yc.YEAR)
        self.assertGreater(high, 1.75 * yc.YEAR)
        self.assertEqual(yc.age_range('less than a minute'), (0.0, 60.0))
        self.assertIsNone(yc.age_range('a while back'))

    def test_every_recorded_age_reads_and_holds_its_exact_date(self):
        """The list's words and the page's datePosted agree on every recorded job."""
        for jobs in FX['lists'].values():
            for job in jobs:
                low, high = yc.age_range(job['createdAt'])
                posted = datetime.fromisoformat(FX['postings'][str(job['id'])]['datePosted'].replace('Z', '+00:00'))
                age = (RECORDED - posted).total_seconds()
                self.assertTrue(low - 600 <= age <= high + 600, (job['id'], job['createdAt'], age / yc.DAY))

    def test_ids_grow_with_the_posting_date(self):
        dated = sorted((int(k), v['datePosted']) for k, v in FX['postings'].items())
        self.assertEqual([d for _, d in dated], sorted(d for _, d in dated))


class Pay(unittest.TestCase):
    def test_currency_period_and_scale_come_from_ycs_text(self):
        self.assertEqual(yc.parse_pay('$150K - $190K'), {'salary': '$150K - $190K', 'salaryMin': 150000,
                                                         'salaryMax': 190000, 'salaryCurrency': 'USD',
                                                         'salaryPeriod': 'year'})
        self.assertEqual(yc.parse_pay('£70K - £120K GBP')['salaryCurrency'], 'GBP')
        self.assertEqual(yc.parse_pay('$116K - $156K CAD')['salaryCurrency'], 'CAD')
        rupees = yc.parse_pay('₹2M - ₹4.5M INR')
        self.assertEqual((rupees['salaryMin'], rupees['salaryMax'], rupees['salaryCurrency']), (2000000, 4500000, 'INR'))
        hourly = yc.parse_pay('$50 - $80 / hourly')
        self.assertEqual((hourly['salaryMin'], hourly['salaryMax'], hourly['salaryPeriod']), (50, 80, 'hour'))
        monthly = yc.parse_pay('€9K - €12K EUR / monthly')
        self.assertEqual((monthly['salaryMin'], monthly['salaryCurrency'], monthly['salaryPeriod']), (9000, 'EUR', 'month'))
        self.assertEqual(yc.parse_pay('$12K')['salaryMax'], 12000)

    def test_an_amount_that_cannot_be_a_years_pay_stays_in_the_text_only(self):
        slip = yc.parse_pay('$120 - $140')              # typed without K
        self.assertEqual((slip['salary'], slip['salaryMin'], slip['salaryMax'], slip['salaryCurrency']),
                         ('$120 - $140', None, None, None))
        self.assertIsNone(yc.parse_pay('$1')['salaryMax'])
        half = yc.parse_pay('$1 - $130K')
        self.assertEqual((half['salaryMin'], half['salaryMax']), (None, 130000))
        self.assertEqual(yc.parse_pay('')['salary'], None)

    def test_equity_and_years(self):
        self.assertEqual(yc.parse_equity('0.25% - 1.00%'), (0.25, 1.0))
        self.assertEqual(yc.parse_equity('0.5%'), (0.5, 0.5))
        self.assertEqual(yc.parse_equity(''), (None, None))
        self.assertEqual(yc.parse_years('3+ years', 'Full-time'), (3, None))
        self.assertEqual(yc.parse_years('Any (new grads ok)', 'Full-time'), (0, None))
        self.assertEqual(yc.parse_years(None, 'Internship'), (0, 0))
        self.assertEqual(yc.parse_years('', 'Full-time'), (None, None))


class Batches(unittest.TestCase):
    def test_a_batch_typed_any_way(self):
        self.assertEqual(yc.batches_typed('W25'), {'W25'})
        self.assertEqual(yc.batches_typed('summer 2024'), {'S24'})
        self.assertEqual(yc.batches_typed('X25'), {'P25'})           # YC wrote Spring 2025 as X25
        self.assertEqual(yc.batches_typed('Spring 2026'), {'P26'})
        self.assertEqual(yc.batches_typed('2025'), {'W25', 'P25', 'S25', 'F25'})
        self.assertEqual(yc.batches_typed('YC S 24'), {'S24'})
        self.assertIsNone(yc.batches_typed('fintech'))
        self.assertEqual(yc.batch_code('Fall 2026'), 'F26')


class Places(unittest.TestCase):
    def test_ycs_place_text(self):
        parts, entries, vague = yc.text_places('US / CA / Remote (US; CA)')
        self.assertEqual(parts, ['US', 'CA', 'Remote (US; CA)'])
        self.assertEqual([e.get('country') for e in entries],
                         ['United States', 'Canada', 'United States', 'Canada'])     # CA alone is Canada
        self.assertFalse(vague)
        _, entries, vague = yc.text_places('Bengaluru, KA, IN')
        self.assertEqual(entries[0], {'name': 'Bengaluru, KA, IN', 'city': 'Bengaluru', 'region': 'KA', 'country': 'India'})
        self.assertFalse(vague)

    def test_a_place_with_no_country_is_vague(self):
        for text in ('SF', 'San Francisco, CA', 'New York City', '901 Market Floor 6, SF, CA 94105'):
            self.assertTrue(yc.text_places(text)[2], text)
        for text in ('Dublin, Ireland', 'England, GB', 'Remote', 'Remote (Asia)', 'Remote - UK or Europe'):
            self.assertFalse(yc.text_places(text)[2], text)

    def test_the_pages_addresses_place_a_vague_job(self):
        posting = FX['postings']['121727']                     # Preload, "San Francisco, CA"
        offices = yc.address_places(posting)
        self.assertEqual((offices[0]['country'], offices[0]['name']), ('United States', 'San Francisco, California, US'))
        self.assertEqual(yc.address_places({'jobLocation': [{'address': 'SF Headquarters'}]}),
                         [{'name': 'SF Headquarters'}])

    def test_work_type(self):
        self.assertEqual(yc.work_type(['San Francisco, CA, US', 'Remote (US)'], 'Engineer'), 'remote')
        self.assertEqual(yc.work_type(['New York City'], 'Engineer (Hybrid)'), 'hybrid')
        self.assertEqual(yc.work_type(['New York City'], 'Engineer'), 'onsite')


class Links(unittest.TestCase):
    def test_links_of_both_sites(self):
        p = YCombinator()
        self.assertEqual(p.read_link('https://www.ycombinator.com/companies/authzed/jobs/ena6RKJ-solutions-engineer'),
                         ('authzed', 'ena6RKJ'))
        self.assertEqual(p.read_link('ycombinator.com/companies/Authzed/jobs'), ('authzed', None))
        self.assertEqual(p.read_link('https://www.ycombinator.com/companies/airbnb'), ('airbnb', None))
        self.assertEqual(p.read_link('https://www.workatastartup.com/jobs/121729'), ('*', '121729'))
        self.assertEqual(p.read_link('https://www.workatastartup.com/companies/phonely'), ('phonely', None))
        self.assertEqual(p.read_link('https://www.ycombinator.com/jobs/role/software-engineer'), (None, None))
        self.assertIsNone(p.read_link('https://example.com/companies/x/jobs/abcdefg-x'))
        self.assertIsNone(p.read_link('python developer'))

    def test_a_role_page_pasted_in_the_search_box_is_its_role_and_place(self):
        p, cfg = platform_for({'searchTerms': ['https://www.ycombinator.com/jobs/role/software-engineer/remote']})
        self.assertEqual(p.settings()['role'], ['engineering'])
        self.assertEqual(cfg.work_types, ['remote'])
        self.assertEqual(cfg.search_terms, [])
        self.assertEqual(cfg.skipped, [])


class OneBox(unittest.TestCase):
    """The owner's one-box rule (2026-11-09): a place typed with the job title is read as the place. The engine knows the
    places of India's boards; YC's own places come from data/ycombinator_places.tsv (2026-11-10: 126 names on 3 or more
    of 6,316 jobs, the top ones San Francisco 2,449, New York 835, London 279)."""

    def test_the_places_yc_jobs_name(self):
        places = yc.yc_places()
        for name in ('san francisco', 'new york', 'london', 'bengaluru', 'austin', 'mountain view'):
            self.assertIn(name, places)
        self.assertNotIn('nyc office', places)                     # an office label, not a place
        self.assertNotIn('residence', places)

    def test_a_place_typed_with_the_title(self):
        for typed, expected in (('product manager in new york', ('product manager', ['New York'])),
                                ('founding engineer in nyc', ('founding engineer', ['New York'])),
                                ('ml engineer in london', ('ml engineer', ['London'])),
                                ('sales in sf', ('sales', ['San Francisco'])),
                                ('designer london', ('designer', ['London'])),
                                ('San Francisco', ('', ['San Francisco'])),
                                ('engineer in residence', ('engineer in residence', [])),
                                ('growth', ('growth', []))):
            p, cfg = platform_for({'searchTerms': [typed]})
            self.assertEqual((' '.join(cfg.search_terms), cfg.locations), expected, typed)

    def test_remote_and_a_place_with_the_title(self):
        p, cfg = platform_for({'searchTerms': ['remote product manager in new york']})
        self.assertEqual((cfg.search_terms, cfg.locations, cfg.work_types), (['product manager'], ['New York'], ['remote']))
        self.assertTrue(any('jobs in New York' in n for n in cfg.notes))

    def test_bay_area_and_nyc_typed_as_places(self):
        p, cfg = platform_for({'location': ['Bay Area', 'NYC']})
        for city in ('San Francisco', 'Palo Alto', 'Mountain View', 'Oakland', 'New York'):
            self.assertIn(city, cfg.locations)
        self.assertEqual(len(cfg.notes), 2)


class Inputs(unittest.TestCase):
    def test_ycs_own_fields_read_as_people_type_them(self):
        p, cfg = platform_for({'role': ['Engineering', 'full-stack', 'ML', 'sales'], 'batch': ['W25', 'summer 2024'],
                               'visa': ['will sponsor'], 'minSalaryUsd': '150k', 'withEquity': True,
                               'teamSize': '1-50', 'companyStage': ['early stage']})
        s = p.settings()
        self.assertEqual(s['role'], ['engineering', 'full stack', 'machine learning', 'sales'])
        self.assertEqual(s['batch'], ['W25', 'S24'])
        self.assertEqual((s['visa'], s['minSalaryUsd'], s['withEquity'], s['teamSize'], s['companyStage']),
                         (['will-sponsor'], 150000.0, True, [1.0, 50.0], ['early']))
        self.assertEqual(cfg.notes, [])

    def test_unreadable_values_are_dropped_with_a_note(self):
        p, cfg = platform_for({'role': ['wizard'], 'batch': ['fintech'], 'visa': ['maybe'], 'minSalaryUsd': 'lots',
                               'teamSize': 'big', 'companyStage': ['unicorn'], 'employmentType': ['part-time']})
        s = p.settings()
        self.assertEqual((s['role'], s['batch'], s['visa'], s['minSalaryUsd'], s['teamSize'], s['companyStage']),
                         ([], [], [], None, None, []))
        self.assertEqual(len(cfg.notes), 7)
        self.assertTrue(any('part-time finds no job' in n for n in cfg.notes))

    def test_pay_typed_in_thousands(self):
        p, cfg = platform_for({'minSalaryUsd': 150})
        self.assertEqual(p.settings()['minSalaryUsd'], 150000)


def listed(slug, job_id):
    p = YCombinator()
    p.cfg = parse_input({}, p, {})
    raw = next(j for j in FX['lists'][slug] if j['id'] == job_id)
    return p, p.from_list(copy.deepcopy(raw), FX['companies'][slug], slug, RECORDED)


class Jobs(unittest.TestCase):
    def test_a_listed_job_in_the_rows_shape(self):
        p, job = listed('phonely', 121729)
        self.assertEqual((job['id'], job['title'], job['company'], job['location']),
                         ('121729', 'Forward Deployed Engineer', 'Phonely', 'San Francisco, CA, US'))
        self.assertEqual((job['salaryMin'], job['salaryMax'], job['employmentType'], job['workType'], job['department']),
                         (110000, 130000, 'full-time', 'onsite', 'Engineering'))
        self.assertEqual((job['extra']['batch'], job['extra']['teamSize'], job['extra']['companyStage']),
                         ('S24', 10, 'early'))
        self.assertEqual(job['extra']['workAtAStartupUrl'], 'https://www.workatastartup.com/jobs/121729')
        self.assertEqual(job['jobUrl'], 'https://www.ycombinator.com/companies/phonely/jobs/bg3qHFa-forward-deployed-engineer')

    def test_the_page_gives_the_exact_date_offices_and_description(self):
        p, job = listed('preload', 121727)
        self.assertTrue(job['vague'])
        full = p.with_page(job, FX['postings']['121727'])
        self.assertEqual(full['postedAt'], '2026-11-10T03:48:20Z')
        self.assertEqual(full['places'][0]['country'], 'United States')
        self.assertTrue(full['description'])
        self.assertNotIn('ages', full)
        row = job_row('ycombinator', {'name': 'Preload', 'slug': 'preload', 'website': None}, full, 'now', True)
        self.assertEqual((row['country'], row['countryCode'], row['salaryPeriod']), ('United States', 'US', 'hour'))

    def test_a_vague_place_is_opened_only_for_a_place_filter(self):
        p, job = listed('preload', 121726)
        p.cfg = parse_input({'location': ['United States']}, p, {})
        self.assertTrue(p.can_match(job, Filters(p.cfg), None, RECORDED))       # its page says US
        p.cfg = parse_input({'location': ['United States'], 'batch': ['W21']}, p, {})
        p.read_input({'batch': ['W21']}, p.cfg)
        self.assertFalse(p.can_match(job, Filters(p.cfg), None, RECORDED))      # it fails a list filter too

    def test_the_date_is_judged_by_the_youngest_age_its_words_allow(self):
        p, job = listed('phonely', 113101)                                     # "about 1 month": 30 to 45 days
        p.cfg = parse_input({'postedWithin': '7 days'}, p, {})
        self.assertFalse(p.can_match(job, Filters(p.cfg), None, RECORDED))
        p.cfg = parse_input({'postedWithin': '40 days'}, p, {})
        self.assertTrue(p.can_match(job, Filters(p.cfg), None, RECORDED))       # may be inside: its page says

    def test_a_listed_job_outside_the_period_stays_outside_on_an_earlier_clock(self):
        """Regression (2026-11-10, local run t02): the engine reads its clock before the job lists are read (25 to 60 s),
        while the jobs are dated by the platform's. A listed job whose words put it just past the period passed the
        engine's check on its earlier clock: counted as a match, and deliverable with a date estimated from YC's words.
        A listed job outside the period is dated a day older than its words allow."""
        p, job = listed('phonely', 113101)
        p.cfg = parse_input({'postedWithin': '30 days'}, p, {})
        window = p.cfg.posted_within_days * yc.DAY
        job['ages'] = (window + 30, window + 3600)          # just past the period, by less than the clock gap
        self.assertFalse(p.in_period(job))
        engine_clock = RECORDED - timedelta(seconds=60)
        out = p.as_listed(job, RECORDED)
        self.assertEqual(Filters(p.cfg).misses(out, engine_clock), ('postedWithin',))
        self.assertNotIn('ages', out)
        job['ages'] = (window - 30, window + 3600)          # may be inside: opened, never handed over as listed
        self.assertTrue(p.can_match(job, Filters(p.cfg), None, RECORDED))
        self.assertEqual(Filters(p.cfg).misses(p.as_listed(job, RECORDED), engine_clock), ())


class Filters_(unittest.TestCase):
    def test_ycs_filters_name_what_a_job_fails(self):
        p, job = listed('phonely', 121729)
        for raw, expected in (({'role': ['sales']}, 'role'), ({'role': ['engineering']}, True),
                              ({'role': ['backend']}, 'role'), ({'batch': ['W21']}, 'batch'), ({'batch': ['S24']}, True),
                              ({'visa': ['will-sponsor']}, 'visa'), ({'visa': ['us-only']}, True),
                              ({'visa': ['US citizen/visa only']}, True), ({'visa': ['Will sponsor']}, 'visa'),
                              ({'minSalaryUsd': 140000}, 'minSalaryUsd'), ({'minSalaryUsd': 120000}, True),
                              ({'withEquity': True}, True), ({'teamSize': '11-50'}, 'teamSize'),
                              ({'teamSize': '1-10'}, True), ({'companyStage': ['growth']}, 'companyStage')):
            p.read_input(raw, parse_input(raw, YCombinator(), {}))
            self.assertEqual(p.fits(job), expected, raw)
            p._settings = YCombinator()._settings

    def test_mobile_is_ios_and_android(self):
        job = {'roleArea': 'eng', 'extra': {'roleType': 'iOS'}}
        self.assertTrue(YCombinator.role_fits(job, ['type:mobile']))
        self.assertFalse(YCombinator.role_fits(job, ['type:backend']))

    def test_pay_in_another_currency_or_hourly(self):
        p, job = listed('preload', 121727)                                  # $50 - $80 / hourly: 166,400 a year
        p.read_input({'minSalaryUsd': 150000}, p.cfg)
        self.assertTrue(p.fits(job))
        p, job = listed('curacel', 121751)                                  # $12K
        p.read_input({'minSalaryUsd': 50000}, p.cfg)
        self.assertEqual(p.fits(job), 'minSalaryUsd')


class Searches(unittest.TestCase):
    def test_the_newest_jobs_come_first_with_their_exact_dates(self):
        run, http = search({'maxJobs': 5})
        rows = run.delivery.pushed
        self.assertEqual([r['jobId'] for r in rows], ['121751', '121729', '121728', '121727', '121726'])
        self.assertEqual([r['postedAt'] for r in rows],
                         [FX['postings'][r['jobId']]['datePosted'] for r in rows])
        self.assertEqual(rows[0]['batch'], 'W22')
        self.assertEqual({r['_event'] for r in rows}, {'job'})
        self.assertNotIn('description', rows[0])
        self.assertIn("on YC's Work at a Startup board", run.message())
        pages = [u for m, u, h in http.calls if '/jobs/' in u and u.startswith(yc.SITE + '/companies/')]
        self.assertLessEqual(len(pages), 10)                               # only the jobs that can make the cut

    def test_jobs_posted_the_same_second_come_newest_id_first(self):
        """Preload posted 121722 and 121723 one second apart; dated the same second, the higher id still comes first
        (rank), as YC numbers jobs in posting order (local run r09: 81 such ties among 2,000 rows)."""
        postings = copy.deepcopy(FX['postings'])
        postings['121722']['datePosted'] = postings['121723']['datePosted']
        run, _ = search({'companies': ['Preload'], 'postedWithin': 'any time'}, lists_routes(postings=postings))
        ids = [r['jobId'] for r in run.delivery.pushed]
        self.assertEqual(ids, sorted(ids, reverse=True))

    def test_job_lists_and_pages_are_asked_for_as_partial_reloads(self):
        run, http = search({'maxJobs': 1})
        lists = [h for m, u, h in http.calls if u == yc.COMPANY_JOBS.format(slug='phonely')]
        self.assertEqual(lists[0].get('X-Inertia-Partial-Data'), 'jobPostings')
        self.assertEqual(lists[0].get('X-Inertia-Version'), FX['version'])

    def test_unopened_matches_count_once_per_company_title_and_place(self):
        """25 jobs listed, 3 asked for: the 10 newest are opened and the 15 older ones count without a call, but Curacel
        lists one title at one place twice (121751, 91494), and the engine keeps the newest copy only."""
        run, http = search({'maxJobs': 3, 'postedWithin': 'any time'})
        out = run.output('done')
        self.assertEqual(run.delivery.rows, 3)
        self.assertEqual((out['matchingJobs'], out['jobsRead']), (24, 24))
        self.assertIn('Found 24 matching jobs at 5 companies', run.message())
        self.assertIn('saved the 3 newest', run.message())
        pages = [u for m, u, h in http.calls if '/jobs/' in u and u.startswith(yc.SITE + '/companies/')]
        self.assertEqual(len(pages), 10)

    def test_a_period_leaves_older_jobs_out_and_says_so(self):
        run, _ = search({'postedWithin': '7 days', 'maxJobs': 100})
        rows = run.delivery.pushed
        cutoff = RECORDED - timedelta(days=7)
        self.assertTrue(rows)
        self.assertTrue(all(datetime.fromisoformat(r['postedAt'].replace('Z', '+00:00')) >= cutoff for r in rows))
        self.assertEqual(len(rows), 15)                    # Preload 6, Phonely 2, Curacel 1, Astranis 6 dated in 7 days
        self.assertIn('Only jobs posted in the last 7 days were looked for', run.message())

    def test_a_vague_place_is_placed_by_the_job_page(self):
        run, _ = search({'location': ['United States'], 'postedWithin': '7 days', 'maxJobs': 100})
        companies = {r['company'] for r in run.delivery.pushed}
        self.assertIn('Preload', companies)                # "San Francisco, CA": its page says US
        self.assertNotIn('Curacel', companies)             # Africa only

    def test_a_country_written_as_its_code(self):
        run, _ = search({'location': ['Canada'], 'postedWithin': 'any time', 'maxJobs': 100})
        self.assertEqual(sorted(r['company'] for r in run.delivery.pushed),
                         ['Phonely', 'authzed', 'authzed'])   # "CA / Remote (CA)", "US / CA / Remote (US; CA)" x2

    def test_ycs_filters_and_the_message_of_an_empty_search(self):
        run, _ = search({'searchTerms': ['engineer'], 'batch': ['W25'], 'postedWithin': 'any time'})
        self.assertEqual(run.delivery.rows, 0)
        self.assertIn('the YC batch alone left out', run.message())
        self.assertIn('clear YC batch', run.message())

    def test_words_that_no_title_holds(self):
        run, _ = search({'searchTerms': ['quantum chef'], 'postedWithin': 'any time'})
        self.assertEqual(run.delivery.rows, 0)
        self.assertIn('No job title had your words', run.message())

    def test_a_job_that_closed_after_its_list_was_read_is_left_out(self):
        postings = copy.deepcopy(FX['postings'])
        del postings['121729']
        run, _ = search({'maxJobs': 3}, lists_routes(postings=postings))
        self.assertEqual([r['jobId'] for r in run.delivery.pushed], ['121751', '121728', '121727'])

    def test_details_give_the_description(self):
        run, _ = search({'maxJobs': 2, 'includeDetails': True})
        rows = run.delivery.pushed
        self.assertTrue(all(r['description'] and r['descriptionHtml'] for r in rows))
        self.assertEqual({r['_event'] for r in rows}, {'job-details'})

    def test_a_company_typed_by_name(self):
        run, http = search({'companies': ['Phonely'], 'postedWithin': 'any time'})
        self.assertEqual({r['company'] for r in run.delivery.pushed}, {'Phonely'})
        self.assertEqual(run.delivery.rows, 6)
        self.assertFalse(any(u == yc.COMPANY_JOBS.format(slug='preload') for _, u, _ in http.calls))

    def test_a_stale_version_is_read_again_whole(self):
        routes = lists_routes()
        url = yc.COMPANY_JOBS.format(slug='phonely')
        routes[url] = [Response(409, '', headers={'x-inertia-location': url}), routes[url]]
        run, _ = search({'companies': ['Phonely'], 'postedWithin': 'any time'}, routes)
        self.assertEqual(run.delivery.rows, 6)


class CheckLinks(unittest.TestCase):
    def test_open_closed_and_not_found(self):
        routes = lists_routes()
        open_url = 'https://www.ycombinator.com/companies/phonely/jobs/bg3qHFa-forward-deployed-engineer'
        raw = next(j for j in FX['lists']['phonely'] if j['id'] == 121729)
        props = {'job': raw, 'company': {'name': 'Phonely', 'batch_name': 'S24', 'team_size': 10,
                                         'website': 'https://phonely.ai'}}
        routes[yc.JOB_PAGE.format(slug='phonely', code='bg3qHFa')] = Response(
            200, job_html(FX['postings']['121729'], props), url=open_url)
        routes[yc.JOB_PAGE.format(slug='phonely', code='zzzzzzz')] = Response(404, 'gone')
        routes[yc.JOB_PAGE.format(slug='nobody', code='zzzzzzz')] = Response(404, 'gone')
        run, _ = search({'jobUrls': [open_url, 'https://www.ycombinator.com/companies/phonely/jobs/zzzzzzz-old-job',
                                     'https://www.ycombinator.com/companies/nobody/jobs/zzzzzzz-x']}, routes)
        rows = run.delivery.pushed
        self.assertEqual([r['status'] for r in rows], ['open', 'closed', 'not found'])
        self.assertEqual((rows[0]['postedAt'], rows[0]['batch'], rows[0]['jobId']), ('2026-11-10T04:23:52Z', 'S24', '121729'))

    def test_a_work_at_a_startup_link_is_read_on_its_yc_page(self):
        routes = lists_routes()
        open_url = 'https://www.ycombinator.com/companies/phonely/jobs/bg3qHFa-forward-deployed-engineer'
        raw = next(j for j in FX['lists']['phonely'] if j['id'] == 121729)
        routes[yc.WAAS_JOB.format(id='121729')] = Response(200, inertia_html(
            'jobs/public/pages/JobDetailV2Page', {'job': {'id': 121729}, 'company': {'slug': 'phonely'}}))
        routes[yc.JOB_PAGE.format(slug='phonely', code='bg3qHFa')] = Response(
            200, job_html(FX['postings']['121729'], {'job': raw, 'company': {'name': 'Phonely'}}), url=open_url)
        run, http = search({'jobUrls': ['https://www.workatastartup.com/jobs/121729',
                                        'https://www.workatastartup.com/jobs/121750']}, routes)
        self.assertEqual([r['status'] for r in run.delivery.pushed], ['open', 'not found'])
        waas = [h for m, u, h in http.calls if u.startswith(yc.WAAS)]
        self.assertTrue(all(h.get('Accept') == 'text/html' for h in waas))       # else Work at a Startup answers 406


if __name__ == '__main__':
    unittest.main()
