"""foundit: links, jobs, places, routing, paging, refusals, details and checked links, on records from foundit's own API
(recorded 2026-11-08, trimmed to the fields the adapter reads); and the engine features it brought: the experience
filter, a platform's own row fields, paging on listedAt, and a filter a platform drops before the filters are built."""
import asyncio
import math
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from ats_jobs import engine
from ats_jobs.http import BoardError
from ats_jobs.inputs import parse_experience, parse_input
from ats_jobs.platforms import foundit as fi
from ats_jobs.rows import Filters, experience_fits, job_row
from tests.test_engine import Fake, search

SYNDICATED = {"jobId": 69755276, "siteContext": "rexmonster", "title": "Polarion ALM Developer", "companyName": "career soft solutions pvt. ltd.", "hideCompanyName": 0, "locations": "Bengaluru, India", "createdAt": 1794072730000, "freshness": 1794072730000, "closedAt": 1797877800000, "minimumExperience": {"years": 2}, "maximumExperience": {"years": 12}, "minimumSalary": {"currency": "INR", "absoluteValue": 0, "absoluteMonthlyValue": 0}, "maximumSalary": {"currency": "INR", "absoluteValue": 0, "absoluteMonthlyValue": 0}, "currencyCode": "INR", "hideSalary": 0, "jobSalaryConfidential": False, "jobTypes": [], "employmentTypes": ["Full time"], "functions": ["Software Development"], "industries": ["Other"], "skills": "Subversion, Teamcenter, VSCode, Polarion ALM,C#", "jobSource": "SCRAPPING", "totalApplicants": 1, "applyUrl": "https://www.linkedin.com/jobs/view/4486590080/", "redirectUrl": "https://www.linkedin.com/jobs/view/4486590080/", "seoJdUrl": "/job/polarion-alm-developer-career-soft-solutions-pvt-ltd-bengaluru-bangalore-india-69755276", "jdUrl": "/job/polarion-alm-developer-career-soft-solutions-pvt-ltd-bengaluru-bangalore-69755276", "seoCompanyUrl": "/search/career-soft-solutions-pvt-ltd-680966-jobs-career"}  # noqa: E501
RENEWED = {"jobId": 69651557, "siteContext": "rexmonster", "title": "OPPORTUNITY for: - Recruitment Business Partner (With Large MNC): bD_Ch-bSC_1026", "companyName": "Neugene International Inc", "hideCompanyName": 0, "locations": "Bidar, Bilaspur, Chhattisgarh, Bokaro Steel City", "createdAt": 1793431629000, "freshness": 1794079540000, "closedAt": 1799260200000, "minimumExperience": {"years": 14}, "maximumExperience": {"years": 24}, "minimumSalary": {"currency": "INR", "absoluteValue": 3000000, "absoluteMonthlyValue": 250000}, "maximumSalary": {"currency": "INR", "absoluteValue": 6500000, "absoluteMonthlyValue": 541667}, "currencyCode": "INR", "hideSalary": 1, "jobSalaryConfidential": True, "jobTypes": [], "employmentTypes": ["Full time"], "employerTypes": ["Consultant"], "functions": ["Others"], "industries": ["Chemical", "IT Management"], "skills": "Ai, IT, Chemical, Construction", "jobSource": "ORGANIC", "totalApplicants": 0, "redirectUrl": "", "seoJdUrl": "/job/opportunity-for-recruitment-business-partner-with-large-mnc-bdch-bsc1026-neugene-international-inc-bidar-bilaspur-chhattisgarh-bokaro-steel-city-69651557", "jdUrl": "/job/opportunity-for-recruitment-business-partner-with-large-mnc-bd-ch-bsc-1026-neugene-international-inc-bidar-69651557", "seoCompanyUrl": "/search/neugene-international-inc-96016-jobs-career"}  # noqa: E501
SINGAPORE_PAY = {"jobId": 69756896, "siteContext": "monstersingapore", "title": "IT Process & SDM Governance Specialist (Risk & Compliance)", "companyName": "Optimum Solutions (Singapore) Pte Ltd.", "hideCompanyName": 0, "locations": "Singapore", "createdAt": 1794091366000, "freshness": 1794091366000, "closedAt": 1799260200000, "minimumExperience": {"years": 3}, "maximumExperience": {"years": 10}, "minimumSalary": {"currency": "SGD", "absoluteValue": 60000, "absoluteMonthlyValue": 5000}, "maximumSalary": {"currency": "SGD", "absoluteValue": 72000, "absoluteMonthlyValue": 6000}, "currencyCode": "SGD", "hideSalary": 0, "jobSalaryConfidential": False, "jobTypes": [], "employmentTypes": ["Full time"], "employerTypes": ["Company"], "functions": ["Banking", "IT"], "industries": ["Information Technology", "Banking"], "skills": "Risk Assessment, Compliance Management, It Governance, Process Optimization", "jobSource": "ORGANIC", "totalApplicants": 0, "redirectUrl": "", "seoJdUrl": "/job/it-process-sdm-governance-specialist-risk-compliance-optimum-solutions-singapore-pte-ltd-singapore-69756896", "jdUrl": "/job/it-process-sdm-governance-specialist-risk-compliance-optimum-solutions-singapore-pte-ltd-singapore-69756896", "seoCompanyUrl": "/search/optimum-solutions-singapore-pte-ltd-260836-jobs-career"}  # noqa: E501
CROSS_SITE = {"jobId": 54015063, "siteContext": "monstersingapore", "title": "Junior Application Consultant", "companyName": "Ethereal Digital Sdn Bhd", "hideCompanyName": 0, "locations": "Kuala Lumpur", "createdAt": 1779935606000, "freshness": 1794079537000, "closedAt": 1799260200000, "minimumExperience": {"years": 0}, "maximumExperience": {"years": 0}, "minimumSalary": {"currency": "MYR", "absoluteValue": 30000, "absoluteMonthlyValue": 2500}, "maximumSalary": {"currency": "MYR", "absoluteValue": 36000, "absoluteMonthlyValue": 3000}, "currencyCode": "MYR", "hideSalary": 0, "jobSalaryConfidential": False, "jobTypes": [], "employmentTypes": ["Full time"], "employerTypes": ["Company"], "functions": ["Others"], "skills": "Problem Solving, Customer Support,Sql, Java, HTML", "jobSource": "ORGANIC", "totalApplicants": 55, "redirectUrl": "", "seoJdUrl": "/job/junior-application-consultant-ethereal-digital-sdn-bhd-kuala-lumpur-54015063", "jdUrl": "/job/junior-application-consultant-ethereal-digital-sdn-bhd-kuala-lumpur-54015063", "seoCompanyUrl": "/search/ethereal-digital-sdn-bhd-462252-jobs-career"}  # noqa: E501
GURUGRAM = {"jobId": 69755282, "siteContext": "rexmonster", "title": "Customer Support Associate", "companyName": "Wellversed", "hideCompanyName": 0, "locations": "Gurugram, Gurugram, India", "createdAt": 1794072682000, "freshness": 1794072682000, "closedAt": 1797877800000, "minimumExperience": {"years": 0}, "maximumExperience": {"years": 2}, "currencyCode": "INR", "hideSalary": 0, "jobTypes": [], "employmentTypes": ["Full time"], "employerTypes": ["Company"], "functions": ["Customer Support"], "industries": ["Other"], "skills": "CRM Tools,google sheets , Excel", "jobSource": "SCRAPPING", "totalApplicants": 0, "applyUrl": "https://www.linkedin.com/jobs/view/4486963201/", "seoJdUrl": "/job/customer-support-associate-wellversed-gurugram-gurgaon-gurugram-india-69755282", "seoCompanyUrl": "/search/wellversed-658572-jobs-career"}  # noqa: E501
NO_COUNTRY = {"jobId": 69753034, "siteContext": "rexmonster", "title": "fresher job urgent hiring internship live traing 3 to 6 month technical job", "companyName": "Guidance Tech & HR Solutions", "hideCompanyName": 0, "locations": "Delhi, Gurugram, Noida", "createdAt": 1794079541000, "freshness": 1794079541000, "closedAt": 1797967541000, "minimumExperience": {"years": 0}, "maximumExperience": {"years": 0}, "minimumSalary": {"currency": "INR", "absoluteValue": 150000, "absoluteMonthlyValue": 12500}, "maximumSalary": {"currency": "INR", "absoluteValue": 350000, "absoluteMonthlyValue": 29167}, "currencyCode": "INR", "hideSalary": 0, "jobSalaryConfidential": False, "jobTypes": [], "employmentTypes": ["Full time"], "employerTypes": ["Consultant"], "functions": ["Others"], "skills": "Data Analysis, Business Analyst", "jobSource": "ORGANIC", "totalApplicants": 5, "seoJdUrl": "/job/fresher-job-urgent-hiring-internship-guidance-tech-hr-solutions-delhi-gurgaon-gurugram-noida-69753034", "seoCompanyUrl": "/search/guidance-tech-hr-solutions-1324011-jobs-career"}  # noqa: E501
DETAIL = {"jobDetailStatus": 200, "activeJob": True, "jobDetailResponse": {"jobId": 69651557, "siteContext": "rexmonster", "title": "OPPORTUNITY for: - Recruitment Business Partner (With Large MNC): bD_Ch-bSC_1026", "companyName": "Neugene International Inc", "hideCompanyName": 0, "freshness": 1794079540000, "closedAt": 1799260200000, "createdAt": "8 days ago", "postedAt": "5 hours ago", "minimumExperience": {"years": 14}, "maximumExperience": {"years": 24}, "minimumSalary": {"currency": "INR", "absoluteValue": 3000000, "absoluteMonthlyValue": 250000}, "maximumSalary": {"currency": "INR", "absoluteValue": 6500000, "absoluteMonthlyValue": 541667}, "hideSalary": 1, "jobSalaryConfidential": True, "jobTypes": ["Permanent Job"], "employmentTypes": ["Full time"], "employerTypes": ["Consultant"], "functions": ["Others"], "jobSource": "ORGANIC", "totalApplicants": 0, "status": 1, "jdUrl": "/job/opportunity-for-recruitment-business-partner-with-large-mnc-bd-ch-bsc-1026-neugene-international-inc-bidar-69651557", "locations": [{"city": "Bidar", "country": "India", "state": "Karnataka", "isoCode": "IN"}, {"city": "Bilaspur, Chhattisgarh", "country": "India", "state": "Chhattisgarh", "isoCode": "IN"}], "description": "<p>Opportunity for: - HR Recruitment Business Partner</p><ul><li>Hire &amp; grow teams</li></ul>", "qualifications": [{"text": "Bachelor Of Technology (B.Tech/B.E)"}, {"text": "Masters in Technology (M.Tech/M.E)"}], "skills": [{"text": "Ai"}, {"text": "IT"}, {"text": "Chemical"}]}}  # noqa: E501
CLOSED = {"jobDetailStatus": 200, "activeJob": False, "jobDetailResponse": {"jobId": 69300000, "title": "Full Stack Developer (.NET + React + Azure)", "status": 2, "isJobActive": False, "siteContext": "rexmonster", "closedAt": 1786557555000}}  # noqa: E501
MISSING = {"jobDetailStatus": 404, "jobDetailStatusText": "Not Found", "jobDetailResponse": {"message": "invalid json response body"}}  # noqa: E501

