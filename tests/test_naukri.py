"""Naukri: the handshake, links, settings, places, pay, dates, rows and refusals, on answers recorded from naukri.com's
own API on 2026-11-08."""
import asyncio
import base64
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from ats_jobs.http import BoardError
from ats_jobs.inputs import parse_input
from ats_jobs.platforms import naukri
from ats_jobs.platforms.naukri import (EVERYONE, Naukri, Refused, education, employment_type, first_posted, nkparam,
                                       pay, place, split_places)
from ats_jobs.rows import Filters, job_row

# Search records, trimmed to the fields the adapter reads.
SHOWN = {'jobId': '081126000173', 'title': 'AI Data Engineer', 'companyName': 'HCLTech', 'staticUrl': 'hcltech-jobs-careers-460',
         'jdURL': '/job-listings-ai-data-engineer-hcltech-bengaluru-5-to-9-years-081126000173', 'createdDate': 1794084335111,
         'placeholders': [{'type': 'experience', 'label': '5-9 Yrs'}, {'type': 'salary', 'label': '7-17 Lacs PA'},
                          {'type': 'location', 'label': 'Hybrid - Bengaluru'}],
         'salaryDetail': {'minimumSalary': 700000, 'maximumSalary': 1700000, 'currency': 'INR', 'hideSalary': False},
         'experienceText': '5-9 Yrs', 'minimumExperience': '5', 'maximumExperience': '9',
         'tagsAndSkills': 'Data,Artificial Intelligence,Snowflake,Data Engineering',
         'ambitionBoxData': {'AggregateRating': '3.4', 'ReviewsCount': 51522}, 'walkinJob': False,
         'jobDescription': '<p>Hi Dear,</p><br /><p>We have an urgent opening for the role of AI Data engineer.</p>',
         'logoPathV3': 'https://img.naukimg.com/logo_images/groups/v1/194354.gif', 'mode': 'jp'}
INTERN = {'jobId': '021126012377', 'title': 'Interns - Job Sourcing, Job Applications , MIS Reporting',
          'companyName': 'Careerist Pro', 'staticUrl': 'careerist-pro-jobs-careers-123861765',
          'jdURL': '/job-listings-interns-sourcing-applications-mis-reporting-careerist-pro-india-0-to-1-years-021126012377',
          'createdDate': 0, 'placeholders': [{'type': 'duration', 'label': '3 months duration'},
                                              {'type': 'salary', 'label': '5,000/month'}, {'type': 'location', 'label': 'Remote'}],
          'salaryDetail': {'minimumSalary': 0, 'maximumSalary': 0, 'currency': 'INR', 'hideSalary': True}, 'vacancy': 4,
          'walkinJob': False, 'jobDescription': '<p><strong>Role &amp; responsibilities</strong></p>', 'mode': 'jp'}
CRAWLED = {'jobId': '311026502082', 'title': 'Sales Manager - Retail Sales', 'companyName': 'Tata Capital',
           'staticUrl': 'tata-capital-jobs-careers-246425',
           'jdURL': '/job-listings-sales-manager-retail-sales-tata-capital-ltd-vijaynagar-0-to-5-years-311026502082',
           'createdDate': 1793449313000, 'placeholders': [{'type': 'experience', 'label': '0-5 Yrs'},
                                                         {'type': 'salary', 'label': 'Not disclosed'},
                                                         {'type': 'location', 'label': 'Vijaynagar'}],
           'salaryDetail': {'minimumSalary': 0, 'maximumSalary': 0, 'currency': 'INR', 'hideSalary': True},
           'experienceText': '0-5 Yrs', 'minimumExperience': '0', 'maximumExperience': '5',
           'tagsAndSkills': 'Marketing,Business continuity,Cross selling', 'mode': 'crawled'}
