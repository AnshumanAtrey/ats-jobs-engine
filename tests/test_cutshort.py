"""Cutshort: links, the job page's answer, Cutshort's own filters, the sitemap search, the pace and the refusals, on
answers recorded from cutshort.io on 2026-11-08 (trimmed to the fields the adapter reads)."""
import asyncio
import json
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from ats_jobs import engine
from ats_jobs.http import BoardError
from ats_jobs.inputs import parse_input
from ats_jobs.platforms import cutshort as cs
from ats_jobs.platforms.base import Board
from ats_jobs.platforms.cutshort import Cutshort, alias_names, created_at, job_from, listing_parts, short_id
from ats_jobs.rows import Filters, job_area, job_row

ONSITE = json.loads(r'''{"pageData": {"_id": "6aed7a8ef6ef9ae6ea9bc8ef", "headline": "B2B Marketing Executive", "hiringForClient": false, "hiringIntentShownOn": "2026-11-06T07:17:24.046Z", "remoteType": "remote_not_okay", "roleTypes": ["full_time"], "expRange": {"min": 0, "maxInternal": 5, "max": 5, "minVanity": 0, "maxVanity": 5}, "salaryRange": "₹3L - ₹3L / yr", "showOnlyMaxSalary": false, "locations": "Bengaluru (Bangalore)", "allSkills": ["Communication Skills", "Presentation Skills"], "publicUrl": "https://cutshort.io/job/B2B-Marketing-Executive-Bengaluru-Bangalore-Hello-Mentor-Private-Limited-9e2BHRfZ", "jobFactSummary": {"roleTitle": "B2B Marketing Executive", "companyName": "Hello Mentor Private Limited", "locations": "Bengaluru (Bangalore)", "companyStage": "Bootstrapped", "experience": "0 - 5 years", "salary": "₹3L - ₹3L / yr", "postedDate": "2026-11-06T07:17:24.046Z", "postedDateLabel": "Posted 6 Nov 2026"}, "sanitizedComment": "<p>Role Overview :</p><p>We are looking for a proactive and result-oriented B2B Marketing Executive to support our busin", "companyDetails": {"name": "Hello Mentor Private Limited", "alias": "hello-mentor-private-limited-65-MaMzzYis", "type": "Services", "size": "100-1000", "stage": "Bootstrapped", "founded": 2018, "links": {"website": "https://portal.hellomentor.in"}, "hiringForClient": false}, "companyId": {"alias": "hello-mentor-private-limited-65-MaMzzYis", "name": "Hello Mentor Private Limited"}}, "seoData": {"jsonlds": [{"@type": "JobPosting", "datePosted": "2026-11-06T06:58:22.892Z", "validThrough": "2027-02-04T07:18:35.433Z", "employmentType": ["FULL_TIME"], "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "streetAddress": "-", "addressLocality": "Bengaluru (Bangalore)", "postalCode": "560066", "addressCountry": "IN"}}, "title": "B2B Marketing Executive", "baseSalary": {"@type": "MonetaryAmount", "currency": "INR", "value": {"@type": "QuantitativeValue", "maxValue": 300000, "minValue": 300000, "unitText": "YEAR"}}}]}}''')
FRIENDLY = json.loads(r'''{"pageData": {"_id": "6aed9089f54efca4d3c126e9", "headline": "AI Quality Engineer", "hiringForClient": false, "hiringIntentShownOn": "2026-11-06T13:14:41.407Z", "remoteType": "remote_okay", "roleTypes": ["full_time"], "expRange": {"min": 2, "maxInternal": 10, "max": 10, "minVanity": 2, "maxVanity": 10}, "salaryRange": "Best in industry", "showOnlyMaxSalary": false, "locations": "Remote, Coimbatore", "allSkills": ["Software Testing (QA)", "Artificial Intelligence (AI)", "Anthropic Claude", "Prompt engineering", "Machine Learning (ML)", "Selenium", "Retrieval Augmented Generation (RAG)"], "publicUrl": "https://cutshort.io/job/AI-Quality-Engineer-Coimbatore-Techjays-6h6LkkxS", "jobFactSummary": {"roleTitle": "AI Quality Engineer", "companyName": "Techjays", "locations": "Remote, Coimbatore", "companyStage": "Profitable", "remotePolicy": "Remote friendly", "experience": "2 - 10 years", "postedDate": "2026-11-06T13:14:41.407Z", "postedDateLabel": "Posted 6 Nov 2026"}, "sanitizedComment": "<p><strong><em><u>TECHJAYS</u></em></strong></p><p><em>THE AI REIMAGINATION COMPANY</em></p><p>AI Quality Engineer</p><p", "companyDetails": {"name": "Techjays", "alias": "techjays-92-tGmHamzp", "type": "Services", "size": "100-1000", "stage": "Profitable", "founded": 2020, "links": {"website": "https://techjays.com"}, "hiringForClient": false}, "companyId": {"alias": "techjays-92-tGmHamzp", "name": "Techjays"}}, "seoData": {"jsonlds": [{"@type": "JobPosting", "datePosted": "2026-11-06T08:32:09.163Z", "validThrough": "2027-02-05T07:16:42.548Z", "employmentType": ["FULL_TIME"], "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "streetAddress": "-", "addressLocality": "Coimbatore", "postalCode": "641018", "addressCountry": "IN"}}, "title": "AI Quality Engineer", "baseSalary": {"@type": "MonetaryAmount", "currency": "INR", "value": {"@type": "QuantitativeValue", "unitText": "NA", "value": "Not Disclosed"}}}]}}''')
AGENCY = json.loads(r'''{"pageData": {"_id": "6aedc360d828097d4352ece9", "headline": "Oracle GTM Functional QA Engineer", "hiringForClient": true, "hiringIntentShownOn": "2026-11-06T12:13:01.657Z", "remoteType": "remote_only", "roleTypes": ["full_time"], "expRange": {"min": 6, "maxInternal": 17, "max": 17, "minVanity": 6, "maxVanity": 17}, "salaryRange": "₹20L - ₹50L / yr", "showOnlyMaxSalary": false, "locations": "Remote only", "allSkills": ["Global Trade Management", "GTM", "Global Trade Compliance", "Oracle Fusion Cloud"], "publicUrl": "https://cutshort.io/job/Oracle-GTM-Functional-QA-Engineer-Inceptigon-Solutions-Pty-Limited-PHzzTPnO", "jobFactSummary": {"roleTitle": "Oracle GTM Functional QA Engineer", "companyName": "Milwaukee Tool", "locations": "Remote only", "remotePolicy": "Remote only", "experience": "6 - 17 years", "salary": "₹20L - ₹50L / yr", "postedDate": "2026-11-06T12:13:01.657Z", "postedDateLabel": "Posted 6 Nov 2026"}, "sanitizedComment": "<p>Role Description</p><ul><li>We are looking for a Senior Oracle Global Trade Management (GTM) Functional QA Engineer t", "companyDetails": {"name": "Milwaukee Tool", "alias": null, "hiringForClient": true}, "companyId": {"alias": "jobs-at-inceptigon-solutions-pty-limited-55-iK2eFw3L", "name": "Inceptigon Solutions Pty Limited"}}, "seoData": {"jsonlds": [{"@type": "JobPosting", "datePosted": "2026-11-06T12:09:04.979Z", "validThrough": "2027-02-05T03:35:03.916Z", "employmentType": ["FULL_TIME"], "jobLocationType": "TELECOMMUTE", "applicantLocationRequirements": {"@type": "Country", "name": "NA"}, "title": "Oracle GTM Functional QA Engineer (Remote)", "baseSalary": {"@type": "MonetaryAmount", "currency": "INR", "value": {"@type": "QuantitativeValue", "maxValue": 5000000, "minValue": 2000000, "unitText": "YEAR"}}}]}}''')
INTERN = json.loads(r'''{"pageData": {"_id": "6aef3aa548f274487b2ec57a", "headline": "Social Media & Digital Marketing Intern", "hiringForClient": false, "hiringIntentShownOn": "2026-11-07T14:50:13.733Z", "remoteType": "remote_not_okay", "roleTypes": ["internship"], "expRange": {"min": 0, "maxInternal": 1, "max": 1, "minVanity": 0, "maxVanity": 1}, "salaryRange": "₹12000 - ₹18000 / mo", "showOnlyMaxSalary": false, "locations": "Pune", "allSkills": ["Social Media Marketing (SMM)", "Content Writing"], "publicUrl": "https://cutshort.io/job/Social-Media-Digital-Marketing-Intern-Pune-Clicks-And-Co-Media-BICcToox", "jobFactSummary": {"roleTitle": "Social Media & Digital Marketing Intern", "companyName": "Clicks And Co Media", "locations": "Pune", "experience": "0 - 1 years", "salary": "₹12000 - ₹18000 / mo", "postedDate": "2026-11-07T14:50:13.733Z", "postedDateLabel": "Posted 7 Nov 2026"}, "sanitizedComment": "<p>About us</p><p>Clicks and Co Media is a Pune-based creative and digital marketing studio, founded in 2019. Our in-hou", "companyDetails": {"name": "Clicks And Co Media", "alias": "jobs-at-clicks-and-co-media-57-GgUZfuNR", "founded": 2018, "links": {"website": "https://clicksandco.in"}, "hiringForClient": false}, "companyId": {"alias": "jobs-at-clicks-and-co-media-57-GgUZfuNR", "name": "Clicks And Co Media"}}, "seoData": {"jsonlds": [{"@type": "JobPosting", "datePosted": "2026-11-07T14:50:13.627Z", "validThrough": "2027-02-05T14:55:15.604Z", "employmentType": ["INTERN"], "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "streetAddress": "-", "addressLocality": "Pune", "postalCode": "411013", "addressCountry": "IN"}}, "title": "Social Media & Digital Marketing Intern", "baseSalary": {"@type": "MonetaryAmount", "currency": "INR", "value": {"@type": "QuantitativeValue", "maxValue": 18000, "minValue": 12000, "unitText": "MONTH"}}}]}}''')
ESOP = json.loads(r'''{"pageData": {"_id": "6aecdd8efcba99a3736b3774", "headline": "Senior Product Manager, AI", "hiringForClient": false, "hiringIntentShownOn": "2026-11-06T23:09:31.801Z", "remoteType": "remote_not_okay", "roleTypes": ["full_time"], "expRange": {"min": 2, "maxInternal": 8, "max": 8, "minVanity": 2, "maxVanity": 8}, "salaryRange": "₹30L - ₹65L / yr (ESOP available)", "showOnlyMaxSalary": false, "locations": "Bengaluru (Bangalore)", "allSkills": ["User Experience (UX) Design", "Artificial Intelligence (AI)", "Product Management"], "publicUrl": "https://cutshort.io/job/Senior-Product-Manager-AI-Bengaluru-Bangalore-Asha-Health-YC-F24--ZgeQIcht", "jobFactSummary": {"roleTitle": "Senior Product Manager, AI", "companyName": "Asha Health (YC F24)", "locations": "Bengaluru (Bangalore)", "companyStage": "Raised funding", "experience": "2 - 8 years", "salary": "₹30L - ₹65L / yr (ESOP available)", "postedDate": "2026-11-06T23:09:31.801Z", "postedDateLabel": "Posted 6 Nov 2026"}, "sanitizedComment": "<p><strong>About</strong> </p><p>Asha Health is building an AI OS for outpatient care. It’s the year of AI agents, and s", "companyDetails": {"name": "Asha Health (YC F24)", "alias": "asha-health-38-GJAeWzN7", "type": "Product", "size": "0-20", "stage": "Raised funding", "founded": 2024, "links": {"website": "https://asha.health"}, "hiringForClient": false}, "companyId": {"alias": "asha-health-38-GJAeWzN7", "name": "Asha Health (YC F24)"}}, "seoData": {"jsonlds": [{"@type": "JobPosting", "datePosted": "2026-11-05T19:48:30.813Z", "validThrough": "2027-02-04T23:34:47.081Z", "employmentType": ["FULL_TIME"], "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "streetAddress": "-", "addressLocality": "Bengaluru (Bangalore)", "postalCode": "560066", "addressCountry": "IN"}}, "title": "Senior Product Manager, AI", "baseSalary": {"@type": "MonetaryAmount", "currency": "INR", "value": {"@type": "QuantitativeValue", "maxValue": 6500000, "minValue": 3000000, "unitText": "YEAR"}}}]}}''')


