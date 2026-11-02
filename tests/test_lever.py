"""Lever: links, keys, parsing, opening, paging, the Cloudflare fallback and lookups, on answers shaped like Lever's
Postings API (2026-11-02)."""
import asyncio
import unittest
from unittest.mock import patch

from ats_jobs import engine
from ats_jobs.http import BoardError, BoardMissing
from ats_jobs.inputs import parse_input, read_company, read_job_url
from ats_jobs.platforms import PLATFORMS
from ats_jobs.platforms.base import Board
from ats_jobs.platforms.lever import (PAGE_SIZE, SWITCH_AFTER, Lever, country_name, description_html,
                                      description_text, job_type, pay, places, posted, tidy)
from ats_jobs.rows import job_area, job_places, job_row

JOB_ID = '5168e8fd-fec1-4fea-b7a1-81bdaea65850'
JSON = {'content-type': 'application/json; charset=utf-8'}
XML = {'content-type': 'text/xml; charset=utf-8'}
HTML = {'content-type': 'text/html; charset=UTF-8'}
NOT_FOUND = '{"ok":false,"error":"Document not found"}'
RECORD = {
    'id': JOB_ID, 'text': ' Customer  Success Manager &amp; Lead ', 'createdAt': 1788199939703, 'country': 'US',
    'workplaceType': 'hybrid',
    'categories': {'commitment': 'Regular Full Time (Salary)', 'department': 'Customer Success',
                   'location': 'Atlanta, Georgia', 'team': 'Customer Success Team',
                   'allLocations': ['Atlanta, Georgia', 'Remote', 'Atlanta, Georgia']},
    'description': '<div>Grow our <b>customers</b>.</div>', 'descriptionPlain': 'Grow our customers.\n \n',
    'lists': [{'text': 'Skill Set:', 'content': '\n<li>3+ years of SaaS &amp; account work</li>\n<li>Results first</li>'},
              {'text': '', 'content': ''}],
    'additional': '<div>We value everyone.</div>', 'additionalPlain': 'We value everyone.',
    'salaryRange': {'min': 85000, 'max': 175000, 'currency': 'usd', 'interval': 'per-year-salary'},
    'salaryDescription': '<div>Base pay only.</div>', 'salaryDescriptionPlain': 'Base pay only.',
    'hostedUrl': f'https://jobs.lever.co/leverdemo/{JOB_ID}', 'applyUrl': f'https://jobs.lever.co/leverdemo/{JOB_ID}/apply',
}
FEED = ('<?xml version="1.0" encoding="UTF-8"?><jobs><job>\n\t<id><![CDATA[x]]></id>\n'
        '\t<employer><![CDATA[Engel &amp; Völkers]]></employer>\n</job></jobs>')


class Response:
    def __init__(self, status_code, data=None, text='', headers=None):
        self.status_code, self.data = status_code, data
        self.text = text or ('' if data is None else str(data))
        self.headers = headers if headers is not None else (JSON if status_code in (200, 404) else HTML)

    def json(self):
        if self.data is None:
            raise ValueError('not JSON')
        return self.data


def gone():
    return Response(404, text=NOT_FOUND, headers=JSON)


def sender(answers: list):
    async def send(http, method, url, **kwargs):
        calls.append((method, url, kwargs.get('params')))
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    calls = []
    return send, calls


def fresh(known=()):
    platform = Lever()
    platform._known = set(known)
    return platform


