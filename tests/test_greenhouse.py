"""Greenhouse: links, places, pay, custom fields, dates, blocks and lookups, on answers shaped like the Job Board API's
own (boards-api.greenhouse.io and api.greenhouse.io, 2026-11-02)."""
import asyncio
import unittest
from unittest.mock import patch

from ats_jobs.http import BoardError, BoardMissing
from ats_jobs.inputs import parse_input
from ats_jobs.platforms.base import Board
from ats_jobs.platforms.greenhouse import Greenhouse, custom_fields, names, pay_of, places_of, utc

SESSION = {'token': 'gitlab'}
CONTENT = '&lt;p&gt;Build the &lt;strong&gt;platform&lt;/strong&gt; &amp;amp; ship.&lt;/p&gt;&lt;p&gt;Next&lt;/p&gt;'
RECORD = {
    'absolute_url': 'https://job-boards.greenhouse.io/gitlab/jobs/8638232002', 'id': 8638232002, 'internal_job_id': 6449994002,
    'title': ' AI Transformation  Owner, CRO ', 'company_name': 'GitLab ', 'requisition_id': '6653 ',
    'location': {'name': 'Remote, Canada; Remote, United States'}, 'language': 'en',
    'first_published': '2026-07-22T13:38:40-04:00', 'updated_at': '2026-10-28T12:23:39-04:00', 'application_deadline': None,
    'metadata': [{'id': 1, 'name': 'Employment Type', 'value': 'Full-time', 'value_type': 'single_select'},
                 {'id': 2, 'name': 'Workplace Type', 'value': 'Remote', 'value_type': 'single_select'}],
    'pay_input_ranges': [{'min_cents': 13200000, 'max_cents': 15500000, 'currency_type': 'USD', 'title': 'Zone 1',
                          'blurb': '<p>Base pay</p>'},
                         {'min_cents': 11600000, 'max_cents': 13700000, 'currency_type': 'USD', 'title': 'Zone 2', 'blurb': ''}],
    'content': CONTENT, 'departments': [{'id': 4115239002, 'name': 'Data Engineering', 'child_ids': [], 'parent_id': 1}],
    'offices': [{'id': 1, 'name': 'North America', 'location': None, 'child_ids': [], 'parent_id': None}],
    'data_compliance': [{'type': 'gdpr', 'requires_consent': False}]}


class Response:
    def __init__(self, status_code, data=None, headers=None):
        self.status_code, self.data = status_code, data
        self.headers = headers or {'content-type': 'application/json', 'server': 'istio-envoy'}

    def json(self):
        if self.data is None:
            raise ValueError('not JSON')
        return self.data


CLOUDFRONT_BLOCK = Response(403, None, {'content-type': 'text/html', 'server': 'CloudFront', 'x-cache': 'Error from cloudfront'})
ORIGIN_BLOCK = Response(406, None, {'content-type': 'text/html', 'x-cache': 'Error from cloudfront'})


def sender(answers: list):
    async def send(http, method, url, **kwargs):
        calls.append((url, kwargs.get('params')))
        return answers.pop(0)
    calls = []
    return send, calls


def platform(sites=None) -> Greenhouse:
    p = Greenhouse()
    p._sites = sites if sites is not None else {'stripe.com': 'stripe', 'careers.airbnb.com': 'airbnb'}
    return p


P = platform()