def answer(data):
    return {'success': True, 'data': data}


class Response:
    def __init__(self, status_code, body=None, text='', headers=None):
        self.status_code, self.body = status_code, body
        self.text = text or (json.dumps(body) if body is not None else '')
        self.headers = headers or {'content-type': 'application/json; charset=utf-8'}

    def json(self):
        if self.body is None:
            raise ValueError('not JSON')
        return self.body


GONE = {'job_deactivated': 'closed', 'job_deleted': 'closed', 'job_not_found': 'not found'}


def gone(code):
    return Response(400, {'success': False, 'error': {'errorMsg': 'x', 'errorCode': code}})


class FakeHttp:
    """Answers by URL: a dict of url -> Response, or a list of them (one per call)."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def get(self, url, params=None, **kwargs):
        if params:
            url += '?' + '&'.join(f'{k}={v}' for k, v in params.items())
        self.calls.append(url)
        found = self.routes.get(url)
        if isinstance(found, list):
            return found.pop(0) if len(found) > 1 else found[0]
        return found or Response(404, None, 'not here', {'content-type': 'text/html'})


def sitemap(*slugs):
    return Response(200, None, ''.join(f'<url> <loc>https://cutshort.io/job/{s}</loc> <lastmod>2026-11-07T00:00:00Z'
                                       f'</lastmod> </url>' for s in slugs), {'content-type': 'text/xml'})


def job(slug, title, posted, where='Pune', remote='remote_not_okay', skills=(), exp=(0, 5), pay=None, company='Acme',
        stage=None, kind='Product', agency=False):
    """A job page answer in Cutshort's shape."""
    low, high = pay or (None, None)
    page = {'_id': 'a' * 24, 'headline': title, 'hiringForClient': agency, 'hiringIntentShownOn': posted,
            'remoteType': remote, 'roleTypes': ['full_time'], 'expRange': {'min': exp[0], 'max': exp[1]},
            'salaryRange': f'₹{low // 100000}L - ₹{high // 100000}L / yr' if pay else 'Best in industry',
            'locations': where, 'allSkills': list(skills), 'publicUrl': f'https://cutshort.io/job/{slug}',
            'jobFactSummary': {}, 'sanitizedComment': f'<p>{title}</p>',
            'companyDetails': {'name': company, 'alias': f'{company.lower()}-AbCdEfGh', 'type': kind,
                               'stage': stage}, 'companyId': {'name': 'Agency Co'}}
    salary = {'currency': 'INR', 'value': {'minValue': low, 'maxValue': high, 'unitText': 'YEAR'}} if pay else \
        {'currency': 'INR', 'value': {'unitText': 'NA', 'value': 'Not Disclosed'}}
    posting = {'@type': 'JobPosting', 'datePosted': posted, 'baseSalary': salary,
               'jobLocation': {'address': {'addressCountry': 'IN'}}}
    return Response(200, answer({'pageData': page, 'seoData': {'jsonlds': [posting]}}))


