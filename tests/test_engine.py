"""One search from end to end on a fake platform: which pages are read, what is kept, what is charged."""
import asyncio
import time
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


def search(platform, raw, boards=None, delivery=None):
    @asynccontextmanager
    async def no_client(actor, connections=64):
        yield None
    keys = boards or list(platform.sites)
    run = engine.Run(platform, parse_input(raw, platform), [Board(k, k.split('/')[0]) for k in keys], None,
                     delivery or Delivered())
    with patch.object(engine, 'client', no_client), patch.object(engine, 'safe_status', _quiet), \
            patch.object(engine, 'seconds_left_in_run', lambda: None):
        asyncio.run(run.search())
    return run


async def _quiet(message):
    return None


class Linked(Fake):
    """Links x/<company>/<job>; company keys are read in any case, as on SmartRecruiters and Workday."""

    def read_link(self, text):
        parts = text.split('/')
        return (parts[1], parts[2]) if len(parts) == 3 and parts[0] == 'x' else None

    async def open(self, http, board):
        return board.key.lower() if board.key.lower() in self.sites else None

    async def lookup(self, http, session, job_id):
        site = self.sites[session]
        if site == 'down':
            raise BoardError('HTTP 500')
        found = [j for page in site for j in page if j['id'] == job_id]
        return ('open', dict(found[0], jobUrl=f'https://x/{job_id}')) if found else ('not found', None)


def check(platform, links):
    @asynccontextmanager
    async def no_client(actor, connections=64):
        yield None
    run = engine.Run(platform, parse_input({'jobUrls': links}, platform), [Board(k, k) for k in platform.sites], None,
                     Delivered())
    with patch.object(engine, 'client', no_client), patch.object(engine, 'safe_status', _quiet), \
            patch.object(engine, 'seconds_left_in_run', lambda: None):
        asyncio.run(run.check_links())
    return run


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

    def test_jobs_the_platform_tells_apart_beyond_title_and_place_are_all_kept(self):
        # lever live review 2026-11-03: one title at three Edmonton malls came back as one row
        class Stores(Fake):
            def copy_key(self, job):
                return (*super().copy_key(job), job.get('team'))
        jobs = [{**job('1'), 'team': 'Kingsway'}, {**job('2'), 'team': 'West Edmonton'}, {**job('3'), 'team': 'Kingsway'}]
        run = search(Stores({'acme': [jobs]}), {})
        self.assertEqual((run.delivery.rows, run.duplicates), (2, 1))
        self.assertEqual(search(Fake({'acme': [jobs]}), {}).delivery.rows, 1)    # the default: title and place

    def test_two_words_finding_one_job_is_one_row(self):
        site = [[job('1', title='Data Engineer')]]
        run = search(Fake({'a': site}), {'searchTerms': ['data', 'engineer']})
        self.assertEqual(run.delivery.rows, 1)

    def test_a_missing_board_and_a_failing_board_are_reported(self):
        run = search(Fake({'a': 'missing', 'b': 'down', 'c': [[job('1')]]}), {})
        self.assertEqual((run.boards_missing, run.delivery.rows, len(run.board_errors)), (['a'], 1, 1))

    def test_a_board_with_a_record_of_an_unexpected_shape_is_one_failed_board_not_a_failed_run(self):
        # live review 2026-11-03: one record {'location': 'Remote'} on one board ended the run with exit 91, OUTPUT
        # left 'running' and 0 rows, although the other boards were fine
        class Odd(Fake):
            async def pages(self, http, session, term, days):
                if session == 'odd':
                    raise AttributeError("'str' object has no attribute 'get'")
                async for page in super().pages(http, session, term, days):
                    yield page
        run = search(Odd({'odd': [[job('x')]], 'a': [[job('1')]], 'b': [[job('2', 'Analyst')]]}), {})
        self.assertEqual(sorted(i for i, _ in run.delivery.pushed), ['1', '2'])
        self.assertEqual(run.board_errors, [{'board': 'odd', 'error': "AttributeError: 'str' object has no attribute 'get'"}])
        self.assertTrue(run.ran())

    def test_details_of_an_unexpected_shape_leave_the_job_without_them(self):
        class Odd(Fake):
            async def details(self, http, session, job):
                raise TypeError('odd details')
        run = search(Odd({'a': [[job('1')]]}), {'includeDetails': True})
        self.assertEqual((run.delivery.pushed, run.board_errors[0]['error']), ([('1', 'job')], 'TypeError: odd details'))

    def test_details_are_charged_only_when_they_came(self):
        run = search(Fake({'a': [[job('ok'), job('nodetails', title='Analyst')]]}), {'includeDetails': True})
        self.assertEqual(dict(run.delivery.pushed), {'ok': 'job-details', 'nodetails': 'job'})