class Links(unittest.TestCase):
    def test_board_and_job_links_on_every_host(self):
        for text, expected in (
                ('https://job-boards.greenhouse.io/gitlab', ('gitlab', None)),
                ('job-boards.greenhouse.io/gitlab/', ('gitlab', None)),
                ('https://job-boards.greenhouse.io/gitlab/jobs/8638232002', ('gitlab', '8638232002')),
                ('https://boards.greenhouse.io/gitlab/jobs/8638232002?gh_src=34e5917e2', ('gitlab', '8638232002')),
                ('http://boards.greenhouse.io/gitlab?gh_jid=8638232002', ('gitlab', '8638232002')),
                ('https://job-boards.eu.greenhouse.io/feverup/jobs/4982619101', ('feverup', '4982619101')),
                ('https://boards.eu.greenhouse.io/feverup', ('feverup', None))):
            self.assertEqual(P.read_link(text), expected, text)

    def test_capitals_trailing_slashes_and_queries(self):
        self.assertEqual(P.read_link('HTTPS://BOARDS.GREENHOUSE.IO/GitLab/Jobs/8638232002/?utm_source=x#app'),
                         ('gitlab', '8638232002'))
        self.assertEqual(P.read_link('  https://job-boards.greenhouse.io/GITLAB  '), ('gitlab', None))

    def test_embed_links(self):
        for text, expected in (
                ('https://boards.greenhouse.io/embed/job_board?for=gitlab', ('gitlab', None)),
                ('https://boards.greenhouse.io/embed/job_board/js?for=gitlab', ('gitlab', None)),
                ('https://boards.greenhouse.io/embed/job_app?for=gitlab&token=8638232002', ('gitlab', '8638232002')),
                ('https://job-boards.greenhouse.io/embed/job_app?for=GitLab&token=8638232002', ('gitlab', '8638232002')),
                ('https://app.greenhouse.io/embed/job_board?for=gitlab', ('gitlab', None))):
            self.assertEqual(P.read_link(text), expected, text)

    def test_api_links(self):
        self.assertEqual(P.read_link('https://boards-api.greenhouse.io/v1/boards/stripe/jobs/8172487'), ('stripe', '8172487'))
        self.assertEqual(P.read_link('https://api.greenhouse.io/v1/boards/stripe/jobs?content=true'), ('stripe', None))
        self.assertEqual(P.read_link('https://api.greenhouse.io/v1/boards/stripe/embed/job?id=8172487'), ('stripe', '8172487'))

    def test_tokens_keep_hyphens_underscores_and_digits(self):
        self.assertEqual(P.read_link('https://job-boards.greenhouse.io/dicefm-careers'), ('dicefm-careers', None))
        self.assertEqual(P.read_link('https://job-boards.greenhouse.io/co-op_intern/jobs/12'), ('co-op_intern', '12'))
        self.assertEqual(P.read_link('https://job-boards.greenhouse.io/1800contacts'), ('1800contacts', None))

    def test_a_company_site_with_gh_jid_reads_through_the_board_list(self):
        self.assertEqual(P.read_link('https://stripe.com/jobs/search?gh_jid=8172487'), ('stripe', '8172487'))
        self.assertEqual(P.read_link('https://www.stripe.com/jobs/search?gh_jid=8172487'), ('stripe', '8172487'))
        self.assertEqual(P.read_link('careers.airbnb.com/positions/8345532?gh_jid=8345532'), ('airbnb', '8345532'))
        self.assertIsNone(P.read_link('https://unknown.example/careers?gh_jid=123'))
        self.assertIsNone(P.read_link('https://stripe.com/jobs/search?gh_jid=abc'))
        self.assertIsNone(P.read_link('https://stripe.com/jobs'))

    def test_a_site_shared_by_two_boards_is_not_guessed(self):
        boards = [Board('acme', 'Acme', 'https://careers.acme.com'), Board('acmeeu', 'Acme EU', 'https://careers.acme.com'),
                  Board('stripe', 'Stripe', 'https://www.stripe.com')]
        p = Greenhouse()
        with patch.object(Greenhouse, 'boards', lambda self: (boards, None)):
            self.assertEqual(p.sites(), {'stripe.com': 'stripe'})
        self.assertEqual(p.aliases(boards[2]), ['www.stripe.com', 'stripe.com'])
        self.assertEqual(p.aliases(Board('gitlab', 'GitLab')), [])

    def test_a_host_or_a_page_that_names_no_board(self):
        for text in ('boards.greenhouse.io', 'https://job-boards.greenhouse.io/', 'https://boards.greenhouse.io/embed/job_board',
                     'https://app.greenhouse.io/users/sign_in', 'https://job-boards.greenhouse.io/internal_job_board',
                     'https://boards.greenhouse.io/embed/job_app?token=8638232002'):
            self.assertEqual(P.read_link(text), (None, None), text)

    def test_a_job_id_that_is_no_number_is_left_out(self):
        self.assertEqual(P.read_link('https://job-boards.greenhouse.io/gitlab/jobs/abc'), ('gitlab', None))

    def test_other_text_is_no_link(self):
        for text in ('https://example.com/careers', 'software engineer', 'gitlab', 'jobs.lever.co/gitlab', 'https://grnh.se/a1b2',
                     'https://www.greenhouse.com/careers', 'https://greenhouse.io/gitlab', 'https://jobs.ashbyhq.com/ramp', ''):
            self.assertIsNone(P.read_link(text), text)

    def test_a_pasted_dork_limits_the_search_to_one_board(self):
        cfg = parse_input({'searchTerms': ['site:boards.greenhouse.io/gitlab intext:engineer']}, P, {})
        self.assertEqual((cfg.search_terms, cfg.companies), (['engineer'], ['gitlab']))
        cfg = parse_input({'searchTerms': ['site:boards.greenhouse.io intext:"data engineer"']}, P, {})
        self.assertEqual((cfg.search_terms, cfg.companies), (['data engineer'], []))

    def test_links_in_the_search_box(self):
        cfg = parse_input({'searchTerms': ['https://job-boards.greenhouse.io/gitlab/jobs/8638232002', 'job-boards.greenhouse.io/figma']}, P, {})
        self.assertEqual((cfg.job_urls[0][:2], cfg.companies), (('gitlab', '8638232002'), ['figma']))

    def test_a_typed_company_becomes_a_token(self):
        self.assertEqual(P.guess_board('Acme Corp'), 'acmecorp')
        self.assertEqual(P.guess_board('GitLab'), 'gitlab')
        self.assertEqual(P.guess_board('dicefm-careers'), 'dicefm-careers')
        self.assertEqual(P.guess_board("O'Reilly Media"), 'oreillymedia')
        self.assertIsNone(P.guess_board('...'))


