"""Deel: links, places, pay, dates and rows, on answers shaped like Deel's guest API (2026-10-30)."""
import asyncio
import unittest
from unittest.mock import patch

from ats_jobs.http import BoardError
from ats_jobs.inputs import parse_input
from ats_jobs.platforms.base import Board
from ats_jobs.platforms.deel import Deel, job_type, names, pay_text, place, published
from ats_jobs.rows import job_area

P = Deel()
JOB_ID = '87bd8299-48ef-4c79-9005-0347ecf8b054'
SESSION = {'slug': 'klarna', 'org': 'org-1', 'board': 'board-1', 'name': 'Klarna'}
SETTINGS = {'organizationId': 'org-1', 'urlSlug': 'klarna', 'preferredOrganizationName': 'Klarna', 'jobBoard': {'id': 'board-1'}}
RECORD = {
    'id': JOB_ID, 'title': ' Business  Development Associate ', 'richtextDescription': '<p>Grow the <b>market</b></p><p>Next</p>',
    'isCompensationVisible': True, 'createdAt': '2026-07-10T13:51:21.094Z',
    'jobPostingPublications': [{'currentState': {'stateSlug': 'published_basic', 'createdAt': '2026-07-17T14:16:35.046Z'}}],
    'job': {'workArrangementEnum': 'ON_SITE',
            'jobEmploymentTypes': [{'employmentType': {'name': 'Full-time'}}],
            'jobLocations': [{'location': {'name': 'Vienna', 'country': {'code': 'AT', 'name': 'Austria'}}},
                             {'location': {'name': 'Remote', 'country': None}}],
            'currentCompensation': {'currencyIsoCode': 'EUR', 'minAmount': 69786, 'maxAmount': 78161},
            'jobTeams': [{'team': {'name': 'Partnerships'}}],
            'jobDepartments': [{'department': {'name': 'Sales'}}, {'department': {'name': 'Growth'}}]}}


class Response:
    def __init__(self, status_code, data=None, text=''):
        self.status_code, self.data, self.text = status_code, data, text

    def json(self):
        if self.data is None:
            raise ValueError('not JSON')
        return self.data


class Links(unittest.TestCase):
    def test_every_link_form(self):
        self.assertEqual(P.read_link('https://jobs.deel.com/klarna'), ('klarna', None))
        self.assertEqual(P.read_link('jobs.deel.com/klarna/'), ('klarna', None))
        self.assertEqual(P.read_link(f'https://jobs.deel.com/klarna/job-details/{JOB_ID}/overview'), ('klarna', JOB_ID))
        self.assertEqual(P.read_link(f'https://jobs.deel.com/klarna/job-details/{JOB_ID}/application?x=1'), ('klarna', JOB_ID))
        self.assertEqual(P.read_link(f'https://jobs.deel.com/klarna/job-details/{JOB_ID.upper()}'), ('klarna', JOB_ID))

    def test_the_old_address_still_reads(self):
        self.assertEqual(P.read_link('https://jobs.deel.com/job-boards/cardo'), ('cardo', None))
        self.assertEqual(P.read_link(f'https://jobs.deel.com/job-boards/cardo/job-details/{JOB_ID}'), ('cardo', JOB_ID))

    def test_a_slug_keeps_its_case(self):
        self.assertEqual(P.read_link('https://jobs.deel.com/Range'), ('Range', None))
        self.assertEqual(P.read_link('https://jobs.deel.com/zema.global'), ('zema.global', None))

    def test_deels_own_careers_page(self):
        for text in ('https://www.deel.com/careers/', 'deel.com/careers', 'deel.com'):
            self.assertEqual(P.read_link(text), ('deel', None))
        self.assertIsNone(P.read_link('https://www.deel.com/blog/'))

    def test_the_host_alone_names_no_company(self):
        self.assertEqual(P.read_link('jobs.deel.com'), (None, None))
        self.assertEqual(P.read_link('https://jobs.deel.com/'), (None, None))

    def test_other_text_is_no_link(self):
        for text in ('https://example.com/careers', 'software engineer', 'ramp', 'jobs.lever.co/ramp'):
            self.assertIsNone(P.read_link(text))

    def test_a_typed_company_becomes_a_slug(self):
        self.assertEqual(P.guess_board('Acme Corp'), 'Acme-Corp')
        self.assertEqual(P.guess_board('LANDIGlobal'), 'LANDIGlobal')
        self.assertEqual(P.guess_board('zema.global'), 'zema.global')
        self.assertIsNone(P.guess_board('...'))

    def test_a_pasted_dork_limits_the_search_to_one_company(self):
        cfg = parse_input({'searchTerms': ['site:jobs.deel.com/klarna intext:engineer']}, P, {})
        self.assertEqual((cfg.search_terms, cfg.companies), (['engineer'], ['klarna']))
        cfg = parse_input({'searchTerms': ['site:jobs.deel.com intext:engineer']}, P, {})
        self.assertEqual((cfg.search_terms, cfg.companies), (['engineer'], []))


