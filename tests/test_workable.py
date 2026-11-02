"""Workable: links, the feed, career pages, jobs and lookups, on answers shaped like Workable's own (2026-11-02)."""
import asyncio
import gzip
import unittest
from unittest.mock import patch

import httpx

from ats_jobs.http import BoardError
from ats_jobs.inputs import parse_input
from ats_jobs.platforms import workable
from ats_jobs.platforms.base import Board
from ats_jobs.platforms.workable import EVERYONE, Workable, job_type, pay_text, place

BIFFA = Board('biffa', 'Biffa Waste Services', 'https://www.biffa.co.uk/')
ENTRY = {'title': ' Class 1  ADR Driver ', 'shortcode': '8A29959BE6', 'code': 'I&C', 'employment_type': 'Full-time',
         'telecommuting': False, 'department': 'Driver', 'url': 'https://apply.workable.com/j/8A29959BE6',
         'shortlink': 'https://apply.workable.com/j/8A29959BE6', 'application_url': 'https://apply.workable.com/j/8A29959BE6/apply',
         'published_on': '2026-10-30', 'created_at': '2026-10-28', 'country': 'United Kingdom', 'city': 'Greenland',
         'state': 'England', 'education': '', 'experience': '', 'function': '', 'industry': 'Renewables & Environment',
         'locations': [{'country': 'United Kingdom', 'countryCode': 'GB', 'city': 'Greenland', 'region': 'England', 'hidden': False}]}
SECOND_PLACE = {**ENTRY, 'city': 'Sheffield', 'locations': [{**ENTRY['locations'][0], 'city': 'Sheffield'}]}
WIDGET = {'name': 'Biffa Waste Services', 'description': '<p>Waste</p>', 'jobs': [ENTRY, SECOND_PLACE]}
V2 = {'id': 6230970, 'shortcode': '584D288213', 'title': 'Accounts Payable', 'remote': False,
      'location': {'city': 'High Wycombe', 'country': 'United Kingdom', 'countryCode': 'GB', 'region': 'England'},
      'locations': [{'city': 'High Wycombe', 'country': 'United Kingdom', 'countryCode': 'GB', 'hidden': False, 'region': 'England'}],
      'state': 'published', 'isInternal': False, 'code': '8271', 'published': '2026-10-28T00:00:00.000Z', 'type': 'full',
      'language': 'en', 'department': ['Group Central Functions'], 'accountUid': '5e8562c2-fa9d-4396-966e-5bfafe6c37ec',
      'approvalStatus': 'approved', 'workplace': 'on_site', 'salary_from': 28000, 'salary_to': 28000,
      'salary_currency_iso_code': 'GBP', 'salary_frequency': 'year', 'description': '<h3>Accounts Payable</h3><p>Own the ledger</p>',
      'requirements': '<p>AP experience</p>', 'benefits': ''}
FEED_XML = b'''<?xml version="1.0" encoding="utf-8"?>
<source>
  <publisher>Workable</publisher>
  <publisherurl>https://www.workable.com</publisherurl>
  <job>
    <title>
      <![CDATA[System  Engineer]]>
    </title>
    <date>
      <![CDATA[Mon, 02 Nov 2026 10:24:17 UTC]]>
    </date>
    <referencenumber>
      <![CDATA[f44ed9e40a]]>
    </referencenumber>
    <url>
      <![CDATA[https://apply.workable.com/j/F44ED9E40A]]>
    </url>
    <company>
      <![CDATA[Biffa Waste Services]]>
    </company>
    <city>
      <![CDATA[Hyderabad]]>
    </city>
    <state>
      <![CDATA[Telangana]]>
    </state>
    <country>
      <![CDATA[IN]]>
    </country>
    <remote>
      <![CDATA[true]]>
    </remote>
    <postalcode>
      <![CDATA[]]>
    </postalcode>
    <description>
      <![CDATA[<p>Build &amp; run <b>servers</b></p><p>Next</p>]]>
    </description>
    <education>
      <![CDATA[Bachelor's Degree]]>
    </education>
    <jobtype>
      <![CDATA[Contract]]>
    </jobtype>
    <category>
      <![CDATA[Information Technology]]>
    </category>
    <experience>
      <![CDATA[Mid-Senior level]]>
    </experience>
    <website>
      <![CDATA[https://www.biffa.co.uk/]]>
    </website>
  </job>
  <job>
    <title><![CDATA[No code]]></title>
    <referencenumber><![CDATA[]]></referencenumber>
  </job>
  <job>
    <title><![CDATA[Driver]]></title>
    <date><![CDATA[Sun, 01 Nov 2026 08:00:00 UTC]]></date>
    <referencenumber><![CDATA[0908074E5D]]></referencenumber>
    <company><![CDATA[Odorzx]]></company>
    <country><![CDATA[]]></country>
    <remote><![CDATA[false]]></remote>
    <jobtype><![CDATA[Other]]></jobtype>
  </job>
</source>
'''