IN, SG, MY, GULF, VN = (fi.BY_HOST[h] for h in ('foundit.in', 'foundit.sg', 'foundit.my', 'founditgulf.com', 'monster.com.vn'))
NOW = datetime(2026, 11, 8, 1, 0, tzinfo=timezone.utc)
P = fi.Foundit()


def routed(raw: dict) -> tuple:
    platform = fi.Foundit()
    cfg = parse_input(raw, platform)
    boards, _ = platform.boards()
    return platform, cfg, [b.key for b in platform.search_all(boards, cfg)]


def passes(raw: dict, job: dict) -> bool:
    platform = fi.Foundit()
    cfg = parse_input(raw, platform)
    platform.search_all(platform.boards()[0], cfg)
    return Filters(cfg).passes(job, NOW)


class Response:
    def __init__(self, status: int, body=None, text: str = ''):
        self.status_code, self.body, self.text = status, body, text

    def json(self):
        if self.body is None:
            raise ValueError('not JSON')
        return self.body


def answering(platform, answers: list):
    """platform.get answers from the list in turn; the calls made are kept on platform.calls."""
    platform.calls = []

    async def fake(http, url, site, params=None):
        platform.calls.append((url, site.host, list(params or [])))
        return answers.pop(0) if len(answers) > 1 else answers[0]
    platform.get = fake
    return platform


