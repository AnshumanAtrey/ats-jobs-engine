"""SmartRecruiters: links, places, pay, paging and lookups, on answers shaped like the Posting API (2026-11-02)."""
import asyncio
import re
import unittest
from unittest.mock import patch

from ats_jobs.http import BoardError, BoardMissing
from ats_jobs.inputs import parse_input
from ats_jobs.platforms.base import Board
from ats_jobs.platforms.smartrecruiters import (DORMANT, SmartRecruiters, clean, country_name, dormant_sites, job_ad,
                                                job_type, pay, place, posting_id, slug)
from ats_jobs.rows import job_area

P = SmartRecruiters()
P._known = {'boschgroup', 'servicenow'}
UUID = 'b016f5d8-9ee3-4413-bf78-1249428b6880'
SESSION = {'company': 'BoschGroup'}
RECORD = {
    'id': '744000159116379', 'name': ' PMI Project  Lead_ EM ', 'uuid': UUID, 'refNumber': 'REF296620S',
    'company': {'identifier': 'BoschGroup', 'name': 'Bosch Group'}, 'releasedDate': '2026-11-02T11:51:05.135Z',
    'location': {'city': 'Shanghai', 'region': 'Shanghai', 'country': 'cn', 'remote': False, 'hybrid': False,
                 'fullLocation': 'Shanghai, Shanghai, China'},
    'industry': {'id': 'automotive', 'label': 'Automotive'}, 'department': {},
    'function': {'id': 'engineering', 'label': 'Engineering'},
    'typeOfEmployment': {'id': 'permanent', 'label': 'Full-time'},
    'experienceLevel': {'id': 'associate', 'label': 'Associate'}, 'customField': [], 'visibility': 'PUBLIC',
    'creator': {'name': 'A Recruiter'}, 'language': {'code': 'en', 'label': 'English', 'labelNative': 'English (US)'},
}
DETAIL = {
    **RECORD, 'active': True,
    'postingUrl': 'https://jobs.smartrecruiters.com/BoschGroup/744000159116379-pmi-project-lead-em',
    'applyUrl': 'https://jobs.smartrecruiters.com/BoschGroup/744000159116379-pmi-project-lead-em?oga=true',
    'compensation': {'min': 60000, 'max': 70000, 'currency': 'USD', 'period': 'YEARLY'},
    'jobAd': {'sections': {
        'companyDescription': {'title': 'Company Description', 'text': '<p>Welcome to Bosch.</p>'},
        'jobDescription': {'title': 'Job Description', 'text': '<ul><li>Lead PMI projects &amp; audits</li></ul>'},
        'qualifications': {'title': 'Qualifications', 'text': ''},
        'additionalInformation': {'title': 'Additional Information', 'text': ''}}},
}


class Response:
    def __init__(self, status_code, data=None, text=''):
        self.status_code, self.data, self.text = status_code, data, text

    def json(self):
        if self.data is None:
            raise ValueError('not JSON')
        return self.data


def listing(records, total=None, offset=0):
    return Response(200, {'offset': offset, 'limit': 100, 'totalFound': len(records) if total is None else total,
                          'content': records})


def sender(answers: list):
    async def send(http, method, url, **kwargs):
        calls.append((url, kwargs.get('params') or {}))
        return answers.pop(0)
    calls = []
    return send, calls


def run(coro, answers):
    send, calls = sender(answers)
    with patch('ats_jobs.platforms.smartrecruiters.send', send):
        return asyncio.run(coro), calls


async def collect(platform, session, term=None, days=None):
    return [page async for page in platform.pages(None, session, term, days)]