class Response:
    def __init__(self, status_code, data=None, text='', headers=None):
        self.status_code, self.data, self.text, self.headers = status_code, data, text, headers or {}

    def json(self):
        if self.data is None:
            raise ValueError('not JSON')
        return self.data


def sender(answers: list):
    async def send(http, method, url, **kwargs):
        calls.append((method, url))
        return answers.pop(0)
    calls = []
    return send, calls


def platform() -> Workable:
    p = Workable()
    p._known = {'biffa': BIFFA, 'bask-health-1': Board('bask-health-1', 'Bask Health'),
                'bask-health': Board('bask-health', 'Bask Health')}
    return p


P = platform()


class Links(unittest.TestCase):
    def test_career_pages(self):
        for text in ('https://apply.workable.com/biffa', 'https://apply.workable.com/biffa/', 'apply.workable.com/biffa/?lng=en',
                     'https://apply.workable.com/BIFFA/', 'https://biffa.workable.com/', 'http://biffa.workable.com',
                     'https://apply.workable.com/api/v1/widget/accounts/biffa?details=true',
                     'https://www.workable.com/api/accounts/biffa?details=true', 'https://apply.workable.com/api/v1/accounts/biffa',
                     'https://apply.workable.com/api/v3/accounts/biffa/jobs', 'https://apply.workable.com/biffa/jobs'):
            self.assertEqual(P.read_link(text), ('biffa', None), text)

    def test_job_links(self):
        for text in ('https://apply.workable.com/biffa/j/584D288213', 'https://apply.workable.com/biffa/j/584d288213/',
                     'https://apply.workable.com/biffa/j/584D288213/apply?utm_source=x', 'HTTPS://APPLY.WORKABLE.COM/BIFFA/J/584D288213/',
                     'https://biffa.workable.com/j/584D288213', 'https://apply.workable.com/biffa/jobs/view/584D288213.md',
                     'https://apply.workable.com/api/v2/accounts/biffa/jobs/584D288213',
                     'https://apply.workable.com/biffa?lng=en%2Fj%2F584D288213&utm_source=board'):
            self.assertEqual(P.read_link(text), ('biffa', '584D288213'), text)

    def test_old_job_numbers(self):
        self.assertEqual(P.read_link('https://apply.workable.com/biffa/jobs/6230970'), ('biffa', '6230970'))
        self.assertEqual(P.read_link('https://contact.workable.com/jobs/2639701/candidates/new'), ('contact', '2639701'))
        self.assertEqual(P.read_link('https://apply.workable.com/bw-energy/j/5805616/'), ('bw-energy', '5805616'))

    def test_links_that_name_no_account(self):
        for text in ('https://apply.workable.com/j/584D288213', 'apply.workable.com/j/584d288213/apply',
                     'https://www.workable.com/j/584D288213'):
            self.assertEqual(P.read_link(text), (EVERYONE.key, '584D288213'), text)
        self.assertEqual(P.read_link('https://jobs.workable.com/view/omtQvzG25ZmkM1dS9dWiDj/remote-engineer-at-bask'),
                         (EVERYONE.key, 'omtQvzG25ZmkM1dS9dWiDj'))

    def test_workable_links_with_no_company(self):
        for text in ('https://apply.workable.com/', 'apply.workable.com', 'https://apply.workable.com/oops',
                     'https://apply.workable.com/j/', 'https://www.workable.com/boards/workable.xml', 'https://jobs.workable.com/'):
            self.assertEqual(P.read_link(text), (None, None), text)

    def test_other_text_is_no_link(self):
        for text in ('https://example.com/j/584D288213', 'software engineer', 'biffa', 'https://help.workable.com/hc',
                     'https://resources.workable.com/hiring', 'https://www.workable.com/pricing', 'https://evilworkable.com/biffa',
                     'https://apply.workable.com.example.com/biffa', 'jobs.lever.co/biffa'):
            self.assertIsNone(P.read_link(text), text)

    def test_a_website_finds_its_company(self):
        self.assertEqual(P.aliases(BIFFA), ['biffa.co.uk'])
        self.assertEqual(P.aliases(Board('x', 'X', 'bask.health')), ['bask.health'])
        self.assertEqual(P.aliases(Board('y', 'Y')), [])

    def test_a_typed_company_becomes_an_account(self):
        self.assertEqual(P.guess_board('Biffa Waste Services'), 'biffa-waste-services')
        self.assertEqual(P.guess_board('Hugging Face'), 'hugging-face')
        self.assertIsNone(P.guess_board('...'))

    def test_a_pasted_dork(self):
        cfg = parse_input({'searchTerms': ['site:apply.workable.com intext:engineer']}, P, {})
        self.assertEqual((cfg.search_terms, cfg.companies), (['engineer'], []))
        cfg = parse_input({'searchTerms': ['site:apply.workable.com/biffa engineer']}, P, {})
        self.assertEqual((cfg.search_terms, cfg.companies), (['engineer'], ['biffa']))

    def test_a_job_link_in_the_search_box_is_checked(self):
        cfg = parse_input({'searchTerms': ['https://apply.workable.com/j/584D288213']}, P, {})
        self.assertEqual(cfg.job_urls, [('*', '584D288213', 'https://apply.workable.com/j/584D288213')])


