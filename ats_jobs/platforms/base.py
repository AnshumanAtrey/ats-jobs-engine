"""What the engine needs from a platform. One small subclass per ATS, one board list per subclass."""
from dataclasses import dataclass
from importlib import resources
from typing import AsyncIterator

from ..rows import dedupe_key


@dataclass(frozen=True)
class Board:
    key: str                 # the platform's own address of the board: ramp, newcold/5
    name: str                # the company as people call it
    website: str | None = None


class Platform:
    key = ''                 # the platform column of every row
    name = ''                # the word users know: Cornerstone
    site_word = 'board'      # what the platform calls one company's job page
    actor = ''               # the Apify actor's name, for the user agent
    board_example = ''       # shown when a link cannot be read
    job_example = ''
    concurrency = 24         # boards read at once
    server_search = False    # the platform searches words itself, so pages() gets one term at a time
    newest_first = False     # pages come newest first, so a board can stop at the jobs that can no longer make the cut
    ranked_search = False    # a word search comes back by relevance, so only a list with no word is newest first
    page_cap: int | None = None    # pages read per board and word, unless every match was asked for
    never: tuple = ()        # row fields this platform never publishes: left out of its rows
    extra: tuple = ()        # job fields of this platform alone that its rows carry too (Hirist: years of experience)
    filter_words: dict = {}  # its own filters that fits() names -> (words for them, how to loosen them), for the
                             # message of a run that finds nothing: {'minSalaryLakhs': ('salary', 'type a lower Salary from')}
    server_filters = False   # its own search applies place, period or other filters besides the words, so the engine
                             # never sees the jobs they leave out and cannot say which filter did
    description_terms: tuple = ()     # set by the engine: the words of a run that searches descriptions, so a
                                      # platform whose job list leaves descriptions out can ask for them and skip
                                      # the text that holds none of these words
    incomplete = False       # set during a run that read only part of what the platform lists (a feed cut short)
    unopened_matches = 0     # set during a run: matches the platform counted and never handed over, since opening each
                             # costs a call and none can make the cut (Instahyre: a search's matches past the newest
                             # maxJobs, their years vouched for by its own filter). They count; they are never delivered
    unopened_companies: frozenset = frozenset()    # their companies, for the run's count of companies
    count_is_floor = False   # set during a run: matches may hide among records the platform did not open, so the count
                             # says "at least" (Instahyre: a range of years, which its own search cannot take)
    progress: str | None = None       # set by a platform that works a while before its first page: the status line


    def search_all(self, boards: list[Board], cfg) -> list[Board]:
        """What a search reads when no company is named: every board (or some of them), or one board that is not on
        the list and lists every company's jobs, when the platform has one and the settings (inputs.Config, given on
        every search) suit it. The jobs of such a place carry their own company, companySlug and companyWebsite."""
        return boards

    def copy_key(self, job: dict) -> tuple:
        """What makes two jobs of one company one job, kept once (the newest): the title at its place. A platform whose
        listings repeat a title at a place for other stores, teams or pay adds those, so distinct jobs stay."""
        return dedupe_key(job)

    def boards(self) -> tuple[list[Board], str | None]:
        """data/<key>.tsv -> (boards, the date of the list). Lines: key, name, website; # lines are comments."""
        boards, updated = [], None
        text = resources.files('ats_jobs').joinpath(f'data/{self.key}.tsv').read_text(encoding='utf-8')
        for line in text.splitlines():
            if line.startswith('#'):
                if 'updated' in line:
                    updated = line.split('updated', 1)[1].strip().split(' ')[0].strip(',:')
                continue
            parts = line.split('\t')
            if parts[0].strip():
                name = parts[1].strip() if len(parts) > 1 else ''
                boards.append(Board(parts[0].strip(), name or parts[0].strip(),
                                    (parts[2].strip() or None) if len(parts) > 2 else None))
        return boards, updated

    def vague_place(self, job: dict) -> bool:
        """The list does not say where this job is ("2 Locations"), so a place filter needs details() first."""
        return False

    def aliases(self, board: Board) -> list[str]:
        """Words that find this board in the companies field, besides its name and key."""
        return []

    def read_link(self, text: str) -> tuple[str | None, str | None] | None:
        """A link of this platform -> (board key, job id), either can be None. Anything else -> None."""
        raise NotImplementedError

    def search_link(self, text: str) -> tuple[list[str], list[str]]:
        """A search page link of this platform -> (words, places) it asks for besides ?query= and ?location= (a site
        that puts the words in the path: /search/python-jobs)."""
        return [], []

    def read_input(self, raw: dict, cfg) -> None:
        """Inputs of this platform alone, read from the raw input once the shared ones are in cfg (inputs.Config)."""

    def fits(self, job: dict) -> bool | str:
        """A filter of this platform alone, asked for every job whose words match (Cutshort: salary, funding stage).
        True when the job fits; else the input name of the filter it fails ('minSalaryLakhs'), which a run that finds
        nothing names (filter_words); False when the job's words did not match after all, so it is no near miss
        (Hirist's skills, with searchDescriptions). Years of experience is the engine's own filter (experienceYears)."""
        return True

    def settings(self) -> dict:
        """This platform's own settings of the run, for the OUTPUT record."""
        return {}

    def guess_board(self, name: str) -> str | None:
        """A company typed that is not on our list -> the board key to try, or None."""
        return None

    async def open(self, http, board: Board):
        """Whatever the later calls need for this board; None when the board does not exist."""
        return board

    def pages(self, http, session, term: str | None, days: int | None) -> AsyncIterator[list[dict]]:
        """Pages of jobs in the shape of rows.py. term and days narrow what the platform returns when it can;
        the engine checks every job itself."""
        raise NotImplementedError

    async def details(self, http, session, job: dict) -> dict:
        """The job with its full description, when pages() does not carry it."""
        return job

    async def lookup(self, http, session, job_id: str) -> tuple[str, dict | None]:
        """One job -> ('open', job) | ('closed', None) | ('not found', None)."""
        raise NotImplementedError