class Links(unittest.TestCase):
    def test_board_links(self):
        for text in ('https://careers.smartrecruiters.com/BoschGroup', 'careers.smartrecruiters.com/BoschGroup/',
                     'HTTPS://CAREERS.SMARTRECRUITERS.COM/BoschGroup?search=&page=0', 'https://jobs.smartrecruiters.com/BoschGroup',
                     'http://careers.smartrecruiters.com/BoschGroup/bosch-careers-page', 'careers.smartrecruiters.com/BoschGroup/en',
                     'https://www.smartrecruiters.com/BoschGroup', 'https://smartrecruiters.com/BoschGroup/',
                     'https://jobs.smartrecruiters.com/my-applications/BoschGroup/input-email?dcr_ci=BoschGroup',
                     'https://jobs.smartrecruiters.com/?keyword=x&company=BoschGroup',
                     'https://api.smartrecruiters.com/v1/companies/BoschGroup/postings?limit=100'):
            self.assertEqual(P.read_link(text), ('BoschGroup', None), text)

    def test_job_links(self):
        job = '744000159116379'
        for text in (f'https://jobs.smartrecruiters.com/BoschGroup/{job}-pmi-project-lead-em',
                     f'jobs.smartrecruiters.com/BoschGroup/{job}-pmi-project-lead-em?oga=true&trid=x',
                     f'https://jobs.smartrecruiters.com/BoschGroup/{job}', f'https://jobs.smartrecruiters.com/BoschGroup/{job}/',
                     f'https://jobs.smartrecruiters.com/BoschGroup/{job}-pmi/apply',
                     f'https://jobs.smartrecruiters.com/BoschGroup/{job}/porcher-h-f',
                     f'https://jobs.smartrecruiters.com/BoschGroup/{job}-servicetechniker-(m/w/d)-windenergieanlagen',
                     f'https://www.smartrecruiters.com/BoschGroup/{job}-pmi-project-lead-em',
                     f'https://jobs.smartrecruiters.com/oneclick-ui/company/BoschGroup/job/{job}?lang=en&dcr_ci=BoschGroup',
                     f'https://api.smartrecruiters.com/v1/companies/BoschGroup/postings/{job}'):
            self.assertEqual(P.read_link(text), ('BoschGroup', job), text)

    def test_links_that_name_a_job_by_its_uuid(self):
        for text in (f'https://jobs.smartrecruiters.com/oneclick-ui/company/BoschGroup/publication/{UUID}?dcr_ci=BoschGroup',
                     f'https://jobs.smartrecruiters.com/external-referrals/company/BoschGroup/publication/{UUID}?dcr_ci=BoschGroup',
                     f'https://jobs.smartrecruiters.com/ni/BoschGroup/{UUID}-process-workers-imlay',
                     f'https://jobs.smartrecruiters.com/BoschGroup/{UUID.upper()}'):
            self.assertEqual(P.read_link(text), ('BoschGroup', UUID), text)

    def test_an_old_short_id_is_a_job(self):
        self.assertEqual(P.read_link('https://jobs.smartrecruiters.com/AUDIENCEX/95757342'), ('AUDIENCEX', '95757342'))

    def test_a_link_with_no_company(self):
        for text in ('jobs.smartrecruiters.com', 'https://careers.smartrecruiters.com/', 'https://jobs.smartrecruiters.com/sr-jobs/search',
                     'https://careers.smartrecruiters.com/?search=&page=0&department=12097751', 'https://www.smartrecruiters.com/'):
            self.assertEqual(P.read_link(text), (None, None), text)

    def test_other_text_is_no_link(self):
        for text in ('https://example.com/careers', 'software engineer', 'BoschGroup', 'jobs.lever.co/ramp',
                     'https://www.smartrecruiters.com/resources/blog/hiring', 'https://c.smartrecruiters.com/logo.png',
                     'https://api.smartrecruiters.com/v1/configuration'):
            self.assertIsNone(P.read_link(text), text)

    def test_ids_are_read_from_the_start_of_a_segment(self):
        self.assertEqual(posting_id('744000159116379-pmi-project-lead-em'), '744000159116379')
        self.assertEqual(posting_id(f'{UUID}-process-workers'), UUID)
        self.assertIsNone(posting_id('bosch-careers-page'))
        self.assertIsNone(posting_id('12345'))

    def test_a_typed_company_becomes_an_identifier(self):
        self.assertEqual(P.guess_board('Bosch Group'), 'BoschGroup')
        self.assertEqual(P.guess_board("McDonald's Corporation"), 'McDonaldsCorporation')
        self.assertEqual(P.guess_board('Bosch-HomeComfort'), 'Bosch-HomeComfort')
        self.assertIsNone(P.guess_board('...'))

    def test_a_pasted_dork_limits_the_search_to_one_company(self):
        cfg = parse_input({'searchTerms': ['site:jobs.smartrecruiters.com/BoschGroup intext:engineer']}, P, {})
        self.assertEqual((cfg.search_terms, cfg.companies), (['engineer'], ['BoschGroup']))
        cfg = parse_input({'searchTerms': ['site:careers.smartrecruiters.com intext:"nurse"']}, P, {})
        self.assertEqual((cfg.search_terms, cfg.companies), (['nurse'], []))

    def test_links_in_the_search_box_and_the_job_links_field(self):
        cfg = parse_input({'searchTerms': ['https://careers.smartrecruiters.com/BoschGroup'],
                           'jobUrls': ['https://jobs.smartrecruiters.com/BoschGroup/744000159116379-x']}, P, {})
        self.assertEqual(cfg.job_urls[0][:2], ('BoschGroup', '744000159116379'))


