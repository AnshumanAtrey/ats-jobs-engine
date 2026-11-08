"""Instahyre: links, inputs, the job shape, the search's own words, dates by bisection, the walk and the choice between
walk and search, on answers shaped like Instahyre's own (2026-11-08; the recruiter's name replaced)."""
import asyncio
import copy
import gzip
import json
import os
import unittest
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from ats_jobs import engine
from ats_jobs.inputs import parse_input
from ats_jobs.platforms.base import Board
from ats_jobs.platforms.instahyre import SITE, Instahyre, Refused, read_page, stipend
from ats_jobs.rows import Filters, job_row

RECORD = {
    'resource_uri': '/api/v1/job_search/447415',
    'employer': {'id': 54291, 'company_name': 'The Walt Disney Company', 'company_tagline': 'Be part of the story',
                 'company_founded': 1923, 'employee_count': 1000,
                 'profile_image_src': 'https://media.instahyre.com/images/profile/base/employer/54291/7c148374b4/x.webp'},
    'public_url': 'https://www.instahyre.com/job-447415-senior-data-engineer-at-the-walt-disney-company-bangalore/',
    'candidate_title': 'Senior Data Engineer', 'id': 447415, 'title': 'Senior Data Engineer', 'locations': 'Bangalore',
    'keywords': ['Airflow', 'Data Engineer', 'Databricks', 'PySpark', 'SQL', 'Snowflake', 'Spark'],
    'accept_outstation': False, 'is_sourced': False}
ANSWER = {
    'opportunity_url': '/job-447415-senior-data-engineer-at-the-walt-disney-company-bangalore/',
    'employer_profile_url': 'https://media.instahyre.com/images/profile/base/employer/54291/7c148374b4/x.webp',
    'hiring_company_name': 'The Walt Disney Company', 'candidate_title': 'Senior Data Engineer', 'is_internship': False,
    'id': 447415, 'title': 'Senior Data Engineer', 'is_active': True, 'locations': ['Bangalore'],
    'keywords': ['Airflow', 'Data Engineer', 'Databricks', 'PySpark', 'SQL', 'Snowflake', 'Spark'],
    'accept_outstation': False, 'job_category': 'Software Engineering',
    'description': '<html><body><p><strong>Requirements: </strong></p><ul><li>3+ years of data engineering.</li></ul></body></html>',
    'workex_min': 2, 'workex_max': 4, 'internship': None, 'recruiter_name': 'A Recruiter',
    'recruiter_designation': 'Manager-TA', 'recruiter_company_name': 'Zyoin',
    'agency_function_names': ['Data Engineering / DWH / ETL'],
    'job_function_dict': {'Software Engineering': ['Data Engineering / DWH / ETL']}}
INTERN = {**ANSWER, 'id': 450020, 'title': 'Founders Office Intern - Marketing Sales & Growth', 'is_internship': True,
          'hiring_company_name': 'MyGyan', 'recruiter_company_name': 'MyGyan', 'workex_min': 0, 'workex_max': 0,
          'locations': ['Bangalore', 'Work From Home'], 'job_category': 'Marketing', 'agency_function_names': None,
          'job_function_dict': {'Marketing': ['Brand Management'], 'Sales': ['Sales / Business Development']},
          'internship': {'hide_stipend': False, 'duration': 6, 'start_date': '2026-11-16', 'is_paid': True,
                         'stipend': 25000},
          'employer_profile_url': None, 'opportunity_url': '/job-450020-founders-office-intern/'}
PAGE = ('<html><head><script type="application/ld+json">{"@type": "Organization", "name": "Instahyre"}</script>'
        '<script type="application/ld+json">{"@context": "https://schema.org", "@type": "JobPosting", '
        '"employmentType": "FULL_TIME", "title": "Senior Data Engineer", "description": "<p>Build pipelines</p>", '
        '"hiringOrganization": {"@type": "Organization", "name": "The Walt Disney Company", '
        '"sameAs": "https://www.disneycareers.com/en"}, "datePosted": "2026-10-19"}</script></head><body>'
        '<div class="job-locations"><span class="location-span"><img src="x.svg" alt="">Bangalore</span>'
        '<span class="experience"><img src="y.svg" alt="">2-4 Years</span> </div>'
        '<p><span class="bold-text">Function:</span> <span>Software Engineering</span> '
        '<span ng-class="{}">&rarr;</span> <span>Data Engineering / DWH / ETL</span></p></body></html>')


def platform(raw: dict | None = None) -> Instahyre:
    p = Instahyre()
    cfg = parse_input(raw or {}, p, {})
    p.search_all([], cfg)
    return p


class Response:
    def __init__(self, status_code, data=None, headers=None, text=''):
        self.status_code, self.data, self.headers, self.text = status_code, data, headers or {}, text

    def json(self):
        return self.data


