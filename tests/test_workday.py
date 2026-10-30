"""Workday: links, relative dates, places and details, on answers shaped like myworkdayjobs.com's own (2026-10-30)."""
import asyncio
import unittest
from datetime import date

from ats_jobs.platforms.base import Board
from ats_jobs.platforms.workday import Workday, posted_on

P = Workday()
SESSION = asyncio.run(P.open(None, Board('nvidia/wd5/NVIDIAExternalCareerSite', 'NVIDIA')))
TODAY = date(2026, 10, 30)
RECORD = {'title': ' Federal Program Director ', 'externalPath': '/job/US-DC-Remote/Federal-Program-Director_JR2027714',
          'locationsText': 'US, DC, Remote', 'postedOn': 'Posted Today', 'bulletFields': ['JR2027714']}


class Links(unittest.TestCase):
    def test_every_link_form(self):
        self.assertEqual(P.read_link('https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite'),
                         ('nvidia/wd5/NVIDIAExternalCareerSite', None))
        self.assertEqual(P.read_link('https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-DC-Remote/Director_JR1'),
                         ('nvidia/wd5/NVIDIAExternalCareerSite', 'US-DC-Remote/Director_JR1'))
        self.assertEqual(P.read_link('nvidia.wd5.myworkdayjobs.com'), ('nvidia', None))

    def test_other_text_is_no_link(self):
        for text in ('https://example.com/careers', 'software engineer'):
            self.assertIsNone(P.read_link(text))

    def test_a_tenant_finds_its_sites(self):
        self.assertEqual(P.aliases(Board('nvidia/wd5/NVIDIAExternalCareerSite', 'NVIDIA')), ['nvidia'])


class Dates(unittest.TestCase):
    def test_relative_dates(self):
        self.assertEqual(posted_on('Posted Today', TODAY), '2026-10-30')
        self.assertEqual(posted_on('Posted Yesterday', TODAY), '2026-10-29')
        self.assertEqual(posted_on('Posted 3 Days Ago', TODAY), '2026-10-27')
        self.assertEqual(posted_on('Posted 1 Day Ago', TODAY), '2026-10-29')

    def test_thirty_plus_days_says_only_that_it_is_old(self):
        self.assertIsNone(posted_on('Posted 30+ Days Ago', TODAY))
        self.assertIsNone(posted_on(None, TODAY))


class Jobs(unittest.TestCase):
    def test_a_record_becomes_a_job(self):
        job = P.job(SESSION, RECORD)
        self.assertEqual((job['id'], job['title'], job['location'], job['reference'], job['dateOnly']),
                         ('US-DC-Remote/Federal-Program-Director_JR2027714', 'Federal Program Director', 'US, DC, Remote', 'JR2027714', True))
        self.assertEqual(job['jobUrl'], 'https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-DC-Remote/Federal-Program-Director_JR2027714')
        self.assertFalse(P.vague_place(job))

    def test_two_locations_names_no_place(self):
        job = P.job(SESSION, {**RECORD, 'locationsText': '2 Locations'})
        self.assertEqual(job['places'], [])
        self.assertTrue(P.vague_place(job))

    def test_details_name_every_place_and_the_exact_date(self):
        info = {'title': 'Director', 'location': 'US, DC, Remote', 'additionalLocations': ['US, CA, Santa Clara'],
                'country': {'descriptor': 'United States of America'}, 'startDate': '2026-10-29', 'timeType': 'Full time',
                'jobReqId': 'JR2027714', 'remoteType': 'Hybrid', 'jobDescription': '<p>Lead the team.</p>',
                'externalUrl': 'https://nvidia.wd5.myworkdayjobs.com/x'}
        job = P.enrich(P.job(SESSION, {**RECORD, 'locationsText': '2 Locations'}), info)
        self.assertEqual((job['location'], job['otherLocations'], job['postedAt'], job['employmentType'], job['workType']),
                         ('US, DC, Remote', ['US, CA, Santa Clara'], '2026-10-29', 'full-time', 'hybrid'))
        self.assertEqual((job['description'], job['reference'], job['applyUrl']),
                         ('Lead the team.', 'JR2027714', 'https://nvidia.wd5.myworkdayjobs.com/x'))
        self.assertEqual(job['places'][0]['country'], 'United States of America')


class Lookup(unittest.TestCase):
    def status(self, answer):
        async def fake(http, session, job_id):
            return answer
        platform = Workday()
        platform._detail = fake
        return asyncio.run(platform.lookup(None, SESSION, 'x'))[0]

    def test_a_gone_posting_is_closed(self):
        self.assertEqual(self.status((200, {'title': 'Director', 'canApply': True})), 'open')
        self.assertEqual(self.status((200, {'title': 'Director', 'canApply': False})), 'closed')
        self.assertEqual(self.status((404, {})), 'closed')


if __name__ == '__main__':
    unittest.main()