class Links(unittest.TestCase):
    def test_links_to_one_company_in_any_case_are_all_checked(self):
        # live review 2026-11-03: three open Bosch links typed boschgroup, BoschGroup and BOSCHGROUP gave 1 row and
        # "2 links could not be checked (SmartRecruiters did not answer)" after 3 answered calls
        run = check(Linked({'acme': [[job('1'), job('2', 'Analyst'), job('3', 'Cook')]]}), ['x/acme/1', 'x/Acme/2', 'x/ACME/3'])
        self.assertEqual(([i for i, _ in run.delivery.pushed], run.links['couldNotCheck']), (['1', '2', '3'], 0))
        self.assertEqual(run.links['open'], 3)

    def test_a_board_that_fails_leaves_its_links_unchecked(self):
        run = check(Linked({'acme': 'down', 'other': [[job('9')]]}), ['x/acme/1', 'x/ACME/2', 'x/other/9'])
        self.assertEqual(([i for i, _ in run.delivery.pushed], run.links['couldNotCheck']), (['9'], 2))


    def test_a_link_answer_of_an_unexpected_shape_is_a_link_not_checked(self):
        class Odd(Linked):
            async def lookup(self, http, session, job_id):
                if session == 'odd':
                    raise KeyError('active')
                return await super().lookup(http, session, job_id)
        run = check(Odd({'odd': [[job('1')]], 'acme': [[job('2')]]}), ['x/odd/1', 'x/acme/2'])
        self.assertEqual(([i for i, _ in run.delivery.pushed], run.links['couldNotCheck']), (['2'], 1))
class Status(unittest.TestCase):
    def test_a_platform_that_read_part_of_what_it_lists_makes_the_run_partial(self):
        run = search(Fake({'a': [[job('1')]]}, incomplete=True), {})
        self.assertEqual((run.status(), run.delivery.rows), ('partial', 1))
        self.assertEqual(search(Fake({'a': [[job('1')]]}), {}).status(), 'done')
        self.assertEqual(search(Fake({'a': 'down'}), {}).status(), 'failed')


class FailedDetails(unittest.TestCase):
    def test_details_that_fail_do_not_fail_a_run_that_read_its_boards(self):
        class NoDetails(Fake):
            async def details(self, http, session, job):
                raise BoardError('HTTP 429')
        run = search(NoDetails({'a': [[job('1'), job('2', 'Analyst'), job('3', 'Cook')]]}), {'includeDetails': True})
        self.assertEqual((run.delivery.rows, len(run.board_errors), run.ran()), (3, 3, True))
        self.assertEqual(run.output('done')['status'], 'done')