class Parsing(unittest.TestCase):
    def test_a_place_reads_like_an_address(self):
        self.assertEqual(place(RECORD['location']), {'name': 'Shanghai, China', 'city': 'Shanghai', 'region': 'Shanghai', 'country': 'China'})
        spot = place({'city': 'Singapore', 'country': 'sg', 'fullLocation': 'Singapore, , Singapore'})
        self.assertEqual((spot['name'], spot['region']), ('Singapore', None))
        spot = place({'city': 'Corpus Christi', 'region': 'TX', 'country': 'us', 'fullLocation': 'Corpus Christi, TX, United States'})
        self.assertEqual(spot['name'], 'Corpus Christi, TX, United States')
        self.assertEqual(job_area({'places': [spot]}).countries, {'US'})

    def test_a_place_with_almost_nothing(self):
        self.assertIsNone(place({}))
        self.assertEqual(place({'city': None, 'country': 'de'})['name'], 'Germany')
        self.assertEqual(place({'city': '  coimbatore ', 'region': '', 'country': 'in', 'fullLocation': 'coimbatore, , India'})['name'],
                         'coimbatore, India')

    def test_the_country_is_smartrecruiters_word_when_it_names_the_country(self):
        self.assertEqual(country_name('us', 'Sumter, , United States'), 'United States')
        self.assertEqual(country_name('kr', 'Seoul, , Korea, Republic of'), 'South Korea')
        self.assertEqual(country_name('ae', None), 'United Arab Emirates')
        self.assertEqual(country_name('xx', None), 'XX')
        self.assertIsNone(country_name(None, 'Somewhere'))

    def test_every_employment_type(self):
        for kind, label, want in (('permanent', 'Full-time', 'full-time'), ('part-time', 'Part-time', 'part-time'),
                                  ('contract', 'Contract', 'contract'), ('intern', 'Intern', 'intern'),
                                  ('temporary', 'Temporary', 'temporary'), (None, 'Full time', 'full-time'),
                                  ('other', 'Volunteer', None), (None, None, None)):
            self.assertEqual(job_type({'id': kind, 'label': label}), want, (kind, label))
        self.assertIsNone(job_type(None))

    def test_pay_is_what_the_company_stated(self):
        self.assertEqual(pay(DETAIL['compensation']), {'salary': 'USD 60,000 - 70,000 a year', 'salaryMin': 60000,
                                                       'salaryMax': 70000, 'salaryCurrency': 'USD', 'salaryPeriod': 'year'})
        self.assertEqual(pay({'min': 18, 'max': 18, 'currency': 'USD', 'period': 'HOURLY'})['salary'], 'USD 18 an hour')
        self.assertEqual(pay({'min': 22.5, 'currency': 'EUR', 'period': 'DAILY'})['salary'], 'EUR 22.50 a day')
        self.assertEqual(pay({'salary': {'min': 20, 'currency': 'USD', 'period': 'hourly'}})['salaryPeriod'], 'hour')
        self.assertIsNone(pay({'min': 50000, 'currency': 'GBP'})['salaryPeriod'])

    def test_a_ceiling_alone_reads_up_to(self):
        # Dungarvin 744000159209739 on 2026-11-02 states only a maximum, and its job page says "up to USD 18.50 - hourly"
        self.assertEqual(pay({'max': 18.5, 'currency': 'USD', 'period': 'HOURLY'}),
                         {'salary': 'up to USD 18.50 an hour', 'salaryMin': None, 'salaryMax': 18.5,
                          'salaryCurrency': 'USD', 'salaryPeriod': 'hour'})
        self.assertEqual(pay({'min': 0, 'max': 2020.24, 'currency': 'EUR', 'period': 'MONTHLY'})['salary'],
                         'up to EUR 2,020.24 a month')

    def test_zero_pay_is_no_pay(self):
        for nothing in ({'min': 0, 'max': 0, 'currency': 'USD', 'period': 'HOURLY'}, {'currency': 'USD'}, {}, None,
                        {'min': 'n/a', 'currency': 'USD'}):
            self.assertEqual(pay(nothing), {})

    def test_the_job_ad_keeps_its_sections(self):
        self.assertEqual(job_ad(DETAIL), '<h3>Company Description</h3><p>Welcome to Bosch.</p>'
                                         '<h3>Job Description</h3><ul><li>Lead PMI projects &amp; audits</li></ul>')
        self.assertIsNone(job_ad({'jobAd': {'sections': {}}}))
        self.assertIsNone(job_ad({}))
        self.assertEqual(job_ad({'jobAd': {'sections': {'jobDescription': {'title': 'R&D <x>', 'text': '<p>a</p>'}}}}),
                         '<h3>R&amp;D &lt;x&gt;</h3><p>a</p>')

    def test_the_slug_is_smartrecruiters_own(self):
        self.assertEqual(slug('PMI Project Lead_ EM'), 'pmi-project-lead-em')
        self.assertEqual(slug(' Ingénieur Maintenance Prédictive H/F'), '-ingenieur-maintenance-predictive-h-f')
        self.assertEqual(slug('Projektleder – Anlæg Øst'), 'projektleder-anl-g-st')
        self.assertEqual(slug('CNC Machinist (Night-Shift) '), 'cnc-machinist-night-shift-')

    def test_titles_are_cleaned(self):
        self.assertEqual(clean(' Sales  &amp; Marketing '), 'Sales & Marketing')
        self.assertEqual(clean('R&D Engineer'), 'R&D Engineer')
        self.assertEqual(clean(None), '')

    def test_a_record_becomes_a_job(self):
        job = P.job(SESSION, RECORD)
        self.assertEqual((job['id'], job['company'], job['title'], job['location'], job['workType'], job['employmentType']),
                         ('744000159116379', 'Bosch Group', 'PMI Project Lead_ EM', 'Shanghai, China', 'onsite', 'full-time'))
        self.assertEqual((job['postedAt'], job['dateOnly'], job['reference'], job['department']),
                         ('2026-11-02T11:51:05.135Z', False, 'REF296620S', None))
        self.assertEqual(job['jobUrl'], 'https://jobs.smartrecruiters.com/BoschGroup/744000159116379-pmi-project-lead-em')
        self.assertEqual(job['applyUrl'], job['jobUrl'] + '?oga=true')
        self.assertNotIn('A Recruiter', str(job))

    def test_remote_and_hybrid_are_what_the_company_set(self):
        remote = P.job(SESSION, {**RECORD, 'location': {**RECORD['location'], 'remote': True}})
        hybrid = P.job(SESSION, {**RECORD, 'location': {**RECORD['location'], 'hybrid': True}})
        self.assertEqual((remote['workType'], hybrid['workType']), ('remote', 'hybrid'))

    def test_a_record_with_almost_nothing(self):
        job = P.job(SESSION, {'id': 1, 'name': None, 'company': None, 'location': None, 'typeOfEmployment': None,
                              'department': None})
        self.assertEqual((job['id'], job['title'], job['company'], job['location'], job['places'], job['employmentType']),
                         ('1', '', None, None, [], None))
        self.assertEqual(job['jobUrl'], 'https://jobs.smartrecruiters.com/BoschGroup/1')

    def test_details_add_the_ad_pay_and_links(self):
        job = P.enrich(P.job(SESSION, RECORD), DETAIL)
        self.assertEqual((job['salary'], job['salaryPeriod'], job['applyUrl']),
                         ('USD 60,000 - 70,000 a year', 'year', DETAIL['applyUrl']))
        self.assertEqual(job['description'], 'Company Description\nWelcome to Bosch.\nJob Description\nLead PMI projects & audits')