# Job answers (jobapi/v4/job/<id>), trimmed.
CRAWLED_DETAILS = {
    'jobId': '311026502082', 'title': 'Sales Manager - Retail Sales', 'createdDate': '2026-10-31 17:51:53',
    'description': '<p> Job Dimensions</p> <p> Responsible for handling team: No</p>', 'employmentType': 'Full Time, Permanent',
    'jobType': 'fulltime', 'wfhType': '0', 'functionalArea': 'Sales & Business Development', 'referenceCode': '3.00005E+14',
    'companyDetail': {'name': 'Tata Capital', 'websiteUrl': None},
    'applyRedirectUrl': 'https://eofh.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/56447',
    'keySkills': {'other': [{'label': 'business continuity'}, {'label': 'cross selling'}], 'preferred': []},
    'industry': 'Financial Services', 'jobRole': 'Retail Sales', 'roleCategory': 'Retail & B2C Sales',
    'education': {'ug': ['Any Graduate'], 'pg': ['Any Postgraduate'], 'ppg': []}, 'applyCount': 99, 'viewCount': 231,
    'consultant': False, 'vacancy': 1, 'locations': [{'label': 'Vijaynagar'}],
    'staticUrl': 'https://www.naukri.com/job-listings-sales-manager-retail-sales-tata-capital-ltd-vijaynagar-0-to-5-years-311026502082',
    'staticCompanyName': 'tata-capital-jobs-careers-246425',
    'salaryDetail': {'minimumSalary': 0, 'maximumSalary': 0, 'currency': 'INR', 'hideSalary': True, 'label': 'Not Disclosed'},
    'experienceText': '0-5 Yrs', 'minimumExperience': 0, 'maximumExperience': 5, 'walkIn': False,
    'shortDescription': 'Position Type: Full time| Job Qualifications. Graduate : Yes', 'mode': 'crawled'}
CONSULTANT_DETAILS = {
    'jobId': '081126001281', 'title': 'Social Media Intern - Remote, Part-Time, Evenings', 'createdDate': '2026-11-08 05:24:54',
    'description': '<p><strong>Remote Research Intern </strong>(3 months)</p>', 'employmentType': 'Part Time, Permanent',
    'jobType': 'internship', 'wfhType': '2', 'functionalArea': 'Marketing & Communication', 'referenceCode': 'RE-INT',
    'companyDetail': {'name': 'Independent Recruitment Consultant (freelancer)', 'websiteUrl': ''},
    'keySkills': {'other': [{'label': 'Social Media Marketing'}], 'preferred': [{'label': 'Instagram'}]},
    'education': {'ug': ['Graduation Not Required'], 'pg': [], 'ppg': []}, 'applyCount': 0, 'viewCount': 18,
    'consultant': True, 'vacancy': 1, 'locations': [{'label': 'Delhi / NCR'}],
    'staticUrl': 'https://www.naukri.com/job-listings-social-media-intern-remote-part-time-evenings-081126001281',
    'staticCompanyName': 'independent-recruitment-consultant-freelancer-jobs-careers-5030',
    'salaryDetail': {'minimumSalary': None, 'maximumSalary': None, 'currency': 'INR', 'hideSalary': False, 'label': 'Unpaid'},
    'experienceText': '0-1 Yrs', 'minimumExperience': 0, 'maximumExperience': 1, 'walkIn': False, 'mode': 'jp'}
JOB_LINK = 'https://www.naukri.com/job-listings-ai-data-engineer-hcltech-bengaluru-5-to-9-years-081126000173'


def search(raw):
    platform = Naukri()
    cfg = parse_input(raw, platform)
    platform.search_all([], cfg)
    return platform, cfg


def row_of(platform, job, details=False):
    """A row as the engine builds it (engine.Run.row): the shared fields, then the platform's extra ones."""
    row = job_row('naukri', {'name': 'Naukri', 'slug': 'naukri'}, job, 'now', details)
    row.update({k: job.get(k) for k in platform.extra})
    return {k: v for k, v in row.items() if k not in platform.never}


class Response:
    def __init__(self, status_code, data=None, text=''):
        self.status_code, self.data = status_code, data
        self.text = text or ('' if data is None else str(data))

    def json(self):
        if self.data is None:
            raise ValueError('not JSON')
        return self.data


class Handshake(unittest.TestCase):
    P = 82839956622684801505820403919640517020327188519425239475008671459381802161631
    Q = 112156132879326820167923186405131085983204045322450282989454183590148066125183

    def test_the_pages_key_is_a_512_bit_rsa_key(self):
        self.assertEqual((naukri.MODULUS.bit_length(), naukri.EXPONENT), (512, 65537))

    def test_nkparam_is_the_timestamped_page_name_encrypted_the_way_jsencrypt_does(self):
        n, e = self.P * self.Q, 65537
        d = pow(e, -1, (self.P - 1) * (self.Q - 1))
        token = nkparam('srp', now_ms=1794098689123, modulus=n, exponent=e)
        block = pow(int.from_bytes(base64.b64decode(token), 'big'), d, n).to_bytes(64, 'big')
        self.assertEqual(block[:2], b'\x00\x02')                      # PKCS#1 v1.5, block type 2
        filler, _, message = block[2:].partition(b'\x00')
        self.assertGreaterEqual(len(filler), 8)
        self.assertNotIn(0, filler)
        self.assertEqual(message, b'v0|1794098689123|121_srp')

    def test_two_tokens_differ_by_their_random_filler(self):
        self.assertNotEqual(nkparam('srp', now_ms=1), nkparam('srp', now_ms=1))

    def test_every_call_carries_the_pages_headers_and_a_browser_agent(self):
        h = naukri.headers('051126035541', naukri.JOB_APP)
        self.assertEqual((h['appid'], h['systemid']), ('121', 'Naukri'))
        self.assertIn('Chrome/', h['User-Agent'])
        self.assertNotIn('compatible', h['User-Agent'])           # Akamai answers 403 to a bot's agent


