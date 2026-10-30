"""Every platform the engine reads."""
from .base import Board, Platform
from .cornerstone import Cornerstone
from .workday import Workday

PLATFORMS = {p.key: p for p in (Cornerstone, Workday)}
__all__ = ['Board', 'PLATFORMS', 'Platform']