class Parsing(unittest.TestCase):
    def test_dates_become_utc(self):
        self.assertEqual(utc('2026-10-19T11:17:24-04:00'), '2026-10-19T15:17:24Z')
        self.assertEqual(utc('2026-11-02T01:26:54Z'), '2026-11-02T01:26:54Z')
        self.assertIsNone(utc(None))
        self.assertIsNone(utc('soon'))

    def test_several_places_in_one_location(self):
        self.assertEqual(places_of({'name': 'New York, NY;  San Francisco, CA | Remote'}),
                         [{'name': 'New York, NY'}, {'name': 'San Francisco, CA'}, {'name': 'Remote'}])
        self.assertEqual(places_of({'name': 'Dublin; Dublin;'}), [{'name': 'Dublin'}])
        self.assertEqual(places_of({'name': 'Remote / New York'}), [{'name': 'Remote / New York'}])
        self.assertEqual(places_of({'name': ''}), [])
        self.assertEqual(places_of(None), [])

    def test_a_job_ad_pasted_as_the_location_is_no_place(self):
        ad = ('If you love your community, this is the outside sales opportunity built for that. Best Version Media has '
              'spent nearly 20 years bringing communities together. Today, more than 1,300 local magazines help local '
              'businesses grow; We are looking for the next person to build one in the area. What You Bring: sales '
              'ability, or the drive to build it. Your own transportation, phone and laptop. Apply today.')
        self.assertEqual(places_of({'name': ad}), [])
        towns = '; '.join(f'Town {i}, TX' for i in range(40))      # a long list of places is still places
        self.assertEqual(len(places_of({'name': towns})), 40)

    def test_custom_fields_state_the_work_type_and_job_type(self):
        self.assertEqual(custom_fields(RECORD['metadata']), ('remote', 'full-time'))
        for value, work in (('On-site', 'onsite'), ('Onsite', 'onsite'), ('In-office', 'onsite'), ('Hybrid (Travel-Required)', 'hybrid'),
                            ('Hybrid, 3 days in office', 'hybrid'), ('Fully Remote', 'remote'), ('United States (Remote)', 'remote'),
                            (['On-site'], 'onsite'), ('Remote or Hybrid', None), ('Client Site', None), (None, None)):
            self.assertEqual(custom_fields([{'name': 'Workplace Type', 'value': value}])[0], work, value)
        for value, kind in (('Full-time', 'full-time'), ('Full Time', 'full-time'), ('Part-time', 'part-time'), ('Regular', None),
                            ('Temporary/ locum', 'temporary'), ('Permanent', 'full-time'), ('Contract', 'contract'),
                            ('Intern', 'intern'), ('Permanent Part-time', None), ('Other', None), (3, None)):
            self.assertEqual(custom_fields([{'name': 'Employment Type', 'value': value}])[1], kind, value)

    def test_a_yes_no_remote_field_says_only_yes(self):
        self.assertEqual(custom_fields([{'name': 'Remote Worker', 'value': True}])[0], 'remote')
        self.assertIsNone(custom_fields([{'name': 'Remote Worker', 'value': False}])[0])

    def test_unknown_fields_and_odd_metadata_say_nothing(self):
        self.assertEqual(custom_fields([{'name': 'Job Family', 'value': 'Remote Sales'}, 'junk', None]), (None, None))
        self.assertEqual(custom_fields(None), (None, None))
        self.assertEqual(custom_fields({'name': 'Employment Type'}), (None, None))

    def test_pay_ranges(self):
        self.assertEqual(pay_of(RECORD['pay_input_ranges']), ('USD 132,000 - 155,000; USD 116,000 - 137,000', 116000, 155000, 'USD'))
        self.assertEqual(pay_of([{'min_cents': 4000, 'max_cents': 4000, 'currency_type': 'USD', 'title': 'Hourly Rate:'}]),
                         ('USD 40', 40, 40, 'USD'))
        self.assertEqual(pay_of([{'min_cents': 2235, 'max_cents': 2403, 'currency_type': 'USD'}])[0], 'USD 22.35 - 24.03')

    def test_pay_in_two_currencies_counts_the_first(self):
        ranges = [{'min_cents': 10000000, 'max_cents': 12000000, 'currency_type': 'USD'},
                  {'min_cents': 13000000, 'max_cents': 15000000, 'currency_type': 'CAD'}]
        self.assertEqual(pay_of(ranges), ('USD 100,000 - 120,000; CAD 130,000 - 150,000', 100000, 120000, 'USD'))

    def test_empty_or_broken_pay_is_no_pay(self):
        for ranges in (None, [], [{'min_cents': None, 'max_cents': None, 'currency_type': 'USD'}],
                       [{'min_cents': 0, 'max_cents': 0, 'currency_type': 'USD'}], [{'min_cents': 100, 'max_cents': 200}], ['x']):
            self.assertEqual(pay_of(ranges), (None, None, None, None), ranges)
        self.assertEqual(pay_of([{'min_cents': None, 'max_cents': 9000000, 'currency_type': 'EUR'}]), ('EUR 90,000', 90000, 90000, 'EUR'))

    def test_departments(self):
        self.assertEqual(names(RECORD['departments']), 'Data Engineering')
        self.assertEqual(names([{'name': 'Sales'}, {'name': ' Sales '}, {'name': 'Growth'}]), 'Sales, Growth')
        self.assertIsNone(names(None))

    def test_a_record_becomes_a_job(self):
        job = P.job(SESSION, RECORD)
        self.assertEqual((job['id'], job['company'], job['title'], job['workType'], job['employmentType'], job['dateOnly']),
                         ('8638232002', 'GitLab', 'AI Transformation Owner, CRO', 'remote', 'full-time', False))
        self.assertEqual((job['location'], job['otherLocations']), ('Remote, Canada', ['Remote, United States']))
        self.assertEqual((job['postedAt'], job['expiresAt']), ('2026-07-22T17:38:40Z', None))
        self.assertEqual((job['salary'], job['salaryMin'], job['salaryMax'], job['salaryCurrency']),
                         ('USD 132,000 - 155,000; USD 116,000 - 137,000', 116000, 155000, 'USD'))
        self.assertEqual((job['department'], job['reference']), ('Data Engineering', '6653'))
        self.assertEqual(job['descriptionHtml'], '<p>Build the <strong>platform</strong> &amp; ship.</p><p>Next</p>')
        self.assertEqual(job['description'], 'Build the platform & ship.\nNext')
        self.assertEqual(job['jobUrl'], RECORD['absolute_url'])
        self.assertEqual(job['applyUrl'], RECORD['absolute_url'])

    def test_a_record_from_the_light_list_has_no_description(self):
        light = {k: v for k, v in RECORD.items() if k not in ('content', 'departments', 'offices')}
        job = P.job(SESSION, light)
        self.assertEqual((job['description'], job['descriptionHtml'], job['department']), (None, None, None))

    def test_a_record_with_almost_nothing(self):
        job = P.job(SESSION, {'id': 7, 'title': None, 'location': None, 'metadata': None, 'pay_input_ranges': None,
                              'first_published': None, 'requisition_id': None, 'content': ''})
        self.assertEqual((job['title'], job['places'], job['location'], job['salary'], job['postedAt'], job['company'],
                          job['reference'], job['description']), ('', [], None, None, None, None, None, None))
        self.assertEqual(job['jobUrl'], 'https://job-boards.greenhouse.io/gitlab/jobs/7')