class Links(unittest.TestCase):
    P = Naukri()

    def test_a_job_link_in_every_form(self):
        for text in (JOB_LINK, JOB_LINK + '?src=jobsearchDesk&sid=1794&xp=1&px=1', JOB_LINK.replace('www.', 'm.'),
                     JOB_LINK.removeprefix('https://'), '081126000173', 'https://www.naukri.com/?jobId=081126000173'):
            self.assertEqual(self.P.read_link(text), ('naukri', '081126000173'), text)

    def test_a_company_page_names_the_company(self):
        self.assertEqual(self.P.read_link('https://www.naukri.com/tata-capital-overview-172058'), ('172058', None))
        self.assertEqual(self.P.read_link('https://www.naukri.com/infosys-jobs-careers-11244'), ('infosys', None))

    def test_a_search_page_is_a_search(self):
        for text in ('https://www.naukri.com/python-developer-jobs-in-bangalore?k=python%20developer&l=bangalore',
                     'https://www.naukri.com/', 'naukri.com/jobs-in-india'):
            self.assertEqual(self.P.read_link(text), (None, None), text)

    def test_other_text_is_no_link(self):
        for text in ('python developer', 'https://www.foundit.in/job/x-123', 'https://naukrigulf.com/jobs', '1234'):
            self.assertIsNone(self.P.read_link(text), text)

    def test_a_search_link_reads_its_words_and_place(self):
        self.assertEqual(self.P.search_link('https://www.naukri.com/python-developer-jobs-in-bangalore?k=python%20developer'
                                            '&l=bangalore&experience=3'), (['python developer'], ['bangalore']))
        self.assertEqual(self.P.search_link('https://www.naukri.com/data-analyst-jobs-in-pune'), (['data analyst'], ['pune']))
        self.assertEqual(self.P.search_link('https://www.naukri.com/sales-jobs'), (['sales'], []))
        self.assertEqual(self.P.search_link('https://www.naukri.com/jobs-in-india'), ([], []))     # India: every job
        self.assertEqual(self.P.search_link('https://www.naukri.com/mnjuser/profile'), ([], []))

    def test_a_pasted_search_link_searches_its_words_place_and_filters(self):
        platform, cfg = search({'searchTerms': ['https://www.naukri.com/python-developer-jobs-in-bangalore?k=python%20developer'
                                         '&l=bangalore&experience=3&jobAge=7&wfhType=2&wfhType=3&ctcFilter=10to15'
                                         '&ctcFilter=15to25&qbusinessSize=62&jobPostType=1&qctopGroupId=13832']})
        self.assertEqual((cfg.search_terms, cfg.locations), (['python developer'], ['bangalore']))
        self.assertEqual((cfg.posted_within_days, sorted(cfg.work_types), cfg.companies), (7.0, ['hybrid', 'remote'], ['13832']))
        self.assertEqual(platform.settings(), {'experience': 3, 'minSalaryLakhs': 10, 'companyType': ['startup'],
                                               'postedBy': 'company'})
        self.assertIn('as a search for python developer in bangalore', cfg.notes[0])

    def test_a_link_that_shows_no_search_is_skipped_and_its_filters_are_not_read(self):
        platform, cfg = search({'searchTerms': ['https://www.naukri.com/jobs-in-india?experience=3', 'sales']})
        self.assertEqual((cfg.search_terms, platform.settings()), (['sales'], {}))
        self.assertIn('is a Naukri link with no company in it', cfg.skipped[0][1])

    def test_the_forms_own_values_win_over_the_links(self):
        platform, cfg = search({'searchTerms': ['https://www.naukri.com/sales-jobs?experience=3&jobAge=7'], 'experience': 5,
                                'postedWithin': '24 hours'})
        self.assertEqual((platform.settings()['experience'], cfg.posted_within_days), (5, 1.0))

    def test_a_typed_company_and_its_page_name(self):
        self.assertEqual(self.P.guess_board('Tata  Consultancy Services'), 'Tata Consultancy Services')
        self.assertEqual(self.P.guess_board('infosys-jobs-careers-11244'), 'infosys')

    def test_a_job_link_checks_that_job(self):
        _, cfg = search({'jobUrls': [JOB_LINK, '311026502082', 'https://www.naukri.com/jobs-in-india']})
        self.assertEqual([(k, j) for k, j, _ in cfg.job_urls], [('naukri', '081126000173'), ('naukri', '311026502082')])
        self.assertIn('without a job in it', cfg.skipped[0][1])