class Parsing(unittest.TestCase):
    def test_places_and_types(self):
        self.assertEqual(place({'city': 'High Wycombe', 'region': 'England', 'country': 'United Kingdom'})['name'],
                         'High Wycombe, England, United Kingdom')
        self.assertEqual(place({'city': '', 'region': None, 'country': 'France'}),
                         {'name': 'France', 'city': None, 'region': None, 'country': 'France'})
        self.assertIsNone(place({'city': None, 'region': '', 'country': None}))
        for text, kind in (('Full-time', 'full-time'), ('Part-time', 'part-time'), ('Contract', 'contract'),
                           ('Temporary', 'temporary'), ('Internship', 'intern'), ('Other', None), (None, None)):
            self.assertEqual(job_type(text), kind)

    def test_pay(self):
        self.assertEqual(pay_text(28000, 28000, 'GBP', 'year'), 'GBP 28,000 per year')
        self.assertEqual(pay_text(18.5, 22, 'USD', 'hour'), 'USD 18.50 - 22 per hour')
        self.assertEqual(pay_text(0, 50000, 'EUR', None), 'EUR 50,000')
        self.assertIsNone(pay_text(None, 0, 'EUR', 'year'))

    def test_a_job_in_two_places_is_one_job(self):
        session = {'account': 'biffa', 'name': 'Biffa Waste Services', 'website': 'https://www.biffa.co.uk/'}
        jobs = P.from_widget(session, WIDGET['jobs'])
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertEqual((job['id'], job['title'], job['location'], job['otherLocations']),
                         ('8A29959BE6', 'Class 1 ADR Driver', 'Greenland, England, United Kingdom', ['Sheffield, England, United Kingdom']))
        self.assertEqual((job['postedAt'], job['dateOnly'], job['employmentType'], job['department'], job['reference']),
                         ('2026-10-30', True, 'full-time', 'Driver', 'I&C'))
        self.assertEqual((job['company'], job['companySlug'], job['companyWebsite'], job['workType']),
                         ('Biffa Waste Services', 'biffa', 'https://www.biffa.co.uk/', None))
        self.assertEqual((job['jobUrl'], job['applyUrl']),
                         ('https://apply.workable.com/j/8A29959BE6', 'https://apply.workable.com/j/8A29959BE6/apply'))

    def test_odd_widget_entries(self):
        session = {'account': 'x', 'name': 'X'}
        odd = [{'shortcode': None, 'title': 'No code'}, {'shortcode': 'ABCDEF1234', 'title': None, 'locations': None,
               'telecommuting': True, 'department': '', 'code': None, 'published_on': None, 'employment_type': None}]
        jobs = P.from_widget(session, odd)
        self.assertEqual([(j['id'], j['title'], j['places'], j['workType'], j['department'], j['postedAt']) for j in jobs],
                         [('ABCDEF1234', '', [], 'remote', None, None)])

    def test_details_add_pay_workplace_and_the_whole_text(self):
        job = P.enrich({'id': '584D288213', 'title': 'Accounts Payable', 'postedAt': '2026-10-28', 'dateOnly': True}, V2)
        self.assertEqual((job['salary'], job['salaryMin'], job['salaryMax'], job['salaryCurrency'], job['salaryPeriod']),
                         ('GBP 28,000 per year', 28000, 28000, 'GBP', 'year'))
        self.assertEqual((job['workType'], job['employmentType'], job['department'], job['reference']),
                         ('onsite', 'full-time', 'Group Central Functions', '8271'))
        self.assertEqual(job['description'], 'Accounts Payable\nOwn the ledger\nAP experience')
        self.assertEqual(job['location'], 'High Wycombe, England, United Kingdom')

    def test_details_without_pay(self):
        job = P.enrich({'id': 'x', 'title': 'T'}, {**V2, 'salary_from': None, 'salary_to': 0, 'workplace': None, 'remote': True})
        self.assertEqual((job['salary'], job['salaryMin'], job['salaryCurrency'], job['workType'], job['postedAt']),
                         (None, None, None, 'remote', '2026-10-28'))