def platform_for(raw):
    p = Cutshort()
    cfg = parse_input(raw, p, {})
    p.search_all([], cfg)
    return p, cfg


def collect(p, http, session, days=None):
    async def go():
        return [j async for j in p.pages(http, session, None, days)]
    with patch.object(cs, 'PACE', 0):
        return asyncio.run(go())


class Links(unittest.TestCase):
    P = Cutshort()

    def test_job_links(self):
        for text in ('https://cutshort.io/job/Sr-Backend-Engineering-Manager-Egnyte-koBBDTQ1',
                     'cutshort.io/job/Sr-Backend-Engineering-Manager-Egnyte-koBBDTQ1?ref=email',
                     'https://www.cutshort.io/job/koBBDTQ1', 'https://cutshort.io/job/Sr-Backend-Engineering-Manager-Egnyte-koBBDTQ1/.'):
            self.assertEqual(self.P.read_link(text), ('*', 'koBBDTQ1'), text)

    def test_the_apply_link_names_the_job_by_its_object_id(self):
        self.assertEqual(self.P.read_link('https://cutshort.io/profile/view/j/6a69d90f4613efecf4d13392'),
                         ('*', '6a69d90f4613efecf4d13392'))

    def test_a_company_link(self):
        self.assertEqual(self.P.read_link('https://cutshort.io/company/egnyte-SClW7oZH'), ('egnyte-SClW7oZH', None))

    def test_listings_and_searches_name_no_company(self):
        for text in ('https://cutshort.io/jobs/python-jobs', 'https://cutshort.io/search-jobs?free_text=react',
                     'cutshort.io', 'https://cutshort.io/companies/fintech'):
            self.assertEqual(self.P.read_link(text), (None, None), text)

    def test_other_text_is_no_link(self):
        for text in ('python developer', 'https://jobs.lever.co/acme', 'Egnyte'):
            self.assertIsNone(self.P.read_link(text))

    def test_helpers(self):
        self.assertEqual(short_id('Senior-Python-Developer-Pune-Acme-AbCd1234'), 'AbCd1234')
        self.assertIsNone(short_id('Senior-Python-Developer'))
        self.assertEqual(created_at('6a69d90f4613efecf4d13392'), '2026-07-29T10:42:23+00:00')
        self.assertEqual(alias_names('hello-mentor-private-limited-65-MaMzzYis'),
                         {'hello mentor private limited 65', 'hello mentor private limited'})
        self.assertEqual(alias_names('jobs-at-am:pm-network-31-iYNcGQRZ'), {'am pm network 31', 'am pm network'})