class Links(unittest.TestCase):
    P = Lever()

    def test_a_board_in_every_form(self):
        for text in ('https://jobs.lever.co/palantir', 'jobs.lever.co/palantir/', 'http://jobs.lever.co/palantir',
                     'https://jobs.lever.co/palantir?lever-source=x&utm_source=y', 'https://jobs.lever.co/palantir/?team=Ops',
                     'JOBS.LEVER.CO/palantir', 'www.jobs.lever.co/palantir', 'https://jobs.lever.co/palantir#top',
                     'https://api.lever.co/v0/postings/palantir?mode=json', 'https://api.lever.co/v0/postings/palantir?mode=iframe',
                     'https://jobs.lever.co/v0/postings/palantir?mode=json&skip=0&limit=500'):
            self.assertEqual(self.P.read_link(text), ('palantir', None), text)

    def test_a_job_in_every_form(self):
        for text in (f'https://jobs.lever.co/palantir/{JOB_ID}', f'jobs.lever.co/palantir/{JOB_ID}/',
                     f'https://jobs.lever.co/palantir/{JOB_ID}/apply', f'https://jobs.lever.co/palantir/{JOB_ID}/apply/',
                     f'https://jobs.lever.co/palantir/{JOB_ID}?lever-origin=applied&lever-source%5B%5D=x',
                     f'https://jobs.lever.co/palantir/{JOB_ID}/apply?utm_source=y', f'https://jobs.lever.co/palantir/{JOB_ID}#li',
                     f'https://jobs.lever.co/palantir/{JOB_ID}%20', f'https://jobs.lever.co/palantir/{JOB_ID.upper()}',
                     f'https://api.lever.co/v0/postings/palantir/{JOB_ID}'):
            self.assertEqual(self.P.read_link(text), ('palantir', JOB_ID), text)

    def test_an_eu_site_carries_its_region(self):
        self.assertEqual(self.P.read_link('https://jobs.eu.lever.co/engelvoelkers'), ('engelvoelkers/eu', None))
        self.assertEqual(self.P.read_link(f'jobs.eu.lever.co/engelvoelkers/{JOB_ID}/apply'), ('engelvoelkers/eu', JOB_ID))
        self.assertEqual(self.P.read_link('https://api.eu.lever.co/v0/postings/engelvoelkers?mode=json'), ('engelvoelkers/eu', None))

    def test_a_site_keeps_its_case_and_its_dots(self):
        self.assertEqual(self.P.read_link('https://jobs.lever.co/AIFund'), ('AIFund', None))
        self.assertEqual(self.P.read_link(f'https://jobs.lever.co/arcteryx.com/{JOB_ID}'), ('arcteryx.com', JOB_ID))

    def test_the_host_alone_names_no_site(self):
        for text in ('jobs.lever.co', 'https://jobs.lever.co/', 'https://jobs.lever.co/?swcfpc=1', 'https://api.lever.co/',
                     'https://api.lever.co/v1/postings', 'https://jobs.eu.lever.co',
                     'https://jobs.lever.co/embed/08a52d7b-2b64-46ab-8932-3026bd5a58d7',   # the old embed link: no site
                     'https://jobs.lever.co/cdn-cgi/l/email-protection', 'https://jobs.lever.co/robots.txt'):
            self.assertEqual(self.P.read_link(text), (None, None), text)

    def test_other_text_is_no_link(self):
        for text in ('https://example.com/careers', 'software engineer', 'palantir', 'https://www.lever.co/',
                     'lever.co/careers', 'boards.greenhouse.io/palantir', 'https://jobs.ashbyhq.com/ramp',
                     'https://jobs.lever.co.evil.com/palantir'):
            self.assertIsNone(self.P.read_link(text), text)

    def test_a_typed_company_becomes_a_site_name(self):
        self.assertEqual(self.P.guess_board('Acme Corp'), 'AcmeCorp')
        self.assertEqual(self.P.guess_board("Arc'teryx"), 'Arcteryx')
        self.assertEqual(self.P.guess_board('arcteryx.com'), 'arcteryx.com')
        self.assertIsNone(self.P.guess_board('...'))
        self.assertIsNone(self.P.guess_board('!!'))

    def test_a_pasted_dork_reads_as_a_keyword_and_a_site(self):
        cfg = parse_input({'searchTerms': ['site:jobs.lever.co/palantir intext:engineer']}, self.P, {})
        self.assertEqual((cfg.search_terms, cfg.companies), (['engineer'], ['palantir']))
        cfg = parse_input({'searchTerms': ['"site:http://jobs.lever.co" intext:Frontend']}, self.P, {})
        self.assertEqual((cfg.search_terms, cfg.companies), (['Frontend'], []))

    def test_links_in_the_inputs(self):
        names = engine.name_index(self.P, [Board('engelvoelkers/eu', 'Engel & Völkers'), Board('palantir', 'Palantir Technologies')])
        self.assertEqual(read_company('https://jobs.eu.lever.co/engelvoelkers', self.P, names), (['engelvoelkers/eu'], None))
        self.assertEqual(read_company('engelvoelkers', self.P, names)[0], ['engelvoelkers/eu'])
        self.assertEqual(read_company('Palantir', self.P, names)[0], ['palantir'])
        self.assertEqual(read_job_url(f'https://jobs.lever.co/palantir/{JOB_ID}/apply', self.P), (('palantir', JOB_ID), None))
        self.assertIsNone(read_job_url('https://jobs.lever.co/palantir', self.P)[0])

    def test_the_platform_is_registered(self):
        self.assertIs(PLATFORMS['lever'], Lever)