class Opening(unittest.TestCase):
    def test_a_board_is_opened_without_a_call(self):
        self.assertEqual(asyncio.run(P.open(None, Board('GitLab', 'GitLab'))), {'token': 'gitlab'})

    def test_text_that_cannot_be_a_token_is_never_sent(self):
        for key in ('../../secret', '', 'two words', 'a/b'):
            self.assertIsNone(asyncio.run(P.open(None, Board(key, 'x'))), key)


class Reading(unittest.TestCase):
    def collect(self, answers, p=None):
        p = p or platform()

        async def run():
            return [page async for page in p.pages(None, dict(SESSION), None, None)]
        send, calls = sender(answers)
        with patch('ats_jobs.platforms.greenhouse.send', send):
            return asyncio.run(run()), calls

    def test_a_board_is_one_page_from_the_light_list_with_pay(self):
        pages, calls = self.collect([Response(200, {'jobs': [RECORD, {**RECORD, 'id': 2}, {'title': 'no id'}], 'meta': {'total': 3}})])
        self.assertEqual((len(pages), [j['id'] for j in pages[0]]), (1, ['8638232002', '2']))
        self.assertEqual(calls, [('https://api.greenhouse.io/v1/boards/gitlab/jobs', {'pay_transparency': 'true'})])

    def test_descriptions_are_asked_for_only_when_searched(self):
        p = platform()
        p.description_terms = ('platform engineer',)
        _, calls = self.collect([Response(200, {'jobs': []})], p)
        self.assertEqual(calls[0][1], {'pay_transparency': 'true', 'content': 'true'})

    def test_only_descriptions_that_can_match_are_read_in_full(self):
        p = platform()
        p.description_terms = ('kubernetes', 'front end')
        records = [{**RECORD, 'id': 1, 'content': '&lt;p&gt;Run &lt;b&gt;Kubernetes&lt;/b&gt; clusters&lt;/p&gt;'},
                   {**RECORD, 'id': 2, 'content': '&lt;p&gt;Sell software&lt;/p&gt;'},
                   {**RECORD, 'id': 3, 'content': 'Build our &lt;i&gt;frontend&lt;/i&gt;'},
                   {**RECORD, 'id': 4, 'content': 'Caf&amp;eacute; kubern&amp;#101;tes'}]
        pages, _ = self.collect([Response(200, {'jobs': records})], p)
        self.assertEqual([j['description'] for j in pages[0]],
                         ['Run Kubernetes clusters', None, 'Build our frontend', 'Caf\u00e9 kubernetes'])

    def test_a_board_the_engine_stops_reading_early_hands_its_slot_on(self):
        p = platform()
        p.description_terms, p.heavy = ('kubernetes',), asyncio.Semaphore(1)
        send, _ = sender([Response(200, {'jobs': []}), Response(200, {'jobs': [RECORD]})])

        async def two():
            async for _ in p.pages(None, {'token': 'a'}, None, None):
                break                               # the engine stops at an empty page
            return await asyncio.wait_for(p.pages(None, {'token': 'b'}, None, None).__anext__(), 2)
        with patch('ats_jobs.platforms.greenhouse.send', send):
            self.assertEqual(len(asyncio.run(two())), 1)

    def test_a_missing_board(self):
        with self.assertRaises(BoardMissing):
            self.collect([Response(404, {'status': 404, 'error': 'Job not found'})])

    def test_answers_that_are_not_a_job_list_are_errors(self):
        for bad in (Response(500, {}), Response(200), Response(200, {'jobs': None}), Response(200, ['x']),
                    Response(403, {'status': 403}), Response(301, None)):
            with self.assertRaises(BoardError):
                self.collect([bad])