class Answers(unittest.TestCase):
    def test_an_office_job_with_pay(self):
        job = job_from(ONSITE)
        self.assertEqual((job['id'], job['title'], job['company'], job['companySlug']),
                         ('9e2BHRfZ', 'B2B Marketing Executive', 'Hello Mentor Private Limited',
                          'hello-mentor-private-limited-65-MaMzzYis'))
        self.assertEqual((job['location'], job['places'][0]['country'], job['workType'], job['employmentType']),
                         ('Bengaluru (Bangalore)', 'India', 'onsite', 'full-time'))
        self.assertEqual((job['salary'], job['salaryMin'], job['salaryMax'], job['salaryCurrency'], job['salaryPeriod']),
                         ('₹3L - ₹3L / yr', 300000, 300000, 'INR', 'year'))
        self.assertEqual(job['postedAt'], '2026-11-06T06:58:22.892Z')          # the markup's datePosted, as Cutshort's lists
        extra = job
        self.assertEqual((extra['experienceMin'], extra['experienceMax'], extra['companyType'], extra['companyStage'],
                          extra['companySize'], extra['companyFounded'], extra['agency'], extra['remotePolicy']),
                         (0, 5, 'Services', 'Bootstrapped', '100-1000', 2018, None, None))
        self.assertEqual(extra['lastActiveAt'], '2026-11-06T07:17:24.046Z')
        self.assertEqual(job['companyWebsite'], 'https://portal.hellomentor.in')

    def test_remote_friendly_is_hybrid_at_the_office_it_names_and_hidden_pay_is_none(self):
        job = job_from(FRIENDLY)
        self.assertEqual((job['workType'], job['location'], job['otherLocations'], job['remotePolicy']),
                         ('hybrid', 'Coimbatore', [], 'Remote friendly'))
        self.assertEqual((job['salary'], job['salaryMin'], job['salaryCurrency']), (None, None, None))
        self.assertIn('Retrieval Augmented Generation (RAG)', job['skills'])

    def test_an_agency_job_names_the_client_and_the_agency(self):
        job = job_from(AGENCY)
        self.assertEqual((job['company'], job['companySlug'], job['agency']),
                         ('Milwaukee Tool', None, 'Inceptigon Solutions Pty Limited'))

    def test_remote_only_is_remote_in_india(self):
        job = job_from(AGENCY)
        self.assertEqual((job['workType'], job['location'], job['places'][0]['country']), ('remote', 'Remote only', 'India'))
        area = job_area(job)
        self.assertEqual((area.countries, area.open_remote), ({'IN'}, False))

    def test_remote_only_paid_in_another_currency_names_no_country(self):
        data = json.loads(json.dumps(AGENCY))
        data['seoData']['jsonlds'][0]['baseSalary']['currency'] = 'USD'
        data['pageData']['salaryRange'] = '$80K - $120K / yr'
        self.assertIsNone(job_from(data)['places'][0]['country'])

    def test_an_internship_paid_by_the_month(self):
        job = job_from(INTERN)
        self.assertEqual((job['employmentType'], job['salaryMin'], job['salaryMax'], job['salaryPeriod']),
                         ('intern', 12000, 18000, 'month'))
        self.assertEqual((job['companyType'], job['companyStage']), (None, None))

    def test_esop_and_a_later_active_date(self):
        job = job_from(ESOP)
        self.assertTrue(job['offersEquity'])
        self.assertEqual((job['postedAt'], job['lastActiveAt']),
                         ('2026-11-05T19:48:30.813Z', '2026-11-06T23:09:31.801Z'))
        self.assertEqual(job['salary'], '₹30L - ₹65L / yr (ESOP available)')

    def test_a_reopened_job_is_dated_by_its_reopening_as_cutshorts_lists_date_it(self):
        data = json.loads(json.dumps(ONSITE))
        data['pageData']['_id'] = '67eb9465b0d7390027ce5f50'                  # created on 2025-04-01
        data['seoData']['jsonlds'][0]['datePosted'] = '2026-11-02T17:26:42.688Z'
        self.assertEqual(job_from(data)['postedAt'], '2026-11-02T17:26:42.688Z')
        del data['seoData']['jsonlds'][0]['datePosted']                       # no markup date: the ObjectId's time
        self.assertEqual(job_from(data)['postedAt'], '2025-04-01T07:23:17+00:00')

    def test_upto_pay_has_no_lowest_amount(self):
        data = json.loads(json.dumps(ONSITE))
        data['pageData'].update(showOnlyMaxSalary=True, salaryRange='Upto ₹3L / yr')
        job = job_from(data)
        self.assertEqual((job['salary'], job['salaryMin'], job['salaryMax']), ('Upto ₹3L / yr', None, 300000))

    def test_a_row_carries_cutshorts_fields_and_leaves_out_what_it_never_has(self):
        p, cfg = platform_for({'includeDetails': True})
        run = engine.Run(p, cfg, [], None, None)
        row = run.row(cs.EVERYONE, job_from(ESOP), True)                 # as the engine builds every row
        self.assertEqual(row['skills'], ['User Experience (UX) Design', 'Artificial Intelligence (AI)', 'Product Management'])
        self.assertEqual((row['offersEquity'], row['salaryMin'], row['countryCode'], row['jobId'], row['companyStage']),
                         (True, 3000000, 'IN', 'ZgeQIcht', 'Raised funding'))
        for key in ('department', 'team', 'expiresAt', 'reference', 'region'):
            self.assertNotIn(key, row)
        self.assertTrue(row['description'].startswith('About'))
        plain = run.row(cs.EVERYONE, job_from(ESOP), False)               # a plain row has them too, with no description
        self.assertEqual((plain['experienceMin'], plain['salaryMax'], plain.get('description')), (2, 6500000, None))

    def test_an_answer_without_a_page_is_no_job(self):
        self.assertIsNone(job_from({}))
        self.assertIsNone(job_from({'pageData': {'headline': 'x'}}))