class Parsing(unittest.TestCase):
    def test_a_city_gets_its_country(self):
        self.assertEqual(place({'name': 'Vienna', 'country': {'code': 'AT', 'name': 'Austria'}}),
                         {'name': 'Vienna, Austria', 'country': 'Austria'})

    def test_a_text_that_names_its_country_stays(self):
        for name, code, full in (('Spain', 'ES', 'Spain'), ('Barcelona, Spain', 'ES', 'Spain'),
                                 ('Hayward, California, USA', 'US', 'United States'),
                                 ('United States of America', 'US', 'United States')):
            self.assertEqual(place({'name': name, 'country': {'code': code, 'name': full}})['name'], name)

    def test_remote_with_a_country_is_tied_to_it(self):
        spot = place({'name': 'Remote', 'country': {'code': 'US', 'name': 'United States'}})
        self.assertEqual(spot['name'], 'Remote, United States')
        self.assertFalse(job_area({'places': [spot]}).open_remote)

    def test_a_place_with_no_country_keeps_the_employers_words(self):
        self.assertEqual(place({'name': ' Remoto  (Latam) ', 'country': None}), {'name': 'Remoto (Latam)', 'country': None})
        self.assertTrue(job_area({'places': [place({'name': 'Remote', 'country': None})]}).open_remote)

    def test_no_place_at_all(self):
        self.assertIsNone(place({'name': '', 'country': None}))
        self.assertEqual(place({'name': None, 'country': {'code': 'FR', 'name': 'France'}}), {'name': 'France', 'country': 'France'})

    def test_pay_is_what_the_company_wrote(self):
        self.assertEqual(pay_text(160000, 210000, 'USD'), 'USD 160,000 - 210,000')
        self.assertEqual(pay_text(22.35, 24.03, 'USD'), 'USD 22.35 - 24.03')
        self.assertEqual(pay_text(39600, None, 'USD'), 'USD 39,600')
        self.assertIsNone(pay_text(None, None, 'USD'))

    def test_every_employment_label_reads(self):
        for label, kind in (('Full-time', 'full-time'), ('Part-time', 'part-time'), ('Contract', 'contract'),
                            ('Consultant', 'contract'), ('Intern', 'intern'), ('Temporary', 'temporary')):
            self.assertEqual(job_type([{'employmentType': {'name': label}}]), kind)
        self.assertIsNone(job_type([{'employmentType': {'name': 'Volunteer'}}]))
        self.assertIsNone(job_type(None))

    def test_the_posted_date_is_the_published_date(self):
        self.assertEqual(published(RECORD), '2026-07-17T14:16:35.046Z')
        self.assertEqual(published({'createdAt': '2026-07-10T13:51:21.094Z'}), '2026-07-10T13:51:21.094Z')

    def test_many_departments_join(self):
        self.assertEqual(names(RECORD['job']['jobDepartments'], 'department'), 'Sales, Growth')
        self.assertIsNone(names([], 'team'))

    def test_a_record_becomes_a_job(self):
        job = P.job(SESSION, RECORD)
        self.assertEqual((job['id'], job['company'], job['title'], job['workType'], job['employmentType']),
                         (JOB_ID, 'Klarna', 'Business Development Associate', 'onsite', 'full-time'))
        self.assertEqual((job['location'], job['otherLocations']), ('Vienna, Austria', ['Remote']))
        self.assertEqual((job['department'], job['team']), ('Sales, Growth', 'Partnerships'))
        self.assertEqual((job['salary'], job['salaryMin'], job['salaryMax'], job['salaryCurrency']),
                         ('EUR 69,786 - 78,161', 69786, 78161, 'EUR'))
        self.assertEqual((job['description'], job['descriptionHtml']),
                         ('Grow the market\nNext', RECORD['richtextDescription']))
        self.assertEqual(job['jobUrl'], f'https://jobs.deel.com/klarna/job-details/{JOB_ID}/overview')
        self.assertEqual(job['applyUrl'], f'https://jobs.deel.com/klarna/job-details/{JOB_ID}/application')

    def test_hidden_pay_stays_hidden(self):
        job = P.job(SESSION, {**RECORD, 'isCompensationVisible': False})
        self.assertIsNone(job['salary'])
        self.assertIsNone(job['salaryMin'])

    def test_a_record_with_almost_nothing(self):
        job = P.job(SESSION, {'id': JOB_ID, 'title': 'Engineer'})
        self.assertEqual((job['title'], job['places'], job['location'], job['salary'], job['description']),
                         ('Engineer', [], None, None, None))


def sender(answers: list):
    async def send(http, method, url, **kwargs):
        calls.append(url)
        return answers.pop(0)
    calls = []
    return send, calls