class Blocks(unittest.TestCase):
    def test_a_blocked_address_hands_over_to_the_other(self):
        p = platform()
        send, calls = sender([CLOUDFRONT_BLOCK, Response(200, {'jobs': []}), Response(200, {'jobs': []})])

        async def two():
            return [page async for page in p.pages(None, dict(SESSION), None, None)] + \
                   [page async for page in p.pages(None, {'token': 'figma'}, None, None)]
        with patch('ats_jobs.platforms.greenhouse.send', send):
            self.assertEqual(asyncio.run(two()), [[], []])
        self.assertEqual([url.split('/v1')[0] for url, _ in calls],
                         ['https://api.greenhouse.io', 'https://boards-api.greenhouse.io', 'https://boards-api.greenhouse.io'])

    def test_both_addresses_blocked_waits_then_says_why(self):
        p = platform()
        waits = []

        async def sleep(seconds):
            waits.append(seconds)
        send, calls = sender([CLOUDFRONT_BLOCK] * 8)
        with patch('ats_jobs.platforms.greenhouse.send', send), patch('ats_jobs.platforms.greenhouse.asyncio.sleep', sleep), \
                patch('ats_jobs.engine.seconds_left_in_run', lambda: None):
            with self.assertRaisesRegex(BoardError, 'HTTP 403 from both API addresses'):
                asyncio.run(p.get(None, 'gitlab/jobs'))
        self.assertEqual((waits, len(calls)), ([30, 60, 90], 8))

    def test_the_406_page_of_an_address_block_is_a_block_too(self):
        p = platform()

        async def sleep(seconds):
            return None
        send, calls = sender([ORIGIN_BLOCK, ORIGIN_BLOCK, Response(200, {'jobs': []})])
        with patch('ats_jobs.platforms.greenhouse.send', send), patch('ats_jobs.platforms.greenhouse.asyncio.sleep', sleep), \
                patch('ats_jobs.engine.seconds_left_in_run', lambda: None):
            self.assertEqual(asyncio.run(p.get(None, 'gitlab/jobs')).json(), {'jobs': []})
        self.assertEqual(len(calls), 3)

    def test_an_api_answer_in_json_is_never_a_block(self):
        p = platform()
        send, calls = sender([Response(403, {'status': 403, 'error': 'Forbidden'})])
        with patch('ats_jobs.platforms.greenhouse.send', send):
            self.assertEqual(asyncio.run(p.get(None, 'gitlab/jobs')).status_code, 403)
        self.assertEqual(len(calls), 1)

    def test_no_wait_runs_past_the_time_limit(self):
        p = platform()
        send, calls = sender([CLOUDFRONT_BLOCK] * 2)
        with patch('ats_jobs.platforms.greenhouse.send', send), patch('ats_jobs.engine.seconds_left_in_run', lambda: 45.0):
            with self.assertRaises(BoardError):
                asyncio.run(p.get(None, 'gitlab/jobs'))
        self.assertEqual(len(calls), 2)

    def test_a_block_comes_back_after_a_wait(self):
        p = platform()

        async def sleep(seconds):
            return None
        send, _ = sender([CLOUDFRONT_BLOCK, CLOUDFRONT_BLOCK, Response(200, {'name': 'GitLab'})])
        with patch('ats_jobs.platforms.greenhouse.send', send), patch('ats_jobs.platforms.greenhouse.asyncio.sleep', sleep), \
                patch('ats_jobs.engine.seconds_left_in_run', lambda: None):
            self.assertEqual(asyncio.run(p.get(None, 'gitlab')).json(), {'name': 'GitLab'})