BOARD_ITEM = {'department': 'Engineering', 'id': 'b50514a8-a2ec-4f76-aa25-f94e6d4efa88', 'title': ' Senior  Back-End Engineer ',
              'state': 'published', 'description': '<p>Build &amp; ship</p>', 'requirementsSection': '<p>Python</p>',
              'benefitsSection': '', 'employmentType': 'Full-time',
              'url': 'https://jobs.workable.com/view/omtQvzG25ZmkM1dS9dWiDj/remote-senior-back-end-engineer',
              'language': 'en', 'locations': ['TELECOMMUTE', 'New York, New York, United States'],
              'location': {'city': 'New York', 'subregion': 'New York', 'countryName': 'United States'},
              'created': '2026-11-02T09:00:00.000Z', 'updated': '2026-11-02T09:00:00.000Z',
              'company': {'id': '5e8562c2', 'title': 'Biffa Waste Services', 'website': 'https://www.biffa.co.uk/'},
              'isFeatured': False, 'workplace': 'hybrid'}


def item(job_id, created, **over):
    return {**BOARD_ITEM, 'id': job_id, 'created': created, **over}


class Board_(unittest.TestCase):
    def test_a_job_board_job(self):
        job = P.from_board(BOARD_ITEM['id'], BOARD_ITEM)
        self.assertEqual((job['title'], job['company'], job['companySlug'], job['companyWebsite']),
                         ('Senior Back-End Engineer', 'Biffa Waste Services', 'biffa', 'https://www.biffa.co.uk/'))
        self.assertEqual((job['location'], job['places'][0]['country'], job['workType'], job['employmentType'], job['department']),
                         ('New York, New York, United States', 'United States', 'hybrid', 'full-time', 'Engineering'))
        self.assertEqual((job['postedAt'], job['dateOnly'], job['salary']), ('2026-11-02T09:00:00.000Z', False, None))
        self.assertEqual(job['description'], 'Build & ship\nPython')
        self.assertEqual(job['jobUrl'], BOARD_ITEM['url'])

    def test_odd_job_board_records(self):
        job = P.from_board('x', {'id': 'x', 'title': None, 'locations': None, 'location': None, 'company': None,
                                 'workplace': None, 'created': None})
        self.assertEqual((job['title'], job['places'], job['company'], job['workType'], job['postedAt'], job['jobUrl']),
                         ('', [], None, None, None, 'https://jobs.workable.com/view/x'))
        job = P.from_board('y', {'locations': ['TELECOMMUTE'], 'workplace': None})
        self.assertEqual((job['workType'], job['places']), ('remote', []))

    def test_a_filter_reads_the_feed_and_the_newest_jobs_the_biggest_career_pages(self):
        p = platform()
        boards = [Board(f'c{i}', f'C{i}') for i in range(workable.CAREER_PAGES + 5)]
        for raw in ({'searchTerms': ['engineer']}, {'location': ['Germany']}, {'workType': ['hybrid']},
                    {'employmentType': ['part-time']}, {'location': ['remote']}):
            self.assertEqual(p.search_all(boards, parse_input(raw, p, {})), [workable.EVERYONE], raw)
        self.assertEqual(p.search_all(boards, parse_input({'postedWithin': '24 hours'}, p, {})), boards[:workable.CAREER_PAGES])
        self.assertEqual(p.search_all(boards, parse_input({}, p, {})), boards[:workable.CAREER_PAGES])
        self.assertEqual(p.cfg.search_terms, [])


def settings(raw=None):
    p = platform()
    p.search_all([BIFFA], parse_input(raw or {'searchTerms': ['engineer']}, p, {}))
    return p


