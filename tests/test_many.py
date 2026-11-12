"""One run over many platforms (ats_jobs/many.py), on fake platforms: which run, in what order the jobs come, what is
kept once, and that the platforms are read at the same time."""
import asyncio
import multiprocessing
import re
import time
import unittest
from contextlib import asynccontextmanager
from decimal import Decimal
from datetime import date, timedelta
from unittest.mock import patch

from ats_jobs import engine, many
from ats_jobs.http import BoardError
from ats_jobs.platforms import PLATFORMS
from ats_jobs.platforms.base import Board, Platform

TODAY = date.today()


def day(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


def job(job_id, title='Engineer', posted=0, where='Berlin', **more):
    return {'id': job_id, 'title': title, 'location': where, 'postedAt': day(posted), 'dateOnly': True, **more}


class Base(Platform):
    """Boards are self.sites keys; links are <key>://<board>[/<job>]."""
    sites: dict = {}
    server_search = True
    delay = 0.0
    broken = False

    def boards(self):
        if self.broken:
            raise RuntimeError('the board list is gone')
        return [Board(k, k) for k in self.sites], None

    def read_link(self, text):
        m = re.match(rf'^{self.key}://([a-z0-9]+)(?:/([a-z0-9]+))?$', text)
        return (m.group(1), m.group(2)) if m else None

    async def open(self, http, board):
        return board.key if board.key in self.sites else None

    async def pages(self, http, session, term, days):
        if self.delay:
            await asyncio.sleep(self.delay)
        site = self.sites[session]
        if site == 'down':
            raise BoardError('HTTP 500')
        for page in site:
            yield [dict(j, jobUrl=j.get('jobUrl') or f'https://{self.key}.example/{session}/{j["id"]}') for j in page]

    async def details(self, http, session, job):
        return {**job, 'description': f'{self.key} text'}

    async def lookup(self, http, session, job_id):
        found = [j for page in self.sites[session] for j in page if j['id'] == job_id]
        if not found:
            return 'not found', None
        return 'open', dict(found[0], jobUrl=f'https://{self.key}.example/{session}/{job_id}')


def make(key, sites, **attrs):
    return type(key.title(), (Base,), {'key': key, 'name': key.title(), 'actor': key, 'sites': sites, **attrs})


class ApartSite(Base):
    """A platform importable by a spawned worker process (many.Worker), for the tests of APART."""
    key, name, actor = 'apartsite', 'Apart', 'apart'
    sites = {'acme': [[job('w1', 'Engineer W', posted=0), job('w2', 'Engineer X', posted=4)]]}


class BrokenApart(Base):
    key, name, actor = 'brokenapart', 'Broken', 'broken'
    sites = {'acme': []}

    def boards(self):
        if multiprocessing.parent_process() is not None:      # only in the worker process
            raise RuntimeError('no board list')
        return super().boards()


class Delivered:
    ppe = True

    def __init__(self, budget=None):
        self.pushed, self.rows, self.limit, self.budget = [], 0, False, budget

    def can_pay(self, event):
        return not self.limit and (self.budget is None or self.rows < self.budget)

    async def push(self, rows, event):
        n = len(rows) if self.budget is None else max(0, min(len(rows), self.budget - self.rows))
        self.pushed += [(r['platform'], r['jobId'], event) for r in rows[:n]]
        self.rows += n
        self.limit = self.limit or n < len(rows)
        return n


async def _quiet(message):
    return None


def run_many(raw, *classes, delivery=None):
    @asynccontextmanager
    async def no_client(actor, connections=64):
        yield None
    delivery = delivery or Delivered()
    with patch.object(many, 'PLATFORMS', {c.key: c for c in classes}), patch.object(engine, 'client', no_client), \
            patch.object(engine, 'safe_status', _quiet), patch.object(engine, 'seconds_left_in_run', lambda: None):
        run = many.Many(raw, 'job-boards-search', delivery)
        asyncio.run(run.run())
        run.final = run.output(run.status())
    return run, delivery


class Order(unittest.TestCase):
    def test_the_newest_jobs_of_every_platform_come_first_and_max_jobs_counts_them_all(self):
        alpha = make('alpha', {'acme': [[job('a1', posted=3), job('a2', 'Designer', posted=0)]]})
        beta = make('beta', {'zeta': [[job('b1', 'Analyst', posted=1), job('b2', 'Manager', posted=5)]]})
        run, d = run_many({'maxJobs': 3}, alpha, beta)
        self.assertEqual([(p, j) for p, j, _ in d.pushed], [('alpha', 'a2'), ('beta', 'b1'), ('alpha', 'a1')])
        self.assertEqual(run.final['jobsSaved'], 3)
        self.assertEqual(run.final['matchingJobs'], 4)
        self.assertIn('saved the 3 newest', run.final['message'])
        self.assertIn('Alpha 2, Beta 1', run.final['message'])

    def test_jobs_of_the_same_day_keep_each_platforms_own_order(self):
        alpha = make('alpha', {'acme': [[job('a1', 'Engineer A'), job('a2', 'Engineer B')]]})
        beta = make('beta', {'zeta': [[job('b1', 'Engineer C')]]})
        alone, _ = run_many({}, alpha)
        _, d = run_many({}, alpha, beta)
        # one platform's jobs of a day come in the order its own actor gives them, and the platforms in their order
        self.assertEqual([j for _, j, _ in d.pushed], [j for _, j, _ in alone.delivery.pushed] + ['b1'])

    def test_every_board_the_engine_reads_has_a_place_in_the_order(self):
        self.assertEqual(sorted(many.known()), sorted(PLATFORMS))
        self.assertEqual(many.known()[:len([k for k in many.ORDER if k in PLATFORMS])],
                         [k for k in many.ORDER if k in PLATFORMS])


class OnceEach(unittest.TestCase):
    def test_a_job_on_two_platforms_is_saved_once(self):
        alpha = make('alpha', {'acme': [[job('a1', 'Data Analyst', where='London')]]})
        beta = make('beta', {'acme': [[job('b1', 'Data Analyst', where='London')]]})
        run, d = run_many({}, alpha, beta)
        self.assertEqual([(p, j) for p, j, _ in d.pushed], [('alpha', 'a1')])
        self.assertEqual(run.final['duplicatesAcrossBoards'], 1)
        self.assertIn('1 job listed on two boards was saved once', run.final['message'])

    def test_the_same_job_link_on_two_platforms_is_one_job(self):
        link = 'https://job-boards.greenhouse.io/acme/jobs/123'
        alpha = make('alpha', {'acme': [[job('a1', 'Senior Data Analyst', where='London, UK', jobUrl=link)]]})
        beta = make('beta', {'acmeinc': [[job('b1', 'Senior Data Analyst', where='London', applyUrl=link + '?src=x')]]})
        _, d = run_many({}, alpha, beta)
        self.assertEqual([p for p, _, _ in d.pushed], ['alpha'])

    def test_a_careers_page_many_jobs_share_is_no_proof_of_one_job(self):
        page = 'https://acme.example/careers/open-roles'
        alpha = make('alpha', {'acme': [[job('a1', 'Data Analyst', where='London', applyUrl=page)]]})
        beta = make('beta', {'zeta': [[job('b1', 'Designer', where='Paris', applyUrl=page),
                                       job('b2', 'Data Analyst', where='Leeds', applyUrl=page)]]})
        _, d = run_many({}, alpha, beta)
        self.assertEqual(sorted(j for _, j, _ in d.pushed), ['a1', 'b1', 'b2'])

    def test_jobs_one_platform_tells_apart_stay_apart(self):
        # Lever keeps one title at one place for two teams (Platform.copy_key); the run over many must not merge them
        class Teams(Base):
            def copy_key(self, job):
                return (*super().copy_key(job), job.get('team'))
        alpha = type('Alpha', (Teams,), {'key': 'alpha', 'name': 'Alpha', 'actor': 'alpha', 'sites': {
            'acme': [[job('a1', team='Ads'), job('a2', team='Search')]]}})
        _, d = run_many({}, alpha)
        self.assertEqual(sorted(j for _, j, _ in d.pushed), ['a1', 'a2'])

    def test_company_names_match_without_their_legal_words(self):
        self.assertEqual(many.company_key('Acme, Inc.'), many.company_key('ACME'))
        self.assertEqual(many.company_key('Acme Technologies Pvt Ltd'), many.company_key('acme technologies'))
        self.assertEqual(many.link_key('HTTPS://www.Example.com/jobs/1/?utm_source=x#frag'), 'example.com/jobs/1')
        self.assertEqual(many.link_key('https://jobs.smartrecruiters.com/Wise/744000161101989-staff-data-analyst-'),
                         many.link_key('https://jobs.smartrecruiters.com/Wise/744000161101989-staff-data-analyst?oga=true'))
        self.assertEqual(many.link_key('https://careers.nebius.com/?gh_jid=5027907101&gh_src=abc'),
                         'careers.nebius.com?gh_jid=5027907101')
        self.assertNotEqual(many.link_key('https://careers.nebius.com/?gh_jid=1'), many.link_key('https://careers.nebius.com/?gh_jid=2'))
        self.assertIsNone(many.link_key('https://example.com'))

    def test_one_job_written_two_ways_on_two_boards_is_one_job(self):
        # 2026-11-13, data analyst in London: eFinancialCareers and HiringCafe both listed Wise's role, places written apart
        alpha = make('alpha', {'wise': [[job('a1', 'Lead Data Analyst - FinCrime Operations', posted=5,
                                             where='London, United Kingdom')]]})
        beta = make('beta', {'wise': [[job('b1', 'Lead Data Analyst - FinCrime Operations', posted=7,
                                            where='London, England, United Kingdom')]]})
        _, d = run_many({}, alpha, beta)
        self.assertEqual([p for p, _, _ in d.pushed], ['alpha'])

    def test_the_same_posting_time_to_the_second_is_one_job_wherever_its_place(self):
        stamp = '2026-11-11T14:47:55.123Z'
        alpha = make('alpha', {'wise': [[dict(job('a1', 'Staff Data Analyst', where='London, England'), postedAt=stamp,
                                              dateOnly=False)]]})
        beta = make('beta', {'wise': [[dict(job('b1', 'Staff Data Analyst', where='Remote, UK'),
                                            postedAt='2026-11-11T14:47:55Z', dateOnly=False)]]})
        _, d = run_many({}, alpha, beta)
        self.assertEqual(len(d.pushed), 1)

    def test_the_same_title_in_two_cities_stays_two_jobs(self):
        alpha = make('alpha', {'wise': [[job('a1', 'Data Analyst', where='London, UK')]]})
        beta = make('beta', {'wise': [[job('b1', 'Data Analyst', where='Tallinn, Estonia')]]})
        _, d = run_many({}, alpha, beta)
        self.assertEqual(len(d.pushed), 2)


class Platforms(unittest.TestCase):
    def test_names_aliases_and_keys_are_read_and_an_unknown_one_is_skipped(self):
        notes, skipped = [], []
        keys = many.read_platforms(['Greenhouse', 'hiring cafe', 'YC', 'otta', 'nope', 'greenhouse'], notes, skipped)
        self.assertEqual(keys, ['greenhouse', 'hiringcafe', 'ycombinator', 'welcometothejungle'])
        self.assertEqual([t for t, _ in skipped], ['nope'])

    def test_empty_or_all_is_every_board(self):
        self.assertEqual(many.read_platforms([], [], []), many.known())
        self.assertEqual(many.read_platforms('all', [], []), many.known())
        self.assertEqual(many.read_platforms('Lever, Workday', [], []), ['lever', 'workday'])

    def test_no_board_that_can_be_read_searches_every_board_and_says_so(self):
        notes = []
        self.assertEqual(many.read_platforms(['monster jobs'], notes, []), many.known())
        self.assertTrue(notes)

    def test_only_the_platforms_asked_for_run(self):
        alpha = make('alpha', {'acme': [[job('a1')]]})
        beta = make('beta', {'zeta': [[job('b1')]]})
        run, d = run_many({'platforms': ['beta']}, alpha, beta)
        self.assertEqual(list(run.runs), ['beta'])
        self.assertEqual([p for p, _, _ in d.pushed], ['beta'])


class Routing(unittest.TestCase):
    def test_a_board_link_searches_that_board_and_platforms_it_is_not_a_link_of_sit_out(self):
        alpha = make('alpha', {'acme': [[job('a1')]], 'other': [[job('a9')]]})
        beta = make('beta', {'zeta': [[job('b1')]]})
        run, d = run_many({'searchTerms': ['alpha://acme']}, alpha, beta)
        self.assertEqual([(p, j) for p, j, _ in d.pushed], [('alpha', 'a1')])
        self.assertIn('beta', run.sat_out)
        self.assertEqual(run.final['boards'][1]['status'], 'skipped')

    def test_words_with_a_link_still_search_every_platform(self):
        alpha = make('alpha', {'acme': [[job('a1', 'Data Analyst')]], 'other': [[job('a9', 'Data Analyst')]]})
        beta = make('beta', {'zeta': [[job('b1', 'Data Analyst')]]})
        run, d = run_many({'searchTerms': ['alpha://acme', 'data analyst']}, alpha, beta)
        self.assertEqual(run.sat_out, {})
        self.assertIn(('beta', 'b1'), [(p, j) for p, j, _ in d.pushed])

    def test_a_link_of_no_board_is_skipped_not_searched_as_words(self):
        alpha = make('alpha', {'acme': [[job('a1')]]})
        run, d = run_many({'searchTerms': ['https://unknown.example/jobs/1']}, alpha)
        self.assertEqual([t for t, _ in run.skipped], ['https://unknown.example/jobs/1'])
        self.assertEqual(run.runs['alpha'].cfg.search_terms, [])

    def test_a_company_with_no_board_on_a_platform_keeps_that_platform_out(self):
        alpha = make('alpha', {'acme': [[job('a1')]], 'other': [[job('a9')]]})
        beta = make('beta', {'zeta': [[job('b1')]]})
        run, d = run_many({'companies': ['acme']}, alpha, beta)
        self.assertEqual([(p, j) for p, j, _ in d.pushed], [('alpha', 'a1')])
        self.assertIn('beta', run.sat_out)
        self.assertNotIn('None of the companies could be read', run.final['message'])

    def test_job_links_are_checked_by_their_own_platform_in_the_order_typed(self):
        alpha = make('alpha', {'acme': [[job('a1')]]})
        beta = make('beta', {'zeta': [[job('b1')]]})
        run, d = run_many({'jobUrls': ['beta://zeta/b1', 'alpha://acme/a1', 'alpha://acme/gone', 'https://x.example/1']},
                          alpha, beta)
        self.assertEqual(run.mode, 'check')
        self.assertEqual([(p, j) for p, j, _ in d.pushed], [('beta', 'b1'), ('alpha', 'a1'), ('alpha', 'gone')])
        self.assertIn('2 open, 0 closed, 1 not found', run.final['message'])
        self.assertEqual([t for t, _ in run.skipped], ['https://x.example/1'])


class Failures(unittest.TestCase):
    def test_a_platform_that_breaks_leaves_the_others_delivering(self):
        alpha = make('alpha', {'acme': [[job('a1')]]})
        beta = make('beta', {'zeta': 'down'})
        run, d = run_many({}, alpha, beta)
        self.assertEqual([p for p, _, _ in d.pushed], ['alpha'])
        self.assertEqual(run.final['status'], 'partial')
        self.assertIn('Could not read Beta (HTTP 500)', run.final['message'])

    def test_nothing_read_anywhere_fails(self):
        run, d = run_many({}, make('alpha', {'acme': 'down'}), make('beta', {'zeta': 'down'}))
        self.assertEqual(run.final['status'], 'failed')
        self.assertFalse(run.ran())

    def test_the_spending_limit_stops_the_delivery(self):
        alpha = make('alpha', {'acme': [[job(f'a{i}', f'Engineer {i}', posted=i) for i in range(5)]]})
        run, d = run_many({}, alpha, delivery=Delivered(budget=2))
        self.assertEqual(d.rows, 2)
        self.assertEqual(run.final['jobsSaved'], 2)
        self.assertIn('Stopped early: your spending limit', run.final['message'])


class Details(unittest.TestCase):
    def test_each_platform_reads_the_details_of_its_own_chosen_jobs(self):
        alpha = make('alpha', {'acme': [[job('a1', 'Engineer A', posted=1), job('a2', 'Engineer B', posted=9)]]})
        beta = make('beta', {'zeta': [[job('b1', 'Engineer C', posted=0)]]})
        run, d = run_many({'includeDetails': True, 'maxJobs': 2}, alpha, beta)
        self.assertEqual(d.pushed, [('beta', 'b1', 'job-details'), ('alpha', 'a1', 'job-details')])
        self.assertEqual(run.runs['alpha'].details_wanted, 1)


class Budget(unittest.TestCase):
    def test_a_board_still_reading_at_its_limit_stops_and_its_jobs_so_far_are_used(self):
        class Slow(Base):
            async def pages(self, http, session, term, days):
                yield [dict(job('s1', 'Engineer S'), jobUrl='https://slow.example/1')]
                await asyncio.sleep(5)
                yield [dict(job('s2', 'Engineer T'), jobUrl='https://slow.example/2')]
        slow = type('Slow', (Slow,), {'key': 'slow', 'name': 'Slow', 'actor': 'slow', 'sites': {'s': []}})
        fast = make('fast', {'f': [[job('f1', 'Engineer F')]]})
        started = time.monotonic()
        with patch.object(many, 'read_seconds', lambda value, notes: 0.3):
            run, d = run_many({}, slow, fast)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(sorted(j for _, j, _ in d.pushed), ['f1', 's1'])
        self.assertEqual(run.final['status'], 'partial')
        self.assertIn('Slow (stopped at 0.3 s, secondsPerBoard)', run.final['message'])
        self.assertNotIn('Stopped early', run.final['message'])

    def test_the_limit_reads_what_people_type(self):
        notes = []
        self.assertEqual(many.read_seconds(None, notes), many.BOARD_SECONDS)
        self.assertIsNone(many.read_seconds('0', notes))
        self.assertEqual(many.read_seconds('90s', notes), 90)
        self.assertEqual(many.read_seconds(5, notes), 30)
        self.assertEqual(many.read_seconds('soon', notes), many.BOARD_SECONDS)
        self.assertEqual(len(notes), 2)


class Boards(unittest.TestCase):
    class Manager:
        def __init__(self, limit):
            self.limit = Decimal(limit)

        def get_pricing_info(self):
            return type('Info', (), {'per_event_prices': {'board': Decimal('0.005'), 'job': Decimal('0.003')}})()

        def get_max_total_charge_usd(self):
            return self.limit

        def calculate_total_charged_amount(self):
            return Decimal(0)

    def charged(self, limit, boards=4):
        classes = [make(k, {f'{k}co': [[job(f'{k}1')]]}) for k in ('alpha', 'beta', 'gamma', 'delta')[:boards]]
        delivery = Delivered()
        delivery.cm = self.Manager(limit)
        calls = []

        async def charge(event_name, count):
            calls.append((event_name, count))
        with patch.object(many, 'PLATFORMS', {c.key: c for c in classes}), patch.object(many.Actor, 'charge', charge):
            run = many.Many({}, 'job-boards-search', delivery)
            ok = asyncio.run(many.charge_boards(run, delivery, 'job'))
        return ok, calls, run

    def test_one_board_event_per_board_searched(self):
        ok, calls, run = self.charged('inf')
        self.assertTrue(ok)
        self.assertEqual(calls, [('board', 4)])

    def test_boards_past_the_maximum_cost_sit_out_and_room_for_a_job_is_kept(self):
        ok, calls, run = self.charged('0.02')     # 0.02 - 0.003 for a job leaves 3 boards at 0.005
        self.assertEqual(calls, [('board', 3)])
        self.assertEqual(list(run.runs), ['alpha', 'beta', 'gamma'])
        self.assertIn('delta', run.sat_out)

    def test_a_maximum_cost_under_one_board_and_one_job_searches_nothing(self):
        ok, calls, run = self.charged('0.007')
        self.assertFalse(ok)
        self.assertEqual(calls, [])


class Apart(unittest.TestCase):
    def test_a_platform_apart_searches_in_a_process_of_its_own_and_its_rows_come_back_in_order(self):
        beta = make('beta', {'zeta': [[job('b1', 'Engineer B', posted=2)]]})
        with patch.object(many, 'APART', ('apartsite',)):
            run, d = run_many({'includeDetails': True}, ApartSite, beta)
        self.assertEqual(d.pushed, [('apartsite', 'w1', 'job-details'), ('beta', 'b1', 'job-details'),
                                    ('apartsite', 'w2', 'job-details')])
        board = run.final['boards'][0]
        self.assertEqual((board['matchingJobs'], board['jobsSaved'], board['status']), (2, 2, 'done'))
        self.assertIn('Found and saved 3 matching jobs on 2 boards', run.final['message'])

    def test_a_worker_that_breaks_costs_its_board_and_the_run_goes_on(self):
        beta = make('beta', {'zeta': [[job('b1', 'Engineer B')]]})
        with patch.object(many, 'APART', ('brokenapart',)):
            run, d = run_many({}, BrokenApart, beta)
        self.assertEqual([p for p, _, _ in d.pushed], ['beta'])
        self.assertEqual(run.final['status'], 'partial')
        self.assertIn('Could not read Broken (its search stopped', run.final['message'])

    def test_alone_it_searches_in_this_process(self):
        with patch.object(many, 'APART', ('apartsite',)):
            run, d = run_many({}, ApartSite)
        self.assertEqual(run.apart(), [])
        self.assertEqual([j for _, j, _ in d.pushed], ['w1', 'w2'])


class Parallel(unittest.TestCase):
    def test_platforms_are_read_at_the_same_time(self):
        classes = [make(k, {f'{k}co': [[job(f'{k}1')]]}, delay=0.3) for k in ('alpha', 'beta', 'gamma', 'delta')]
        started = time.monotonic()
        run, d = run_many({}, *classes)
        took = time.monotonic() - started
        self.assertEqual(d.rows, 4)
        self.assertLess(took, 0.3 * 2, f'4 platforms of 0.3 s each took {took:.2f} s: not read at once')


if __name__ == '__main__':
    unittest.main()
