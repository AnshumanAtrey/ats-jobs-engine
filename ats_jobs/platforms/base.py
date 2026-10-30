"""What the engine needs from a platform. One small subclass per ATS, one board list per subclass."""
from dataclasses import dataclass
from importlib import resources
from typing import AsyncIterator


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