class Details(unittest.TestCase):
    def details(self, answers, job):
        send, calls = sender(answers)
        with patch('ats_jobs.platforms.greenhouse.send', send):
            return asyncio.run(P.details(None, dict(SESSION), job)), calls

    def test_details_add_the_description_and_department(self):
        light = P.job(SESSION, {k: v for k, v in RECORD.items() if k not in ('content', 'departments')})
        job, calls = self.details([Response(200, RECORD)], light)
        self.assertEqual((job['description'], job['department'], job['title']), ('Build the platform & ship.\nNext', 'Data Engineering',
                                                                                   'AI Transformation Owner, CRO'))
        self.assertEqual(calls, [('https://api.greenhouse.io/v1/boards/gitlab/jobs/8638232002', {'pay_transparency': 'true'})])

    def test_a_job_read_with_its_description_is_not_read_again(self):
        job, calls = self.details([], P.job(SESSION, RECORD))
        self.assertEqual((job['department'], calls), ('Data Engineering', []))

    def test_a_job_closed_since_the_list_keeps_what_the_list_said(self):
        light = P.job(SESSION, {k: v for k, v in RECORD.items() if k != 'content'})
        job, _ = self.details([Response(404, {'status': 404, 'error': 'Job not found'})], light)
        self.assertEqual(job, light)