class Parsing(unittest.TestCase):
    def test_every_commitment_reads(self):
        for words, kind in (('Full-time', 'full-time'), ('Full Time', 'full-time'), ('Employee: Full Time', 'full-time'),
                            ('Regular, Full-Time Employee', 'full-time'), ('Permanent', 'full-time'), ('CDI', 'full-time'),
                            ('Efetivo', 'full-time'), ('Fulltime/Permanent', 'full-time'), ('Full-times', 'full-time'),
                            ('Part Time', 'part-time'), ('Regular Part-Time', 'part-time'), ('Contract: Full-Time', 'contract'),
                            ('Contract: Part-Time', 'contract'), ('Contractor', 'contract'), ('Internship', 'intern'),
                            ('Intern | Summer', 'intern'), ('Apprentice', 'intern'), ('Seasonal', 'temporary'),
                            ('Temporary', 'temporary'), ('Fixed Term', 'temporary'), ('CDD', 'temporary')):
            self.assertEqual(job_type(words), kind, words)
        for words in ('Full Time/Part Time', 'Remote - Latam', 'Hourly W2', '', None, 7):
            self.assertIsNone(job_type(words), words)

    def test_a_country_code_reads_as_its_name(self):
        self.assertEqual(country_name('US'), 'United States')
        self.assertEqual(country_name('gb'), 'United Kingdom')
        self.assertEqual(country_name('ZZ'), 'ZZ')
        self.assertIsNone(country_name(None))
        self.assertIsNone(country_name(''))

    def test_the_country_belongs_to_the_first_place(self):
        found = places(RECORD)
        self.assertEqual([p['name'] for p in found], ['Atlanta, Georgia', 'Remote'])
        self.assertEqual([p['country'] for p in found], ['United States', None])
        self.assertEqual(job_places({'places': found})[1], {'US'})        # Georgia the state, not the country
        self.assertFalse(job_area({'places': found}).open_remote)         # Remote with a US address is not worldwide

    def test_a_job_with_only_a_country_or_nothing(self):
        self.assertEqual(places({'country': 'DE', 'categories': {'location': None, 'allLocations': None}}),
                         [{'name': 'Germany', 'country': 'Germany'}])
        self.assertEqual(places({'categories': None}), [])

    def test_pay_with_its_stated_period(self):
        self.assertEqual(pay(RECORD), {'salary': 'USD 85,000 - 175,000 per year', 'salaryMin': 85000, 'salaryMax': 175000,
                                       'salaryCurrency': 'USD', 'salaryPeriod': 'year'})
        hourly = pay({'salaryRange': {'min': 22.5, 'max': 30, 'currency': 'CAD', 'interval': 'per-hour-wage'}})
        self.assertEqual((hourly['salary'], hourly['salaryPeriod']), ('CAD 22.50 - 30 per hour', 'hour'))

    def test_a_period_lever_names_but_we_do_not_guess(self):
        biweekly = pay({'salaryRange': {'min': 2000, 'max': None, 'currency': 'USD', 'interval': 'bi-week-salary'}})
        self.assertEqual((biweekly['salary'], biweekly['salaryPeriod'], biweekly['salaryMax']),
                         ('USD 2,000 every two weeks', None, None))
        odd = pay({'salaryRange': {'min': 100, 'max': 200, 'currency': None, 'interval': 'per-galaxy'}})
        self.assertEqual((odd['salary'], odd['salaryPeriod'], odd['salaryCurrency']), ('100 - 200', None, None))

    def test_no_pay(self):
        for record in ({}, {'salaryRange': None}, {'salaryRange': 'USD 5'}, {'salaryRange': {'currency': 'USD'}},
                       {'salaryRange': {'min': True, 'max': 'abc'}}):
            self.assertEqual(pay(record), {}, record)

    def test_the_description_is_what_the_page_shows(self):
        self.assertEqual(description_html(RECORD),
                         '<div>Grow our <b>customers</b>.</div><h3>Skill Set:</h3><ul>\n<li>3+ years of SaaS &amp; account '
                         'work</li>\n<li>Results first</li></ul><div>Base pay only.</div><div>We value everyone.</div>')
        tidied = 'Grow our customers.\nSkill Set:\n3+ years of SaaS & account work\nResults first\nBase pay only.\nWe value everyone.'
        self.assertEqual(tidy(description_text(RECORD)), tidied)
        self.assertIn('SaaS & account work', description_text(RECORD))      # words match before the lines are tidied

    def test_a_plain_text_lever_left_empty_is_read_from_its_html(self):
        record = {**RECORD, 'descriptionPlain': '', 'description': '<div><p>We are seeking an <strong>Atomic Source '
                  'Physicist</strong>.</p><p>&nbsp;</p></div>', 'additionalPlain': None}
        text = tidy(description_text(record))
        self.assertEqual(text.splitlines()[:2], ['We are seeking an Atomic Source Physicist .', 'Skill Set:'])   # text_of's spacing
        self.assertTrue(text.endswith('Base pay only.\nWe value everyone.'), text)

    def test_no_description(self):
        self.assertIsNone(description_html({}))
        self.assertIsNone(description_text({'lists': None, 'descriptionPlain': None}))
        self.assertIsNone(tidy(None))
        self.assertIsNone(tidy(' \n\u00a0\n'))

    def test_a_job_chosen_for_details_gets_tidy_lines_and_no_call(self):
        job = Lever().job({'site': 'x', 'region': 'global', 'link': 'https://jobs.lever.co/x'}, RECORD)
        send, calls = sender([])
        with patch('ats_jobs.platforms.lever.send', send):
            detailed = asyncio.run(Lever().details(None, None, job))
        self.assertEqual((detailed['description'].splitlines()[:2], calls), (['Grow our customers.', 'Skill Set:'], []))
        self.assertEqual(detailed['descriptionHtml'], job['descriptionHtml'])

    def test_the_posted_time_is_created_at(self):
        self.assertEqual(posted(1788199939703), '2026-08-31T18:12:19Z')
        for bad in (None, 'soon', float('inf'), [1]):
            self.assertIsNone(posted(bad))

    def test_a_record_becomes_a_job(self):
        job = Lever().job({'site': 'leverdemo', 'region': 'global', 'name': None, 'link': 'https://jobs.lever.co/leverdemo'},
                          RECORD)
        self.assertEqual((job['id'], job['title'], job['workType'], job['employmentType'], job['department'], job['team']),
                         (JOB_ID, 'Customer Success Manager & Lead', 'hybrid', 'full-time', 'Customer Success',
                          'Customer Success Team'))
        self.assertEqual((job['location'], job['otherLocations'], job['postedAt']),
                         ('Atlanta, Georgia', ['Remote'], '2026-08-31T18:12:19Z'))
        self.assertEqual((job['jobUrl'], job['applyUrl']), (RECORD['hostedUrl'], RECORD['applyUrl']))
        self.assertEqual((job['salary'], job['salaryPeriod']), ('USD 85,000 - 175,000 per year', 'year'))
        self.assertNotIn('company', job)
        self.assertNotIn('dateOnly', job)

    def test_a_record_with_almost_nothing(self):
        session = {'site': 'AIFund', 'region': 'eu', 'name': 'AI Fund', 'link': 'https://jobs.eu.lever.co/AIFund'}
        job = Lever().job(session, {'id': JOB_ID, 'text': None, 'categories': None, 'lists': [None, 'x'],
                                    'workplaceType': 'unspecified', 'createdAt': None, 'country': None})
        self.assertEqual((job['title'], job['location'], job['places'], job['workType'], job['employmentType'], job['postedAt']),
                         ('', None, [], None, None, None))
        self.assertEqual((job['jobUrl'], job['applyUrl'], job['company']),
                         (f'https://jobs.eu.lever.co/AIFund/{JOB_ID}', f'https://jobs.eu.lever.co/AIFund/{JOB_ID}/apply', 'AI Fund'))
        self.assertIsNone(job['description'])
        self.assertNotIn('salary', job)

    def test_the_fields_a_row_carries(self):
        job = Lever().job({'site': 'leverdemo', 'region': 'global', 'link': 'https://jobs.lever.co/leverdemo'}, RECORD)
        company = {'name': 'Lever Demo', 'slug': 'leverdemo', 'website': None}
        listed = {k for k in job_row('lever', company, job, 'now', False) if k not in Lever.never}
        detailed = {k for k in job_row('lever', company, job, 'now', True) if k not in Lever.never}
        self.assertEqual(listed, {'title', 'company', 'location', 'otherLocations', 'workType', 'employmentType',
                                  'department', 'team', 'salary', 'postedAt', 'jobUrl', 'applyUrl', 'status', 'platform',
                                  'companySlug', 'jobId', 'checkedAt'})
        self.assertEqual(detailed - listed, {'description', 'descriptionHtml', 'salaryMin', 'salaryMax', 'salaryCurrency',
                                             'salaryPeriod', 'country', 'countryCode'})
        row = job_row('lever', company, job, 'now', True)
        self.assertEqual((row['country'], row['countryCode']), ('United States', 'US'))

    def test_the_documented_and_the_measured_work_type(self):
        for said, kind in (('onsite', 'onsite'), ('on-site', 'onsite'), ('Remote', 'remote'), ('hybrid', 'hybrid'),
                           ('unspecified', None), (None, None)):
            job = Lever().job({'site': 'x', 'region': 'global', 'link': 'https://jobs.lever.co/x'},
                              {'id': JOB_ID, 'workplaceType': said})
            self.assertEqual(job['workType'], kind, said)