class Links(unittest.TestCase):
    def test_every_job_link_form(self):
        p = Instahyre()
        self.assertEqual(p.read_link(RECORD['public_url']), ('*', '447415'))
        self.assertEqual(p.read_link('instahyre.com/job-447415/'), ('*', '447415'))
        self.assertEqual(p.read_link('https://www.instahyre.com/job-447415'), ('*', '447415'))

    def test_a_company_page_and_a_search_link(self):
        p = Instahyre()
        self.assertEqual(p.read_link('https://www.instahyre.com/jobs-at-zomato/'), ('zomato', None))
        self.assertEqual(p.read_link('https://www.instahyre.com/search-jobs/?skills=python'), (None, None))
        for text in ('https://jobs.lever.co/x', 'python developer', 'Bangalore'):
            self.assertIsNone(p.read_link(text))

    def test_a_pasted_search_link_reads_its_skills_and_place(self):
        p = Instahyre()
        cfg = parse_input({'searchTerms': ['https://www.instahyre.com/search-jobs/?skills=Python,Django&location=Pune']},
                          p, {})
        self.assertEqual((cfg.search_terms, cfg.locations), (['Python', 'Django'], ['Pune']))

    def test_companies_are_keyed_by_name(self):
        boards, updated = Instahyre().boards()
        self.assertIn('Flipkart', {b.key for b in boards})
        self.assertEqual(len({b.key.lower() for b in boards}), len(boards))
        self.assertEqual(updated, '2026-11-08')
        cfg = parse_input({'companies': ['flipkart', 'Walt Disney']}, Instahyre(), engine.name_index(Instahyre(), boards))
        self.assertEqual(cfg.companies, ['Flipkart', 'The Walt Disney Company'])

    def test_a_name_with_the_is_found_without_it(self):
        self.assertEqual(Instahyre().aliases(Board('54291', 'The Walt Disney Company')), ['Walt Disney Company'])


class Inputs(unittest.TestCase):
    def test_experience_is_the_engines_and_one_number_goes_to_instahyre(self):
        for value, years in ((3, 3), ('3 years', 3), ('5 yrs', 5), ('fresher', 0), ('2.5', 2), (0, 0)):
            cfg = parse_input({'experienceYears': value}, Instahyre(), {})
            self.assertEqual(Instahyre.server_years(cfg), years, value)
        for value in ('2-5', '5+'):                        # a range is the engine's to check, job by job
            self.assertIsNone(Instahyre.server_years(parse_input({'experienceYears': value}, Instahyre(), {})), value)

    def test_unreadable_experience_keeps_every_job_with_a_note(self):
        for value in ('lots', -2):
            cfg = parse_input({'experienceYears': value}, Instahyre(), {})
            self.assertIsNone(cfg.experience)
            self.assertTrue(any('years of experience' in n for n in cfg.notes), cfg.notes)
        self.assertIsNone(parse_input({'experienceYears': None}, Instahyre(), {}).experience)

    def test_words_match_skill_tags_on_instahyre_only(self):
        self.assertTrue(parse_input({}, Instahyre(), {}).match_skills)

    def test_company_size_words(self):
        cfg = parse_input({'companySize': ['startup', 'Medium', 'huge']}, Instahyre(), {})
        self.assertEqual(cfg.extra['company_sizes'], ['small', 'medium'])
        self.assertTrue(any('no option huge' in n for n in cfg.notes))
        self.assertNotIn('company_sizes', parse_input({'companySize': ['small', 'medium', 'large']}, Instahyre(), {}).extra)

    def test_other_platforms_read_no_extra_fields(self):
        from tests.stub import Stub
        cfg = parse_input({'experienceYears': 3}, Stub(), {})
        self.assertEqual((cfg.extra, cfg.match_skills), ({}, False))


