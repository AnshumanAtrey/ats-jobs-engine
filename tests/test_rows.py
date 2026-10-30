"""Matching and rows on the engine's job shape.

Run: python -m unittest discover -s tests -t .
"""
import unittest
from datetime import datetime, timedelta, timezone

from ats_jobs.inputs import parse_input
from ats_jobs.rows import Filters, Term, country_code, dedupe_key, index, job_places, job_row, work_type

from .stub import Stub

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
RAMP = {'name': 'Ramp', 'slug': 'ramp', 'website': 'https://ramp.com'}


def job(**over) -> dict:
    base = {
        'id': '34413f8d', 'title': ' Security Engineer, Cloud', 'department': 'Engineering', 'team': 'Backend',
        'employmentType': 'full-time', 'workType': 'hybrid', 'location': 'New York, NY (HQ)',
        'otherLocations': ['Remote (Canada)', 'Miami, FL'],
        'places': [{'name': 'New York, NY (HQ)', 'city': 'New York City', 'region': 'NY', 'country': 'USA'},
                   {'name': 'Remote (Canada)', 'country': 'Canada'},
                   {'name': 'Miami, FL', 'city': 'Miami', 'region': 'Florida', 'country': 'USA'}],
        'postedAt': (NOW - timedelta(days=2)).isoformat(), 'jobUrl': 'https://jobs.example.com/ramp/34413f8d',
        'applyUrl': 'https://jobs.example.com/ramp/34413f8d/application',
        'description': 'We build finance automation with Python and Kubernetes.',
        'descriptionHtml': '<p>We build finance automation with Python and Kubernetes.</p>',
        'salary': '$211.4K - $290.6K', 'salaryMin': 211400, 'salaryMax': 290600, 'salaryCurrency': 'USD',
        'salaryPeriod': 'year',
    }
    base.update(over)
    return base


def filters(**raw) -> Filters:
    return Filters(parse_input(raw, Stub()))


class TitleWords(unittest.TestCase):
    def test_joined_and_split_words_match_each_other(self):
        for title in ('Senior Frontend Engineer', 'Front-End Engineer', 'Front End Developer'):
            for term in ('frontend', 'front end', 'front-end'):
                self.assertTrue(Term(term).in_title(index(title)), (term, title))

    def test_a_word_inside_another_word_does_not_match(self):
        self.assertFalse(Term('java').in_title(index('JavaScript Engineer')))
        self.assertTrue(Term('java').in_title(index('Java Engineer')))

    def test_plurals_and_signs(self):
        self.assertTrue(Term('engineers').in_title(index('Software Engineer')))
        self.assertTrue(Term('c++').in_title(index('C++ Developer')))
        self.assertFalse(Term('c++').in_title(index('C Developer')))

    def test_every_word_of_the_term_is_needed(self):
        self.assertTrue(Term('security engineer').in_title(index(' Security Engineer, Cloud')))
        self.assertFalse(Term('data engineer').in_title(index(' Security Engineer, Cloud')))


class Countries(unittest.TestCase):
    def test_names_and_codes(self):
        for text in ('United States', 'USA', 'US', 'u.s.a.'):
            self.assertEqual(country_code(text, True), 'US', text)
        self.assertEqual(country_code('UK', True), 'GB')
        self.assertEqual(country_code('India', False), 'IN')

    def test_a_state_in_free_text_is_not_a_country(self):
        self.assertIsNone(country_code('CA', False))
        self.assertEqual(country_code('CA', True), 'CA')

    def test_job_places_hold_every_country(self):
        texts, codes = job_places(job())
        self.assertEqual(codes, {'US', 'CA'})
        self.assertIn('Miami', texts)

    def test_a_job_with_only_location_text(self):
        texts, codes = job_places({'location': 'Berlin, Germany'})
        self.assertEqual(codes, {'DE'})
        self.assertIn('Berlin, Germany', texts)