class Paging(unittest.TestCase):
    def pages(self, n=3):
        return [[job(f'{p}-{i}', title=f'Engineer {p} {i}', posted=p * 10) for i in range(2)] for p in range(n)]

    def test_a_newest_first_board_stops_once_it_cannot_make_the_cut(self):
        platform = Fake({'a': [[job('x', 'Engineer X', 5), job('y', 'Engineer Y', 5)]], 'b': self.pages()}, newest_first=True, concurrency=1)
        search(platform, {'maxJobs': 2})
        self.assertEqual([r for r in platform.read if r[0] == 'b'], [('b', None, 1), ('b', None, 2)])

    def test_a_count_cut_short_says_at_least(self):
        # live review 2026-11-03: the same empty search said 4,593 matching jobs with maxJobs 5, about 13,000 with 100
        # and 226,371 with every page read: boards that stop early never count what they held below the cut
        def sites():
            return {'a': [[job('x', 'Engineer X', 5), job('y', 'Engineer Y', 5)]], 'b': self.pages()}
        cut = search(Fake(sites(), newest_first=True, concurrency=1), {'maxJobs': 2})
        self.assertIn('Found at least 6 matching jobs at 2 companies on 2 Fake boards, saved the 2 newest', cut.message())
        every = search(Fake(sites(), newest_first=True, concurrency=1), {'maxJobs': 0})
        self.assertIn('Found and saved 8 matching jobs', every.message())
        window = search(Fake({'a': self.pages()}, newest_first=True), {'postedWithin': '3 days', 'maxJobs': 0})
        self.assertNotIn('at least', window.message())

    def test_a_spending_limit_on_every_match_says_how_many_matched(self):
        # f-everything, 2026-11-03: maxJobs 0 stopped by the limit said 'Found and saved 20,000 matching jobs' when
        # 222,724 matched
        class Limited(Delivered):
            def can_pay(self, event):
                return not self.limit

            async def push(self, rows, event):
                done = await super().push(rows[:2], event)
                self.limit = True
                return done

        @asynccontextmanager
        async def no_client(actor, connections=64):
            yield None
        platform = Fake({'a': self.pages()})
        run = engine.Run(platform, parse_input({'maxJobs': 0}, platform), [Board('a', 'a')], None, Limited())
        with patch.object(engine, 'client', no_client), patch.object(engine, 'safe_status', _quiet), \
                patch.object(engine, 'seconds_left_in_run', lambda: None):
            asyncio.run(run.search())
        self.assertIn('Found 6 matching jobs at 1 company on 1 Fake board, saved the 2 newest', run.message())

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


class TimeLimit(unittest.TestCase):
    def test_a_call_still_out_at_the_time_limit_does_not_hold_the_delivery(self):
        class Slow(Fake):
            async def pages(self, http, session, term, days):
                if session == 'slow':
                    await asyncio.sleep(5)          # an answer that would come after the platform stops the run
                async for page in super().pages(http, session, term, days):
                    yield page

        @asynccontextmanager
        async def no_client(actor, connections=64):
            yield None
        platform = Slow({'fast': [[job('1')]], 'slow': [[job('2', 'Analyst')]]})
        run = engine.Run(platform, parse_input({}, platform), [Board('fast', 'fast'), Board('slow', 'slow')], None,
                         Delivered())
        started = time.monotonic()
        with patch.object(engine, 'client', no_client), patch.object(engine, 'safe_status', _quiet), \
                patch.object(engine, 'seconds_left_in_run', lambda: engine.RUN_SAFETY_MARGIN_S + 0.3):
            asyncio.run(run.search())
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(([i for i, _ in run.delivery.pushed], run.stop), (['1'], 'time'))
        self.assertIn('time limit', run.message())