class Settings(unittest.TestCase):
    def test_naukris_own_fields(self):
        platform, _ = search({'experience': '4', 'minSalaryLakhs': '12 LPA', 'companyType': ['Foreign MNC', 'startup', 'Govt/PSU'],
                              'postedBy': 'consultant'})
        self.assertEqual(platform.settings(), {'experience': 4, 'minSalaryLakhs': 12.0,
                                               'companyType': ['foreign-mnc', 'startup', 'govt-psu'], 'postedBy': 'consultant'})

    def test_unreadable_values_fall_back_with_a_note(self):
        platform, cfg = search({'experience': 'lots', 'minSalaryLakhs': 'a lot', 'companyType': ['bank'], 'postedBy': 'anyone'})
        self.assertEqual(platform.settings(), {})
        self.assertEqual(len(cfg.notes), 4)
        self.assertIn('companyType has no option bank', ' '.join(cfg.notes))

    def test_every_choice_is_the_same_as_none(self):
        platform, _ = search({'postedBy': ['company', 'consultant'], 'companyType': list(naukri.COMPANY_TYPES)})
        self.assertEqual(platform.settings(), {})

    def test_the_search_naukri_runs(self):
        platform, _ = search({'searchTerms': ['data analyst'], 'location': ['Mumbai', 'Pune'], 'workType': ['remote', 'hybrid'],
                              'postedWithin': '24 hours', 'experience': 2, 'minSalaryLakhs': 10, 'companyType': ['startup'],
                              'postedBy': 'company'})
        query = platform.query({'groups': ['13832']}, 'data analyst')
        self.assertEqual({k: query[k] for k in ('keyword', 'location', 'jobAge', 'wfhType', 'experience', 'qbusinessSize',
                                                 'jobPostType', 'qctopGroupId', 'sort', 'noOfResults')},
                         {'keyword': 'data analyst', 'location': 'Mumbai,Pune', 'jobAge': 1, 'wfhType': ['2', '3'],
                          'experience': 2, 'qbusinessSize': ['62'], 'jobPostType': '1', 'qctopGroupId': ['13832'],
                          'sort': 'f', 'noOfResults': 100})
        self.assertEqual(query['ctcFilter'], ['10to15', '15to25', '25to50', '50to75', '75to100', '100to500', '501'])

    def test_internships_alone_ask_naukris_internship_search(self):
        platform, _ = search({'searchTerms': ['marketing'], 'employmentType': ['internship']})
        self.assertEqual(platform.query({}, 'marketing')['keyword'], 'marketing internship')
        self.assertEqual(platform.query({}, None)['keyword'], 'internship')
        platform, _ = search({'searchTerms': ['marketing'], 'employmentType': ['intern', 'part-time']})
        self.assertEqual(platform.query({}, 'marketing')['keyword'], 'marketing')

    def test_the_period_asks_naukri_for_the_smallest_bucket_that_holds_it(self):
        for typed, age in (('24 hours', 1), ('2 days', 3), ('7 days', 7), ('2 weeks', 15), ('30 days', 30), ('45 days', None)):
            platform, _ = search({'postedWithin': typed})
            self.assertEqual(platform.query({}, None).get('jobAge'), age, typed)

    def test_a_place_naukri_cannot_search_leaves_every_place_to_the_check(self):
        for where in (['India'], ['Bangalore', 'India'], ['APAC'], ['Worldwide']):
            platform, _ = search({'location': where})
            self.assertNotIn('location', platform.query({}, None), where)
        platform, _ = search({'location': ['Karnataka', 'Dubai']})
        self.assertEqual(platform.query({}, None)['location'], 'Karnataka,Dubai')