class Feed(unittest.TestCase):
    def read(self, data: bytes, size: int, p=None):
        feed, close = (p or settings({'searchTerms': ['engineer'], 'includeDetails': True})).reader()
        jobs = []
        for i in range(0, len(data), size):
            jobs += feed(data[i:i + size])
        return jobs + close()

    def test_a_feed_job(self):
        jobs = self.read(FEED_XML, 4096)
        self.assertEqual([j['id'] for j in jobs], ['F44ED9E40A', '0908074E5D'])
        job = jobs[0]
        self.assertEqual((job['title'], job['company'], job['companySlug'], job['companyWebsite']),
                         ('System Engineer', 'Biffa Waste Services', 'biffa', 'https://www.biffa.co.uk/'))
        self.assertEqual((job['location'], job['places'][0]['country'], job['workType'], job['employmentType']),
                         ('Hyderabad, Telangana, India', 'IN', 'remote', 'contract'))
        self.assertEqual((job['postedAt'], job['dateOnly'], job['jobUrl'], job['applyUrl']),
                         ('2026-11-02T10:24:17+00:00', False, 'https://apply.workable.com/j/F44ED9E40A',
                          'https://apply.workable.com/j/F44ED9E40A/apply'))
        self.assertEqual(job['description'].split(), ['Build', '&', 'run', 'servers', 'Next'])

    def test_texts_are_kept_only_when_asked(self):
        job = self.read(FEED_XML, 4096, settings())[0]
        self.assertEqual((job['description'], job['descriptionHtml']), (None, None))

    def test_any_chunk_size_reads_the_same(self):
        self.assertEqual(self.read(FEED_XML, 7), self.read(FEED_XML, 100000))

    def test_odd_records(self):
        job = self.read(FEED_XML, 512)[1]
        self.assertEqual((job['company'], job['companySlug'], job['places'], job['workType'], job['employmentType'],
                          job['description'], job['jobUrl']),
                         ('Odorzx', None, [], None, None, None, 'https://apply.workable.com/j/0908074E5D'))

    def test_a_job_listed_once_per_place_is_one_job(self):
        def entry(code, city, when):
            return (f'<job><title><![CDATA[Driver]]></title><date><![CDATA[{when}]]></date><referencenumber><![CDATA[{code}]]>'
                    f'</referencenumber><company><![CDATA[Zgraph]]></company><city><![CDATA[{city}]]></city>'
                    f'<state><![CDATA[Florida]]></state><country><![CDATA[US]]></country></job>')
        when, later = 'Sun, 15 Mar 2026 20:17:38 UTC', 'Mon, 16 Mar 2026 08:00:00 UTC'
        xml = ('<source>' + entry('D7C890D403', 'Miami', when) + entry('D7C890D403', 'Orlando', when)
               + entry('ABCDEF1234', 'Tampa', when) + entry('D7C890D403', 'Tampa', later) + '</source>').encode()
        jobs = self.read(xml, 50)
        self.assertEqual([(j['id'], j['location'], j['otherLocations']) for j in jobs],
                         [('D7C890D403', 'Miami, Florida, United States', ['Orlando, Florida, United States']),
                          ('ABCDEF1234', 'Tampa, Florida, United States', []),
                          ('D7C890D403', 'Tampa, Florida, United States', [])])

    def test_a_shared_company_name_names_no_account(self):
        job = P.from_feed({'referencenumber': 'ABCDEF1234', 'title': 'Engineer', 'company': 'Bask Health'})
        self.assertIsNone(job['companySlug'])

    def test_dates_and_countries(self):
        self.assertEqual(workable.feed_date('Wed, 28 Jul 2021 10:24:17 UTC'), '2021-07-28T10:24:17+00:00')
        self.assertIsNone(workable.feed_date(''))
        self.assertEqual((workable.country_name('GB'), workable.country_name('us'), workable.country_name('XK2')),
                         ('United Kingdom', 'United States', 'XK2'))

    def test_the_word_and_the_period_choose_what_is_kept(self):
        p = settings({'searchTerms': ['engineer']})
        keep = p.wanted(None)
        jobs = self.read(FEED_XML, 4096, p)
        self.assertEqual([j['id'] for j in jobs if keep(j)], ['F44ED9E40A'])
        self.assertFalse(p.wanted(2)({'title': 'Engineer', 'postedAt': '2020-01-01T00:00:00+00:00'}))
        driver = {'title': 'Driver', 'description': 'Drive our fleet', 'postedAt': '2026-11-02T00:00:00+00:00'}
        self.assertFalse(settings({'searchTerms': ['fleet'], 'includeDetails': True}).wanted(None)(driver))
        self.assertTrue(settings({'searchTerms': ['fleet'], 'searchDescriptions': True}).wanted(None)(driver))


class Stream:
    """http.stream() of a feed: (status, chunks, encoding), where a chunk can be an error that breaks the download."""
    def __init__(self, plays: list):
        self.plays, self.opened = plays, 0

    def stream(self, method, url, **kwargs):
        play = self.plays.pop(0)
        self.opened += 1

        class Context:
            async def __aenter__(ctx):
                if isinstance(play, Exception):
                    raise play
                return ctx

            async def __aexit__(ctx, *args):
                return False

            status_code = play[0] if isinstance(play, tuple) else None
            headers = {'content-encoding': play[2]} if isinstance(play, tuple) and len(play) > 2 else {}
            request = httpx.Request('GET', url)

            async def aiter_raw(ctx, size=None):
                for chunk in play[1]:
                    if isinstance(chunk, Exception):
                        raise chunk
                    yield chunk
        return Context()