class CutRuns(unittest.TestCase):
    def test_a_cut_details_phase_still_delivers_the_listings_descriptions(self):
        # live review 2026-11-03: a time-limited includeDetails run delivered rows without their descriptions
        class Listed(Fake):
            async def details(self, http, session, job):
                await asyncio.sleep(5)       # the per-job call the time limit cancels
                return job

        @asynccontextmanager
        async def no_client(actor, connections=64):
            yield None

        def left():
            return 1000 if run.phase != 'details' else engine.RUN_SAFETY_MARGIN_S
        platform = Listed({'a': [[{**job('1'), 'description': 'Full text from the list'}]]})
        run = engine.Run(platform, parse_input({'includeDetails': True}, platform), [Board('a', 'a')], None, Delivered())
        with patch.object(engine, 'client', no_client), patch.object(engine, 'safe_status', _quiet), \
                patch.object(engine, 'seconds_left_in_run', left):
            asyncio.run(run.search())
        self.assertEqual([e for _, e in run.delivery.pushed], [engine.DETAILS_EVENT])
        self.assertEqual(run.stop, 'time')

    def test_boards_cancelled_at_the_time_limit_are_not_counted_as_read(self):
        # live review 2026-11-03: cancelled boards counted as read, so a down platform reported success
        class Slow(Fake):
            async def pages(self, http, session, term, days):
                if session == 'slow':
                    await asyncio.sleep(5)
                async for page in super().pages(http, session, term, days):
                    yield page

        @asynccontextmanager
        async def no_client(actor, connections=64):
            yield None
        platform = Slow({'fast': [[job('1')]], 'slow': [[job('2')]]})
        run = engine.Run(platform, parse_input({}, platform), [Board('fast', 'fast'), Board('slow', 'slow')], None,
                         Delivered())
        with patch.object(engine, 'client', no_client), patch.object(engine, 'safe_status', _quiet), \
                patch.object(engine, 'seconds_left_in_run', lambda: engine.RUN_SAFETY_MARGIN_S + 0.3):
            asyncio.run(run.search())
        self.assertEqual((run.boards_read, run.ran(), run.stop), (1, True, 'time'))

    def test_a_platform_that_never_answers_fails_instead_of_reporting_success(self):
        # live review 2026-11-03: every board cancelled at the time limit must not read as a done run with no match
        class Slow(Fake):
            async def pages(self, http, session, term, days):
                await asyncio.sleep(5)
                yield []

        @asynccontextmanager
        async def no_client(actor, connections=64):
            yield None
        platform = Slow({'a': [[job('1')]], 'b': [[job('2')]]})
        run = engine.Run(platform, parse_input({}, platform), [Board('a', 'a'), Board('b', 'b')], None, Delivered())
        with patch.object(engine, 'client', no_client), patch.object(engine, 'safe_status', _quiet), \
                patch.object(engine, 'seconds_left_in_run', lambda: engine.RUN_SAFETY_MARGIN_S + 0.3):
            asyncio.run(run.search())
        self.assertEqual((run.boards_read, run.ran(), run.status()), (0, False, 'failed'))


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


class Descriptions(unittest.TestCase):
    def test_a_platform_hears_the_words_of_a_description_search(self):
        class Listening(Fake):
            heard = []

            async def pages(self, http, session, term, days):
                Listening.heard.append(self.description_terms)
                async for page in super().pages(http, session, term, days):
                    yield page
        search(Listening({'a': [[job('1')]]}), {'searchTerms': ['engineer'], 'searchDescriptions': True})
        search(Listening({'a': [[job('1')]]}), {'searchTerms': ['engineer']})
        self.assertEqual(Listening.heard, [('engineer',), ()])


class Summary(unittest.TestCase):
    """The OUTPUT record and the status message say what was read, so a lost board is never silent."""

    def test_a_board_that_fails_is_not_counted_as_read_and_makes_the_run_partial(self):
        run = search(Fake({'a': 'down', 'b': [[job('1')]], 'c': [[job('2', 'Two')]]}), {})
        self.assertIn('on 2 Fake boards', run.message())
        self.assertIn('1 Fake board could not be read: boardsWithErrors in OUTPUT says why.', run.message())
        self.assertEqual(run.status(), 'partial')

    def test_a_missing_board_is_not_counted_as_read(self):
        run = search(Fake({'a': 'missing', 'b': [[job('1')]]}), {})
        self.assertIn('on 1 Fake board in', run.message())
        self.assertEqual(run.status(), 'done')

    def test_companies_that_all_have_no_board_read_nothing_and_say_so(self):
        class Named(Fake):
            def guess_board(self, name):
                return name
        run = search(Named({'nosuch': 'missing'}), {'companies': ['nosuch']})
        self.assertRegex(run.message(), r'^Nothing was read in \d+ s\. No Fake board for: nosuch\.$')
        self.assertEqual(run.status(), 'done')

    def test_details_that_fail_do_not_fail_the_run_that_delivered(self):
        class Flaky(Fake):
            async def details(self, http, session, job):
                if job['id'] == 'bad':
                    raise BoardError('HTTP 502')
                return await super().details(http, session, job)
        run = search(Flaky({'a': [[job('ok'), job('bad', 'Analyst')]]}), {'includeDetails': True})
        self.assertTrue(run.ran(), 'two rows were delivered and charged, so the run must not end FAILED')
        self.assertEqual((run.status(), run.delivery.rows), ('partial', 2))
        self.assertIn('The details of 1 job could not be read, so they were saved without them.', run.message())

    def test_the_record_written_at_the_start_says_it_is_reading(self):
        platform = Fake({'a': [[job('1')]], 'b': [[job('2')]]})
        run = engine.Run(platform, parse_input({}, platform), [Board('a', 'a'), Board('b', 'b')], None, Delivered())
        self.assertEqual(run.output('running')['message'], 'Reading 2 Fake boards.')

    def test_a_spending_limit_says_how_many_matched_and_how_many_were_saved(self):
        class Limited(Delivered):
            async def push(self, rows, event):
                self.limit = True
                return await super().push(rows[:2], event)
        run = search(Fake({'a': [[job('1', 'One'), job('2', 'Two')]], 'b': [[job('3', 'Three')]]}), {'maxJobs': 0},
                     delivery=Limited())
        self.assertIn('Found 3 matching jobs at 2 companies on 2 Fake boards, saved the 2 newest in', run.message())
        self.assertIn('Stopped early: your spending limit', run.message())
        self.assertEqual(run.status(), 'partial')


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