class Opening(unittest.TestCase):
    def test_a_page_gives_its_ids_and_name(self):
        send, calls = sender([Response(200, SETTINGS)])
        with patch('ats_jobs.platforms.deel.send', send):
            session = asyncio.run(P.open(None, Board('klarna', 'Klarna')))
        self.assertEqual(session, SESSION)
        self.assertIn('/organizations/klarna/career_page_settings', calls[0])

    def test_a_slug_in_the_wrong_case_is_tried_in_lower_case(self):
        send, calls = sender([Response(404), Response(200, SETTINGS)])
        with patch('ats_jobs.platforms.deel.send', send):
            session = asyncio.run(P.open(None, Board('Klarna', 'Klarna')))
        self.assertEqual((session['slug'], len(calls)), ('klarna', 2))

    def test_a_missing_page_is_a_missing_board(self):
        send, calls = sender([Response(404)])
        with patch('ats_jobs.platforms.deel.send', send):
            self.assertIsNone(asyncio.run(P.open(None, Board('acme', 'Acme'))))
        self.assertEqual(len(calls), 1)

    def test_a_page_with_no_board_is_a_missing_board(self):
        send, _ = sender([Response(200, {**SETTINGS, 'jobBoard': None})])
        with patch('ats_jobs.platforms.deel.send', send):
            self.assertIsNone(asyncio.run(P.open(None, Board('acme', 'Acme'))))

    def test_a_copied_page_carries_the_companys_name(self):
        send, _ = sender([Response(200, {**SETTINGS, 'preferredOrganizationName': 'Shufti (copy)'})])
        with patch('ats_jobs.platforms.deel.send', send):
            self.assertEqual(asyncio.run(P.open(None, Board('shufti-1', 'Shufti')))['name'], 'Shufti')

    def test_a_server_failure_stays_an_error(self):
        send, _ = sender([Response(403)])
        with patch('ats_jobs.platforms.deel.send', send):
            with self.assertRaises(BoardError):
                asyncio.run(P.open(None, Board('acme', 'Acme')))

    def test_text_that_cannot_be_a_slug_is_never_sent(self):
        send, calls = sender([])
        with patch('ats_jobs.platforms.deel.send', send):
            self.assertIsNone(asyncio.run(P.open(None, Board('../../secret', 'x'))))
        self.assertEqual(calls, [])


class Reading(unittest.TestCase):
    def collect(self, answers):
        async def run():
            return [page async for page in P.pages(None, dict(SESSION), None, None)]
        send, calls = sender(answers)
        with patch('ats_jobs.platforms.deel.send', send):
            return asyncio.run(run()), calls

    def test_the_whole_board_is_one_page(self):
        pages, calls = self.collect([Response(200, [RECORD, {**RECORD, 'id': 'two'}])])
        self.assertEqual((len(pages), [j['id'] for j in pages[0]], len(calls)), (1, [JOB_ID, 'two'], 1))
        self.assertIn('/organizations/org-1/job_boards/board-1/job_postings', calls[0])

    def test_an_answer_that_is_no_list_is_an_error(self):
        for bad in (Response(200, {'errors': []}), Response(200), Response(500)):
            with self.assertRaises(BoardError):
                self.collect([bad])


class Lookup(unittest.TestCase):
    def check(self, answers, job_id=JOB_ID):
        send, calls = sender(answers)
        with patch('ats_jobs.platforms.deel.send', send):
            return asyncio.run(P.lookup(None, dict(SESSION), job_id)), calls

    def test_a_listed_job_is_open_with_its_published_date(self):
        (status, job), calls = self.check([Response(200, [RECORD])])
        self.assertEqual((status, job['postedAt'], len(calls)), ('open', '2026-07-17T14:16:35.046Z', 1))

    def test_a_closed_job_and_a_job_that_never_was(self):
        closed = Response(404, text='{"responseMessage":{"message":"Job posting is not published"}}')
        missing = Response(404, text='{"responseMessage":{"message":"Job posting not found"}}')
        self.assertEqual(self.check([Response(200, []), closed])[0], ('closed', None))
        self.assertEqual(self.check([Response(200, []), missing])[0], ('not found', None))

    def test_a_job_off_the_list_that_still_answers_is_open(self):
        (status, job), _ = self.check([Response(200, []), Response(200, RECORD)])
        self.assertEqual((status, job['id']), ('open', JOB_ID))

    def test_many_links_of_one_board_read_the_list_once(self):
        send, calls = sender([Response(200, [RECORD, {**RECORD, 'id': 'two'}])])
        session = dict(SESSION)

        async def both():
            return [await P.lookup(None, session, JOB_ID), await P.lookup(None, session, 'two')]
        with patch('ats_jobs.platforms.deel.send', send):
            self.assertEqual([found[0] for found in asyncio.run(both())], ['open', 'open'])
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