class Lookup(unittest.TestCase):
    def check(self, answers, session=None):
        send, calls = sender(answers)
        with patch('ats_jobs.platforms.greenhouse.send', send):
            return asyncio.run(P.lookup(None, session if session is not None else dict(SESSION), '8638232002')), calls

    def test_an_open_job(self):
        (status, job), calls = self.check([Response(200, RECORD)])
        self.assertEqual((status, job['postedAt'], job['salaryMin'], len(calls)), ('open', '2026-07-22T17:38:40Z', 116000, 1))

    def test_a_closed_job_on_a_board_that_exists(self):
        (status, job), calls = self.check([Response(404, {'status': 404, 'error': 'Job not found'}), Response(200, {'name': 'GitLab'})])
        self.assertEqual((status, job, calls[1][0]), ('closed', None, 'https://api.greenhouse.io/v1/boards/gitlab'))

    def test_a_job_on_a_board_that_does_not_exist(self):
        (status, _), _ = self.check([Response(404, {'status': 404, 'error': 'Job not found'}),
                                     Response(404, {'status': 404, 'error': 'Job board not found'})])
        self.assertEqual(status, 'not found')

    def test_many_links_of_one_board_ask_about_the_board_once(self):
        session = dict(SESSION)
        missing = Response(404, {'status': 404, 'error': 'Job not found'})
        send, calls = sender([missing, Response(200, {'name': 'GitLab'}), missing])

        async def both():
            return [await P.lookup(None, session, '1'), await P.lookup(None, session, '2')]
        with patch('ats_jobs.platforms.greenhouse.send', send):
            self.assertEqual([found[0] for found in asyncio.run(both())], ['closed', 'closed'])
        self.assertEqual(len(calls), 3)

    def test_a_failure_stays_an_error(self):
        with self.assertRaises(BoardError):
            self.check([Response(502, None)])
        with self.assertRaises(BoardError):
            self.check([Response(404, {}), Response(500, None)])


if __name__ == '__main__':
    unittest.main()