class Opening(unittest.TestCase):
    def open(self, platform, key, answers, name=None):
        send, calls = sender(answers)
        with patch('ats_jobs.platforms.lever.send', send):
            return asyncio.run(platform.open(None, Board(key, name or key))), calls

    def test_a_site_on_the_list_needs_no_call(self):
        session, calls = self.open(fresh({'palantir', 'engelvoelkers/eu'}), 'engelvoelkers/eu', [])
        self.assertEqual((session['site'], session['region'], session['link'], calls),
                         ('engelvoelkers', 'eu', 'https://jobs.eu.lever.co/engelvoelkers', []))

    def test_a_site_off_the_list_is_checked_and_named(self):
        session, calls = self.open(fresh(), 'spotify', [Response(200, [RECORD]), Response(200, text=FEED, headers=XML)])
        self.assertEqual((session['site'], session['region'], session['name'], session['live']),
                         ('spotify', 'global', 'Engel & Völkers', True))
        self.assertEqual(calls[0], ('GET', 'https://jobs.lever.co/v0/postings/spotify', {'mode': 'json', 'limit': 1}))
        self.assertEqual(calls[1][2], {'mode': 'xml', 'limit': 1})

    def test_a_site_with_no_posting_is_not_asked_for_its_name(self):
        session, calls = self.open(fresh(), 'lever', [Response(200, [])])
        self.assertEqual((session['name'], len(calls)), (None, 1))

    def test_a_site_not_on_the_global_instance_is_tried_on_the_eu_one(self):
        session, calls = self.open(fresh(), 'engelvoelkers', [gone(), Response(200, [])])
        self.assertEqual((session['region'], calls[1][1]), ('eu', 'https://jobs.eu.lever.co/v0/postings/engelvoelkers'))

    def test_a_name_in_the_wrong_case_is_tried_in_lower_case(self):
        session, calls = self.open(fresh(), 'Palantir', [gone(), gone(), Response(200, [])])
        self.assertEqual((session['site'], session['region'], len(calls)), ('palantir', 'global', 3))

    def test_a_missing_site_is_a_missing_board(self):
        session, calls = self.open(fresh(), 'Nosuch', [gone(), gone(), gone(), gone()])
        self.assertEqual((session, len(calls)), (None, 4))
        session, calls = self.open(fresh(), 'nosuch/eu', [gone()])
        self.assertEqual((session, len(calls)), (None, 1))

    def test_a_failure_stays_an_error(self):
        with self.assertRaises(BoardError):
            self.open(fresh(), 'acme', [Response(500), Response(500)])
        with self.assertRaises(BoardError):
            self.open(fresh(), 'acme', [Response(200, text='<html>', headers=JSON), Response(200, text='<html>', headers=JSON)])

    def test_text_that_cannot_be_a_site_is_never_sent(self):
        for key in ('../../secret', 'palantir\n', 'bitpanda\n/eu'):     # a new line would make httpx refuse the link
            session, calls = self.open(fresh(), key, [])
            self.assertEqual((session, calls), (None, []), repr(key))