def page(records: list, total: int, status: int = 200) -> Response:
    return Response(200, {'jobSearchStatus': status, 'jobSearchResponse': {
        'data': records + [{'type': 'adsense'}], 'meta': {'paging': {'total': total}}}})


def read_all(platform, session, term=None, days=None) -> list[list[dict]]:
    async def go():
        return [p async for p in platform.pages(None, session, term, days)]
    return asyncio.run(go())


class Links(unittest.TestCase):
    def test_job_links_of_every_site(self):
        self.assertEqual(P.read_link('https://www.foundit.in/job/polarion-alm-developer-career-soft-solutions-pvt-ltd-bengaluru-bangalore-india-69755276'),
                         ('foundit.in', '69755276'))
        self.assertEqual(P.read_link('foundit.sg/job/it-process-sdm-governance-specialist-singapore-69756896/'), ('foundit.sg', '69756896'))
        self.assertEqual(P.read_link('https://m.founditgulf.com/job/sales-executive-dubai-69400000'), ('founditgulf.com', '69400000'))
        self.assertEqual(P.read_link('https://www.foundit.in/seeker/job-details?id=69651557'), ('foundit.in', '69651557'))

    def test_old_monster_addresses_are_the_same_sites(self):
        self.assertEqual(P.read_link('https://www.monsterindia.com/job/java-developer-acme-pune-12345678.html'), ('foundit.in', '12345678'))
        self.assertEqual(P.read_link('https://www.monstergulf.com/'), ('founditgulf.com', None))

    def test_a_search_page_is_read_for_its_words_and_places(self):
        link = 'https://www.foundit.in/srp/results?query=python%20developer&locations=Bangalore'
        self.assertEqual(P.read_link(link), (None, None))
        cfg = parse_input({'searchTerms': [link]}, fi.Foundit())
        self.assertEqual((cfg.search_terms, cfg.locations), (['python developer'], ['Bangalore']))

    def test_a_site_page_names_the_site_and_other_text_is_no_link(self):
        self.assertEqual(P.read_link('https://www.foundit.hk'), ('foundit.hk', None))
        for text in ('https://www.naukri.com/job-listings-python-developer-12345678', 'python developer'):
            self.assertIsNone(P.read_link(text))