class Feed(unittest.TestCase):
    """A platform with one feed of every company's jobs reads the feed when no company is named."""
    class Rows(Delivered):
        def __init__(self):
            super().__init__()
            self.saved = []

        async def push(self, rows, event):
            self.saved += rows
            return await super().push(rows, event)

    class WithFeed(Fake):
        def search_all(self, boards, cfg):
            self.settings = cfg
            return [Board('*', 'the Fake feed')]

    def feed_search(self, raw):
        jobs = [dict(job('1'), company='Acme', companySlug='acme', companyWebsite='https://acme.example'),
                dict(job('2'), company='Beta', companySlug=None, companyWebsite=None)]
        platform = self.WithFeed({'*': [jobs], 'acme': [[job('a1', 'Analyst')]], 'beta': [[job('b1', 'Cook')]]})

        @asynccontextmanager
        async def no_client(actor, connections=64):
            yield None
        boards = [Board('acme', 'Acme'), Board('beta', 'Beta')]
        cfg = parse_input(raw, platform, engine.name_index(platform, boards))
        run = engine.Run(platform, cfg, boards, None, self.Rows())
        with patch.object(engine, 'client', no_client), patch.object(engine, 'safe_status', _quiet), \
                patch.object(engine, 'seconds_left_in_run', lambda: None):
            asyncio.run(run.search())
        return platform, run

    def test_only_the_feed_is_read_and_each_job_keeps_its_company(self):
        platform, run = self.feed_search({})
        self.assertEqual({r[0] for r in platform.read}, {'*'})
        rows = {r['jobId']: r for r in run.delivery.saved}
        self.assertEqual(sorted(rows), ['1', '2'])              # one title at one place, two companies: two jobs
        self.assertEqual((rows['1']['company'], rows['1']['companySlug'], rows['1']['companyWebsite']),
                         ('Acme', 'acme', 'https://acme.example'))
        self.assertEqual((rows['2']['company'], rows['2']['companySlug']), ('Beta', None))
        self.assertEqual(len(run.companies_matched), 2)
        self.assertIn('on the Fake feed', run.message())

    def test_a_note_the_platform_adds_while_reading_is_in_the_message(self):
        class Noting(self.WithFeed):
            async def pages(self, http, session, term, days):
                self.settings.notes.append('The feed was late, so the biggest boards were read.')
                async for page in super().pages(http, session, term, days):
                    yield page
        platform = Noting({'*': [[job('1')]]})

        @asynccontextmanager
        async def no_client(actor, connections=64):
            yield None
        run = engine.Run(platform, parse_input({'location': ['remote']}, platform), [Board('acme', 'Acme')], None, self.Rows())
        with patch.object(engine, 'client', no_client), patch.object(engine, 'safe_status', _quiet), \
                patch.object(engine, 'seconds_left_in_run', lambda: None):
            asyncio.run(run.search())
        message = run.message()
        self.assertIn('The feed was late, so the biggest boards were read.', message)
        self.assertNotIn('Read "remote" in location', message)      # notes about the input stay in OUTPUT only

    def test_some_of_the_boards_are_boards_not_a_feed(self):
        class Biggest(Fake):
            def search_all(self, boards, cfg):
                return boards[:1]
        platform = Biggest({'acme': [[job('a1', 'Analyst')]], 'beta': [[job('b1', 'Cook')]]})

        @asynccontextmanager
        async def no_client(actor, connections=64):
            yield None
        run = engine.Run(platform, parse_input({}, platform), [Board('acme', 'Acme'), Board('beta', 'Beta')], None, self.Rows())
        with patch.object(engine, 'client', no_client), patch.object(engine, 'safe_status', _quiet), \
                patch.object(engine, 'seconds_left_in_run', lambda: None):
            asyncio.run(run.search())
        self.assertEqual(({r[0] for r in platform.read}, run.feed), ({'acme'}, None))
        self.assertIn('on 1 Fake board', run.message())

    def test_a_named_company_reads_its_own_board(self):
        platform, run = self.feed_search({'companies': ['acme']})
        self.assertEqual({r[0] for r in platform.read}, {'acme'})
        self.assertEqual([r['jobId'] for r in run.delivery.saved], ['a1'])
        self.assertIsNone(run.feed)
        self.assertEqual(platform.settings.companies, ['acme'])      # the platform sees the settings on every search