class Dormant(unittest.TestCase):
    """Career sites with nothing posted in two years: comment lines of the company file, read only when named."""
    TEXT = ('# SmartRecruiters career sites, updated 2026-11-02: every career site found with a job posted since 2024-11-02.\n'
            'BoschGroup\tBosch Group\n'
            '# Left out of runs, read when named: 2 career sites with nothing posted since 2024-11-02.\n'
            f'{DORMANT}McKesson1\tMcKesson\t2023-09-14\t38\n'
            f'{DORMANT}AdeebaEServicesPvtLtd\tAdeeba e Services Pvt. Ltd.\t2017-08-30\t23736\n')

    def setUp(self):
        self.platform = SmartRecruiters()
        self.platform._dormant = dormant_sites(self.TEXT)

    def test_the_lines_are_read_by_name_squeezed_name_and_key(self):
        self.assertEqual(dormant_sites(self.TEXT), {
            'mckesson1': 'McKesson1', 'mckesson': 'McKesson1', 'adeebaeservicespvtltd': 'AdeebaEServicesPvtLtd',
            'adeeba e services pvt. ltd.': 'AdeebaEServicesPvtLtd'})
        self.assertEqual(dormant_sites('BoschGroup\tBosch Group\n# a comment\n'), {})

    def test_a_typed_company_finds_its_dormant_site(self):
        for typed in ('McKesson', 'mckesson', 'Mc Kesson', 'mckesson1', 'Adeeba e Services Pvt. Ltd.'):
            self.assertIn(self.platform.guess_board(typed), ('McKesson1', 'AdeebaEServicesPvtLtd'), typed)
        self.assertEqual(self.platform.guess_board('Some New Company'), 'SomeNewCompany')

    def test_a_dormant_site_opens_without_a_call(self):
        self.platform._known = None
        self.assertIn('mckesson1', self.platform.known())
        session, calls = run(self.platform.open(None, Board('McKesson1', 'McKesson1')), [])
        self.assertEqual((session, calls), ({'company': 'McKesson1'}, []))

    def test_the_company_file_agrees_with_its_own_counts(self):
        from importlib import resources
        text = resources.files('ats_jobs').joinpath('data/smartrecruiters.tsv').read_text(encoding='utf-8')
        boards, updated = SmartRecruiters().boards()
        counts = re.search(r'^# ([\d,]+) career sites', text, re.M)
        self.assertEqual(len(boards), int(counts.group(1).replace(',', '')))
        self.assertRegex(updated or '', r'^\d{4}-\d\d-\d\d$')
        left_out = re.search(r'^# Left out of runs, read when named: ([\d,]+) career sites', text, re.M)
        dormant = [line for line in text.splitlines() if line.startswith(DORMANT)]
        self.assertEqual(len(dormant), int(left_out.group(1).replace(',', '')) if left_out else 0)
        keys = {b.key.lower() for b in boards}
        self.assertFalse(keys & {line[len(DORMANT):].split('\t')[0].lower() for line in dormant})
        self.assertEqual(len(keys), len(boards))


