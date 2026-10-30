"""A platform for the tests: Ashby's link format, read the way the Ashby adapter will read it."""
import re
from urllib.parse import unquote, urlparse

from ats_jobs.platforms.base import Platform

HOST = 'jobs.ashbyhq.com'
UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
SLUG = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._~ &-]{0,99}$')


class Stub(Platform):
    key = 'stub'
    name = 'Ashby'
    board_example = 'https://jobs.ashbyhq.com/ramp'
    job_example = 'https://jobs.ashbyhq.com/ramp/34413f8d-26bf-4bbc-8ade-eb309a0e2245'

    def read_link(self, text):
        if HOST not in text.lower():
            return None
        link = text if re.match(r'^[a-z][a-z0-9+.-]*://', text, re.I) else f'https://{text}'
        parsed = urlparse(link)
        if (parsed.hostname or '').lower() != HOST:
            return None
        parts = [unquote(p) for p in parsed.path.split('/') if p]
        if not parts or not SLUG.match(parts[0]):
            return None, None
        return parts[0], parts[1].lower() if len(parts) >= 2 and UUID.match(parts[1]) else None

    def guess_board(self, name):
        return name if SLUG.match(name) else None