async def _instant(seconds):
    return None


class EveryCompany(unittest.TestCase):
    def collect(self, plays, raw=None, fallback=None):
        http = Stream(plays)
        p = settings(raw)

        async def career(http_, board):
            return fallback.get(board.key) if fallback else None
        p.open = career

        async def run():
            return [[j['id'] for j in page] async for page in p.pages(http, {'everyone': True}, 'engineer', None)]
        with patch.object(workable.asyncio, 'sleep', _instant):
            return asyncio.run(run()), http.opened, p

    def test_the_whole_feed(self):
        chunks = [FEED_XML[i:i + 200] for i in range(0, len(FEED_XML), 200)]
        pages, opened, _ = self.collect([(200, chunks)], {'searchTerms': ['engineer', 'driver']})
        self.assertEqual((pages, opened), ([['F44ED9E40A', '0908074E5D']], 1))

    def test_a_compressed_feed(self):
        packed = gzip.compress(FEED_XML)
        self.assertEqual(self.collect([(200, [packed[:300], packed[300:]], 'gzip')])[:2], ([['F44ED9E40A']], 1))

    def test_one_download_serves_every_word(self):
        http = Stream([(200, [FEED_XML])])
        p = settings({'searchTerms': ['engineer', 'driver']})
        session = {'everyone': True}

        async def run():
            first = [j['id'] for page in [x async for x in p.pages(http, session, 'engineer', None)] for j in page]
            second = [x async for x in p.pages(http, session, 'driver', None)]
            return first, second
        self.assertEqual((asyncio.run(run()), http.opened), ((['F44ED9E40A', '0908074E5D'], []), 1))

    def test_a_server_error_is_tried_again(self):
        self.assertEqual(self.collect([(503, []), (200, [FEED_XML])])[:2], ([['F44ED9E40A']], 2))

    def test_a_cut_feed_falls_back_to_the_biggest_career_pages(self):
        cut = (200, [FEED_XML[:FEED_XML.index(b'</job>') + 10]])
        session = {'account': 'biffa', 'name': 'Biffa Waste Services', 'website': None,
                   'jobs': [{**ENTRY, 'title': 'HGV Engineer'}]}
        pages, opened, p = self.collect([cut], fallback={'biffa': session})
        self.assertEqual((pages, opened), ([['8A29959BE6']], 1))       # a cut download is not tried again
        self.assertIn('could not be read (the download was cut before its end)', p.cfg.notes[-1])
        self.assertIn('1 biggest', p.cfg.notes[-1])

    def test_a_refusal_falls_back_at_once(self):
        pages, opened, p = self.collect([(429, [])], fallback={})
        self.assertEqual((pages, opened), ([], 1))
        self.assertIn('429', p.cfg.notes[-1])


class CareerPages(unittest.TestCase):
    def run_open(self, answers, board=BIFFA):
        send, calls = sender(answers)
        with patch.object(workable, 'send', send), patch.object(workable, 'PACE', 0):
            return asyncio.run(platform().open(None, board)), calls

    def test_a_career_page(self):
        session, calls = self.run_open([Response(200, WIDGET)])
        self.assertEqual((session['account'], session['name'], session['website'], len(session['jobs'])),
                         ('biffa', 'Biffa Waste Services', 'https://www.biffa.co.uk/', 2))
        self.assertEqual(calls, [('GET', 'https://apply.workable.com/api/v1/widget/accounts/biffa')])

    def test_no_such_account(self):
        self.assertEqual(self.run_open([Response(404, text='Not Found')])[0], None)

    def test_text_that_cannot_be_an_account_is_never_sent(self):
        session, calls = self.run_open([], Board('../secret', 'x'))
        self.assertEqual((session, calls), (None, []))

    def test_a_failure_stays_an_error(self):
        for bad in (Response(500), Response(200), Response(200, {'name': 'x'})):
            with self.assertRaises(BoardError):
                self.run_open([bad])

    def test_the_job_board_needs_no_call(self):
        self.assertEqual(self.run_open([], EVERYONE), ({'everyone': True}, []))

    def test_a_word_on_a_career_page_is_matched_here(self):
        other = {**ENTRY, 'shortcode': 'ABCDEF1234', 'title': 'Accounts Payable', 'locations': [ENTRY['locations'][0]],
                 'description': '<p>Drive our ADR fleet</p>'}

        async def run(term, session):
            return [[j['id'] for j in page] async for page in P.pages(None, session, term, None)]
        plain = {'account': 'biffa', 'name': 'Biffa', 'jobs': [ENTRY, other]}
        self.assertEqual(asyncio.run(run('driver', plain)), [['8A29959BE6']])
        self.assertEqual(asyncio.run(run('fleet', plain)), [['ABCDEF1234']])      # only when the page gave descriptions
        self.assertEqual(asyncio.run(run('chef', plain)), [])

    def test_descriptions_are_asked_for_only_when_searched(self):
        send, calls = sender([Response(200, WIDGET), Response(200, WIDGET)])
        p = platform()
        with patch.object(workable, 'send', send), patch.object(workable, 'PACE', 0):
            p.search_all([BIFFA], parse_input({'searchTerms': ['driver'], 'searchDescriptions': True}, p, {}))
            asyncio.run(p.open(None, BIFFA))
            p.search_all([BIFFA], parse_input({'searchTerms': ['driver']}, p, {}))
            asyncio.run(p.open(None, BIFFA))
        self.assertEqual([c[1] for c in calls], ['https://apply.workable.com/api/v1/widget/accounts/biffa?details=true',
                                                 'https://apply.workable.com/api/v1/widget/accounts/biffa'])

    def test_pages_of_a_career_page(self):
        async def run():
            return [page async for page in P.pages(None, {'account': 'biffa', 'name': 'Biffa', 'jobs': WIDGET['jobs']}, None, None)]
        pages = asyncio.run(run())
        self.assertEqual([[j['id'] for j in page] for page in pages], [['8A29959BE6']])