class Opening(unittest.TestCase):
    def test_a_company_on_our_list_needs_no_call(self):
        session, calls = run(P.open(None, Board('BoschGroup', 'Bosch Group')), [])
        self.assertEqual((session, calls), ({'company': 'BoschGroup'}, []))

    def test_a_company_off_our_list_with_jobs_takes_its_own_identifier(self):
        session, calls = run(P.open(None, Board('abbvie', 'abbvie')),
                             [listing([{**RECORD, 'company': {'identifier': 'AbbVie', 'name': 'AbbVie'}}], total=1989)])
        self.assertEqual((session, len(calls), calls[0][1]), ({'company': 'AbbVie'}, 1, {'limit': 1}))

    def test_a_company_with_no_open_job_is_still_a_company(self):
        session, calls = run(P.open(None, Board('Acme', 'Acme')),
                             [listing([]), Response(200, {'identifier': 'acme', 'name': 'Acme Inc'})])
        self.assertEqual((session, len(calls)), ({'company': 'acme'}, 2))
        self.assertIn('company-name-lookup', calls[1][0])

    def test_a_company_that_locked_its_posting_api_is_a_missing_board(self):
        session, calls = run(P.open(None, Board('CHECK24', 'CHECK24')), [Response(401, text='401 Authorization Required')])
        self.assertEqual((session, len(calls)), (None, 1))

    def test_a_blocked_address_is_an_error_not_a_missing_company(self):
        blocked = Response(403, text='<title>Attention Required! | Cloudflare</title>')
        with self.assertRaises(BoardError):
            run(P.open(None, Board('Acme', 'Acme')), [blocked])

    def test_a_company_that_does_not_exist_is_a_missing_board(self):
        session, calls = run(P.open(None, Board('NoSuchCompany', 'x')), [listing([]), Response(200, '')])
        self.assertIsNone(session)
        self.assertEqual(len(calls), 2)

    def test_text_that_cannot_be_an_identifier_is_never_sent(self):
        for key in ('../secret', 'a b', 'Bosch_Group', ''):
            session, calls = run(P.open(None, Board(key, 'x')), [])
            self.assertEqual((session, calls), (None, []), key)

    def test_a_server_failure_stays_an_error(self):
        for answers in ([Response(503)], [listing([]), Response(500)], [listing([]), Response(200)], [Response(200)]):
            with self.assertRaises(BoardError):
                run(P.open(None, Board('Acme', 'Acme')), list(answers))


