"""HTTP for public job sites: one client per run, and retries for the answers that pass by themselves."""
import asyncio

import httpx

RETRY_STATUS = {429, 500, 502, 503, 504}
BACKOFF_S = (1, 3, 7)


class BoardError(Exception):
    """A board that could not be read after the retries (network or server trouble, not a missing board)."""


class BoardMissing(Exception):
    """The platform says this board does not exist."""


def client(actor: str, connections: int = 64) -> httpx.AsyncClient:
    limits = httpx.Limits(max_connections=connections, max_keepalive_connections=connections)
    agent = f'Mozilla/5.0 (compatible; {actor}/1.0; +https://apify.com/anshumanatrey/{actor})'
    return httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=15.0), limits=limits, follow_redirects=True,
                             headers={'User-Agent': agent, 'Accept-Encoding': 'gzip'})


async def send(http: httpx.AsyncClient, method: str, url: str, **kwargs) -> httpx.Response:
    """The answer, whatever its status, after retrying what can pass. BoardError when nothing came back."""
    last = None
    for attempt in range(len(BACKOFF_S) + 1):
        try:
            response = await http.request(method, url, **kwargs)
            if response.status_code not in RETRY_STATUS:
                return response
            last = f'HTTP {response.status_code}'
        except httpx.HTTPError as exc:
            last = f'{type(exc).__name__}: {exc}'[:200]
        if attempt < len(BACKOFF_S):
            await asyncio.sleep(BACKOFF_S[attempt])
    raise BoardError(last or 'no answer')