class Jobs(unittest.TestCase):
    def test_a_syndicated_job(self):
        job = P.job(IN, SYNDICATED)
        self.assertEqual((job['id'], job['title'], job['company'], job['location']),
                         ('69755276', 'Polarion ALM Developer', 'career soft solutions pvt. ltd.', 'Bengaluru, India'))
        self.assertEqual((job['postedAt'], job['listedAt'], job['expiresAt']),
                         ('2026-11-07T17:32:10Z', '2026-11-07T17:32:10Z', '2026-12-21T18:30:00Z'))
        self.assertEqual((job['experienceMin'], job['experienceMax'], job['employmentType'], job['department']),
                         (2.0, 12.0, 'full-time', 'Software Development'))
        self.assertEqual(job['jobUrl'], 'https://www.foundit.in/job/polarion-alm-developer-career-soft-solutions-pvt-ltd-bengaluru-bangalore-india-69755276')
        self.assertEqual(job['applyUrl'], 'https://www.linkedin.com/jobs/view/4486590080/')
        self.assertEqual(job['companySlug'], 'career-soft-solutions-pvt-ltd-680966')
        self.assertEqual(job['extra'], {'site': 'foundit.in', 'skills': ['Subversion', 'Teamcenter', 'VSCode', 'Polarion ALM', 'C#'],
                                        'industry': None, 'employerType': None, 'jobSource': 'syndicated', 'applicants': 1,
                                        'refreshedAt': '2026-11-07T17:32:10Z'})
        self.assertNotIn('salary', job)

    def test_a_renewed_job_keeps_its_first_date_and_hidden_pay_stays_hidden(self):
        job = P.job(IN, RENEWED)
        self.assertEqual((job['postedAt'], job['listedAt'], job['extra']['refreshedAt']),
                         ('2026-10-31T07:27:09Z', '2026-11-07T19:25:40Z', '2026-11-07T19:25:40Z'))
        self.assertNotIn('salary', job)
        self.assertEqual((job['extra']['jobSource'], job['extra']['employerType'], job['department']), ('foundit', 'consultant', None))
        self.assertEqual(job['applyUrl'], job['jobUrl'])        # applied for on foundit

    def test_shown_pay_is_a_years_pay(self):
        job = P.job(SG, SINGAPORE_PAY)
        self.assertEqual((job['salary'], job['salaryMin'], job['salaryMax'], job['salaryCurrency'], job['salaryPeriod']),
                         ('SGD 60,000 - 72,000 per year', 60000, 72000, 'SGD', 'year'))

    def test_a_job_found_on_another_site_links_to_its_own(self):
        job = P.job(MY, CROSS_SITE)
        self.assertTrue(job['jobUrl'].startswith('https://www.foundit.sg/job/'))
        self.assertEqual(job['extra']['site'], 'foundit.sg')
        self.assertEqual((job['experienceMin'], job['experienceMax']), (None, None))   # 0 to 0 is "not stated"
        self.assertEqual(job['places'][0]['country'], 'Malaysia')                      # Kuala Lumpur, whatever the site

    def test_work_and_job_types(self):
        self.assertEqual(P.job(IN, {**SYNDICATED, 'jobTypes': ['Work From Home']})['workType'], 'remote')
        self.assertEqual(P.job(IN, {**SYNDICATED, 'jobTypes': ['Contract Job'], 'employmentTypes': None})['employmentType'], 'contract')
        self.assertEqual(P.job(IN, {**SYNDICATED, 'employmentTypes': ['Part time']})['employmentType'], 'part-time')
        self.assertIsNone(P.job(IN, SYNDICATED)['workType'])

    def test_a_hidden_company(self):
        job = P.job(IN, {**SYNDICATED, 'hideCompanyName': 1})
        self.assertEqual((job['company'], job['companySlug']), ('Not disclosed', None))