class Inputs(unittest.TestCase):
    def test_cutshorts_own_fields(self):
        p, _ = platform_for({'skills': ['python', 'react'], 'experienceYears': 3, 'minSalaryLakhs': 12,
                             'companyStage': ['raised funding'], 'companyType': ['product'], 'excludeAgencies': True})
        self.assertEqual(p.settings(), {'skills': ['python', 'react'], 'experienceYears': [3.0, 3.0], 'minSalaryLakhs': 12.0,
                                     'companyStage': ['funded'], 'companyType': ['product'], 'excludeAgencies': True})

    def test_years_and_lakhs_as_people_type_them(self):
        notes = []
        self.assertEqual(Cutshort.read_years('2-5', notes), [2.0, 5.0])
        self.assertEqual(Cutshort.read_years('fresher', notes), [0.0, 0.0])
        self.assertEqual(Cutshort.read_years('5 years', notes), [5.0, 5.0])
        self.assertIsNone(Cutshort.read_years('', notes))
        self.assertEqual(Cutshort.read_lakhs('12 LPA', notes), 12.0)
        self.assertEqual(Cutshort.read_lakhs('1.2 crore', notes), 120.0)
        self.assertEqual(Cutshort.read_lakhs(1200000, notes), 12.0)
        self.assertIn('rupees', notes[-1])
        self.assertIsNone(Cutshort.read_lakhs('lots', notes))

    def test_every_stage_or_type_is_no_filter(self):
        p, _ = platform_for({'companyStage': ['bootstrapped', 'funded', 'profitable'], 'companyType': ['product', 'services']})
        self.assertEqual((p.settings()['companyStage'], p.settings()['companyType']), ([], []))

    def test_a_pasted_listing_link_is_read_as_its_skill_place_and_remote(self):
        p, cfg = platform_for({'searchTerms': ['https://cutshort.io/jobs/remote-python-jobs-in-bangalore-bengaluru']})
        self.assertEqual((p.settings()['skills'], cfg.locations, cfg.work_types), (['python'], ['Bangalore', 'Bengaluru'], ['remote']))
        self.assertEqual(cfg.skipped, [])
        self.assertTrue(any('Read the Cutshort link' in n for n in cfg.notes))

    def test_a_company_listing_and_a_search_link(self):
        p, _ = platform_for({'skills': ['https://cutshort.io/jobs/product-based-company-jobs',
                                        'https://cutshort.io/search-jobs?free_text=golang']})
        self.assertEqual((p.settings()['companyType'], p.settings()['skills']), (['product'], ['golang']))

    def test_listing_parts(self):
        self.assertEqual(listing_parts('datascience-jobs')['skills'], ['datascience'])
        self.assertEqual(listing_parts('python-jobs-in-delhi-ncr-gurgaon-noida')['places'], ['Delhi', 'Ncr', 'Gurgaon', 'Noida'])
        self.assertEqual(listing_parts('funded-startup-jobs')['setting'], ('companyStage', 'funded'))
        self.assertEqual(listing_parts('startup-jobs')['skills'], [])

    def test_a_city_also_searches_its_other_name(self):
        _, cfg = platform_for({'location': ['Gurgaon']})
        self.assertEqual(cfg.locations, ['Gurgaon', 'Gurugram'])

    def test_job_types_cutshort_does_not_have_say_so(self):
        _, cfg = platform_for({'employmentType': ['contract']})
        self.assertTrue(any('two job types' in n for n in cfg.notes))

    def test_a_typed_company_is_kept_for_open_to_find(self):
        _, cfg = platform_for({'companies': ['Egnyte', 'https://cutshort.io/company/egnyte-SClW7oZH']})
        self.assertEqual(cfg.companies, ['Egnyte', 'egnyte-SClW7oZH'])


class Candidates(unittest.TestCase):
    ADDRESSES = ['Senior-Python-Developer-Pune-Acme-AbCd1234', 'C-Developer-Mumbai-Beta-AbCd1235',
                 'Sales-Manager-Bengaluru-Bangalore-Gamma-AbCd1236', 'Python-Developer-Remote-Corp-AbCd1237',
                 'Frontend-Engineer-Pune-Delta-AbCd1238']

    def picked(self, raw):
        p, _ = platform_for(raw)
        return p.candidates(self.ADDRESSES)

    def test_words_in_the_address(self):
        self.assertEqual(self.picked({'searchTerms': ['python developer']}),
                         ['Senior-Python-Developer-Pune-Acme-AbCd1234', 'Python-Developer-Remote-Corp-AbCd1237'])

    def test_signs_are_not_in_addresses(self):
        self.assertEqual(self.picked({'searchTerms': ['c++ developer']}), ['C-Developer-Mumbai-Beta-AbCd1235'])

    def test_joined_words_match(self):
        self.assertEqual(self.picked({'searchTerms': ['front end']}), ['Frontend-Engineer-Pune-Delta-AbCd1238'])

    def test_towns_narrow_but_countries_do_not(self):
        self.assertEqual(self.picked({'location': ['Bangalore']}), ['Sales-Manager-Bengaluru-Bangalore-Gamma-AbCd1236'])
        self.assertEqual(len(self.picked({'location': ['India']})), 5)
        self.assertEqual(len(self.picked({'location': ['Pune', 'India']})), 5)

    def test_descriptions_and_skills_cannot_be_narrowed_by_address(self):
        self.assertEqual(len(self.picked({'searchTerms': ['python'], 'searchDescriptions': True})), 5)
        self.assertEqual(len(self.picked({'skills': ['python']})), 5)


