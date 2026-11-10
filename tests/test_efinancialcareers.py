"""eFinancialCareers: links, list jobs, pay, places and filters sent to the site, the newest-first merge of its PAID and
UNPAID lists, the relevance read of a word, opening a job for its first posting, expired jobs, the poster's profile,
checked links and a whole engine run, on answers recorded from the site's own APIs on 2026-11-10 (trimmed)."""
import asyncio
import gzip
import json
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from ats_jobs import engine
from ats_jobs.inputs import parse_input
from ats_jobs.platforms import efinancialcareers as efc

RECORDED = json.loads(gzip.open(Path(__file__).parent / 'data' / 'efinancialcareers_2026-11-10.json.gz').read())
PAID, UNPAID = RECORDED['paid'], RECORDED['unpaid']
DETAIL = RECORDED['detail']
NOW = datetime(2026, 11, 10, 11, 0, tzinfo=timezone.utc)


def at(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def record(job_id: str, title: str, minutes_ago: float, kind: str = 'PAID', **more) -> dict:
    """A list record in the shape of the recorded ones."""
    base = dict(PAID[0])
    base.update({'id': f'k{job_id}', 'jobId': job_id, 'title': title, 'postedDate': at(minutes_ago), 'jobPaymentType': kind,
                 'detailsPageUrl': f'/jobs-United_Kingdom-London-{title.replace(" ", "_")}.id{job_id}',
                 'jobLocation': {'displayName': 'London, United Kingdom', 'city': 'London', 'state': 'England',
                                 'country': 'United Kingdom', 'region': 'Europe'},
                 'companyName': 'Acme Capital', 'clientBrandName': 'Acme Capital'})
    base.update(more)
    return base


def page_record(first_posted: str, posted: str | None = None, company: str = 'acct1', brand: str = 'brand1') -> dict:
    data = dict(DETAIL['data'])
    data.update({'allocation_date': first_posted, 'posted_date': posted or first_posted, 'company_id': company,
                 'brand': {**DETAIL['data']['brand'], 'brand_id': brand}})
    return {'data': data, 'meta': DETAIL['meta']}


class Answer:
    def __init__(self, status: int, body=None):
        self.status_code, self.body = status, body
        self.text = json.dumps(body) if body is not None else ''
        self.content = self.text.encode()

    def json(self):
        if self.body is None:
            raise ValueError('no body')
        return self.body


class Site:
    """A fake eFinancialCareers. The search lists records in the order given, filtered by the payment kind, words (title
    matches first, as the site ranks them) and company; job pages, profiles and apply links come from dicts by id."""

    def __init__(self, records, pages=None, profiles=None, totals=None, applies=None):
        self.records, self.pages, self.profiles = records, pages or {}, profiles or {}
        self.totals, self.applies = totals or {}, applies or {}
        self.calls: list = []

    def search(self, params: dict) -> Answer:
        found = list(self.records)
        if params.get('filters.jobPaymentType'):
            found = [r for r in found if r.get('jobPaymentType') == params['filters.jobPaymentType']]
        if params.get('filters.clientBrandNameFilter'):
            found = [r for r in found if r.get('companyName') == params['filters.clientBrandNameFilter']]
        if params.get('q'):
            word = params['q'].lower()
            titled = [r for r in found if word in r['title'].lower()]
            found = titled + [r for r in found if r not in titled and word in (r.get('jobSummary') or '').lower()]
        key = params.get('location')
        total = self.totals.get(key, len(found)) if key else len(found)
        page, size = int(params.get('page', 1)), int(params.get('pageSize', efc.PAGE))
        if page * size > efc.WINDOW:
            return Answer(400, RECORDED['beyond']['body'])
        facets = []
        if params.get('facets') == 'clientBrandNameFilter':
            facets = [{'facetName': 'clientBrandNameFilter', 'facetResults': [{'value': n} for n in
                                                                              dict.fromkeys(r['companyName'] for r in found)]}]
        return Answer(200, {'data': found[(page - 1) * size:page * size],
                            'meta': {'totalResults': total, 'facetQueryResults': facets}})

    async def send(self, http, method, url, params=None, headers=None, **kwargs):
        params = dict(params or {})
        self.calls.append((url, params))
        if url == efc.SEARCH:
            return self.search(params)
        if url.startswith('https://job-branding-facade.efinancialcareers.com/job/'):
            answer = self.pages.get(url.rsplit('/', 1)[1])
            return answer if isinstance(answer, Answer) else Answer(200, answer) if answer else \
                Answer(404, RECORDED['missing']['body'])
        if url.startswith('https://company-profile-v2.efinancialcareers.com/'):
            found = self.profiles.get(params.get('fallbackCompanyId') or url.rsplit('/', 1)[1])
            return Answer(200, found) if found else Answer(204)
        if url.startswith('https://job-application.efinancialcareers.com/'):
            return Answer(200, self.applies.get(url.split('/')[-2], {'data': {}}))
        raise AssertionError(f'unexpected call {url}')

    def opened(self) -> list[str]:
        return [url.rsplit('/', 1)[1] for url, _ in self.calls if 'job-branding-facade' in url]


def platform_for(raw: dict):
    platform = efc.EFinancialCareers()
    cfg = parse_input(raw, platform)
    platform.search_all(platform.boards()[0], cfg)
    return platform, cfg


def collect(gen) -> list:
    async def go():
        return [x async for x in gen]
    return asyncio.run(go())


class Collect:
    def __init__(self):
        self.rows, self.limit, self.pushed = 0, False, []

    def can_pay(self, event):
        return True

    async def push(self, rows, event):
        self.pushed += [(row, event) for row in rows]
        self.rows += len(rows)
        return len(rows)


def run_search(site: Site, raw: dict, now: datetime = NOW):
    platform = efc.EFinancialCareers()
    boards, updated = platform.boards()
    cfg = parse_input(raw, platform, engine.name_index(platform, boards))
    delivery = Collect()
    run = engine.Run(platform, cfg, boards, updated, delivery)

    @asynccontextmanager
    async def no_client(actor, connections=64):
        yield None

    async def quiet(message):
        return None

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz else now.replace(tzinfo=None)
    with patch.object(efc, 'send', site.send), patch.object(engine, 'client', no_client), \
            patch.object(engine, 'safe_status', quiet), patch.object(engine, 'seconds_left_in_run', lambda: None), \
            patch.object(engine, 'datetime', Clock), patch.object(efc, 'datetime', Clock):
        asyncio.run(run.check_links() if cfg.job_urls else run.search())
    return run, delivery


class Links(unittest.TestCase):
    P = efc.EFinancialCareers()

    def test_job_links_of_every_country_site(self):
        for link in ('https://www.efinancialcareers.com/jobs-United_States-NY-New_York-Associate_Director.id24879038',
                     'www.efinancialcareers.co.uk/jobs-UK-London-Quant.id24879038?apply=true',
                     'https://www.efinancialcareers-gulf.com/jobs-UAE-Dubai-Analyst.id24879038/',
                     'https://www.efinancialcareers.lu/de/jobs-USA-NY-New_York_City-Associate_Director.id24879038',
                     'https://www.efinancialcareers.com.au/jobs-Australia-Sydney-Analyst.id24879038'):
            self.assertEqual(self.P.read_link(link), ('*', '24879038'), link)

    def test_a_company_page_names_the_company_and_other_pages_name_nothing(self):
        self.assertEqual(self.P.read_link('https://www.efinancialcareers.com/jobs/at-S&P-Global'), ('S&P Global', None))
        self.assertEqual(self.P.read_link('https://www.efinancialcareers.sg/jobs/quant/in-singapore'), (None, None))
        for text in ('https://www.naukri.com/job-listings-x-24879038', 'quant analyst', 'efinancialcareers'):
            self.assertIsNone(self.P.read_link(text), text)

    def test_a_search_page_is_read_for_its_words_and_place(self):
        self.assertEqual(self.P.search_link('https://www.efinancialcareers.com/jobs/quant-analyst/in-new-york'),
                         (['quant analyst'], ['new york']))
        cfg = parse_input({'searchTerms': ['https://www.efinancialcareers.co.uk/jobs/compliance/in-london']},
                          efc.EFinancialCareers())
        self.assertEqual((cfg.search_terms, cfg.locations), (['compliance'], ['london']))
        cfg = parse_input({'searchTerms': ['https://www.efinancialcareers.com/jobs?q=risk%20analyst&location=Zurich']},
                          efc.EFinancialCareers())
        self.assertEqual((cfg.search_terms, cfg.locations), (['risk analyst'], ['Zurich']))


class ListJobs(unittest.TestCase):
    P = efc.EFinancialCareers()

    def test_a_list_record(self):
        job = self.P.job(PAID[0])
        self.assertEqual((job['id'], job['key'], job['title'], job['company']),
                         ('24545714', 'XCJke22AyCyjWD7s', 'Deputy MLRO - Group Level', 'Michael Page'))
        self.assertEqual((job['location'], job['places'][0]['country'], job['places'][0]['city']),
                         ('Manama, Bahrain', 'Bahrain', 'Manama'))
        self.assertEqual((job['postedAt'], job['listedAt'], job['refreshedAt']), ('2026-11-10T10:40:41Z',) * 3)
        self.assertEqual(job['jobUrl'], 'https://www.efinancialcareers.com/jobs-Bahrain-Manama-Deputy_MLRO_-_Group_Level.id24545714')
        self.assertEqual((job['employmentType'], job['contractType'], job['paid']), ('full-time', 'Permanent', 'PAID'))
        self.assertEqual(job['sectors'][0], 'Compliance/Legal: Compliance Regulatory')
        self.assertEqual((job['salary'], job['salaryMin'], job['salaryMax']), ('Competitive', None, None))

    def test_work_arrangement_and_contract(self):
        hybrid = {r['jobId']: self.P.job(r) for r in RECORDED['hybrid']}
        self.assertEqual((hybrid['24969279']['workType'], hybrid['24969279']['employmentType']), ('hybrid', 'contract'))
        self.assertEqual(hybrid['24987362']['employmentType'], 'temporary')
        self.assertEqual(hybrid['24733901']['employmentType'], 'intern')
        self.assertIsNone(self.P.job(PAID[0])['workType'])
        self.assertEqual(efc.job_type('PERMANENT', 'PART_TIME'), 'part-time')
        self.assertEqual(efc.job_type('Self Employed', 'Full time'), 'contract')

    def test_pay_shown_hidden_and_a_range_stored_as_one_amount(self):
        self.assertEqual(efc.pay({'salary': '£45k - £50k', 'minSalary': '45000', 'maxSalary': '50000', 'salaryCurrency': 'GBP'}),
                         {'salary': '£45k - £50k', 'salaryMin': 45000, 'salaryMax': 50000, 'salaryCurrency': 'GBP',
                          'salaryPeriod': None})
        hidden = efc.pay({'salary': 'Competitive', 'minSalary': '112500', 'maxSalary': '187500', 'salaryCurrency': 'USD'})
        self.assertEqual((hidden['salary'], hidden['salaryMin'], hidden['salaryMax'], hidden['salaryCurrency']),
                         ('Competitive', None, None, None))
        one = efc.pay({'salary': 'salary range for this position is $123,000 USD to $192,000 USD', 'minSalary': '123000',
                       'maxSalary': '123000', 'salaryCurrency': 'USD'})
        self.assertEqual((one['salaryMin'], one['salaryMax']), (123000, None))
        self.assertEqual(efc.pay({'salary': 'S$13k - S$13k', 'minSalary': '13000', 'maxSalary': '13000'})['salaryMax'], 13000)
        self.assertIsNone(efc.pay({'salary': '£76,077 - £141,286 per year', 'minSalary': '0', 'maxSalary': '0'})['salaryMin'])
        self.assertIsNone(efc.pay({})['salary'])

    def test_dates(self):
        self.assertEqual(efc.iso('2026-11-10T09:20:41.543Z'), '2026-11-10T09:20:41Z')
        self.assertIsNone(efc.expiry('2050-01-01T23:45:00.000Z'))
        self.assertEqual(efc.expiry('2027-01-27T23:45:00.000Z'), '2027-01-27T23:45:00Z')

    def test_sector_names(self):
        self.assertEqual(efc.sector_names(['ACCOUNTING_FINANCE/TAX', 'ACCOUNTING_FINANCE/OTHER', 'SALES_MARKETING']),
                         ['Accounting & Finance: Tax', 'Accounting & Finance', 'Sales & Marketing'])


class Inputs(unittest.TestCase):
    def test_seniority_in_everyday_words(self):
        platform, cfg = platform_for({'seniority': ['VP', 'managing director', 'grad', 'avp-senior']})
        self.assertEqual(platform.levels, ['VP_PRINCIPAL', 'MANAGING_DIRECTOR', 'INTERN_GRADUATE', 'AVP_SENIOR'])
        platform, cfg = platform_for({'seniority': 'chief wizard'})
        self.assertEqual(platform.levels, [])
        self.assertTrue(any('seniority has no option chief wizard' in n for n in cfg.notes))

    def test_sectors_by_form_value_name_word_or_sub_sector(self):
        platform, cfg = platform_for({'sector': ['quant', 'Hedge Funds', 'investment banking',
                                                 'ASSET_MANAGEMENT/PORTFOLIO_MANAGER', 'basket weaving']})
        self.assertEqual(platform.sectors, ['QUANTITATIVE_ANALYTICS', 'HEDGE_FUNDS', 'INVESTMENT_BANKING_M_A',
                                            'ASSET_MANAGEMENT/PORTFOLIO_MANAGER'])
        platform, _ = platform_for({'sector': ['wealth-management', 'quantitative-analytics', 'PRIVATE_EQUITY_VENTURE_CAPITAL']})
        self.assertEqual(platform.sectors, ['PRIVATE_BANKING_WEALTH_MANAGEMENT', 'QUANTITATIVE_ANALYTICS',
                                            'PRIVATE_EQUITY_VENTURE_CAPITAL'])
        names = efc.taxonomy()[0]
        self.assertTrue(all(code in names for code in efc.FORM_SECTORS.values()))    # every form value is a sector of the site
        self.assertTrue(any('sector has no option basket weaving' in n for n in cfg.notes))

    def test_minimum_pay_as_people_type_it(self):
        for typed, picked, expected in (('£100k', None, (100000.0, 'GBP')), ('150,000 USD', None, (150000.0, 'USD')),
                                        ('HK$1m', None, (1000000.0, 'HKD')), (120000, 'eur', (120000.0, 'EUR')),
                                        ('S$90k', 'USD', (90000.0, 'SGD'))):
            platform, _ = platform_for({'minSalary': typed, 'salaryCurrency': picked})
            self.assertEqual(platform.min_pay, expected, typed)
        platform, cfg = platform_for({'minSalary': 100000})
        self.assertIsNone(platform.min_pay)
        self.assertTrue(any('names no currency' in n for n in cfg.notes))

    def test_places_go_to_the_sites_own_filters(self):
        platform, cfg = platform_for({'location': ['United Kingdom', 'Singapore', 'London', 'DACH', 'Gulf', 'Atlantis Country']})
        self.assertEqual(platform.places[0]['filters.locationPath'].split('|'),
                         ['Europe/United Kingdom', 'Asia/Singapore', 'Europe/Austria', 'Europe/Switzerland', 'Europe/Germany',
                          'Gulf/United Arab Emirates', 'Gulf/Bahrain', 'Gulf/Kuwait', 'Gulf/Qatar', 'Gulf/Saudi Arabia'])
        self.assertEqual(platform.places[1:], [{'location': 'London'}, {'location': 'Atlantis Country'}])
        self.assertTrue(any('Read "Gulf" in location as the GCC' in n for n in cfg.notes))

    def test_worldwide_is_looked_for_in_the_newest_jobs(self):
        platform, cfg = platform_for({'location': ['Worldwide']})
        self.assertEqual((platform.places, platform.scan_places), ([], ['Worldwide']))
        self.assertEqual(platform.searches({'everyone': True})[0][0].get('scan'), True)

    def test_job_and_work_types_go_to_the_site_when_one_search_can_say_them(self):
        platform, _ = platform_for({'employmentType': ['contract', 'temporary']})
        self.assertEqual(platform.server, {'filters.positionType': 'CONTRACT|SELF_EMPLOYED|TEMPORARY'})
        platform, _ = platform_for({'employmentType': ['full-time']})
        self.assertEqual(platform.server, {'filters.positionType': 'PERMANENT', 'filters.employmentType': 'FULL_TIME'})
        platform, _ = platform_for({'employmentType': ['part-time', 'contract']})      # two of the site's filters: checked here
        self.assertEqual(platform.server, {})
        platform, cfg = platform_for({'workType': ['remote', 'hybrid']})
        self.assertEqual(platform.server, {'filters.workArrangementType': 'REMOTE|TEMPORARILY_REMOTE|HYBRID|FLEXIBLE'})
        self.assertTrue(any('about 1 job in 25' in n for n in cfg.notes))

    def test_one_search_per_place_and_level(self):
        platform, _ = platform_for({'location': ['London', 'Zurich'], 'seniority': ['vp', 'director'], 'sector': 'quant'})
        searches = platform.searches({'brand': 'Citi'})
        self.assertEqual([(p.get('location'), p.get('filters.seniority'), level) for p, level in searches],
                         [('London', 'VP_PRINCIPAL', 'VP_PRINCIPAL'), ('London', 'DIRECTOR', 'DIRECTOR'),
                          ('Zurich', 'VP_PRINCIPAL', 'VP_PRINCIPAL'), ('Zurich', 'DIRECTOR', 'DIRECTOR')])
        self.assertTrue(all(p['filters.sectors'] == 'QUANTITATIVE_ANALYTICS' and p['filters.clientBrandNameFilter'] == 'Citi'
                            and p['countryCode2'] == '' for p, _ in searches))
        self.assertNotIn('filters.postedDate', searches[0][0])      # its cut moves in steps: periods are checked here


class NewestFirst(unittest.TestCase):
    def run_merge(self, streams: dict, size: int = 100):
        """streams: (location, kind) -> pages of jobs; the merge of what by_date hands on, chunk by chunk."""
        platform = efc.EFinancialCareers()
        platform.cfg = parse_input({}, platform)
        read = []

        async def stream(http, params, level):
            pages = streams.get((params.get('location'), params.get('filters.jobPaymentType')), [])
            for page in pages:
                read.append((params.get('location'), params.get('filters.jobPaymentType'), page[0]['id'] if page else None))
                yield page
        platform.stream = stream
        searches = [({'location': where}, None) for where in dict.fromkeys(w for w, _ in streams)]
        return collect(platform.by_date(None, searches, size)), read

    def job(self, job_id: str, minutes_ago: float, kind: str) -> dict:
        return efc.EFinancialCareers().job(record(job_id, f'Job {job_id}', minutes_ago, kind))

    def test_paid_and_unpaid_lists_are_merged_newest_listed_first(self):
        paid = [[self.job('1', 1, 'PAID'), self.job('2', 30, 'PAID')], [self.job('3', 90, 'PAID')]]
        unpaid = [[self.job('4', 10, 'UNPAID'), self.job('5', 60, 'UNPAID')], [self.job('6', 400, 'UNPAID')]]
        chunks, _ = self.run_merge({(None, 'PAID'): paid, (None, 'UNPAID'): unpaid})
        self.assertEqual([j['id'] for chunk in chunks for j in chunk], ['1', '4', '2', '5', '3', '6'])

    def test_a_job_goes_only_once_no_list_can_hand_a_newer_one(self):
        paid = [[self.job('1', 1, 'PAID'), self.job('2', 30, 'PAID')], [self.job('3', 90, 'PAID')]]
        unpaid = [[self.job('4', 50, 'UNPAID')], [self.job('5', 120, 'UNPAID')]]
        chunks, read = self.run_merge({(None, 'PAID'): paid, (None, 'UNPAID'): unpaid}, size=1)
        # job 2 (30 minutes) cannot go before the UNPAID list has read past 30 minutes
        order = [j['id'] for chunk in chunks for j in chunk]
        self.assertEqual(order, ['1', '2', '4', '3', '5'])
        self.assertLess(read.index((None, 'UNPAID', '4')), len(read))

    def test_a_job_waits_for_a_newer_one_on_the_next_page_of_the_other_list(self):
        paid = [[self.job('1', 1, 'PAID'), self.job('2', 200, 'PAID')]]
        unpaid = [[self.job('3', 10, 'UNPAID')], [self.job('4', 50, 'UNPAID')]]
        chunks, _ = self.run_merge({(None, 'PAID'): paid, (None, 'UNPAID'): unpaid}, size=1)
        self.assertEqual([j['id'] for chunk in chunks for j in chunk], ['1', '3', '4', '2'])

    def test_a_kind_a_list_did_not_split_off_can_sit_late_behind(self):
        """When a list holds both kinds (the payment filter stopped working) and only PAID jobs were read so far, an UNPAID
        job may still come up to LATE newer: nothing older than that line goes until it shows or the list ends."""
        read = {'any': NOW - timedelta(minutes=30), 'PAID': NOW - timedelta(minutes=30)}
        self.assertEqual(efc.bound(read, ('PAID',)), NOW - timedelta(minutes=30) + efc.JITTER)
        self.assertEqual(efc.bound(read, efc.KINDS), NOW - timedelta(minutes=30) + efc.LATE)
        read['UNPAID'] = NOW - timedelta(minutes=400)
        self.assertEqual(efc.bound(read, efc.KINDS), NOW - timedelta(minutes=30) + efc.JITTER)

    def test_several_places_merge_and_a_job_in_both_comes_once(self):
        london = [[self.job('1', 5, 'PAID'), self.job('2', 50, 'PAID')]]
        canary = [[self.job('2', 50, 'PAID'), self.job('3', 20, 'PAID')]]
        chunks, _ = self.run_merge({('London', 'PAID'): london, ('Canary Wharf', 'PAID'): canary})
        self.assertEqual([j['id'] for chunk in chunks for j in chunk], ['1', '3', '2'])


class Ranked(unittest.TestCase):
    def test_a_word_is_read_until_a_page_holds_no_title_match_then_sorted_by_listing(self):
        platform = efc.EFinancialCareers()
        platform.cfg = parse_input({'searchTerms': ['quant']}, platform)
        pages = [[platform.job(record('1', 'Quant Analyst', 300)), platform.job(record('2', 'Quant Developer', 5))],
                 [platform.job(record('3', 'Risk Manager', 1)), platform.job(record('4', 'Senior Quant', 60))],
                 [platform.job(record('5', 'Controller', 2))],
                 [platform.job(record('6', 'Quant Researcher', 1))]]
        read = []

        async def stream(http, params, level, ahead=1):
            for page in pages:
                read.append(page[0]['id'])
                yield page
        platform.stream = stream
        found = asyncio.run(platform.ranked(None, [({}, None)], 'quant'))
        self.assertEqual(read, ['1', '3', '5'])                   # the third page held no quant title: no fourth
        self.assertEqual([j['id'] for j in found], ['3', '5', '2', '4', '1'])
        platform.cfg = parse_input({'searchTerms': ['quant'], 'searchDescriptions': True}, platform)
        read.clear()
        asyncio.run(platform.ranked(None, [({}, None)], 'quant'))
        self.assertEqual(read, ['1', '3', '5', '6'])               # descriptions too: every page


class Opening(unittest.TestCase):
    def test_a_job_is_dated_by_its_first_posting_and_an_expired_one_is_left_out(self):
        platform, _ = platform_for({'postedWithin': '60 days'})
        site = Site([], pages={'1': page_record('2026-10-11T09:00:59Z', '2026-11-04T09:15:41Z'),
                               '2': Answer(410, RECORDED['expired']['body']),
                               '3': page_record('2026-08-01T09:00:00Z')},
                    profiles={'acct1': RECORDED['profile_company']})
        chunk = [platform.job(record('1', 'Associate Director', 20)), platform.job(record('2', 'Analyst', 30)),
                 platform.job(record('3', 'Old Bumped Job', 40)), platform.job(record('4', 'Too Old Listing', 60 * 24 * 70))]
        with patch.object(efc, 'send', site.send), patch.object(efc, 'datetime', clock(NOW)):
            out = asyncio.run(platform.handed(None, chunk, None))
        self.assertEqual([j['id'] for j in out], ['1', '3', '4'])
        self.assertEqual(sorted(site.opened()), ['1', '2', '3'])    # a listing over 60 days old is not opened
        first = out[0]
        self.assertEqual((first['postedAt'], first['refreshedAt']), ('2026-10-11T09:00:59Z', at(20).replace('.000', '')))
        self.assertEqual((first['reference'], first['skills'][:3]), ('331563', ['NBFI', 'Credit', 'Finance']))
        self.assertEqual((first['postedBy'], first['postedByIndustry'], first['agency']), ('S&P Global', 'Information Services', False))
        self.assertTrue(first['description'].startswith('About the Role:'))
        self.assertEqual(out[1]['postedAt'], '2026-08-01T09:00:00Z')     # dated: outside the 60 days, so never kept ...
        self.assertIsNone(out[1]['postedBy'])                             # ... and its poster was not looked up
        self.assertTrue(any('1 jobs eFinancialCareers still listed had expired' in n for n in platform.cfg.notes))

    def test_an_agency_is_a_poster_filed_under_hr_and_recruitment(self):
        platform, _ = platform_for({'excludeAgencies': True})
        agency = {'data': {'name': 'Workwise 2', 'type': 'COMPANY', 'industry': {'id': 'HR_RECRUITMENT', 'en': 'HR & Recruitment'}}}
        site = Site([], pages={'1': page_record('2026-11-10T10:00:00Z', company='ww'), '2': page_record('2026-11-10T10:00:00Z',
                                                                                                     company='none')},
                    profiles={'ww': agency})
        chunk = [platform.job(record('1', 'Steuerberater', 20)), platform.job(record('2', 'Tax Manager', 25))]
        with patch.object(efc, 'send', site.send), patch.object(efc, 'datetime', clock(NOW)):
            out = asyncio.run(platform.handed(None, chunk, None))
        self.assertEqual([(j['agency'], j['postedBy'], j['postedByIndustry']) for j in out],
                         [(True, 'Workwise 2', 'HR & Recruitment'), (None, None, None)])
        self.assertEqual([platform.fits(j) for j in out], ['excludeAgencies', True])


class Filters(unittest.TestCase):
    def test_sector_and_pay_checked_on_what_a_job_shows(self):
        platform, _ = platform_for({'sector': 'quant', 'minSalary': '£100k'})
        job = platform.job(record('1', 'Quant', 5, sectors=['QUANTITATIVE_ANALYTICS/EQUITY'], salary='£80k - £90k',
                                  minSalary='80000', maxSalary='90000', salaryCurrency='GBP'))
        self.assertEqual(platform.fits(job), 'minSalary')
        job.update({'salaryMax': 120000})
        self.assertIs(platform.fits(job), True)
        job.update({'salaryMin': None, 'salaryMax': None})            # pay not shown: the site's own filter decided
        self.assertIs(platform.fits(job), True)
        job['sectorValues'] = ['TRADING/EQUITIES']
        self.assertEqual(platform.fits(job), 'sector')


class Calls(unittest.TestCase):
    def test_past_the_window_ends_a_search_with_a_note(self):
        platform, cfg = platform_for({})
        records = [record(str(i), f'Job {i}', i) for i in range(efc.WINDOW + 5)]
        site = Site(records)
        with patch.object(efc, 'send', site.send):
            pages = collect(platform.stream(None, efc.search_params(), None))
        self.assertEqual(sum(len(p) for p in pages), efc.WINDOW)
        self.assertTrue(platform.incomplete)
        self.assertTrue(any('shows the 10,000 first jobs of one search: every job had 10,005' in n for n in cfg.notes))

    def test_a_search_read_as_its_paid_and_unpaid_lists_counts_both_in_the_window_note(self):
        platform, cfg = platform_for({})
        platform.window({**efc.search_params(), 'filters.jobPaymentType': 'PAID'}, 13005)
        platform.window({**efc.search_params(), 'filters.jobPaymentType': 'UNPAID'}, 57402)
        self.assertTrue(any('every job had 70,407' in n for n in cfg.notes))
        self.assertEqual(sum('shows the 10,000 first jobs' in n for n in cfg.notes), 1)

    def test_a_words_pages_come_four_at_once_and_in_order(self):
        platform, cfg = platform_for({'searchTerms': ['quant']})
        # 450 quant titles ranked first, then 750 jobs whose text alone holds the word
        records = [record(str(i), 'Quant' if i < 450 else 'Clerk', i, jobSummary='quant desk') for i in range(1200)]
        site = Site(records)
        with patch.object(efc, 'send', site.send):
            found = asyncio.run(platform.ranked(None, [(efc.search_params(), None)], 'quant'))
        pages = sorted(p['page'] for url, p in site.calls if url == efc.SEARCH)
        self.assertEqual(len(found), 800)                    # pages 1 to 4 read: the fourth held no quant title
        self.assertEqual(pages[:4], [1, 2, 3, 4])            # the first four asked at once
        self.assertLessEqual(pages[-1], 7)                   # and never more than three past the last page used
        self.assertEqual([j['id'] for j in found][:3], ['0', '1', '2'])

    def test_a_place_the_site_does_not_know_reads_the_newest_jobs_only(self):
        platform, cfg = platform_for({'location': ['Atlantis']})
        site = Site([record(str(i), f'Job {i}', i) for i in range(3000)], totals={'Atlantis': 3000})
        with patch.object(efc, 'send', site.send):
            pages = collect(platform.stream(None, platform.searches({'everyone': True})[0][0], None))
        self.assertEqual(sum(len(p) for p in pages), efc.SCAN_JOBS)
        self.assertTrue(any('does not know the place Atlantis' in n for n in cfg.notes))
        self.assertNotIn('location', site.calls[-1][1])

    def test_an_api_that_starts_asking_for_its_key_gets_the_one_the_pages_send(self):
        platform = efc.EFinancialCareers()
        answers = [Answer(403, {'message': 'Forbidden'}), Answer(200, {'data': [], 'meta': {'totalResults': 0}})]
        seen = []

        async def send(http, method, url, params=None, headers=None, **kwargs):
            seen.append(headers)
            return answers.pop(0)

        async def key(http):
            return {'x-api-key': 'from-the-page'}
        with patch.object(efc, 'send', send), patch.object(platform, 'api_key', key):
            data = asyncio.run(platform.search(None, efc.search_params()))
        self.assertEqual((data['data'], seen), ([], [None, {'x-api-key': 'from-the-page'}]))

    def test_a_company_is_read_as_the_name_the_site_files_it_under(self):
        platform = efc.EFinancialCareers()
        site = Site([record('1', 'Analyst', 1, companyName='Goldman Sachs, Inc.', jobSummary='Goldman Sachs analyst'),
                     record('2', 'VP', 2, companyName='Citi')])
        with patch.object(efc, 'send', site.send):
            self.assertEqual(asyncio.run(platform.company(None, 'Citi')), 'Citi')
            self.assertEqual(asyncio.run(platform.company(None, 'goldman sachs')), 'Goldman Sachs, Inc.')
            self.assertIsNone(asyncio.run(platform.company(None, 'Lehman Brothers')))


class Engine(unittest.TestCase):
    def site(self) -> Site:
        records = [record('2400011', 'Quant Analyst', 2, 'PAID'), record('2400012', 'Quant Researcher', 4, 'PAID'),
                   record('2400013', 'Compliance Officer', 6, 'PAID'), record('2400021', 'Quant Developer', 3, 'UNPAID'),
                   record('2400022', 'Senior Quant', 9, 'UNPAID')]
        pages = {'2400011': page_record('2026-09-01T08:00:00Z'), '2400012': page_record(at(4)), '2400013': page_record(at(6)),
                 '2400021': page_record(at(3)), '2400022': Answer(410, RECORDED['expired']['body'])}
        return Site(records, pages=pages, profiles={'acct1': RECORDED['profile_company']},
                    applies={'k2400012': RECORDED['apply']})

    def test_a_word_search_delivers_new_jobs_by_their_first_posting(self):
        run, delivery = run_search(self.site(), {'searchTerms': ['quant'], 'postedWithin': '30 days'})
        rows = [row for row, _ in delivery.pushed]
        self.assertEqual([r['jobId'] for r in rows], ['2400021', '2400012'])   # 11 first posted in September, 22 expired
        self.assertEqual(rows[0]['postedAt'], at(3).replace('.000', ''))
        self.assertTrue(all(k not in rows[0] for k in ('department', 'team', 'offersEquity', 'companyWebsite',
                                                       'otherLocations', 'salaryPeriod')))
        for key in ('refreshedAt', 'contractType', 'sectors', 'skills', 'agency', 'postedBy', 'postedByIndustry', 'summary',
                    'seniority', 'salaryMin', 'salaryCurrency'):
            self.assertIn(key, rows[0])
        self.assertIn('Found and saved 2 matching jobs', run.message())
        self.assertIn('1 jobs eFinancialCareers still listed had expired', run.message())

    def test_an_empty_form_and_details_with_the_employers_apply_link(self):
        run, delivery = run_search(self.site(), {'maxJobs': 2, 'includeDetails': True})
        rows = [(row, event) for row, event in delivery.pushed]
        self.assertEqual([r['jobId'] for r, _ in rows], ['2400021', '2400012'])
        self.assertEqual({e for _, e in rows}, {'job-details'})
        self.assertTrue(rows[1][0]['applyUrl'].startswith('https://spgi.wd5.myworkdayjobs.com/'))
        self.assertTrue(rows[0][0]['description'].startswith('About the Role:'))

    def test_checked_links(self):
        site = self.site()
        run, delivery = run_search(site, {'jobUrls': ['https://www.efinancialcareers.com/jobs-x.id2400012',
                                                      'https://www.efinancialcareers.co.uk/jobs-y.id2400022',
                                                      'efinancialcareers.sg/jobs-z.id2400099']})
        self.assertEqual([(r['jobId'], r['status']) for r, _ in delivery.pushed],
                         [('2400012', 'open'), ('2400022', 'closed'), ('2400099', 'not found')])
        self.assertEqual(delivery.pushed[0][0]['postedAt'], at(4).replace('.000', ''))


def clock(now: datetime):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz else now.replace(tzinfo=None)
    return Clock


if __name__ == '__main__':
    unittest.main()


class SearchBox(unittest.TestCase):
    def test_a_city_typed_with_the_job_title_is_read_as_its_place(self):
        cases = {'risk manager in Zurich': (['risk manager'], ['Zurich']), 'quant analyst in Frankfurt': (['quant analyst'], ['Frankfurt']),
                 'compliance officer london': (['compliance officer'], ['london']), 'trader in New York': (['trader'], ['New York']),
                 'Zürich': ([], ['Zürich']), 'analyst in training': (['analyst in training'], []),
                 'portfolio manager': (['portfolio manager'], [])}
        for typed, (title, where) in cases.items():
            cfg = parse_input({'searchTerms': [typed]}, efc.EFinancialCareers())
            self.assertEqual((cfg.search_terms, cfg.locations), (title, where), typed)

    def test_other_platforms_read_the_search_box_as_before(self):
        from ats_jobs.platforms import PLATFORMS
        cfg = parse_input({'searchTerms': ['risk manager in Zurich']}, PLATFORMS['naukri']())
        self.assertEqual((cfg.search_terms, cfg.locations), (['risk manager in Zurich'], []))