class Places(unittest.TestCase):
    def test_the_lists_spellings_find_a_job(self):
        self.assertTrue(passes({'location': ['Bangalore']}, P.job(IN, SYNDICATED)))
        self.assertTrue(passes({'location': ['Bengaluru']}, P.job(IN, SYNDICATED)))
        self.assertTrue(passes({'location': ['Gurgaon']}, P.job(IN, GURUGRAM)))
        self.assertFalse(passes({'location': ['Mumbai']}, P.job(IN, SYNDICATED)))

    def test_a_country_reaches_jobs_whose_text_names_none(self):
        self.assertTrue(passes({'location': ['India']}, P.job(IN, NO_COUNTRY)))
        self.assertTrue(passes({'location': ['India']}, P.job(IN, {**SYNDICATED, 'locations': 'Remote'})))
        self.assertTrue(passes({'location': ['Malaysia']}, P.job(SG, CROSS_SITE)))
        self.assertFalse(passes({'location': ['Singapore']}, P.job(SG, CROSS_SITE)))

    def test_a_job_in_two_countries_is_found_by_both(self):
        job = P.job(VN, {**SINGAPORE_PAY, 'locations': 'Singapore, Ho Chi Minh', 'siteContext': 'monstervietnam'})
        self.assertTrue(passes({'location': ['Vietnam']}, job))
        self.assertTrue(passes({'location': ['Singapore']}, job))