class SiteFilters(unittest.TestCase):
    def ok(self, raw, response):
        p, _ = platform_for(raw)
        return p.fits(job_from(response.body['data']))

    def test_skills_match_a_skill_or_the_title(self):
        self.assertTrue(self.ok({'skills': ['react']}, job('x-AbCd1234', 'Engineer', '2026-11-07T00:00:00Z', skills=['React.js'])))
        self.assertTrue(self.ok({'skills': ['golang']}, job('x-AbCd1234', 'Engineer', '2026-11-07T00:00:00Z',
                                                            skills=['Go Programming (Golang)'])))
        self.assertTrue(self.ok({'skills': ['devops']}, job('x-AbCd1234', 'DevOps Engineer', '2026-11-07T00:00:00Z')))
        self.assertFalse(self.ok({'skills': ['java']}, job('x-AbCd1234', 'Engineer', '2026-11-07T00:00:00Z',
                                                           skills=['Javascript'])))

    def test_experience_range_holds_the_years(self):
        self.assertTrue(self.ok({'experienceYears': 3}, job('x-AbCd1234', 'E', '2026-11-07T00:00:00Z', exp=(2, 6))))
        self.assertFalse(self.ok({'experienceYears': 8}, job('x-AbCd1234', 'E', '2026-11-07T00:00:00Z', exp=(2, 6))))
        self.assertTrue(self.ok({'experienceYears': 0}, job('x-AbCd1234', 'E', '2026-11-07T00:00:00Z', exp=(0, 1))))

    def test_pay_reaches_the_lakhs_and_a_stipend_counts_twelve_months(self):
        self.assertTrue(self.ok({'minSalaryLakhs': 12}, job('x-AbCd1234', 'E', '2026-11-07T00:00:00Z', pay=(800000, 1500000))))
        self.assertFalse(self.ok({'minSalaryLakhs': 20}, job('x-AbCd1234', 'E', '2026-11-07T00:00:00Z', pay=(800000, 1500000))))
        self.assertFalse(self.ok({'minSalaryLakhs': 1}, job('x-AbCd1234', 'E', '2026-11-07T00:00:00Z')))     # hidden pay
        p, _ = platform_for({'minSalaryLakhs': 2})
        self.assertTrue(p.fits(job_from(INTERN)))           # 18,000 a month is 2.16 lakhs a year

    def test_stage_type_and_agencies(self):
        self.assertTrue(self.ok({'companyStage': ['funded']}, job('x-AbCd1234', 'E', '2026-11-07T00:00:00Z', stage='Raised funding')))
        self.assertFalse(self.ok({'companyStage': ['funded']}, job('x-AbCd1234', 'E', '2026-11-07T00:00:00Z')))
        self.assertTrue(self.ok({'companyType': ['services']}, job('x-AbCd1234', 'E', '2026-11-07T00:00:00Z',
                                                                    kind='Products & Services')))
        self.assertFalse(self.ok({'companyType': ['product']}, job('x-AbCd1234', 'E', '2026-11-07T00:00:00Z', kind='Services')))
        self.assertFalse(self.ok({'excludeAgencies': True}, job('x-AbCd1234', 'E', '2026-11-07T00:00:00Z', agency=True)))


SLUGS = [f'Python-Developer-Pune-Acme-Job{n:05d}' for n in range(8)]


def routes(*extra):
    found = {cs.JOBS_SITEMAP: sitemap(*SLUGS)}
    for n, slug in enumerate(SLUGS):
        found[cs.JOB_API.format(id=slug)] = job(slug, f'Python Developer {n}', f'2026-11-0{8 - n}T00:00:00Z',
                                                 exp=(n, n + 1))
    for key, value in extra:
        found[key] = value
    return found


class Scan(unittest.TestCase):
    def test_it_stops_once_max_jobs_matches_are_held(self):
        p, _ = platform_for({'maxJobs': 2})
        http = FakeHttp(routes())
        with patch.object(cs, 'PAGE', 3):
            pages = collect(p, http, {'everyone': True})
        self.assertEqual([j['title'] for page in pages for j in page],
                         ['Python Developer 0', 'Python Developer 1', 'Python Developer 2'])
        self.assertEqual(p.read, 3)                  # the first page held 2 matches: nothing older can make the cut

    def test_a_filter_that_drops_every_job_of_a_page_does_not_end_the_search(self):
        p, _ = platform_for({'maxJobs': 1, 'experienceYears': 6})
        http = FakeHttp(routes())
        with patch.object(cs, 'PAGE', 2):
            pages = collect(p, http, {'everyone': True})
        read = [j for page in pages for j in page]
        self.assertTrue(all(pages))                             # no empty page reaches the engine
        self.assertEqual([j['title'] for j in read if p.fits(j)], ['Python Developer 5'])
        self.assertEqual(len(read), 6)                          # it read on past pages with no fit, and stopped at one

    def test_a_note_says_how_many_jobs_cutshorts_own_filters_kept(self):
        p, cfg = platform_for({'maxJobs': 1, 'experienceYears': 6})
        with patch.object(cs, 'PAGE', 2):
            collect(p, FakeHttp(routes()), {'everyone': True})
        self.assertIn("Cutshort's own filters (experience) kept 1 of the 6 jobs read, newest first back to 2026-11-03.",
                      cfg.notes)
        self.assertEqual(sum('own filters' in n for n in cfg.notes), 1)      # one note, kept current

    def test_it_stops_past_the_period(self):
        p, _ = platform_for({'postedWithin': '2 days'})
        http = FakeHttp(routes())
        with patch.object(cs, 'PAGE', 2), patch.object(cs, 'datetime', FixedNow):
            collect(p, http, {'everyone': True}, days=3)
        self.assertLess(p.read, len(SLUGS))

    def test_a_job_closed_since_the_sitemap_is_left_out(self):
        p, _ = platform_for({'maxJobs': 0})
        http = FakeHttp(routes((cs.JOB_API.format(id=SLUGS[1]), gone('job_deactivated'))))
        pages = collect(p, http, {'everyone': True})
        self.assertEqual(sum(len(page) for page in pages), 7)
        self.assertEqual(p.closed, 1)

    def test_a_read_budget_marks_the_run_partial(self):
        p, cfg = platform_for({'maxJobs': 100, 'experienceYears': 50})
        http = FakeHttp(routes())
        with patch.object(cs, 'PAGE', 2), patch.object(cs, 'READ_BUDGET', 4):
            collect(p, http, {'everyone': True})
        self.assertTrue(p.incomplete)
        self.assertTrue(any('the most one run reads' in n for n in cfg.notes))

    def test_a_company_is_read_newest_first(self):
        p, _ = platform_for({'companies': ['acme-AbCdEfGh']})
        listed = [{'_id': '6a0000000000000000000000', 'publicUrl': f'https://cutshort.io/job/{SLUGS[3]}'},
                  {'_id': '6b0000000000000000000000', 'publicUrl': f'https://cutshort.io/job/{SLUGS[0]}'}]
        http = FakeHttp(routes((cs.COMPANY_API.format(alias='acme-AbCdEfGh'),
                                Response(200, answer({'pageData': {'companyJobs': {'jobs': listed, 'page': 1, 'totalPages': 1}}})))))

        async def go():
            session = await p.open(http, Board('acme-AbCdEfGh', 'acme'))
            return [j async for j in p.pages(http, session, None, None)]
        with patch.object(cs, 'PACE', 0):
            pages = asyncio.run(go())
        self.assertEqual([j['title'] for j in pages[0]], ['Python Developer 0', 'Python Developer 3'])

    def test_a_company_typed_by_name_is_found_on_the_company_sitemap(self):
        p, _ = platform_for({})
        companies = Response(200, None, '<loc>https://cutshort.io/company/hello-mentor-private-limited-65-MaMzzYis</loc>'
                                        '<loc>https://cutshort.io/company/egnyte-SClW7oZH</loc>', {'content-type': 'text/xml'})
        http = FakeHttp({cs.COMPANIES_SITEMAP: companies})
        with patch.object(cs, 'PACE', 0):
            self.assertEqual(asyncio.run(p.named(http, 'Hello Mentor Private Limited')), ['hello-mentor-private-limited-65-MaMzzYis'])
            self.assertEqual(asyncio.run(p.named(http, 'Hello Mentor')), ['hello-mentor-private-limited-65-MaMzzYis'])
            self.assertEqual(asyncio.run(p.named(http, 'Nobody Here')), [])