class Fallback(unittest.TestCase):
    def get(self, platform, answers):
        send, calls = sender(answers)
        with patch('ats_jobs.platforms.lever.send', send):
            return asyncio.run(platform.get(None, 'global', 'spotify', {'mode': 'json'})), calls

    def test_lever_answers_through_cloudflare(self):
        response, calls = self.get(fresh(), [Response(200, [])])
        self.assertEqual((response.status_code, [c[1] for c in calls]), (200, ['https://jobs.lever.co/v0/postings/spotify']))
        response, calls = self.get(fresh(), [gone()])
        self.assertEqual((response.status_code, len(calls)), (404, 1))

    def test_a_refusal_goes_to_the_documented_host(self):
        platform = fresh()
        response, calls = self.get(platform, [Response(403, text='<html>challenge</html>'), Response(200, [])])
        self.assertEqual([c[1] for c in calls], ['https://jobs.lever.co/v0/postings/spotify',
                                                 'https://api.lever.co/v0/postings/spotify'])
        self.assertEqual((response.status_code, platform.refusals, platform.direct), (200, 1, False))

    def test_no_answer_through_cloudflare_goes_to_the_documented_host(self):
        platform = fresh()
        response, calls = self.get(platform, [BoardError('ConnectError'), Response(200, [])])
        self.assertEqual((response.status_code, len(calls), platform.refusals), (200, 2, 0))
        response, calls = self.get(platform, [BoardError('HTTP 429'), Response(200, [])])
        self.assertEqual(platform.refusals, 1)

    def test_a_run_refused_again_and_again_reads_only_the_documented_host(self):
        platform = fresh()
        for _ in range(SWITCH_AFTER):
            self.get(platform, [Response(200, text='<html>', headers=HTML), Response(200, [])])
        self.assertTrue(platform.direct)
        response, calls = self.get(platform, [Response(200, [])])
        self.assertEqual([c[1] for c in calls], ['https://api.lever.co/v0/postings/spotify'])