class Shape(unittest.TestCase):
    def test_a_record_and_its_answer_become_a_job(self):
        job = Instahyre().job(RECORD, ANSWER)
        self.assertEqual((job['id'], job['rank'], job['title'], job['company'], job['location']),
                         ('447415', 447415, 'Senior Data Engineer', 'The Walt Disney Company', 'Bangalore'))
        self.assertEqual((job['workType'], job['employmentType'], job['department']), ('onsite', 'full-time', 'Software Engineering'))
        extra = job['extra']
        self.assertEqual((extra['experienceMin'], extra['experienceMax'], extra['companyEmployees'], extra['companySize'],
                          extra['companyFounded'], extra['agencyName']), (2, 4, '1000+', 'large', 1923, 'Zyoin'))
        self.assertEqual(extra['jobFunctions'], ['Data Engineering / DWH / ETL'])
        self.assertEqual(job['description'], 'Requirements:\n3+ years of data engineering.')

    def test_a_place_carries_its_state_country_and_other_names(self):
        places = Instahyre().job(RECORD, ANSWER)['places']
        self.assertEqual(places[0], {'name': 'Bangalore', 'city': 'Bangalore', 'region': 'Karnataka', 'country': 'India'})
        self.assertIn({'name': 'Bengaluru'}, places)

    def test_work_from_home_is_remote_in_india_and_a_stipend_is_pay(self):
        job = Instahyre().job({'id': 450020}, INTERN)
        self.assertEqual((job['workType'], job['employmentType'], job['salary'], job['salaryPeriod']),
                         ('remote', 'intern', 'INR 25,000 a month', 'month'))
        self.assertEqual((job['extra']['internshipMonths'], job['extra']['agencyName']), (6, None))
        self.assertIn({'name': 'Work From Home', 'country': 'India'}, job['places'])

    def test_a_hidden_or_unpaid_stipend_is_no_pay(self):
        self.assertEqual(stipend({'stipend': 25000, 'is_paid': True, 'hide_stipend': True}), {})
        self.assertEqual(stipend({'stipend': 0, 'is_paid': False}), {})

    def test_a_row_has_the_platform_columns_and_no_board_slug(self):
        p = Instahyre()
        row = job_row('instahyre', {'name': 'Instahyre', 'slug': '*', 'website': None}, p.job(RECORD, ANSWER),
                      '2026-11-08T00:00:00+00:00', False)
        self.assertEqual((row['experienceMin'], row['experienceMax'], row['companySlug'], row['skills'][:2]),
                         (2, 4, None, ['Airflow', 'Data Engineer']))

    def test_one_title_with_other_skills_is_another_job(self):
        """On 6,680 jobs read 2026-11-08, title and place alone would drop 703 as copies, with the skill tags 82."""
        p = Instahyre()
        one = p.job({**RECORD, 'id': 1})
        again = p.job({**RECORD, 'id': 2, 'keywords': list(reversed(RECORD['keywords']))})
        other = p.job({**RECORD, 'id': 3, 'keywords': ['Kafka']})
        self.assertEqual(p.copy_key(one), p.copy_key(again))
        self.assertNotEqual(p.copy_key(one), p.copy_key(other))


class Matching(unittest.TestCase):
    def test_a_skill_tag_matches_a_word_and_not_a_longer_one(self):
        p = Instahyre()
        job = p.job(RECORD, ANSWER)
        now = datetime.now(timezone.utc)
        self.assertTrue(Filters(parse_input({'searchTerms': ['pyspark']}, p, {})).passes(job, now))
        self.assertTrue(Filters(parse_input({'searchTerms': ['data engineer']}, p, {})).passes(job, now))
        self.assertFalse(Filters(parse_input({'searchTerms': ['spark streaming']}, p, {})).passes(job, now))
        self.assertFalse(Filters(parse_input({'searchTerms': ['java']}, p, {})).passes(
            p.job(RECORD, {**ANSWER, 'keywords': ['JavaScript']}), now))

    def test_places_by_other_names_states_and_country(self):
        p = Instahyre()
        job = p.job(RECORD, ANSWER)
        now = datetime.now(timezone.utc)
        for place, hit in (('Bengaluru', True), ('Karnataka', True), ('India', True), ('Mumbai', False)):
            self.assertEqual(Filters(parse_input({'location': [place]}, p, {})).passes(job, now), hit, place)

    def test_experience_and_company_size_are_checked_where_known(self):
        job = Instahyre().job(RECORD, ANSWER)
        self.assertTrue(platform({'experienceYears': 3}).wanted(job))
        self.assertFalse(platform({'experienceYears': 7}).wanted(job))
        self.assertFalse(platform({'companySize': ['small']}).wanted(job))
        self.assertTrue(platform({'experienceYears': 7}).wanted(Instahyre().job(RECORD)))     # a record: unknown
        opened = Instahyre().job(RECORD, {**ANSWER, 'workex_min': None, 'workex_max': None})
        self.assertFalse(platform({'experienceYears': 3}).wanted(opened))     # opened and stating none: the engine's rule
        self.assertTrue(platform({}).wanted(opened))


class Query(unittest.TestCase):
    def test_the_runs_filters_in_instahyres_words(self):
        p = platform({'searchTerms': ['python', 'data analyst'], 'location': ['Bengaluru', 'Delhi NCR'],
                      'experienceYears': 3, 'companySize': ['small'], 'employmentType': ['intern']})
        self.assertEqual(p.query('Flipkart'), [('company_size', 1), ('job_type', 2), ('skills', 'python'),
                                               ('skills', 'data analyst'), ('jobLocations', 'Bangalore'),
                                               ('jobLocations', 'Delhi / NCR'), ('years', 3), ('companies', 'Flipkart')])

    def test_a_state_asks_for_its_places_and_remote_for_work_from_home(self):
        p = platform({'location': ['Goa'], 'workType': ['remote']})
        places = [v for k, v in p.query(None) if k == 'jobLocations']
        self.assertIn('Panaji', places)
        self.assertEqual(places[-1], 'Work From Home')
        self.assertEqual([v for k, v in platform({'workType': ['remote']}).query(None) if k == 'jobLocations'],
                         ['Work From Home'])

    def test_a_place_instahyre_does_not_name_leaves_the_search_wide(self):
        for where in (['India'], ['Bangalore', 'Atlantis']):
            self.assertEqual([v for k, v in platform({'location': where}).query(None) if k == 'jobLocations'], [])