class Details(unittest.TestCase):
    JOB = {'id': '584D288213', 'title': 'Accounts Payable', 'company': 'Biffa Waste Services', 'companySlug': 'biffa',
           'postedAt': '2026-10-28', 'dateOnly': True, 'places': []}

    def details(self, answers, job, session=None):
        send, calls = sender(answers)
        with patch.object(workable, 'send', send), patch.object(workable, 'PACE', 0):
            return asyncio.run(platform().details(None, session or {'everyone': True}, dict(job))), calls

    def test_a_career_page_job_reads_its_own_answer(self):
        job, calls = self.details([Response(200, V2)], self.JOB)
        self.assertEqual((job['salary'], job['workType'], job['department'], job['postedAt'], job['description']),
                         ('GBP 28,000 per year', 'onsite', 'Group Central Functions', '2026-10-28',
                          'Accounts Payable\nOwn the ledger\nAP experience'))
        self.assertEqual(calls, [('GET', 'https://apply.workable.com/api/v2/accounts/biffa/jobs/584D288213')])

    def test_a_job_with_no_account_finds_it_first(self):
        moved = Response(301, headers={'location': '/biffa/j/584D288213'})
        job, calls = self.details([moved, Response(200, V2)], {**self.JOB, 'companySlug': None})
        self.assertEqual((job['salary'], len(calls)), ('GBP 28,000 per year', 2))

    def test_a_job_that_closed_meanwhile_stays_as_listed(self):
        job, _ = self.details([Response(404, text='Job not found')], self.JOB)
        self.assertEqual(job, self.JOB)

    def test_a_feed_job_gets_its_whole_text(self):
        job = {**self.JOB, 'descriptionHtml': '<p>Own the</p><p>ledger</p>', 'description': ' Own the  ledger '}
        result, _ = self.details([Response(404, text='Job not found')], job)
        self.assertEqual(result['description'], 'Own the\nledger')


class Refusals(unittest.TestCase):
    def test_after_three_refusals_in_a_row_workable_is_not_asked_again(self):
        calls = []

        async def refused(http, method, url, **kwargs):
            calls.append(url)
            raise BoardError('HTTP 429')
        p = settings({})
        with patch.object(workable, 'send', refused), patch.object(workable, 'PACE', 0):
            for _ in range(5):
                with self.assertRaises(BoardError):
                    asyncio.run(p.job_answer(None, 'biffa', '584D288213'))
        self.assertEqual(len(calls), workable.REFUSALS)
        self.assertIn('stopped answering this address', p.cfg.notes[-1])

    def test_an_answer_in_between_starts_the_count_again(self):
        answers = [BoardError('HTTP 429'), BoardError('HTTP 429'), Response(200, V2), BoardError('HTTP 429'), BoardError('HTTP 429'), Response(200, V2)]

        async def mixed(http, method, url, **kwargs):
            reply = answers.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        p = settings({})
        with patch.object(workable, 'send', mixed), patch.object(workable, 'PACE', 0):
            got = []
            for _ in range(6):
                try:
                    got.append(asyncio.run(p.job_answer(None, 'biffa', '584D288213'))['title'])
                except BoardError:
                    got.append('refused')
        self.assertEqual(got, ['refused', 'refused', 'Accounts Payable', 'refused', 'refused', 'Accounts Payable'])


