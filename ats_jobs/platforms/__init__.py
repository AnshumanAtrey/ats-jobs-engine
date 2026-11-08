"""Every platform the engine reads."""
from .base import Board, Platform
from .cornerstone import Cornerstone
from .deel import Deel
from .greenhouse import Greenhouse
from .lever import Lever
from .naukri import Naukri
from .smartrecruiters import SmartRecruiters
from .workable import Workable
from .workday import Workday

PLATFORMS = {p.key: p for p in (Cornerstone, Deel, Greenhouse, Lever, Naukri, SmartRecruiters, Workable, Workday)}
__all__ = ['Board', 'PLATFORMS', 'Platform']