class Pages(unittest.TestCase):
    def test_a_page_gives_date_experience_function_and_website(self):
        read = read_page(PAGE)
        self.assertEqual((read['date'], read['experience'], read['function'], read['website'], read['employmentType']),
                         ('2026-10-19', (2, 4), ['Software Engineering', 'Data Engineering / DWH / ETL'],
                          'https://www.disneycareers.com/en', 'FULL_TIME'))
        self.assertEqual((read['title'], read['company'], read['locations']),
                         ('Senior Data Engineer', 'The Walt Disney Company', ['Bangalore']))

    def test_a_page_repeating_its_block_per_city_reads_the_jobs_own(self):
        other = PAGE.replace('"datePosted": "2026-10-19"', '"datePosted": "2026-01-01", "identifier": {"value": "job-9-x"}')
        mine = PAGE.replace('"datePosted": "2026-10-19"', '"datePosted": "2026-10-19", "identifier": {"value": "job-447415-x"}')
        html = mine.split('</head>')[0] + other.split('</head>')[0].split('<head>')[1] + '</head>' + mine.split('</head>')[1]
        self.assertEqual(read_page(html, 447415)['date'], '2026-10-19')

    def test_a_pages_skill_tags_and_places(self):
        html = ('<span class="location-span"><img src="x.svg" alt="">Bangalore, Chennai</span>'
                '<ul id="job-skills-description" class="tags"><li id="expand-skills-description">[[ more ]]</li>'
                '<li>Python</li><li>SQL &amp; NoSQL</li></ul>')
        read = read_page(html)
        self.assertEqual((read['locations'], read['skills']), (['Bangalore', 'Chennai'], ['Python', 'SQL & NoSQL']))

    def test_an_internship_page_is_told_by_its_internship_block(self):
        """Its page states no years; every internship's own answer says 0-0 (64 of 64 on 2026-11-08), so it gets that."""
        html = PAGE.replace('<p><span class="bold-text">', '<p>Duration: <strong>[[ job.internship.duration ]] months'
                            '</strong></p><p><span class="bold-text">').replace('<span class="experience"><img src="y.svg" '
                            'alt="">2-4 Years</span>', '')
        p = Instahyre()
        job = p.job(RECORD, p.from_page(RECORD, read_page(html)))
        self.assertEqual((job['employmentType'], job['experienceMin'], job['experienceMax']), ('intern', 0, 0))
        self.assertEqual(p.job(RECORD, p.from_page(RECORD, read_page(PAGE)))['employmentType'], 'full-time')
        no_years = PAGE.replace('<span class="experience"><img src="y.svg" alt="">2-4 Years</span>', '')
        self.assertEqual(p.job(RECORD, p.from_page(RECORD, read_page(no_years)))['experienceMin'], None)

    def test_a_closed_jobs_page_has_no_date(self):
        self.assertNotIn('date', read_page('<span class="experience">4-7 Years</span>'))
        self.assertEqual(read_page('<span class="experience">4-7 Years</span>')['experience'], (4, 7))

    def test_a_page_stands_in_for_a_refused_answer(self):
        p = Instahyre()
        job = p.job(RECORD, p.from_page(RECORD, read_page(PAGE)))
        self.assertEqual((job['extra']['experienceMin'], job['extra']['experienceMax'], job['department'],
                          job['extra']['jobFunctions'], job['employmentType']),
                         (2, 4, 'Software Engineering', ['Data Engineering / DWH / ETL'], 'full-time'))