LISTINGS = Response(200, None, ''.join(f'<loc>https://cutshort.io/jobs/{s}</loc>' for s in (
    'python-jobs', 'remote-python-jobs', 'python-jobs-in-pune', 'python-jobs-in-bangalore-bengaluru', 'reactjs-jobs',
    'machine-learning-ml-jobs', 'java-jobs')), {'content-type': 'text/xml'})


def listing(slugs, full=False):
    jobs = [{'publicUrl': f'https://cutshort.io/job/{s}'} for s in slugs] + [{'publicUrl': 'x'}] * (50 - len(slugs) if full else 0)
    return Response(200, answer({'pageData': {'jobs': jobs}}))


class Listings(unittest.TestCase):
    SLUGS = set(LISTINGS.text.replace('<loc>https://cutshort.io/jobs/', ' ').replace('</loc>', ' ').split())

    def found(self, raw, skill):
        p, _ = platform_for(raw)
        return p.listing_for(skill, self.SLUGS)

    def test_a_typed_skill_finds_its_listing(self):
        self.assertEqual(self.found({}, 'python'), 'python-jobs')
        self.assertEqual(self.found({}, 'react js'), 'reactjs-jobs')
        self.assertEqual(self.found({}, 'machine learning'), 'machine-learning-ml-jobs')
        self.assertIsNone(self.found({}, 'cobol'))

    def test_the_narrowest_listing_the_search_allows(self):
        self.assertEqual(self.found({'workType': ['remote']}, 'python'), 'remote-python-jobs')
        self.assertEqual(self.found({'location': ['Pune']}, 'python'), 'python-jobs-in-pune')
        self.assertEqual(self.found({'location': ['Bangalore']}, 'python'), 'python-jobs-in-bangalore-bengaluru')
        self.assertEqual(self.found({'location': ['Pune', 'Mumbai']}, 'python'), 'python-jobs')

    def test_a_pasted_listing_is_read_as_it_is(self):
        p, _ = platform_for({'searchTerms': ['https://cutshort.io/jobs/remote-python-jobs']})
        self.assertEqual(p.settings()['listings'], ['remote-python-jobs'])
        self.assertEqual(p.listing_for('python', self.SLUGS), 'remote-python-jobs')

    def picked(self, complete):
        addresses = [f'Role-{n}-Acme-Job{n:05d}' for n in range(10)]
        addresses[3] = 'Python-Developer-Acme-Job00003'
        p, _ = platform_for({'skills': ['python']})
        page = listing(['Role-2-Acme-Job00002', 'Role-5-Acme-Job00005'], full=not complete)
        http = FakeHttp({cs.LISTINGS_SITEMAP: LISTINGS, cs.LISTING_API.format(slug='python-jobs'): page,
                         **{f'{cs.LISTING_API.format(slug="python-jobs")}?page={n}': page for n in range(2, 9)}})
        with patch.object(cs, 'PACE', 0):
            return [int(a[-5:]) for a in asyncio.run(p.skill_addresses(http, addresses))]

    def test_only_the_jobs_a_listing_names_inside_its_depth(self):
        # 0 and 1 are newer than the listing's newest job, 3 has the skill in its title, 2 and 5 are listed
        self.assertEqual(self.picked(complete=True), [0, 1, 2, 3, 5])

    def test_past_the_last_page_of_a_full_listing_every_job_is_opened(self):
        self.assertEqual(self.picked(complete=False), [0, 1, 2, 3, 5, 6, 7, 8, 9])

    def test_title_words_or_descriptions_need_no_listing(self):
        for raw in ({'skills': ['python'], 'searchTerms': ['backend']}, {'searchTerms': ['python'], 'searchDescriptions': True}):
            p, _ = platform_for(raw)
            self.assertIsNone(asyncio.run(p.skill_addresses(FakeHttp({}), ['A-Job00001'])))

    def test_a_listing_that_cannot_be_read_falls_back_to_every_job(self):
        p, cfg = platform_for({'skills': ['python'], 'maxJobs': 2})
        http = FakeHttp(routes((cs.LISTINGS_SITEMAP, Response(502, None, 'bad gateway'))))
        with patch.object(cs, 'RETRY_S', ()):
            pages = collect(p, http, {'everyone': True})
        self.assertEqual(p.read, len(SLUGS))                 # every job of the sitemap was a candidate
        self.assertTrue(any('could not be read' in n for n in cfg.notes))