class Reading(unittest.TestCase):
    SESSION = {'site': 'tsmg', 'region': 'global', 'name': None, 'link': 'https://jobs.lever.co/tsmg'}

    def collect(self, answers):
        async def run():
            return [page async for page in fresh().pages(None, dict(self.SESSION), None, None)]
        send, calls = sender(answers)
        with patch('ats_jobs.platforms.lever.send', send):
            return asyncio.run(run()), calls

    def test_a_small_site_is_one_call(self):
        pages, calls = self.collect([Response(200, [RECORD, {**RECORD, 'id': 'two'}, {'text': 'no id'}, 'junk'])])
        self.assertEqual(([j['id'] for j in pages[0]], len(calls)), ([JOB_ID, 'two'], 1))
        self.assertEqual(calls[0][2], {'mode': 'json', 'skip': 0, 'limit': PAGE_SIZE})

    def test_a_big_site_is_read_page_by_page(self):
        full = [{**RECORD, 'id': str(i)} for i in range(PAGE_SIZE)]
        pages, calls = self.collect([Response(200, full), Response(200, [RECORD])])
        self.assertEqual(([len(p) for p in pages], [c[2]['skip'] for c in calls]), ([PAGE_SIZE, 1], [0, PAGE_SIZE]))

    def test_a_site_that_is_gone_is_a_missing_board(self):
        with self.assertRaises(BoardMissing):
            self.collect([gone()])

    def test_an_answer_that_is_no_list_is_an_error(self):
        for bad in (Response(200, {'ok': False}), Response(200, text='not json', headers=JSON)):
            with self.assertRaises(BoardError):
                self.collect([bad])


