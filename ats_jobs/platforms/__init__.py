"""Every platform the engine reads."""
from .base import Board, Platform
from .cornerstone import Cornerstone
from .deel import Deel
from .lever import Lever
from .workday import Workday

PLATFORMS = {p.key: p for p in (Cornerstone, Deel, Lever, Workday)}
__all__ = ['Board', 'PLATFORMS', 'Platform']