class Reading(unittest.TestCase):
    def test_one_job_first_then_full_pages_until_the_total(self):
        rest = [{**RECORD, 'id': f'2{i}'} for i in range(3)]
        pages, calls = run(collect(P, dict(SESSION)), [listing([RECORD], total=4), listing(rest, total=4, offset=1)])
        self.assertEqual([len(p) for p in pages], [1, 3])
        self.assertEqual([c[1] for c in calls], [{'limit': 1, 'offset': 0}, {'limit': 100, 'offset': 1}])
        self.assertIn('/v1/companies/BoschGroup/postings', calls[0][0])

    def test_a_word_search_starts_with_ten(self):
        first = [{**RECORD, 'id': f'1{i}'} for i in range(10)]
        _, calls = run(collect(P, dict(SESSION), 'engineer'), [listing(first, total=13), listing(first[:3], total=13, offset=10)])
        self.assertEqual([(c[1]['limit'], c[1]['offset']) for c in calls], [(10, 0), (100, 10)])

    def test_an_empty_board_yields_nothing(self):
        pages, calls = run(collect(P, dict(SESSION)), [listing([])])
        self.assertEqual((pages, len(calls)), ([], 1))

    def test_a_board_that_shrinks_while_read_stops(self):
        pages, calls = run(collect(P, dict(SESSION)), [listing([RECORD], total=500), listing([], total=1, offset=1)])
        self.assertEqual((len(pages), len(calls)), (1, 2))

    def test_words_and_dates_are_asked_of_smartrecruiters(self):
        pages, calls = run(collect(P, dict(SESSION), 'C++  Developer', 2), [listing([RECORD])])
        params = calls[0][1]
        self.assertEqual(params['q'], 'c++ developer')
        self.assertRegex(params['releasedAfter'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.000Z$')

    def test_operators_typed_in_a_term_are_not_sent(self):
        _, calls = run(collect(P, dict(SESSION), 'sales && "marketing" -remote'), [listing([])])
        self.assertEqual(calls[0][1]['q'], 'sales marketing remote')

    def test_a_missing_company_a_locked_one_and_a_failing_one(self):
        for status in (400, 401, 404):
            with self.assertRaises(BoardMissing):
                run(collect(P, dict(SESSION)), [Response(status)])
        for bad in (Response(403), Response(500), Response(200), Response(200, ['not', 'an', 'object'])):
            with self.assertRaises(BoardError):
                run(collect(P, dict(SESSION)), [bad])

    def test_odd_records_are_skipped_or_read(self):
        pages, _ = run(collect(P, dict(SESSION)), [listing([None, {'name': 'no id'}, {'id': '9', 'name': 'Only an id'}])])
        self.assertEqual([j['title'] for j in pages[0]], ['Only an id'])


class Descriptions(unittest.TestCase):
    def setUp(self):
        self.platform = SmartRecruiters()
        self.platform._known = set()
        self.platform.search_descriptions = True

    def test_any_word_matches_count_only_by_title_or_phrase(self):
        records = [{**RECORD, 'id': '1', 'name': 'Software Engineer', 'releasedDate': '2026-11-02T10:00:00.000Z'},
                   {**RECORD, 'id': '2', 'name': 'Sales Engineer', 'releasedDate': '2026-11-02T09:00:00.000Z'},
                   {**RECORD, 'id': '3', 'name': 'Embedded Developer', 'releasedDate': '2026-11-02T08:00:00.000Z'},
                   {**RECORD, 'id': '4', 'name': 'Engineer, Software Quality', 'releasedDate': '2026-11-02T07:00:00.000Z'}]
        phrase = [records[0], records[2]]
        pages, calls = run(collect(self.platform, dict(SESSION), 'software engineer'), [listing(records), listing(phrase)])
        self.assertEqual([j['id'] for j in pages[0]], ['1', None, '3', '4'])
        self.assertEqual([c[1]['q'] for c in calls], ['software engineer', '"software engineer"'])

    def test_the_phrase_search_is_read_only_as_far_as_the_pages(self):
        new = [{**RECORD, 'id': f'n{i}', 'name': 'Cook', 'releasedDate': f'2026-11-02T1{i}:00:00.000Z'} for i in range(9, -1, -1)]
        phrase = [{**RECORD, 'id': 'p', 'name': 'Cook', 'releasedDate': '2026-11-01T00:00:00.000Z'}] * 10
        pages, calls = run(collect(self.platform, dict(SESSION), 'line cook'),
                           [listing(new, total=10), listing(phrase, total=50)])
        self.assertEqual(len(calls), 2)
        self.assertEqual([j['id'] for j in pages[0]], [None] * 10)

    def test_one_word_needs_no_phrase(self):
        _, calls = run(collect(self.platform, dict(SESSION), 'python'), [listing([RECORD])])
        self.assertEqual([c[1]['q'] for c in calls], ['python'])

    def test_titles_alone_need_no_phrase(self):
        self.platform.search_descriptions = False
        pages, calls = run(collect(self.platform, dict(SESSION), 'software engineer'), [listing([RECORD])])
        self.assertEqual((len(calls), pages[0][0]['id']), (1, RECORD['id']))


class Lookup(unittest.TestCase):
    def check(self, answers, job_id='744000159116379'):
        return run(P.lookup(None, dict(SESSION), job_id), answers)[0]

    def test_an_open_job_comes_with_its_details(self):
        status, job = self.check([Response(200, DETAIL)])
        self.assertEqual((status, job['title'], job['salary']), ('open', 'PMI Project Lead_ EM', 'USD 60,000 - 70,000 a year'))
        self.assertEqual(job['jobUrl'], DETAIL['postingUrl'])

    def test_a_closed_job_and_a_job_that_never_was(self):
        self.assertEqual(self.check([Response(200, {**DETAIL, 'active': False})]), ('closed', None))
        self.assertEqual(self.check([Response(200, {**DETAIL, 'active': None})]), ('closed', None))
        self.assertEqual(self.check([Response(404, {'httpCode': 404, 'code': 'RESOURCE_NOT_FOUND'})]), ('not found', None))
        self.assertEqual(self.check([Response(400, text='Bad Request')], 'abc'), ('not found', None))
        locked = Response(401, text='<html><head><title>401 Authorization Required</title></head></html>')
        self.assertEqual(self.check([locked]), ('not found', None))

    def test_a_server_failure_stays_an_error(self):
        with self.assertRaises(BoardError):
            self.check([Response(502)])
        with self.assertRaises(BoardError):
            self.check([Response(200)])

    def test_a_blocked_address_could_not_check(self):
        with self.assertRaises(BoardError):
            self.check([Response(403, text='Sorry, you have been blocked')])
        with self.assertRaises(BoardError):
            run(P.details(None, dict(SESSION), P.job(SESSION, RECORD)), [Response(403)])

    def test_details_that_are_gone_leave_the_job_as_it_was(self):
        job = P.job(SESSION, RECORD)
        same, _ = run(P.details(None, dict(SESSION), job), [Response(404)])
        self.assertIs(same, job)
        with self.assertRaises(BoardError):
            run(P.details(None, dict(SESSION), job), [Response(500)])


class Refresh(unittest.TestCase):
    """scripts/refresh_smartrecruiters.py: a site that does not answer is not a site with no jobs."""

    def refresh(self, listed, answers: dict):
        """listed: {key: name} rows, or the whole text of the file."""
        import tempfile
        from pathlib import Path

        from scripts import refresh_smartrecruiters as refresh
        out = Path(tempfile.mkdtemp()) / 'smartrecruiters.tsv'
        text = listed if isinstance(listed, str) else '# updated 2026-11-01\n' + ''.join(f'{k}\t{v}\n' for k, v in listed.items())
        out.write_text(text, encoding='utf-8')

        async def box(http):
            return {}

        async def get_json(http, url, params):
            answer = answers[url.split('/companies/')[1].split('/')[0]]
            if answer == 'no answer':
                raise RuntimeError(f'no answer from {url}')
            return answer
        with patch.object(refresh, 'OUT', out), patch.object(refresh, 'company_box', box), \
                patch.object(refresh, 'get_json', get_json), patch('builtins.print'):
            asyncio.run(refresh.main(['--no-wayback']))
        return out.read_text(encoding='utf-8')

    @staticmethod
    def hiring(ident, name, jobs):
        return {'totalFound': jobs, 'content': [{'company': {'identifier': ident, 'name': name}}]}

    def test_a_listed_site_that_does_not_answer_stays(self):
        text = self.refresh({'BoschGroup': 'Bosch Group', 'Gone': 'Gone Inc', 'Flaky': 'Flaky Ltd'},
                            {'BoschGroup': self.hiring('BoschGroup', 'Bosch Group', 5), 'Gone': {'totalFound': 0, 'content': []},
                             'Flaky': 'no answer'})
        rows = [line.split('\t') for line in text.splitlines() if not line.startswith('#')]
        self.assertEqual(rows, [['BoschGroup', 'Bosch Group'], ['Flaky', 'Flaky Ltd']])

    def test_a_site_with_nothing_posted_in_two_years_is_left_out_of_runs_and_checked_again(self):
        def posted(answer, when):
            answer['content'][0]['releasedDate'] = when
            return answer
        bosch = posted(self.hiring('BoschGroup', 'Bosch Group', 5), '2026-11-02T11:51:05.135Z')
        gone = posted(self.hiring('McKesson', 'McKesson updated', 38), '2023-09-14T10:00:00.000Z')
        text = self.refresh({'BoschGroup': 'Bosch Group', 'McKesson': 'McKesson'}, {'BoschGroup': bosch, 'McKesson': gone})
        rows = [line.split('\t') for line in text.splitlines() if not line.startswith('#')]
        self.assertEqual(rows, [['BoschGroup', 'Bosch Group']])
        self.assertIn(f'{DORMANT}McKesson\tMcKesson Updated\t2023-09-14\t38', text.splitlines())
        self.assertEqual(sum('updated' in line for line in text.splitlines() if line.startswith('#')), 1)
        back = posted(self.hiring('McKesson', 'McKesson', 2), '2026-11-01T09:00:00.000Z')     # it posts again
        rows = [line.split('\t') for line in self.refresh(text, {'BoschGroup': bosch, 'McKesson': back}).splitlines()
                if not line.startswith('#')]
        self.assertEqual(rows, [['BoschGroup', 'Bosch Group'], ['McKesson', 'McKesson']])

    def test_many_sites_that_do_not_answer_leave_the_file_as_it_was(self):
        listed = {f'Site{i}': f'Site {i}' for i in range(12)}
        with self.assertRaises(SystemExit):
            self.refresh(listed, {k: 'no answer' for k in listed})

    def test_a_blocked_address_is_retried_then_fails(self):
        from scripts import refresh_smartrecruiters as refresh

        class Blocked:
            async def get(self, url, params=None):
                return Response(403, text='Sorry, you have been blocked')

        async def no_wait(seconds):
            return None
        with patch.object(refresh.asyncio, 'sleep', no_wait), self.assertRaises(RuntimeError):
            asyncio.run(refresh.get_json(Blocked(), 'https://api.smartrecruiters.com/v1/companies/Acme/postings', {}))


if __name__ == '__main__':
    unittest.main()