class Places(unittest.TestCase):
    def test_the_work_type_and_the_places_of_one_location_text(self):
        self.assertEqual(split_places('Hybrid - Hyderabad, Chennai, Bengaluru'), ('hybrid', ['Hyderabad', 'Chennai', 'Bengaluru']))
        self.assertEqual(split_places('Remote'), ('remote', ['Remote']))
        self.assertEqual(split_places('Noida, Greater Noida(Techzone 4)'), ('onsite', ['Noida', 'Greater Noida(Techzone 4)']))
        self.assertEqual(split_places('Bengaluru(Whitefield, Marathahalli)'), ('onsite', ['Bengaluru(Whitefield, Marathahalli)']))
        self.assertEqual(split_places(''), (None, []))

    def test_a_city_gets_its_state_from_naukris_own_list(self):
        self.assertEqual(place('Bengaluru(Hoskote)'), {'name': 'Bengaluru(Hoskote)', 'city': 'Bengaluru', 'region': 'Karnataka',
                                                       'country': 'India'})
        self.assertEqual(place('Gurugram')['region'], 'Haryana')                 # Gurgaon on Naukri's list
        self.assertEqual(place('Mumbai (All Areas)')['region'], 'Maharashtra')
        self.assertEqual(place('Delhi / NCR')['region'], 'Delhi')
        self.assertEqual(place('Thiruvananthapuram')['region'], 'Kerala')        # Trivandrum on Naukri's list

    def test_a_country_a_city_abroad_and_remote(self):
        self.assertEqual(place('United States (USA)')['country'], 'United States')
        self.assertEqual(place('United Arab Emirates')['country'], 'United Arab Emirates')
        self.assertNotIn('country', place('Dubai'))                              # not on Naukri's list of Indian cities
        self.assertEqual(place('Remote')['country'], 'India')

    def test_typed_places_find_naukris_spelling(self):
        platform, cfg = search({'location': ['Bangalore']})
        job = platform.job(SHOWN)
        now = datetime(2026, 11, 8, tzinfo=timezone.utc)
        self.assertTrue(Filters(cfg).passes(job, now))
        for where, found in (('Karnataka', True), ('India', True), ('KA', True), ('Delhi NCR', False), ('Dubai', False)):
            _, cfg = search({'location': [where]})
            self.assertEqual(Filters(cfg).passes(job, now), found, where)