class Lookup(unittest.TestCase):
    def check(self, answers, session=None):
        session = session or {'site': 'spotify', 'region': 'global', 'name': None, 'link': 'https://jobs.lever.co/spotify'}
        send, calls = sender(answers)
        with patch('ats_jobs.platforms.lever.send', send):
            return asyncio.run(fresh().lookup(None, session, JOB_ID)), calls

    def test_an_open_job(self):
        (status, job), calls = self.check([Response(200, RECORD)])
        self.assertEqual((status, job['id'], job['postedAt']), ('open', JOB_ID, '2026-08-31T18:12:19Z'))
        self.assertEqual(job['description'].splitlines()[0], 'Grow our customers.')
        self.assertEqual(calls[0][1], f'https://jobs.lever.co/v0/postings/spotify/{JOB_ID}')

    def test_a_job_gone_from_a_live_site_is_closed(self):
        (found, calls) = self.check([gone(), Response(200, [RECORD])])
        self.assertEqual((found, len(calls)), (('closed', None), 2))

    def test_a_job_on_a_site_that_is_gone_is_not_found(self):
        self.assertEqual(self.check([gone(), gone()])[0], ('not found', None))

    def test_a_site_checked_when_opened_is_not_checked_again(self):
        session = {'site': 'spotify', 'region': 'global', 'name': None, 'link': 'https://jobs.lever.co/spotify', 'live': True}
        found, calls = self.check([gone()], session)
        self.assertEqual((found, len(calls)), (('closed', None), 1))

    def test_many_links_of_one_site_check_the_site_once(self):
        session = {'site': 'spotify', 'region': 'global', 'name': None, 'link': 'https://jobs.lever.co/spotify'}
        send, calls = sender([gone(), Response(200, []), gone()])
        platform = fresh()

        async def both():
            return [await platform.lookup(None, session, JOB_ID), await platform.lookup(None, session, 'other')]
        with patch('ats_jobs.platforms.lever.send', send):
            self.assertEqual([f[0] for f in asyncio.run(both())], ['closed', 'closed'])
        self.assertEqual(len(calls), 3)

    def test_a_failure_stays_an_error(self):
        with self.assertRaises(BoardError):
            self.check([gone(), Response(500), Response(500)])
        with self.assertRaises(BoardError):
            self.check([Response(200, {'ok': True})])


if __name__ == '__main__':
    unittest.main()