class Routing(unittest.TestCase):
    def test_no_place_reads_every_site_whole(self):
        platform, _, keys = routed({})
        self.assertEqual(len(keys), 9)
        self.assertTrue(all(v is None for v in platform.plan.values()))

    def test_a_city_goes_to_the_site_that_lists_it(self):
        self.assertEqual(routed({'location': ['Bangalore']})[0].plan, {'foundit.in': ['Bangalore']})
        self.assertEqual(routed({'location': ['Dubai']})[0].plan, {'founditgulf.com': ['Dubai']})
        self.assertEqual(routed({'location': ['Kuala Lumpur']})[0].plan, {'foundit.my': ['Kuala Lumpur']})
        self.assertEqual(routed({'location': ['Bangalore', 'Pune']})[0].plan, {'foundit.in': ['Bangalore', 'Pune']})

    def test_a_country_or_region_reads_its_sites(self):
        self.assertEqual(routed({'location': ['India']})[0].plan, {'foundit.in': None})
        self.assertEqual(routed({'location': ['UAE']})[0].plan, {'founditgulf.com': ['United Arab Emirates']})
        plan = routed({'location': ['Southeast Asia']})[0].plan
        self.assertEqual(set(plan), {'foundit.sg', 'foundit.com.ph', 'foundit.my', 'foundit.id', 'monster.co.th', 'monster.com.vn'})
        self.assertTrue(all(v is None for v in plan.values()))

    def test_gulf_is_the_gulf_region(self):
        platform, cfg, keys = routed({'location': ['Gulf', 'Philippines']})
        self.assertEqual((keys, cfg.locations), (['founditgulf.com', 'foundit.com.ph'], ['GCC', 'Philippines']))
        self.assertTrue(passes({'location': ['Gulf']}, platform.job(GULF, {**SYNDICATED, 'locations': 'United Arab Emirates, Dubai',
                                                                          'siteContext': 'monstergulf'})))
        self.assertFalse(passes({'location': ['Gulf']}, platform.job(GULF, {**SYNDICATED, 'locations': 'Egypt, Cairo', 'siteContext': 'monstergulf'})))

    def test_a_state_goes_to_its_country_and_only_jobs_with_no_known_city_there_are_opened(self):
        platform, cfg, keys = routed({'location': ['Karnataka']})
        self.assertEqual((keys, platform.states), (['foundit.in'], ['Karnataka']))
        at = lambda where: platform.job(IN, {**SYNDICATED, 'locations': where})  # noqa: E731
        for where in ('Bengaluru, India', 'Bengaluru, Karnataka, India', 'Hubli', 'Coimbatore, Bengaluru / Bangalore'):
            self.assertFalse(platform.vague_place(at(where)), where)      # its state is known: no call
            self.assertTrue(Filters(cfg).passes(at(where), NOW), where)
        for where in ('Coimbatore', 'Bokaro Steel City', 'Hinjewadi'):     # another state, or a city no list places
            self.assertTrue(platform.vague_place(at(where)), where)
            self.assertFalse(Filters(cfg).passes(at(where), NOW), where)

    def test_a_city_name_two_states_share_gives_no_state(self):
        # Bijapur: two states on Naukri's list; Bilaspur: five on foundit's ("Bilaspur, Chhattisgarh", ...)
        self.assertEqual([fi.indian_state(n) for n in ('Bengaluru / Bangalore', 'Kerala', 'Bijapur', 'Bilaspur')],
                         ['Karnataka', 'Kerala', None, None])
        platform, cfg, _ = routed({'location': ['Chhattisgarh']})
        self.assertEqual(platform.states, ['Chhattisgarh'])
        bijapur = platform.job(IN, {**SYNDICATED, 'locations': 'Bijapur'})
        self.assertTrue(platform.vague_place(bijapur))
        self.assertFalse(Filters(cfg).passes(bijapur, NOW))
        self.assertEqual([p['region'] for p in P.job(IN, RENEWED)['places'] if p.get('region')], ['Karnataka', 'Chhattisgarh'])
        self.assertEqual([p.get('region') for p in P.job(SG, SINGAPORE_PAY)['places']], [None])   # outside India: none

    def test_worldwide_asks_every_site_for_its_remote_jobs(self):
        self.assertEqual(routed({'location': ['Worldwide']})[0].plan, {site.host: ['Remote'] for site in fi.SITES})

    def test_a_place_no_list_knows_is_checked_by_name_on_the_newest_jobs(self):
        platform, cfg, keys = routed({'location': ['Atlantis']})
        self.assertEqual((len(keys), platform.scan), (9, True))
        self.assertIn('Atlantis is not on foundit', cfg.notes[-1])
        platform, cfg, keys = routed({'location': ['Bangalore', 'Atlantis']})
        self.assertEqual((keys, platform.scan), (['foundit.in'], False))
        self.assertIn('no site was searched for it', cfg.notes[-1])

    def test_what_foundit_cannot_say_is_dropped_with_a_note(self):
        platform, cfg, _ = routed({'workType': ['onsite', 'remote'], 'employmentType': ['intern']})
        self.assertEqual((cfg.work_types, cfg.job_types), (['remote'], []))
        self.assertEqual(len([n for n in cfg.notes if n.startswith('foundit')]), 2)
        platform, cfg, _ = routed({'workType': ['hybrid']})          # hybrid is read from titles
        self.assertEqual(cfg.work_types, ['hybrid'])

    def test_job_types_foundit_has_go_to_the_site(self):
        self.assertEqual(routed({'employmentType': ['contract']})[0].types, [('jobTypes', 'Contract Job')])
        self.assertEqual(routed({'employmentType': ['part-time']})[0].types, [('employmentTypes', 'Part time')])
        self.assertEqual(routed({'employmentType': ['part-time', 'contract']})[0].types, [])


class Params(unittest.TestCase):
    def test_what_is_sent(self):
        platform, cfg, _ = routed({'location': ['Bangalore', 'Pune'], 'experienceYears': '3', 'employmentType': ['contract']})
        sent = platform.params({'places': platform.plan['foundit.in']}, 'python developer', 8)
        self.assertEqual(sent, [('sort', 2), ('limit', 100), ('query', 'python developer'), ('locations', 'Bangalore'),
                                ('locations', 'Pune'), ('jobFreshness', 8), ('experienceRanges', '3~3'), ('jobTypes', 'Contract Job')])

    def test_open_ended_experience_goes_to_the_sites_top(self):
        platform, _, _ = routed({'experienceYears': '5+'})
        self.assertIn(('experienceRanges', '5~50'), platform.params({'places': None}, None, None))


