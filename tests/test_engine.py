"""One search from end to end on a fake platform: which pages are read, what is kept, what is charged."""
import asyncio
import unittest
from contextlib import asynccontextmanager
from datetime import date, timedelta
from unittest.mock import patch

from ats_jobs import engine
from ats_jobs.http import BoardError, BoardMissing
from ats_jobs.inputs import parse_input
from ats_jobs.platforms.base import Board, Platform

TODAY = date.today()


def day(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


class Fake(Platform):
    key = 'fake'
    name = 'Fake'
    actor = 'fake'
    server_search = True

    def __init__(self, sites: dict, **settings):
        self.sites, self.read = sites, []
        for name, value in settings.items():
            setattr(self, name, value)

    def read_link(self, text):
        return None

    async def open(self, http, board):
        return board.key

    async def pages(self, http, session, term, days):
        site = self.sites[session]
        if site == 'missing':
            raise BoardMissing(session)
        if site == 'down':
            raise BoardError('HTTP 500')
        for number, page in enumerate(site, 1):
            self.read.append((session, term, number))
            yield [dict(j, jobUrl=f'https://x/{j["id"]}') for j in page]

    async def details(self, http, session, job):
        return {**job, 'description': 'Full text'} if job['id'] != 'nodetails' else job


class Delivered:
    def __init__(self):
        self.pushed, self.rows, self.limit = [], 0, False

    def can_pay(self, event):
        return True

    async def push(self, rows, event):
        self.pushed += [(r['jobId'], event) for r in rows]
        self.rows += len(rows)
        return len(rows)


def job(job_id, title='Engineer', posted=0, where='Berlin'):
    return {'id': job_id, 'title': title, 'location': where, 'postedAt': day(posted), 'dateOnly': True}


def search(platform, raw, boards=None):
    @asynccontextmanager
    async def no_client(actor, connections=64):
        yield None
    keys = boards or list(platform.sites)
    run = engine.Run(platform, parse_input(raw, platform), [Board(k, k.split('/')[0]) for k in keys], None, Delivered())
    with patch.object(engine, 'client', no_client), patch.object(engine, 'safe_status', _quiet), \
            patch.object(engine, 'seconds_left_in_run', lambda: None):
        asyncio.run(run.search())
    return run


async def _quiet(message):
    return None


class Delivery(unittest.TestCase):
    def test_the_newest_jobs_of_all_boards_come_first(self):
        run = search(Fake({'a': [[job('1', 'One', 5), job('2', 'Two', 1)]], 'b': [[job('3', 'Three', 0), job('4', 'Four', 9)]]}), {'maxJobs': 3})
        self.assertEqual([i for i, _ in run.delivery.pushed], ['3', '2', '1'])
        self.assertEqual(run.matching, 4)

    def test_an_older_copy_read_first_is_not_delivered_twice(self):
        run = search(Fake({'a': [[job('old', posted=5), job('new', posted=1)]]}), {})
        self.assertEqual([i for i, _ in run.delivery.pushed], ['new'])

    def test_a_replaced_copy_leaves_no_gap_in_the_list(self):
        sites = {'acme/1': [[job('old', posted=2)]],
                 'acme/2': [[job('new', posted=1), job('3', 'Three', 3), job('4', 'Four', 4)]]}
        run = search(Fake(sites, concurrency=1), {'maxJobs': 3})
        self.assertEqual([i for i, _ in run.delivery.pushed], ['new', '3', '4'])
        self.assertEqual(run.duplicates, 1)

    def test_a_replaced_copy_does_not_stretch_the_list_past_max_jobs(self):
        sites = {'acme/1': [[job('old', posted=2)]],
                 'acme/2': [[job('new', posted=1), job('3', 'Three', 3), job('4', 'Four', 4)]]}
        run = search(Fake(sites, concurrency=1), {'maxJobs': 2})
        self.assertEqual([i for i, _ in run.delivery.pushed], ['new', '3'])

    def test_one_job_on_two_sites_of_a_company_is_one_row(self):
        run = search(Fake({'acme/1': [[job('1')]], 'acme/2': [[job('1b')]], 'other/1': [[job('2')]]}), {})
        self.assertEqual(run.delivery.rows, 2)
        self.assertEqual(run.duplicates, 1)

    def test_two_words_finding_one_job_is_one_row(self):
        site = [[job('1', title='Data Engineer')]]
        run = search(Fake({'a': site}), {'searchTerms': ['data', 'engineer']})
        self.assertEqual(run.delivery.rows, 1)

    def test_a_missing_board_and_a_failing_board_are_reported(self):
        run = search(Fake({'a': 'missing', 'b': 'down', 'c': [[job('1')]]}), {})
        self.assertEqual((run.boards_missing, run.delivery.rows, len(run.board_errors)), (['a'], 1, 1))

    def test_details_are_charged_only_when_they_came(self):
        run = search(Fake({'a': [[job('ok'), job('nodetails', title='Analyst')]]}), {'includeDetails': True})
        self.assertEqual(dict(run.delivery.pushed), {'ok': 'job-details', 'nodetails': 'job'})


class Paging(unittest.TestCase):
    def pages(self, n=3):
        return [[job(f'{p}-{i}', title=f'Engineer {p} {i}', posted=p * 10) for i in range(2)] for p in range(n)]

    def test_a_newest_first_board_stops_once_it_cannot_make_the_cut(self):
        platform = Fake({'a': [[job('x', 'Engineer X', 5), job('y', 'Engineer Y', 5)]], 'b': self.pages()}, newest_first=True, concurrency=1)
        search(platform, {'maxJobs': 2})
        self.assertEqual([r for r in platform.read if r[0] == 'b'], [('b', None, 1), ('b', None, 2)])

    def test_a_date_window_stops_a_board_with_no_date_search(self):
        platform = Fake({'a': self.pages()}, newest_first=True)
        search(platform, {'postedWithin': '3 days', 'maxJobs': 0})
        self.assertEqual(len(platform.read), 2)

    def test_a_ranked_search_stops_at_a_page_with_no_match(self):
        pages = [[job('1', 'Engineer'), job('2', 'Analyst')], [job('3', 'Cook')], [job('4', 'Engineer II')]]
        platform = Fake({'a': pages}, ranked_search=True)
        run = search(platform, {'searchTerms': ['engineer']})
        self.assertEqual((len(platform.read), run.delivery.rows), (2, 1))
        platform = Fake({'a': pages}, ranked_search=True)
        search(platform, {'searchTerms': ['engineer'], 'searchDescriptions': True})
        self.assertEqual(len(platform.read), 3)

    def test_a_word_search_ranked_by_relevance_is_not_cut_by_date(self):
        platform = Fake({'a': [[job('x', 'Engineer X', 5), job('y', 'Engineer Y', 5)]], 'b': self.pages()},
                        newest_first=True, ranked_search=True, concurrency=1)
        search(platform, {'searchTerms': ['engineer'], 'maxJobs': 2})
        self.assertEqual(len([r for r in platform.read if r[0] == 'b']), 3)

    def test_the_page_cap_holds_unless_every_match_was_asked_for(self):
        platform = Fake({'a': self.pages(9)}, page_cap=2)
        search(platform, {'maxJobs': 100})
        self.assertEqual(len(platform.read), 2)
        platform = Fake({'a': self.pages(9)}, page_cap=2)
        search(platform, {'maxJobs': 0})
        self.assertEqual(len(platform.read), 9)


class Enough(unittest.TestCase):
    def test_a_run_stops_once_today_fills_the_list(self):
        sites = {f'c{i}': [[job(f'{i}-{n}', f'Job {i} {n}') for n in range(5)]] for i in range(9)}
        platform = Fake(sites, concurrency=1)
        run = search(platform, {'maxJobs': 5})
        self.assertEqual(run.delivery.rows, 5)
        self.assertTrue(run.enough)
        self.assertEqual(len(platform.read), 1)

    def test_older_jobs_do_not_end_the_run(self):
        sites = {f'c{i}': [[job(f'{i}-{n}', f'Job {i} {n}', posted=2) for n in range(5)]] for i in range(3)}
        platform = Fake(sites, concurrency=1)
        run = search(platform, {'maxJobs': 5})
        self.assertFalse(run.enough)
        self.assertEqual(len(platform.read), 3)

    def test_jobs_with_an_exact_time_do_not_end_the_run(self):
        sites = {f'c{i}': [[dict(job(f'{i}-{n}', f'Job {i} {n}'), postedAt=f'{TODAY}T08:00:00+00:00', dateOnly=False) for n in range(5)]] for i in range(3)}
        platform = Fake(sites, concurrency=1)
        self.assertFalse(search(platform, {'maxJobs': 5}).enough)


class Places(unittest.TestCase):
    def test_a_job_that_names_no_place_is_opened_for_a_place_filter(self):
        class Vague(Fake):
            opened = 0

            def vague_place(self, job):
                return job['location'] == '2 Locations'

            async def details(self, http, session, job):
                Vague.opened += 1
                return {**job, 'location': 'Berlin', 'places': [{'name': 'Berlin', 'country': 'DE'}]}
        site = [[job('1', title='Engineer', where='2 Locations'), job('2', title='Analyst', where='2 Locations'), job('3', where='Paris')]]
        run = search(Vague({'a': site}), {'searchTerms': ['engineer'], 'location': ['Germany']})
        self.assertEqual(([i for i, _ in run.delivery.pushed], Vague.opened), (['1'], 1))


if __name__ == '__main__':
    unittest.main()
