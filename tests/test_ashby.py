"""Ashby: links, the board page, the posting API's records and the first-pass skip, on answers shaped like Ashby's own
(ramp, 2026-09-30, from the ashby-jobs-search actor's tests)."""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from ats_jobs.http import BoardError, BoardMissing
from ats_jobs.inputs import parse_input
from ats_jobs.platforms import PLATFORMS
from ats_jobs.platforms.ashby import Ashby, parse_board, pay, places, short_job
from ats_jobs.platforms.base import Board
from ats_jobs.rows import Filters, job_row

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
SESSION = {'slug': 'ramp', 'name': 'Ramp', 'website': 'https://ramp.com', 'postings': []}


def record(**over) -> dict:
    base = {
        'id': '34413f8d-26bf-4bbc-8ade-eb309a0e2245', 'title': ' Security Engineer, Cloud',
        'department': 'Engineering', 'team': 'Backend', 'employmentType': 'FullTime',
        'location': 'New York, NY (HQ)',
        'secondaryLocations': [
            {'location': 'Remote (Canada)', 'address': {'postalAddress': {'addressCountry': 'Canada'}}},
            {'location': 'Miami, FL', 'address': {'postalAddress': {'addressRegion': 'Florida', 'addressCountry': 'USA',
                                                                    'addressLocality': 'Miami'}}}],
        'publishedAt': (NOW - timedelta(days=2)).isoformat(), 'isListed': True, 'isRemote': True,
        'workplaceType': 'Hybrid',
        'address': {'postalAddress': {'addressRegion': 'NY', 'addressCountry': 'USA', 'addressLocality': 'New York City'}},
        'jobUrl': 'https://jobs.ashbyhq.com/ramp/34413f8d-26bf-4bbc-8ade-eb309a0e2245',
        'applyUrl': 'https://jobs.ashbyhq.com/ramp/34413f8d-26bf-4bbc-8ade-eb309a0e2245/application',
        'descriptionHtml': '<p>We build finance automation with Python and Kubernetes.</p>',
        'descriptionPlain': 'We build finance automation with Python and Kubernetes.',
        'compensation': {
            'compensationTierSummary': '$211.4K – $290.6K • Offers Equity',
            'summaryComponents': [
                {'compensationType': 'EquityPercentage', 'interval': 'NONE', 'currencyCode': None, 'minValue': None,
                 'maxValue': None},
                {'compensationType': 'Salary', 'interval': '1 YEAR', 'currencyCode': 'USD', 'minValue': 211400,
                 'maxValue': 290600}]},
    }
    base.update(over)
    return base


class Answer:
    def __init__(self, status, body):
        self.status_code, self.body = status, body
        self.text = body if isinstance(body, str) else ''

    def json(self):
        return self.body


class Links(unittest.TestCase):
    def test_board_and_job_links(self):
        p = Ashby()
        self.assertEqual(p.read_link('https://jobs.ashbyhq.com/ramp'), ('ramp', None))
        self.assertEqual(p.read_link('jobs.ashbyhq.com/ramp/34413F8D-26bf-4bbc-8ade-eb309a0e2245'),
                         ('ramp', '34413f8d-26bf-4bbc-8ade-eb309a0e2245'))
        self.assertEqual(p.read_link('https://jobs.ashbyhq.com/Blackpoint%20Cyber'), ('Blackpoint Cyber', None))
        self.assertIsNone(p.read_link('https://boards.greenhouse.io/ramp'))
        self.assertEqual(p.guess_board('Ramp'), 'Ramp')

    def test_it_is_one_of_the_engines_platforms_with_its_board_list(self):
        self.assertIs(PLATFORMS['ashby'], Ashby)
        boards, updated = Ashby().boards()
        self.assertGreater(len(boards), 3000)
        self.assertEqual(updated, '2026-09-30')
        self.assertIn('ramp', {b.key for b in boards})


class BoardPage(unittest.TestCase):
    HTML = ('<script>window.__appData = {"organization": {"name": "Ramp ", "hostedJobsPageSlug": "ramp", '
            '"publicWebsite": "ramp.com", "isDemoOrg": false}, "jobBoard": {"jobPostings": ['
            '{"title": "Security Engineer", "locationName": "New York", "workplaceType": null, '
            '"employmentType": "FullTime"}]}};</script>')

    def test_the_page_names_the_company_and_lists_its_jobs(self):
        found = parse_board(self.HTML)
        self.assertEqual(found['org'], {'name': 'Ramp', 'slug': 'ramp', 'website': 'https://ramp.com', 'demo': False})
        self.assertEqual(len(found['postings']), 1)
        self.assertIsNone(parse_board('<script>window.__appData = {"organization": null, "jobBoard": null};</script>'))

    def test_open_reads_the_page_and_a_name_nobody_uses_is_no_board(self):
        async def fake(http, method, url, **kwargs):
            if url.endswith('/nobody'):
                return Answer(200, '<script>window.__appData = {"organization": null};</script>')
            if url.endswith('/down'):
                return Answer(500, '')
            return Answer(200, self.HTML)
        with patch('ats_jobs.platforms.ashby.send', fake):
            session = asyncio.run(Ashby().open(None, Board('ramp', 'ramp')))
            self.assertEqual((session['name'], session['website'], len(session['postings'])),
                             ('Ramp', 'https://ramp.com', 1))
            self.assertIsNone(asyncio.run(Ashby().open(None, Board('nobody', 'nobody'))))
            with self.assertRaises(BoardError):
                asyncio.run(Ashby().open(None, Board('down', 'down')))