class Rows(unittest.TestCase):
    P = Naukri()

    def test_a_search_record_becomes_a_job(self):
        job = self.P.job(SHOWN)
        self.assertEqual((job['id'], job['title'], job['company'], job['location'], job['workType'], job['salary']),
                         ('081126000173', 'AI Data Engineer', 'HCLTech', 'Bengaluru', 'hybrid', '7-17 Lacs PA'))
        self.assertEqual((job['postedAt'], job['dateOnly'], job['jobUrl']), ('2026-11-07T20:45:35Z', False, JOB_LINK))
        more = job
        self.assertEqual((more['experience'], more['experienceMin'], more['experienceMax']), ('5-9 Yrs', 5, 9))
        self.assertEqual((more['salaryMin'], more['salaryMax'], more['salaryPeriod']), (700000, 1700000, 'year'))
        self.assertEqual((more['companyRating'], more['companyReviews'], more['firstPostedAt']), (3.4, 51522, '2026-11-08'))
        self.assertEqual(more['skills'], ['Data', 'Artificial Intelligence', 'Snowflake', 'Data Engineering'])
        self.assertTrue(more['summary'].startswith('Hi Dear,\nWe have an urgent opening'))

    def test_an_internship_shows_its_stipend_and_its_day_comes_from_the_id(self):
        job = self.P.job(INTERN)
        self.assertEqual((job['employmentType'], job['workType'], job['salary']), ('intern', 'remote', '5,000/month'))
        self.assertEqual((job['postedAt'], job['dateOnly']), ('2026-11-02', True))
        self.assertEqual((job['salaryMin'], job['salaryMax'], job['salaryPeriod']), (5000, 5000, 'month'))

    def test_an_internships_page_gives_its_real_posting_date(self):
        job = self.P.enrich(self.P.job(INTERN), dict(CONSULTANT_DETAILS, createdDate='2026-11-07 10:00:00'))
        self.assertEqual((job['postedAt'], job['dateOnly'], job['firstPostedAt']), ('2026-11-07T04:30:00Z', False, '2026-11-02'))
        job = self.P.enrich(self.P.job(SHOWN), CRAWLED_DETAILS)                     # a job with its time keeps the list's
        self.assertEqual(job['postedAt'], '2026-11-07T20:45:35Z')

    def test_hidden_pay_is_no_pay(self):
        self.assertEqual(pay({'hideSalary': True, 'minimumSalary': 0}, 'Not disclosed'), {'salary': None})
        self.assertEqual(pay({'hideSalary': False}, 'Unpaid'), {'salary': 'Unpaid'})

    def test_the_id_holds_the_day_naukri_first_listed_the_job(self):
        self.assertEqual(first_posted('040926016871'), '2026-09-04')
        self.assertIsNone(first_posted('991326016871'))
        self.assertIsNone(first_posted('x'))

    def test_the_row_carries_naukris_own_fields(self):
        row = row_of(self.P, self.P.job(SHOWN))
        self.assertEqual((row['companySlug'], row['experience'], row['salaryMax'], row['walkIn']),
                         ('hcltech-jobs-careers-460', '5-9 Yrs', 1700000, False))
        self.assertIsNone(row['industry'])                                         # a details field: empty without them
        self.assertNotIn('team', row)
        details = row_of(self.P, self.P.enrich(self.P.job(CRAWLED), CRAWLED_DETAILS), details=True)
        self.assertEqual((details['industry'], details['department'], details['region']),
                         ('Financial Services', 'Sales & Business Development', 'Andhra Pradesh'))   # Naukri's city list

    def test_details_add_the_job_type_department_and_the_employers_own_page(self):
        job = self.P.enrich(self.P.job(CRAWLED), CRAWLED_DETAILS)
        self.assertEqual((job['employmentType'], job['department'], job['reference'], job['workType']),
                         ('full-time', 'Sales & Business Development', '3.00005E+14', 'onsite'))
        self.assertEqual(job['applyUrl'], CRAWLED_DETAILS['applyRedirectUrl'])
        self.assertEqual(job['jobUrl'], 'https://www.naukri.com' + CRAWLED['jdURL'])
        more = job
        self.assertEqual((more['industry'], more['role'], more['roleCategory'], more['applicants'], more['postedBy']),
                         ('Financial Services', 'Retail Sales', 'Retail & B2C Sales', 99, 'company'))
        self.assertEqual(more['education'], 'UG: Any Graduate; PG: Any Postgraduate')
        self.assertEqual(more['skills'], ['business continuity', 'cross selling'])
        self.assertEqual(job['description'], 'Job Dimensions\nResponsible for handling team: No')

    def test_a_checked_link_is_read_from_its_own_page(self):
        job = self.P.from_details('081126001281', CONSULTANT_DETAILS)
        self.assertEqual((job['title'], job['company'], job['location'], job['employmentType'], job['workType']),
                         ('Social Media Intern - Remote, Part-Time, Evenings', 'Independent Recruitment Consultant (freelancer)',
                          'Delhi / NCR', 'intern', 'remote'))
        self.assertEqual((job['postedAt'], job['postedBy'], job['salary']), ('2026-11-07T23:54:54Z', 'consultant', 'Unpaid'))
        self.assertIsNone(job['companyWebsite'])

    def test_job_types(self):
        for text, kind, found in (('Full Time, Permanent', 'fulltime', 'full-time'), ('Part Time, Permanent', 'parttime', 'part-time'),
                                  ('Full Time, Temporary/Contractual', 'fulltime', 'contract'),
                                  ('Full Time, Freelance/Homebased', 'fulltime', 'contract'), ('Part Time, Permanent', 'internship', 'intern'),
                                  ('', '', None)):
            self.assertEqual(employment_type({'employmentType': text, 'jobType': kind}), found, text)

    def test_education_reads_as_one_line(self):
        self.assertEqual(education({'ug': ['B.Tech/B.E. in Any Specialization'], 'pg': [], 'ppg': ['Ph.D/Doctorate']}),
                         'UG: B.Tech/B.E. in Any Specialization; Doctorate: Ph.D/Doctorate')
        self.assertIsNone(education({'ug': [], 'pg': []}))

    def test_one_title_at_one_place_for_other_experience_is_another_job(self):
        two = dict(SHOWN, jobId='081126000174', experienceText='9-12 Yrs')
        self.assertNotEqual(self.P.copy_key(self.P.job(SHOWN)), self.P.copy_key(self.P.job(two)))
        self.assertEqual(self.P.copy_key(self.P.job(SHOWN)), self.P.copy_key(self.P.job(dict(SHOWN, jobId='1'))))

    def test_experience_and_pay_are_checked_on_what_the_job_shows(self):
        platform, _ = search({'experience': 3, 'minSalaryLakhs': 10})
        self.assertFalse(platform.fits(platform.job(SHOWN)))           # 5-9 years
        platform, _ = search({'experience': 6, 'minSalaryLakhs': 18})
        self.assertFalse(platform.fits(platform.job(SHOWN)))           # pays up to 17 lakhs
        platform, _ = search({'experience': 6, 'minSalaryLakhs': 12})
        self.assertTrue(platform.fits(platform.job(SHOWN)))
        self.assertTrue(platform.fits(platform.job(CRAWLED)) is False)  # 0-5 years
        platform, _ = search({'minSalaryLakhs': 50})
        self.assertTrue(platform.fits(platform.job(CRAWLED)))          # hidden pay: Naukri's own filter decided