def days(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


class Dates(unittest.TestCase):
    def dated(self, day_of, ids):
        """fill_dates with pages that answer day_of(id); -> (dates, pages read)."""
        p = Instahyre()
        asked = []

        async def page(job_id):
            asked.append(job_id)
            return {'date': day_of(job_id)} if day_of(job_id) else None

        async def page_date(job_id):
            if job_id in p.dates:
                return p.dates[job_id]
            read = await page(job_id)
            if read and read.get('date'):
                p.dates[job_id] = read['date']
            return (read or {}).get('date')
        p.page_date = page_date
        asyncio.run(p.fill_dates(ids))
        return p.dates, asked

    def test_two_pages_of_one_day_date_every_job_between(self):
        dates, asked = self.dated(lambda i: '2026-11-07', list(range(1000, 1100)))
        self.assertEqual(len(asked), 2)
        self.assertEqual({dates[i] for i in range(1000, 1100)}, {'2026-11-07'})

    def test_a_day_boundary_is_found_by_halving(self):
        day = lambda i: '2026-11-07' if i >= 1037 else '2026-11-06'          # noqa: E731
        dates, asked = self.dated(day, list(range(1000, 1100)))
        self.assertTrue(all(dates[i] == day(i) for i in range(1000, 1100)))
        self.assertLess(len(asked), 20)

    def test_a_job_with_no_page_gets_no_date_and_no_guess(self):
        dates, _ = self.dated(lambda i: None if i == 1050 else ('2026-11-07' if i > 1050 else '2026-11-06'), list(range(1040, 1060)))
        self.assertNotIn(1050, dates)
        self.assertEqual((dates[1051], dates[1049]), ('2026-11-07', '2026-11-06'))

    def test_the_cutoff_is_a_binary_search(self):
        p = Instahyre()
        asked = []

        async def page_date(job_id):
            asked.append(job_id)
            return days(0) if job_id > 500 else days(10)
        p.page_date = page_date
        index = asyncio.run(p.cutoff_index(list(range(1000, 0, -1)), days(7)))
        self.assertEqual(index, 500)
        self.assertLess(len(asked), 12)


class Walk(unittest.TestCase):
    def run_walk(self, answers: dict, raw: dict, top: int, need, cutoff=None, search_s=None, pages_ok=True, reads=None):
        p = platform(raw)

        async def answer(http, job_id):
            if p.paces['detail'].refused or answers.get(job_id) == 'refused':
                p.paces['detail'].refused = 'limit'
                raise Refused('limit')
            return copy.deepcopy(answers.get(job_id))

        async def page(job_id):
            if not pages_ok:
                p.page_trouble = 'refused'
                return None
            if reads and job_id in reads:
                return reads[job_id]
            return {'date': days(0), 'title': f'Engineer {job_id}', 'company': 'Acme', 'locations': ['Pune'],
                    'skills': ['Python'], 'experience': (2, 4), 'function': ['Software Engineering', 'Backend']}

        async def fill_dates(ids):
            for i in ids:
                p.dates[i] = days(0) if i > 950 else days(30)
        p.answer, p.page, p.fill_dates = answer, page, fill_dates
        found = {}
        stop = asyncio.run(p.walk(None, top, need, cutoff, search_s, found))
        return stop, found

    def answers(self, top=1000, bottom=800, pending=()):
        return {i: {**ANSWER, 'id': i, 'is_active': i not in pending, 'title': f'Engineer {i}',
                    'opportunity_url': f'/job-{i}-x/'} for i in range(bottom, top + 1)}

    def test_the_newest_matches_and_no_more(self):
        stop, found = self.run_walk(self.answers(), {}, 1000, 30)
        self.assertEqual(stop[0], 'enough')
        self.assertEqual(sorted(found, reverse=True)[:3], [1000, 999, 998])

    def test_jobs_not_yet_published_are_passed_over(self):
        stop, found = self.run_walk(self.answers(pending=set(range(960, 1001))), {}, 1000, 10)
        self.assertEqual(max(found), 959)

    def test_a_walk_stops_at_the_posting_period(self):
        stop, found = self.run_walk(self.answers(), {}, 1000, None, cutoff=days(7))
        self.assertEqual(stop[0], 'cutoff')
        self.assertEqual(min(found), 951)

    def test_a_slow_walk_hands_over_to_the_search(self):
        stop, found = self.run_walk(self.answers(), {'searchTerms': ['nothing like this']}, 1000, 100, search_s=30)
        self.assertEqual(stop[0], 'slow')

    def test_a_refused_answer_is_read_from_its_page(self):
        answers = {**self.answers(), 990: 'refused'}
        stop, found = self.run_walk(answers, {}, 1000, 30)
        self.assertEqual(stop[0], 'enough')
        self.assertEqual((found[990]['title'], found[990]['extra']['experienceMin'], found[990]['company']),
                         ('Engineer 990', 2, 'Acme'))

    def test_a_job_whose_page_states_no_years_does_not_count_toward_max_jobs(self):
        """Answers refused, so pages stand in: an internship's page gets 0-0 years, every internship's own answer (64 of
        64 on 2026-11-08), and a job page that states no years fits no years, so the walk reads on for the next."""
        intern = {'date': days(0), 'title': 'Marketing Intern', 'company': 'Acme', 'locations': ['Pune'], 'skills': [],
                  'function': ['Marketing'], 'internship': True}
        bare = {'date': days(0), 'title': 'Engineer 999', 'company': 'Acme', 'locations': ['Pune'], 'skills': ['Python'],
                'function': ['Software Engineering']}
        answers = {**self.answers(), 1000: 'refused'}
        stop, found = self.run_walk(answers, {'experienceYears': '0-3'}, 1000, 3, reads={1000: intern, 999: bare})
        self.assertEqual((stop[0], sorted(found, reverse=True)[:3]), ('enough', [1000, 998, 997]))   # a batch is 5 ids
        self.assertEqual((found[1000]['employmentType'], found[1000]['experienceMin'], found[1000]['experienceMax']),
                         ('intern', 0, 0))

    def test_answers_and_pages_both_refused_end_the_walk(self):
        answers = {**self.answers(), 990: 'refused'}
        self.assertEqual(self.run_walk(answers, {}, 1000, 100, pages_ok=False)[0][0], 'refused')

    def test_the_newest_id_is_found_past_a_gap(self):
        p = Instahyre()
        live = set(range(1, 5001)) - {4990}

        async def answer(http, job_id):
            return {'id': job_id} if job_id in live else None

        async def offline(url):
            raise AssertionError('a unit test read a live page')
        p.answer, p.browser_get = answer, offline
        self.assertEqual(asyncio.run(p.newest_id(None, 4000)), 5000)


class Choice(unittest.TestCase):
    """pages(): a small search is read whole; a big one is walked."""

    def run_pages(self, raw: dict, total: int):
        p = platform(raw)
        calls = {'search': 0, 'walk': 0}

        async def search_page(http, query, offset):
            calls['search'] += 1
            return {'meta': {'total_count': total},
                    'objects': [dict(RECORD, id=900 + i, title=f'Data Engineer {i}') for i in range(min(total, 35))]}

        async def walk(http, top, need, cutoff, search_s, found):
            calls['walk'] += 1
            found.update({i: p.job({'id': i}, {**ANSWER, 'id': i}) for i in range(1000, 1000 - (need or 5), -1)})
            return 'enough', 1000 - (need or 5)

        async def newest_id(http, hint):
            return 1000

        async def nothing(*args, **kwargs):
            return None

        async def answer(http, job_id):
            p.answers[job_id] = {**ANSWER, 'id': job_id}
            return p.answers[job_id]

        async def offline(job_id):
            raise AssertionError('a unit test read a live page')
        p.search_page, p.walk, p.newest_id, p.answer, p.page = search_page, walk, newest_id, answer, offline
        p.fill_dates, p.company_facts = nothing, nothing

        async def collect():
            return [job async for page in p.pages(None, {'company': None}, None, None) for job in page]
        return asyncio.run(collect()), calls

    def test_a_small_search_is_read_whole(self):
        jobs, calls = self.run_pages({'searchTerms': ['data engineer']}, 30)
        self.assertEqual(calls, {'search': 1, 'walk': 0})
        self.assertEqual(len(jobs), 30)

    def test_a_sparse_filter_with_no_words_reads_the_search(self):
        """Freshers: 512 of 13,453 jobs, about 790 new ids to read for 30 matches against 15 search pages."""
        jobs, calls = self.run_pages({'experienceYears': 0, 'maxJobs': 30}, 512)
        self.assertEqual(calls['walk'], 0)

    def test_a_dense_filter_with_no_words_walks(self):
        jobs, calls = self.run_pages({'location': ['Bangalore'], 'maxJobs': 30}, 7_025)
        self.assertEqual(calls['walk'], 1)

    def test_the_empty_form_walks(self):
        jobs, calls = self.run_pages({'maxJobs': 20}, 13_453)
        self.assertEqual((calls['walk'], len(jobs)), (1, 20))
        self.assertEqual([j['rank'] for j in jobs][:2], [1000, 999])


class SearchRead(unittest.TestCase):
    """read_search and from_search on a fake search of 10 pages."""

    def fake(self, raw, page_matches, total=350):
        p = platform(raw)
        asked = []

        async def search_page(http, query, offset):
            asked.append(offset)
            n = offset // 35
            hit = page_matches[n] if n < len(page_matches) else 0
            return {'meta': {'total_count': total}, 'objects': [
                dict(RECORD, id=10_000 - offset - i, title=f'Data Engineer {offset + i}' if i < hit else f'Chef {i}',
                     keywords=RECORD['keywords'] if i < hit else ['Cooking']) for i in range(35)]}
        p.search_page = search_page
        return p, asked

    def test_two_pages_with_no_match_end_a_word_search(self):
        p, asked = self.fake({'searchTerms': ['data engineer']}, [35, 20, 0, 3, 0, 0, 9, 9, 9, 9])
        first = asyncio.run(p.search_page(None, [], 0))
        records, how = asyncio.run(p.read_search(None, [], first, dry=True))
        self.assertEqual((how, asked), ('dry', [0, 35, 70, 105, 140, 175]))
        self.assertEqual(len(records), 6 * 35)

    def test_a_search_goes_on_where_it_stopped(self):
        p, asked = self.fake({'searchTerms': ['data engineer']}, [35] * 10)
        first = asyncio.run(p.search_page(None, [], 0))
        records, how = asyncio.run(p.read_search(None, [], first, dry=True, most=3))
        self.assertEqual((how, asked[-1]), ('most', 70))
        asked.clear()
        more, how = asyncio.run(p.read_search(None, [], {'meta': first['meta'], 'objects': []}, dry=True, offset=105))
        self.assertEqual((how, asked[0], len(records) + len(more)), ('whole', 105, 350))

    def opened(self, raw: dict, need: int = 10):
        """from_search on one page of 35 matches, every other one asking 8-12 years and the rest 2-4."""
        p, _ = self.fake(raw, [35], total=35)

        async def answer(http, job_id):
            old = job_id % 2 == 0
            p.answers[job_id] = {**ANSWER, 'id': job_id, 'title': f'Data Engineer {job_id}', 'opportunity_url': f'/job-{job_id}-x/',
                                 'workex_min': 8 if old else 2, 'workex_max': 12 if old else 4}
            return p.answers[job_id]

        async def nothing(*args, **kwargs):
            return None
        p.answer, p.fill_dates, p.company_facts = answer, nothing, nothing
        first = asyncio.run(p.search_page(None, [], 0))
        return p, asyncio.run(p.from_search(None, [], first['objects'], 'whole', 35, need, None, {}))

    def test_a_job_its_answer_rules_out_makes_room_for_the_next(self):
        p, jobs = self.opened({'searchTerms': ['data engineer'], 'experienceYears': 3})
        self.assertEqual(len(jobs), 10)
        self.assertTrue(all(j['_answered'] and j['extra']['experienceMax'] == 4 for j in jobs))
        # the 15 not opened are counted, never handed over: Instahyre's own search took the one number of years
        self.assertEqual((p.unopened_matches, p.unopened_companies, p.count_is_floor),
                         (15, {'The Walt Disney Company'}, False))

    def test_a_range_of_years_leaves_the_count_a_floor(self):
        """2-5 cannot go to Instahyre's search, so the jobs not opened may or may not ask for those years."""
        p, jobs = self.opened({'searchTerms': ['data engineer'], 'experienceYears': '2-5'})
        self.assertEqual(len(jobs), 10)
        self.assertEqual((p.unopened_matches, p.count_is_floor), (0, True))
        p, jobs = self.opened({'searchTerms': ['data engineer'], 'experienceYears': '2-5'}, need=None)
        self.assertEqual((len(jobs), p.count_is_floor), (17, False))      # every match opened: the count is exact

    def test_a_job_with_no_answer_and_no_page_is_left_out_under_years(self):
        p, _ = self.fake({'searchTerms': ['data engineer'], 'experienceYears': 3}, [35], total=35)

        async def refused(http, job_id):
            raise Refused('limit')

        async def no_page(job_id):
            return None

        async def nothing(*args, **kwargs):
            return None
        p.answer, p.page, p.fill_dates, p.company_facts = refused, no_page, nothing, nothing
        first = asyncio.run(p.search_page(None, [], 0))
        jobs = asyncio.run(p.from_search(None, [], first['objects'], 'whole', 35, 10, None, {}))
        self.assertEqual((jobs, p.unopened_matches), ([], 35))    # Instahyre's search took the years: still matches
        self.assertIn('35 jobs could not be read one by one, so their years of experience are unknown and they were '
                      'left out.', p.cfg.notes)

    def test_one_title_in_other_cities_is_another_job(self):
        p = Instahyre()
        one = p.job({**RECORD, 'id': 1, 'locations': 'Bangalore,Gurgaon,Pune'})
        other = p.job({**RECORD, 'id': 2, 'locations': 'Bangalore,Chennai,Hyderabad'})
        again = p.job({**RECORD, 'id': 3, 'locations': 'Pune,Bangalore,Gurgaon'})
        self.assertNotEqual(p.copy_key(one), p.copy_key(other))
        self.assertEqual(p.copy_key(one), p.copy_key(again))


class Engine(unittest.TestCase):
    def test_jobs_of_one_day_come_newest_id_first(self):
        """Jobs posted the same day keep Instahyre's id order (rank), whatever order they came in."""
        from contextlib import asynccontextmanager
        p = Instahyre()

        async def pages(http, session, term, days_):
            yield [dict(p.job({'id': i}, {**ANSWER, 'id': i, 'title': f'Engineer {i}', 'opportunity_url': f'/job-{i}-x/'}),
                        postedAt=days(0)) for i in (10, 30, 20)]
        p.pages = pages
        p.search_all = lambda boards, cfg: [SITE]

        class Delivered:
            def __init__(self):
                self.pushed, self.rows, self.limit = [], 0, False

            def can_pay(self, event):
                return True

            async def push(self, rows, event):
                self.pushed += [r['jobId'] for r in rows]
                self.rows += len(rows)
                return len(rows)

        async def quiet(message):
            return None

        @asynccontextmanager
        async def no_client(actor, connections=64):
            yield None
        run = engine.Run(p, parse_input({}, p, {}), [], None, Delivered())
        with patch.object(engine, 'client', no_client), patch.object(engine, 'safe_status', quiet), \
                patch.object(engine, 'seconds_left_in_run', lambda: None):
            asyncio.run(run.search())
        self.assertEqual(run.delivery.pushed, ['30', '20', '10'])


FRESHERS = os.path.join(os.path.dirname(__file__), 'data', 'instahyre_freshers_2026-11-08.json.gz')
# run r2-03's rows, newest first
R2_03 = ['451486', '451198', '451143', '451141', '451049', '450921', '450915', '450824', '450810', '450802', '450714',
         '450635', '450464', '450452', '450445', '450414', '450326', '450271', '450201', '450199', '450140', '450128',
         '450077', '450073', '450063', '450055', '450020', '450015', '449970', '449843']


class Freshers(unittest.TestCase):
    """The freshers search of 2026-11-08 (experienceYears 0, maxJobs 30) offline, on Instahyre's answers recorded at
    09:11 UTC: the 524 jobs its years=0 search listed, the same the local runs r2-03 (08:39) and r2-04 read, and the
    answers and pages of the 60 newest (data/instahyre_freshers_2026-11-08.json.gz). On engine 0.5.1 r2-03 said "Found
    and saved 30 matching jobs at 29 companies", its 493 jobs not opened counted as left out by experienceYears, and
    r2-04 (job answers refused) saved 28 of 30: the two internships' pages state no years, so the engine dropped them
    after they had counted toward maxJobs."""

    class Delivered:
        def __init__(self):
            self.rows_saved, self.rows, self.limit = [], 0, False

        def can_pay(self, event):
            return True

        async def push(self, rows, event):
            self.rows_saved += rows
            self.rows += len(rows)
            return len(rows)

    @classmethod
    def setUpClass(cls):
        with gzip.open(FRESHERS, 'rt', encoding='utf-8') as f:
            cls.fx = json.load(f)

    def run_freshers(self, answers_refused: bool) -> engine.Run:
        fx, p = self.fx, Instahyre()

        async def search_page(http, query, offset):
            self.assertEqual([list(q) for q in query], fx['query'])
            return {'meta': {'total_count': fx['total']}, 'objects': copy.deepcopy(fx['records'][offset:offset + 35])}

        async def answer(http, job_id):
            if answers_refused:
                raise Refused('Instahyre limits how often one address may call its detail')
            self.assertIn(str(job_id), fx['answers'], f'the answer of job {job_id} was not recorded')
            p.answers[job_id] = copy.deepcopy(fx['answers'][str(job_id)])
            return p.answers[job_id]

        async def page(job_id):
            self.assertIn(str(job_id), fx['reads'], f'the page of job {job_id} was not recorded')
            read = copy.deepcopy(fx['reads'][str(job_id)])
            if read and read.get('experience'):
                read['experience'] = tuple(read['experience'])
            p.read_pages[job_id] = read
            if read and read.get('date'):
                p.dates[job_id] = read['date']
            return read

        async def profile(http, employer):
            return fx['profiles'].get(employer)

        async def walked(*args, **kwargs):
            raise AssertionError('a search this sparse is read, not walked')

        async def quiet(message):
            return None

        @asynccontextmanager
        async def no_client(actor, connections=64):
            yield None
        p.search_page, p.answer, p.page, p.profile, p.newest_id = search_page, answer, page, profile, walked
        run = engine.Run(p, parse_input({'experienceYears': 0, 'maxJobs': 30}, p, {}), [], None, self.Delivered())
        with patch.object(engine, 'client', no_client), patch.object(engine, 'safe_status', quiet), \
                patch.object(engine, 'seconds_left_in_run', lambda: None):
            asyncio.run(run.search())
        return run

    def test_every_match_is_counted_and_the_newest_30_are_saved(self):
        run = self.run_freshers(answers_refused=False)
        rows = run.delivery.rows_saved
        self.assertEqual([r['jobId'] for r in rows], R2_03)
        self.assertTrue(all(r['experienceMin'] == 0 for r in rows))
        # 524 jobs listed, one a copy: the 30 opened and the 493 that Instahyre's own years filter vouches for
        self.assertEqual((run.matching, run.jobs_listed, run.near_misses), (523, 523, 0))
        self.assertRegex(run.message(), r'^Found 523 matching jobs at \d{3} companies on Instahyre, saved the 30 newest')

    def test_with_the_answers_refused_the_pages_give_the_same_30(self):
        run = self.run_freshers(answers_refused=True)
        rows = run.delivery.rows_saved
        self.assertEqual([r['jobId'] for r in rows], R2_03)      # the two internships too, at 0-0 years
        self.assertEqual([(r['employmentType'], r['experienceMin'], r['experienceMax']) for r in rows
                          if r['jobId'] in ('450020', '450015')], [('intern', 0, 0)] * 2)
        self.assertEqual((run.matching, run.near_misses), (523, 0))
        self.assertIn('Instahyre refused the job answers, so 30 jobs were read from their pages', run.message())


class Lookup(unittest.TestCase):
    def status(self, info):
        p = Instahyre()

        async def answer(http, job_id):
            return info

        async def page(job_id):
            return {'date': '2026-11-07'}

        async def nothing(*args, **kwargs):
            return None
        p.answer, p.page, p.company_facts = answer, page, nothing
        return asyncio.run(p.lookup(None, {'company': None}, '447415'))

    def test_open_closed_and_not_found(self):
        self.assertEqual(self.status(ANSWER)[0], 'open')
        self.assertEqual(self.status({**ANSWER, 'is_active': False}), ('closed', {'company': 'The Walt Disney Company',
                                                                               'companySlug': None, 'companyWebsite': None}))
        self.assertEqual(self.status(None), ('not found', {'company': None, 'companySlug': None, 'companyWebsite': None}))


if __name__ == '__main__':
    unittest.main()