class Lookup(unittest.TestCase):
    SESSION = {'account': 'biffa', 'name': 'Biffa Waste Services', 'website': 'https://www.biffa.co.uk/', 'jobs': [ENTRY]}

    def check(self, answers, job_id, session=None):
        send, calls = sender(answers)
        with patch.object(workable, 'send', send), patch.object(workable, 'PACE', 0):
            return asyncio.run(platform().lookup(None, dict(session or self.SESSION), job_id)), calls

    def test_a_listed_job_is_open_with_its_details(self):
        (status, job), calls = self.check([Response(200, {**V2, 'shortcode': '8A29959BE6'})], '8A29959BE6')
        self.assertEqual((status, job['id'], job['salary'], job['company'], job['companySlug']),
                         ('open', '8A29959BE6', 'GBP 28,000 per year', 'Biffa Waste Services', 'biffa'))
        self.assertIn('/api/v2/accounts/biffa/jobs/8A29959BE6', calls[0][1])

    def test_an_unlisted_job_that_answers_is_open(self):
        (status, job), _ = self.check([Response(200, V2)], '584D288213')
        self.assertEqual((status, job['title'], job['postedAt']), ('open', 'Accounts Payable', '2026-10-28'))

    def test_a_job_workable_does_not_know_is_closed(self):
        (status, job), _ = self.check([Response(404, text='Job not found')], 'ABCDEF1234')
        self.assertEqual((status, job['company'], job['companySlug']), ('closed', 'Biffa Waste Services', 'biffa'))

    def test_an_old_job_number(self):
        found = Response(301, headers={'location': 'https://apply.workable.com/biffa/j/584D288213'})
        (status, job), calls = self.check([found, Response(200, V2)], '6230970')
        self.assertEqual((status, job['id']), ('open', '584D288213'))
        self.assertIn('/biffa/jobs/6230970', calls[0][1])
        gone = Response(302, headers={'location': 'https://apply.workable.com/biffa/?not_found=true'})
        self.assertEqual(self.check([gone], '6230970')[0], ('closed', None))

    def test_a_link_with_no_account(self):
        moved = Response(301, headers={'location': '/biffa/j/584D288213'})
        (status, job), calls = self.check([moved, Response(200, WIDGET), Response(200, V2)], '584D288213', {'everyone': True})
        self.assertEqual((status, job['companySlug'], job['company'], job['companyWebsite']),
                         ('open', 'biffa', 'Biffa Waste Services', 'https://www.biffa.co.uk/'))
        oops = Response(302, headers={'location': '/oops'})
        (status, job), _ = self.check([oops], '584D288213', {'everyone': True})
        self.assertEqual((status, job['company']), ('closed', None))

    def test_a_job_board_link(self):
        data = {'id': 'b50514a8', 'title': 'Senior Back-End Software Engineer', 'state': 'published',
                'company': {'title': 'Bask Health', 'website': 'http://bask.health'}, 'created': '2024-12-13T23:06:09.000Z',
                'locations': ['TELECOMMUTE', 'New York, New York, United States'], 'workplace': 'remote',
                'location': {'city': 'New York', 'subregion': 'New York', 'countryName': 'United States'},
                'employmentType': 'Full-time', 'description': '<p>Build</p>', 'requirementsSection': '', 'benefitsSection': None,
                'salaryFrom': 150000, 'salaryTo': 180000, 'salaryCurrencyIsoCode': 'USD', 'salaryFrequency': 'year',
                'url': 'https://jobs.workable.com/view/omtQvzG25ZmkM1dS9dWiDj/remote-senior'}
        (status, job), _ = self.check([Response(200, data)], 'omtQvzG25ZmkM1dS9dWiDj', {'everyone': True})
        self.assertEqual((status, job['company'], job['companySlug'], job['location'], job['workType'], job['postedAt']),
                         ('open', 'Bask Health', None, 'New York, New York, United States', 'remote', '2024-12-13T23:06:09.000Z'))
        self.assertEqual((job['salary'], job['places'][0]['country']), ('USD 150,000 - 180,000 per year', 'United States'))
        nobody = {'company': None, 'companySlug': None, 'companyWebsite': None}
        self.assertEqual(self.check([Response(410, text='Gone')], 'omtQvzG25ZmkM1dS9dWiDj', {'everyone': True})[0], ('closed', nobody))
        self.assertEqual(self.check([Response(404, text='Job not found')], 'omtQvzG25ZmkM1dS9dWiDj', {'everyone': True})[0],
                         ('not found', nobody))


if __name__ == '__main__':
    unittest.main()