class Calls(unittest.TestCase):
    """Pages, refusals and links on answers in the shape naukri.com gives, with no network."""

    def run_pages(self, answers, raw=None):
        platform, cfg = search(raw or {'searchTerms': ['sales']})
        term = cfg.search_terms[0] if cfg.search_terms else None
        calls = []

        async def fake_send(http, method, url, params=None, headers=None, **kwargs):
            calls.append((url, dict(params or {})))
            return answers.pop(0)

        async def read():
            got = []
            async for jobs in platform.pages(None, {'groups': []}, term, None):
                got.append(jobs)
            return got
        with patch.object(naukri, 'send', fake_send), patch.object(naukri, 'PACE', 0), patch.object(naukri, 'AGAIN_S', (0, 0)):
            return platform, calls, asyncio.run(read())

    def page(self, records, total):
        return Response(200, {'noOfJobs': total, 'jobDetails': records})

    def test_pages_stop_at_the_last_match(self):
        platform, calls, got = self.run_pages([self.page([SHOWN] * 100, 150), self.page([CRAWLED] * 50, 150)])
        self.assertEqual([len(p) for p in got], [100, 50])
        self.assertEqual([c[1]['pageNo'] for c in calls], [1, 2])
        self.assertEqual((calls[0][1]['sort'], calls[0][1]['keyword']), ('f', 'sales'))

    def test_a_search_with_no_word_place_or_company_reads_naukris_freshness_windows_in_turn(self):
        self.assertEqual(Naukri.windows({}), [1, 3, 7, 15, 30])
        self.assertEqual(Naukri.windows({'jobAge': 7, 'wfhType': ['0']}), [1, 3, 7])
        for narrow in ({'keyword': 'sales'}, {'location': 'Pune'}, {'qctopGroupId': ['13832']}, {'keyword': 'x', 'jobAge': 3}):
            self.assertEqual(Naukri.windows(narrow), [narrow.get('jobAge')], narrow)

    def test_each_window_starts_where_the_one_before_ended_and_no_job_comes_twice(self):
        def records(start, count):
            return [dict(SHOWN, jobId=f'0811260{n:05d}') for n in range(start, start + count)]
        answers = [self.page(records(0, 100), 150), self.page(records(100, 50), 150),      # last day: 150 jobs
                   self.page(records(100, 100), 260), self.page(records(200, 60), 260)]     # 3 days, from page 2
        platform, calls, got = self.run_pages(answers, raw={'postedWithin': '3 days'})
        self.assertEqual([(c[1].get('jobAge'), c[1]['pageNo']) for c in calls], [(1, 1), (1, 2), (3, 2), (3, 3)])
        ids = [j['id'] for page in got for j in page]
        self.assertEqual((len(ids), len(set(ids))), (260, 260))
        self.assertNotIn('keyword', calls[0][1])

    def test_a_refusal_after_some_pages_keeps_them_and_marks_the_run_partial(self):
        refused = Response(406, text='{"message":"recaptcha required","statusCode":406}')
        platform, calls, got = self.run_pages([self.page([SHOWN] * 100, 500), refused, refused, refused])
        self.assertEqual(len(got), 1)
        self.assertTrue(platform.incomplete)
        self.assertIn('Naukri refused this address (it asked for a captcha) for search pages', platform.cfg.notes[-1])

    def test_a_refusal_before_any_page_fails_with_the_reason(self):
        denied = Response(403, text='<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD>')
        with self.assertRaises(Refused):
            self.run_pages([denied, denied, denied])

    def test_a_single_refusal_that_passes_on_a_retry_is_no_refusal(self):
        refused = Response(406, text='{"message":"recaptcha required","statusCode":406}')
        platform, calls, got = self.run_pages([refused, self.page([SHOWN] * 10, 10)])
        self.assertEqual((len(calls), len(got[0]), platform.incomplete, platform._refused), (2, 10, False, {}))

    class Proxy:
        def __init__(self):
            self.sessions = []

        async def new_url(self, session_id=None):
            self.sessions.append(session_id)
            return f'http://proxy.example:8000/{session_id}'

    def proxied(self, answers):
        platform, _ = search({'proxyConfiguration': {'useApifyProxy': False, 'proxyUrls': ['http://proxy.example:8000']}})
        platform._proxy = self.Proxy()
        used = []

        async def fake_send(http, method, url, params=None, headers=None, **kwargs):
            used.append(http)
            return answers.pop(0)
        with patch.object(naukri, 'send', fake_send), patch.object(naukri, 'PACE', 0):
            try:
                return platform, used, asyncio.run(platform.call(None, naukri.SEARCH, {}, 'srp', '109', 'search'))
            except Refused as exc:
                return platform, used, exc

    def test_through_a_proxy_a_refusal_moves_the_run_to_a_fresh_address(self):
        refused = Response(406, text='{"message":"recaptcha required"}')
        platform, used, response = self.proxied([refused, Response(200, {'noOfJobs': 0, 'jobDetails': []})])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(platform._proxy.sessions, ['naukri_0', 'naukri_1'])
        self.assertIsNot(used[0], used[1])
        self.assertIsNotNone(used[0])                                   # the proxy's client, not the run's own

    def test_a_proxy_refused_at_every_address_stops_the_run_asking(self):
        refused = Response(406, text='{"message":"recaptcha required"}')
        platform, used, outcome = self.proxied([refused] * (naukri.ROTATIONS + 1))
        self.assertIsInstance(outcome, Refused)
        self.assertEqual(len(platform._proxy.sessions), naukri.ROTATIONS + 1)
        self.assertIn("the proxy's addresses were refused too", platform.cfg.notes[-1])
        self.assertNotIn('proxyConfiguration', platform.settings())   # proxy addresses can hold a password

    def test_a_proxy_the_platform_cannot_use_leaves_the_run_on_its_own_address(self):
        platform, _ = search({'proxyConfiguration': {'useApifyProxy': False, 'proxyUrls': ['socks5://127.0.0.1:1050']}})

        async def refuse(**kwargs):
            raise ValueError("1 validation error\n  URL scheme should be 'http' or 'https'")
        with patch('apify.Actor.create_proxy_configuration', refuse):
            self.assertEqual(asyncio.run(platform.route('own client')), 'own client')
        self.assertIn('Connection (proxyConfiguration) could not be used (1 validation error)', platform.cfg.notes[-1])

    def test_without_a_proxy_the_note_says_how_to_add_one(self):
        platform, _ = search({})
        refused = Response(406, text='{"message":"recaptcha required"}')

        async def fake_send(http, method, url, params=None, headers=None, **kwargs):
            return refused
        with patch.object(naukri, 'send', fake_send), patch.object(naukri, 'PACE', 0), patch.object(naukri, 'AGAIN_S', (0, 0)):
            with self.assertRaises(Refused):
                asyncio.run(platform.call(None, naukri.SEARCH, {}, 'srp', '109', 'search'))
        self.assertIn('turn on Apify Proxy in Connection (proxyConfiguration)', platform.cfg.notes[-1])

    def test_a_refused_endpoint_is_not_asked_again(self):
        platform = Naukri()
        platform._refused['details'] = 'it asked for a captcha'
        with self.assertRaises(Refused):
            asyncio.run(platform._details(None, '081126000173'))

    def lookup(self, response):
        async def fake_send(http, method, url, params=None, headers=None, **kwargs):
            self.assertFalse(kwargs.get('follow_redirects', True))      # a closed job's 303 is read, not followed
            return response
        with patch.object(naukri, 'send', fake_send), patch.object(naukri, 'PACE', 0):
            return asyncio.run(Naukri().lookup(None, {}, '311026502082'))

    def test_a_link_is_open_closed_or_not_found(self):
        status, job = self.lookup(Response(200, {'jobDetails': CRAWLED_DETAILS}))
        self.assertEqual((status, job['title'], job['applyUrl']), ('open', 'Sales Manager - Retail Sales',
                                                                   CRAWLED_DETAILS['applyRedirectUrl']))
        self.assertEqual(self.lookup(Response(303, {'metaSearch': {'isExpiredJob': '1'}})), ('closed', None))
        self.assertEqual(self.lookup(Response(404, {'message': 'HTTP 404 Not Found'})), ('not found', None))
        with self.assertRaises(BoardError):
            self.lookup(Response(200, {'loggedIn': False}))

    def test_a_company_is_found_by_its_name_in_naukris_list(self):
        answers = [Response(200, {'noOfJobs': 3, 'jobDetails': [],
                                  'clusters': {'topGroupId': [{'id': '13832', 'label': 'Infosys', 'count': 4153},
                                                              {'id': '191386', 'label': 'Infosys BPM', 'count': 258}]}})]

        async def fake_send(http, method, url, params=None, headers=None, **kwargs):
            return answers.pop(0)
        with patch.object(naukri, 'send', fake_send), patch.object(naukri, 'PACE', 0):
            session = asyncio.run(Naukri().open(None, naukri.Board('infosys', 'infosys')))
        self.assertEqual(session, {'groups': ['13832'], 'name': 'Infosys'})
        self.assertEqual(asyncio.run(Naukri().open(None, naukri.Board('172058', '172058'))), {'groups': ['172058'], 'name': None})
        self.assertEqual(asyncio.run(Naukri().open(None, EVERYONE)), {'groups': []})


if __name__ == '__main__':
    unittest.main()