class FixedNow(cs.datetime):
    @classmethod
    def now(cls, tz=None):
        return cs.datetime(2026, 11, 8, 12, 0, tzinfo=tz)


class Calls(unittest.TestCase):
    def run_call(self, p, http, url='https://cutshort.io/x'):
        async def go():
            return await p.call(http, url)
        with patch.object(cs, 'PACE', 0), patch.object(cs.asyncio, 'sleep', _no_wait):
            return asyncio.run(go())

    def test_a_refusal_is_waited_out_once(self):
        p, cfg = platform_for({})
        refused = Response(429, None, 'error code: 1015', {'retry-after': '274', 'content-type': 'text/plain'})
        http = FakeHttp({'https://cutshort.io/x': [refused, Response(200, {'ok': 1})]})
        self.assertEqual(self.run_call(p, http).status_code, 200)
        self.assertTrue(any('paused 274 s, once' in n for n in cfg.notes))

    def test_a_second_refusal_stops_every_call(self):
        p, cfg = platform_for({})
        refused = Response(429, None, 'error code: 1015', {'retry-after': '274', 'content-type': 'text/plain'})
        http = FakeHttp({'https://cutshort.io/x': [refused, refused]})
        with self.assertRaises(cs.Stopped):
            self.run_call(p, http)
        with self.assertRaises(cs.Stopped):
            self.run_call(p, http)
        self.assertEqual(len(http.calls), 2)
        self.assertTrue(any('stopped asking it' in n for n in cfg.notes))

    def test_a_refusal_to_a_call_sent_before_the_wait_is_the_same_refusal(self):
        p, _ = platform_for({})
        p._waited, p._resumed = True, 100.0
        refused = Response(429, None, 'error code: 1015', {'retry-after': '274', 'content-type': 'text/plain'})
        asyncio.run(p.refused(refused, sent=50.0))           # returns: sent again, not a second refusal
        self.assertIsNone(p._stopped)

    def test_a_server_error_is_tried_again_then_fails_the_call(self):
        p, _ = platform_for({})
        http = FakeHttp({'https://cutshort.io/x': [Response(502, None, 'bad gateway')]})
        with self.assertRaises(BoardError):
            self.run_call(p, http)
        self.assertEqual(len(http.calls), 3)

    def test_a_job_that_is_gone(self):
        for code, status in GONE.items():
            p, _ = platform_for({})
            http = FakeHttp({cs.JOB_API.format(id='koBBDTQ1'): gone(code)})
            with patch.object(cs, 'PACE', 0):
                found = asyncio.run(p.lookup(http, {'everyone': True}, 'koBBDTQ1'))
            self.assertEqual(found, (status, cs.NOBODY), code)

    def test_an_open_job(self):
        p, _ = platform_for({})
        http = FakeHttp({cs.JOB_API.format(id='9e2BHRfZ'): Response(200, answer(ONSITE))})
        with patch.object(cs, 'PACE', 0):
            status, found = asyncio.run(p.lookup(http, {'everyone': True}, '9e2BHRfZ'))
        self.assertEqual((status, found['title']), ('open', 'B2B Marketing Executive'))


async def _no_wait(seconds):
    return None


class EndToEnd(unittest.TestCase):
    def search(self, raw, http):
        p = Cutshort()
        cfg = parse_input(raw, p, {})

        @asynccontextmanager
        async def fake_client(actor, connections=64):
            yield http

        class Delivered:
            def __init__(self):
                self.pushed, self.rows, self.limit = [], 0, False

            def can_pay(self, event):
                return True

            async def push(self, rows, event):
                self.pushed += rows
                self.rows += len(rows)
                return len(rows)

        run = engine.Run(p, cfg, [], None, Delivered())
        with patch.object(engine, 'client', fake_client), patch.object(engine, 'safe_status', _no_wait), \
                patch.object(engine, 'seconds_left_in_run', lambda: None), patch.object(cs, 'PACE', 0):
            asyncio.run(run.search())
        return run

    def test_the_newest_matching_jobs_come_first_with_cutshorts_fields(self):
        run = self.search({'searchTerms': ['python developer'], 'maxJobs': 3}, FakeHttp(routes()))
        rows = run.delivery.pushed
        self.assertEqual([r['title'] for r in rows], ['Python Developer 0', 'Python Developer 1', 'Python Developer 2'])
        self.assertEqual((rows[0]['experienceMin'], rows[0]['platform'], rows[0]['jobId']), (0, 'cutshort', 'Job00000'))
        self.assertIn('Cutshort', run.message())

    def test_india_finds_remote_only_jobs_and_worldwide_does_not(self):
        http = FakeHttp({cs.JOBS_SITEMAP: sitemap('Remote-Dev-Acme-AbCd1234'),
                         cs.JOB_API.format(id='Remote-Dev-Acme-AbCd1234'): job('Remote-Dev-Acme-AbCd1234', 'Remote Dev',
                                                                                '2026-11-07T00:00:00Z', where='Remote only',
                                                                                remote='remote_only')})
        self.assertEqual(self.search({'location': ['India']}, http).delivery.rows, 1)
        self.assertEqual(self.search({'location': ['Worldwide']}, FakeHttp(dict(http.routes))).delivery.rows, 0)

    def test_settings_of_cutshort_appear_in_output(self):
        run = self.search({'skills': ['python'], 'maxJobs': 1}, FakeHttp(routes()))
        self.assertEqual(run.output('done')['settings']['skills'], ['python'])


if __name__ == '__main__':
    unittest.main()