class Filtering(unittest.TestCase):
    def test_title_place_work_type_and_date(self):
        self.assertTrue(filters(searchTerms=['security engineer'], location=['United States'],
                                workType=['hybrid'], postedWithin='7 days').passes(job(), NOW))
        self.assertTrue(filters(location=['Canada']).passes(job(), NOW))
        self.assertTrue(filters(location=['Miami']).passes(job(), NOW))
        self.assertFalse(filters(location=['Germany']).passes(job(), NOW))
        self.assertFalse(filters(workType=['remote']).passes(job(), NOW))
        self.assertFalse(filters(postedWithin='24 hours').passes(job(), NOW))

    def test_excluded_words(self):
        self.assertFalse(filters(excludeTerms=['cloud']).passes(job(), NOW))

    def test_descriptions_only_when_asked(self):
        self.assertFalse(filters(searchTerms=['kubernetes']).passes(job(), NOW))
        self.assertTrue(filters(searchTerms=['kubernetes'], searchDescriptions=True).passes(job(), NOW))

    def test_the_platform_searched_the_description(self):
        short = job(description='We build finance automation.')
        chosen = filters(searchTerms=['kubernetes'], searchDescriptions=True)
        self.assertFalse(chosen.passes(short, NOW))
        self.assertTrue(chosen.passes(short, NOW, server_match=True))
        self.assertFalse(filters(searchTerms=['kubernetes']).passes(short, NOW, server_match=True))
        self.assertFalse(filters(searchTerms=['kubernetes'], excludeTerms=['cloud'], searchDescriptions=True)
                         .passes(short, NOW, server_match=True))

    def test_a_day_with_no_time_counts_as_recent_for_a_day_more(self):
        yesterday = job(postedAt=(NOW - timedelta(days=1)).date().isoformat(), dateOnly=True)
        older = job(postedAt=(NOW - timedelta(days=3)).date().isoformat(), dateOnly=True)
        self.assertTrue(filters(postedWithin='24 hours').passes(yesterday, NOW))
        self.assertFalse(filters(postedWithin='24 hours').passes(older, NOW))

    def test_first_pass_keeps_jobs_that_can_match(self):
        short = {'title': 'Security Engineer', 'location': 'New York', 'employmentType': 'full-time'}
        self.assertTrue(filters(searchTerms=['security'], workType=['remote']).maybe(short))
        self.assertFalse(filters(searchTerms=['designer']).maybe(short))
        self.assertFalse(filters(employmentType=['intern']).maybe(short))


class Rows(unittest.TestCase):
    def test_work_type_falls_back_to_the_location_and_title(self):
        self.assertEqual(work_type(job()), 'hybrid')
        self.assertEqual(work_type({'title': 'Engineer', 'location': 'Remote (US)'}), 'remote')
        self.assertEqual(work_type({'title': 'Claims Adjuster (Remote)', 'location': 'Ohio'}), 'remote')
        self.assertEqual(work_type({'title': 'Claims Adjuster - Work From Home', 'location': 'Ohio'}), 'remote')
        self.assertEqual(work_type({'title': 'Hybrid Analyst', 'location': 'Ohio'}), 'hybrid')
        self.assertIsNone(work_type({'title': 'Engineer', 'location': 'Ohio'}))

    def test_link_row_and_details_row(self):
        row = job_row('stub', RAMP, job(), '2026-09-30T12:00:00+00:00', details=False)
        self.assertEqual((row['title'], row['company'], row['workType'], row['employmentType'], row['status'], row['platform']),
                         ('Security Engineer, Cloud', 'Ramp', 'hybrid', 'full-time', 'open', 'stub'))
        self.assertEqual(row['otherLocations'], ['Remote (Canada)', 'Miami, FL'])
        self.assertNotIn('description', row)
        full = job_row('stub', RAMP, job(), '2026-09-30T12:00:00+00:00', details=True)
        self.assertEqual((full['city'], full['countryCode'], full['salaryPeriod']), ('New York City', 'US', 'year'))
        self.assertIn('Kubernetes', full['description'])

    def test_same_title_and_place_is_one_job(self):
        self.assertEqual(dedupe_key(job()), dedupe_key(job(id='other', title='Security  Engineer - Cloud')))
        self.assertNotEqual(dedupe_key(job()), dedupe_key(job(location='London')))


if __name__ == '__main__':
    unittest.main()
