"""Cornerstone: links, dates, places and rows, on answers shaped like csod.com's own (2026-10-30)."""
import asyncio
import unittest
from unittest.mock import patch

from ats_jobs.http import BoardError
from ats_jobs.platforms.base import Board
from ats_jobs.platforms.cornerstone import Cornerstone, text_of, us_date

P = Cornerstone()
SESSION = {'corp': 'newcold', 'site': 5, 'cloud': 'https://uk.api.csod.com/', 'headers': {}}
RECORD = {'requisitionId': 3110, 'postingEffectiveDate': '10/29/2026', 'postingExpirationDate': '-',
          'displayJobTitle': 'MFCS Engineer &amp; Lead', 'externalDescription': 'We are &lt;&lt;looking&gt;&gt; for you.',
          'locations': [{'city': 'Herentals', 'country': 'BE'}, {'country': 'BE'},
                        {'city': 'Shanghai', 'state': 'SHANGHAI', 'country': 'CN'}]}


class Links(unittest.TestCase):
    def test_every_link_form(self):
        self.assertEqual(P.read_link('https://newcold.csod.com/ux/ats/careersite/5/home?c=newcold&lang=en-GB'), ('newcold/5', None))
        self.assertEqual(P.read_link('https://newcold.csod.com/ux/ats/careersite/5/home/requisition/3110?c=newcold'), ('newcold/5', '3110'))
        self.assertEqual(P.read_link('NewCold.csod.com'), ('newcold', None))
        self.assertEqual(P.read_link('https://m.csod.com/ats/careersite/jobdetails.aspx?site=1&c=m&id=80144'), ('m/1', '80144'))

    def test_other_text_is_no_link(self):
        for text in ('https://example.com/careers', 'software engineer', 'ramp'):
            self.assertIsNone(P.read_link(text))

    def test_a_typed_company_tries_its_first_site(self):
        self.assertEqual(P.guess_board('Henkel'), 'henkel/1')
        self.assertIsNone(P.guess_board('A Company & Sons'))
        self.assertEqual(P.aliases(Board('newcold/5', 'NewCold')), ['newcold'])


class Parsing(unittest.TestCase):
    def test_dates_are_read_as_asked_for(self):
        self.assertEqual(us_date('10/29/2026'), '2026-10-29')
        self.assertEqual(us_date('9/1/2026'), '2026-09-01')
        self.assertIsNone(us_date('-'))
        self.assertIsNone(us_date(None))

    def test_text_of_html(self):
        self.assertEqual(text_of('<p>One &amp; two</p><ul><li>Three</li><li>Four</li></ul>'), 'One & two\nThree\nFour')
        self.assertIsNone(text_of(''))

    def test_a_record_becomes_a_job(self):
        job = P.job(SESSION, RECORD)
        self.assertEqual((job['id'], job['title'], job['postedAt'], job['expiresAt'], job['dateOnly']),
                         ('3110', 'MFCS Engineer & Lead', '2026-10-29', None, True))
        self.assertEqual(job['location'], 'Herentals, BE')
        self.assertEqual(job['otherLocations'], ['Shanghai, SHANGHAI, CN'])      # the bare country of the same job is dropped
        self.assertEqual(job['jobUrl'], 'https://newcold.csod.com/ux/ats/careersite/5/home/requisition/3110?c=newcold')
        self.assertEqual(job['description'], 'We are <<looking>> for you.')

    def test_a_job_with_only_a_country(self):
        job = P.job(SESSION, {**RECORD, 'locations': [{'country': 'US'}]})
        self.assertEqual((job['location'], job['places'][0]['country']), ('US', 'US'))

    def test_a_posting_nobody_filled_in_is_left_out(self):
        self.assertTrue(P.is_template({'displayJobTitle': '<<ENTER DISPLAY JOB TITLE>>'}))
        self.assertTrue(P.is_template({'displayJobTitle': '&lt;&lt;ENTER DISPLAY JOB TITLE&gt;&gt;'}))
        self.assertFalse(P.is_template({'displayJobTitle': 'Engineer'}))

    def test_details_add_the_address_time_and_apply_link(self):
        data = {'externalDescription': '<p>Full text</p>', 'ref': 'req3110', 'openDate': '2026-10-29T17:30:04',
                'companyApplyUrl': 'https://newcold.csod.com/apply', 'primaryLocation': {'city': 'Herentals', 'country': 'BE'}}
        job = P.enrich(P.job(SESSION, RECORD), data)
        self.assertEqual((job['description'], job['descriptionHtml'], job['reference'], job['applyUrl'], job['postedAt'], job['dateOnly']),
                         ('Full text', '<p>Full text</p>', 'req3110', 'https://newcold.csod.com/apply', '2026-10-29T17:30:04Z', False))
        self.assertEqual(job['otherLocations'], ['Shanghai, SHANGHAI, CN'])


class NoAddress(unittest.TestCase):
    def test_a_company_with_no_address_is_a_missing_board(self):
        async def gone(*args, **kwargs):
            raise BoardError('ConnectError: [Errno -2] Name or service not known')
        with patch('ats_jobs.platforms.cornerstone.send', gone):
            self.assertIsNone(asyncio.run(P.open(None, Board('acme/1', 'Acme'))))

    def test_a_network_failure_stays_an_error(self):
        async def down(*args, **kwargs):
            raise BoardError('HTTP 503')
        with patch('ats_jobs.platforms.cornerstone.send', down):
            with self.assertRaises(BoardError):
                asyncio.run(P.open(None, Board('acme/1', 'Acme')))


class Lookup(unittest.TestCase):
    def status(self, answer):
        async def fake(http, session, job_id):
            return answer
        platform = Cornerstone()
        platform._detail = fake
        return asyncio.run(platform.lookup(None, SESSION, '3110'))[0]

    def test_status_two_is_open_any_other_is_closed_and_404_is_no_job(self):
        self.assertEqual(self.status((200, {'requisitionStatusId': '2', 'displayTitle': 'Engineer'})), 'open')
        self.assertEqual(self.status((200, {'requisitionStatusId': 4})), 'closed')
        self.assertEqual(self.status((404, {})), 'not found')


if __name__ == '__main__':
    unittest.main()