class Records(unittest.TestCase):
    def test_a_posting_becomes_a_job_with_its_places_pay_and_text(self):
        job = Ashby().job(SESSION, record())
        self.assertEqual(job['title'], 'Security Engineer, Cloud')
        self.assertEqual((job['company'], job['companySlug'], job['companyWebsite']), ('Ramp', 'ramp', 'https://ramp.com'))
        self.assertEqual(job['location'], 'New York, NY (HQ)')
        self.assertEqual(job['otherLocations'], ['Remote (Canada)', 'Miami, FL'])
        self.assertEqual(job['places'][0], {'name': 'New York, NY (HQ)', 'city': 'New York City', 'region': 'NY',
                                            'country': 'USA'})
        self.assertEqual((job['workType'], job['employmentType']), ('hybrid', 'full-time'))
        self.assertEqual((job['salaryMin'], job['salaryMax'], job['salaryCurrency'], job['salaryPeriod']),
                         (211400, 290600, 'USD', 'year'))
        self.assertTrue(job['offersEquity'])
        self.assertEqual(job['description'], 'We build finance automation with Python and Kubernetes.')

    def test_the_row_carries_ashbys_fields(self):
        row = job_row('ashby', {'name': 'Ramp', 'slug': 'ramp'}, Ashby().job(SESSION, record()), 'now', True)
        self.assertEqual((row['platform'], row['company'], row['salary']), ('ashby', 'Ramp', '$211.4K – $290.6K • Offers Equity'))
        self.assertEqual((row['city'], row['countryCode']), ('New York City', 'US'))

    def test_remote_flag_without_a_workplace_type_is_remote(self):
        self.assertEqual(Ashby().job(SESSION, record(workplaceType=None))['workType'], 'remote')
        self.assertIsNone(Ashby().job(SESSION, record(workplaceType=None, isRemote=False))['workType'])

    def test_missing_pay_and_places_read_as_unknown(self):
        self.assertIsNone(pay(record(compensation=None))['salary'])
        self.assertEqual(places({'location': None, 'secondaryLocations': []}), [])

    def test_a_job_the_posting_api_marks_unlisted_is_not_read(self):
        async def fake(http, method, url, **kwargs):
            return Answer(200, {'jobs': [record(), record(id='x2', isListed=False)]})
        with patch('ats_jobs.platforms.ashby.send', fake):
            jobs = asyncio.run(Ashby().postings(None, 'ramp'))
        self.assertEqual([j['id'] for j in jobs], ['34413f8d-26bf-4bbc-8ade-eb309a0e2245'])


class FirstPass(unittest.TestCase):
    def pages(self, raw, postings):
        calls = []

        async def fake(http, method, url, **kwargs):
            calls.append(url)
            return Answer(200, {'jobs': [record()]})
        platform = Ashby()
        platform.prefilter = Filters(parse_input(raw, platform))

        async def read():
            return [page async for page in platform.pages(None, {**SESSION, 'postings': postings}, None, None)]
        with patch('ats_jobs.platforms.ashby.send', fake):
            pages = asyncio.run(read())
        return pages, calls

    def test_a_board_whose_short_list_cannot_match_is_not_read_further(self):
        short = [{'title': 'Account Executive', 'employmentType': 'FullTime'}]
        pages, calls = self.pages({'searchTerms': ['security engineer']}, short)
        self.assertEqual((pages, calls), ([], []))

    def test_a_board_with_a_job_that_can_match_reads_its_postings(self):
        short = [{'title': 'Senior Security Engineer', 'employmentType': 'FullTime'}]
        pages, calls = self.pages({'searchTerms': ['security engineer']}, short)
        self.assertEqual(len(calls), 1)
        self.assertEqual(pages[0][0]['title'], 'Security Engineer, Cloud')

    def test_a_job_type_or_work_type_the_short_list_rules_out_skips_the_board(self):
        short = [{'title': 'Security Engineer', 'employmentType': 'Contract', 'workplaceType': 'OnSite'}]
        self.assertEqual(self.pages({'employmentType': ['full-time']}, short)[1], [])
        self.assertEqual(self.pages({'workType': ['remote']}, short)[1], [])
        self.assertEqual(len(self.pages({}, short)[1]), 1)

    def test_a_short_list_entry_reads_in_the_engines_shape(self):
        self.assertEqual(short_job({'title': ' Data  Analyst ', 'locationName': 'Remote', 'workplaceType': 'Remote',
                                    'employmentType': 'PartTime'}),
                         {'title': 'Data Analyst', 'location': 'Remote', 'workType': 'remote',
                          'employmentType': 'part-time'})


class Lookup(unittest.TestCase):
    def test_a_job_on_the_list_is_open_and_one_gone_from_it_is_closed(self):
        async def fake(http, method, url, **kwargs):
            if 'gone' in url:
                return Answer(404, {})
            return Answer(200, {'jobs': [record()]})
        with patch('ats_jobs.platforms.ashby.send', fake):
            status, job = asyncio.run(Ashby().lookup(None, SESSION, '34413F8D-26bf-4bbc-8ade-eb309a0e2245'))
            self.assertEqual((status, job['title']), ('open', 'Security Engineer, Cloud'))
            self.assertEqual(asyncio.run(Ashby().lookup(None, SESSION, 'other'))[0], 'closed')
            with self.assertRaises(BoardMissing):
                asyncio.run(Ashby().lookup(None, {**SESSION, 'slug': 'gone'}, 'x'))


if __name__ == '__main__':
    unittest.main()
