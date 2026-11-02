"""Every platform the engine reads."""
from .base import Board, Platform
from .cornerstone import Cornerstone
from .deel import Deel
from .smartrecruiters import SmartRecruiters
from .workday import Workday

PLATFORMS = {p.key: p for p in (Cornerstone, Deel, SmartRecruiters, Workday)}
__all__ = ['Board', 'PLATFORMS', 'Platform']