class Links(unittest.TestCase):
    def test_a_closed_job_can_name_its_company(self):
        class Linker(Fake):
            def read_link(self, text):
                return ('*', text.rsplit('/', 1)[1]) if text.startswith('https://x/') else None

            async def open(self, http, board):
                return {}

            async def lookup(self, http, session, job_id):
                if job_id == 'gone':
                    return 'closed', {'company': 'Acme', 'companySlug': 'acme', 'companyWebsite': 'https://acme.example'}
                return 'closed', None

        platform = Linker({})
        saved = []

        class Rows(Delivered):
            async def push(self, rows, event):
                saved.extend(rows)
                return await super().push(rows, event)

        @asynccontextmanager
        async def no_client(actor, connections=64):
            yield None
        cfg = parse_input({'jobUrls': ['https://x/gone', 'https://x/other']}, platform)
        run = engine.Run(platform, cfg, [], None, Rows())
        with patch.object(engine, 'client', no_client), patch.object(engine, 'safe_status', _quiet):
            asyncio.run(run.check_links())
        rows = {r['jobId']: r for r in saved}
        self.assertEqual((rows['gone']['company'], rows['gone']['companySlug'], rows['gone']['status']), ('Acme', 'acme', 'closed'))
        self.assertEqual((rows['other']['company'], rows['other']['companySlug']), ('*', '*'))


    def test_one_job_pasted_in_two_link_forms_is_one_row(self):
        class Linker(Fake):
            def read_link(self, text):
                key, job_id = text.removeprefix('https://x/').split('/')
                return key, job_id

            async def open(self, http, board):
                return {}

            async def lookup(self, http, session, job_id):
                return 'open', {'id': job_id, 'title': 'Engineer', 'jobUrl': f'https://x/j/{job_id}'}

        platform = Linker({})
        delivered = Delivered()

        @asynccontextmanager
        async def no_client(actor, connections=64):
            yield None
        cfg = parse_input({'jobUrls': ['https://x/acme/A1', 'https://x/*/A1', 'https://x/acme/B2']}, platform)
        run = engine.Run(platform, cfg, [], None, delivered)
        with patch.object(engine, 'client', no_client), patch.object(engine, 'safe_status', _quiet):
            asyncio.run(run.check_links())
        self.assertEqual([i for i, _ in delivered.pushed], ['A1', 'B2'])
        self.assertEqual(run.links['open'], 3)


if __name__ == '__main__':
    unittest.main()