class Paging(unittest.TestCase):
    def test_pages_until_the_total_and_skips_ads(self):
        platform = answering(fi.Foundit(), [page([SYNDICATED] * 100, 150), page([RENEWED] * 50, 150)])
        pages = read_all(platform, {'site': IN, 'places': None}, 'python')
        self.assertEqual([len(p) for p in pages], [100, 50])
        self.assertEqual([dict(c[2])['start'] for c in platform.calls], [0, 100])

    def test_the_window_ends_a_search_and_says_so(self):
        platform = fi.Foundit()
        platform.cfg = parse_input({}, platform)
        answering(platform, [page([SYNDICATED] * 100, 50_000)])
        pages = read_all(platform, {'site': IN, 'places': None}, 'python')
        self.assertEqual(len(pages), fi.WINDOW // fi.PAGE)
        self.assertTrue(platform.incomplete)
        self.assertIn('9,600 newest jobs', platform.cfg.notes[-1])
        answering(platform, [page([SYNDICATED] * 100, 70_000)])
        read_all(platform, {'site': SG, 'places': None}, 'python')
        self.assertEqual(len(platform.cfg.notes), 1)          # one note names every search that reached the window
        self.assertIn('"python" on foundit.in had 50,000, "python" on foundit.sg had 70,000', platform.cfg.notes[0])

    def test_a_scan_reads_only_the_newest_pages(self):
        platform = fi.Foundit()
        platform.scan = True
        answering(platform, [page([SYNDICATED] * 100, 50_000)])
        self.assertEqual(len(read_all(platform, {'site': IN, 'places': None})), fi.SCAN_PAGES)

    def test_a_bad_answer_is_a_board_error(self):
        for answer in (Response(500, None), Response(200, None), page([], 0, status=500)):
            platform = answering(fi.Foundit(), [answer])
            with self.assertRaises(BoardError):
                read_all(platform, {'site': IN, 'places': None})


class Refusals(unittest.TestCase):
    def call(self, answers: list) -> tuple:
        platform = fi.Foundit()
        platform.cfg = parse_input({}, platform)
        sent = []

        async def fake_send(http, method, url, **kwargs):
            sent.append(url)
            return answers.pop(0)

        async def go():
            return await platform.get(None, fi.SEARCH, IN)
        with patch.object(fi, 'send', fake_send), patch.object(fi, 'REFUSAL_WAITS', (0, 0, 0)):
            try:
                return asyncio.run(go()).status_code, len(sent), platform.cfg.notes
            except BoardError as exc:
                return str(exc), len(sent), platform.cfg.notes

    def test_a_refused_call_waits_and_is_tried_again(self):
        status, sent, notes = self.call([Response(403), Response(403), Response(200, {})])
        self.assertEqual((status, sent), (200, 3))
        self.assertEqual(len(notes), 1)

    def test_a_refusal_that_lasts_fails_the_site(self):
        status, sent, _ = self.call([Response(403)] * 4)
        self.assertIn('refused', status)
        self.assertEqual(sent, 4)

    def test_calls_keep_their_pace(self):
        pace, started = fi.Pace(), asyncio.new_event_loop()

        async def burst(n):
            t = asyncio.get_running_loop().time()
            for _ in range(n):
                await pace.turn()
            return asyncio.get_running_loop().time() - t
        took = started.run_until_complete(burst(fi.BURST + 2))
        started.close()
        self.assertGreaterEqual(took, 2 / fi.RATE - 0.05)


class Details(unittest.TestCase):
    def test_details_add_the_description_every_place_with_its_state_and_qualifications(self):
        job = P.enrich(P.job(IN, RENEWED), DETAIL['jobDetailResponse'])
        self.assertEqual(job['description'], 'Opportunity for: - HR Recruitment Business Partner\nHire & grow teams')
        self.assertEqual((job['location'], job['otherLocations']), ('Bidar, Karnataka, India', ['Bilaspur, Chhattisgarh, India']))
        self.assertEqual(job['places'][0]['region'], 'Karnataka')
        self.assertEqual(job['extra']['qualifications'], ['Bachelor Of Technology (B.Tech/B.E)', 'Masters in Technology (M.Tech/M.E)'])

    def test_a_state_filter_passes_once_the_details_name_it(self):
        platform, cfg, _ = routed({'location': ['Karnataka']})
        job = platform.job(IN, {**RENEWED, 'locations': 'Bokaro Steel City'})   # no list gives it a state
        self.assertFalse(Filters(cfg).passes(job, NOW))
        self.assertTrue(Filters(cfg).passes(platform.enrich(job, DETAIL['jobDetailResponse']), NOW))

    def test_checked_links(self):
        session = {'site': IN}
        for answer, status in ((MISSING, 'not found'), (CLOSED, 'closed')):
            platform = answering(fi.Foundit(), [Response(200, answer)])
            self.assertEqual(asyncio.run(platform.lookup(None, session, '1')), (status, fi.NOBODY))
        platform = answering(fi.Foundit(), [Response(200, DETAIL)])
        status, job = asyncio.run(platform.lookup(None, session, '69651557'))
        self.assertEqual((status, job['title'], job['listedAt'], job['dateOnly']), ('open', RENEWED['title'], '2026-11-07T19:25:40Z', True))
        self.assertEqual(job['postedAt'], (datetime.now(timezone.utc) - timedelta(days=8)).date().isoformat())
        self.assertEqual(job['extra']['skills'], ['Ai', 'IT', 'Chemical'])

    def test_relative_dates(self):
        self.assertEqual(fi.ago('5 hours ago', NOW), ('2026-11-07T20:00:00Z', False))
        self.assertEqual(fi.ago('8 days ago', NOW), ('2026-10-31', True))
        self.assertEqual(fi.ago('a day ago', NOW), ('2026-11-07', True))
        self.assertEqual(fi.ago('2 months ago', NOW), (None, False))


class Experience(unittest.TestCase):
    def test_what_people_type(self):
        notes = []
        for typed, read in (('3', (3, 3)), ('2-5', (2, 5)), ('2 to 5 yrs', (2, 5)), ('5+', (5, math.inf)), ('3 years', (3, 3)),
                            ('fresher', (0, 0)), (4, (4, 4)), ('', None), (None, None), ('any', None)):
            self.assertEqual(parse_experience(typed, notes), read, typed)
        self.assertEqual(notes, [])
        self.assertIsNone(parse_experience('lots', notes))
        self.assertEqual(len(notes), 1)

    def test_ranges_overlap(self):
        job = {'experienceMin': 2, 'experienceMax': 5}
        for wanted, fits in (((3, 3), True), ((0, 2), True), ((5, 8), True), ((6, 9), False), ((0, 0), False)):
            self.assertEqual(experience_fits(job, wanted), fits, wanted)
        self.assertTrue(experience_fits({'experienceMin': 5, 'experienceMax': None}, (10, 10)))
        self.assertFalse(experience_fits({}, (3, 3)))

    def test_rows_carry_experience_and_platform_fields_only_when_a_platform_has_them(self):
        row = job_row('foundit', {'name': 'x'}, P.job(IN, SYNDICATED), '2026-11-08T00:00:00+00:00', False)
        self.assertEqual((row['experienceMin'], row['experienceMax'], row['jobSource'], row['site']), (2.0, 12.0, 'syndicated', 'foundit.in'))
        plain = job_row('stub', {'name': 'x'}, {'id': '1', 'title': 'Engineer'}, '2026-11-08T00:00:00+00:00', True)
        self.assertFalse({'experienceMin', 'experienceMax', 'site', 'skills'} & set(plain))


class EngineHooks(unittest.TestCase):
    def test_a_newest_listed_board_pages_on_listed_at(self):
        """A renewed job (first posted long ago, listed again now) ends page 1: paging on its postedAt would stop there
        and lose E, newer than the held B; paging on listedAt reads on."""
        now = datetime.now(timezone.utc)

        def at(hours):
            return (now - timedelta(hours=hours)).isoformat()

        def listed(job_id, posted, shown):
            return {'id': job_id, 'title': f'Engineer {job_id}', 'location': 'Pune', 'postedAt': at(posted), 'listedAt': at(shown)}
        pages = [[listed('A', 1, 1), listed('B', 3, 1.2), listed('D', 480, 1.5)], [listed('E', 2, 2)]]
        run = search(Fake({'site': pages}, newest_first=True), {'maxJobs': 2})
        self.assertEqual([i for i, _ in run.delivery.pushed], ['A', 'E'])

    def test_a_filter_dropped_by_the_platform_is_not_applied(self):
        class Dropping(Fake):
            def search_all(self, boards, cfg):
                cfg.work_types = []
                return boards
        run = search(Dropping({'site': [[{'id': '1', 'title': 'Engineer', 'location': 'Pune', 'postedAt': '2026-11-01'}]]}),
                     {'workType': ['onsite']})
        self.assertEqual(run.matching, 1)


if __name__ == '__main__':
    unittest.main()
