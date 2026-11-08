"""Every platform the engine reads."""
from .base import Board, Platform
from .cornerstone import Cornerstone
from .cutshort import Cutshort
from .deel import Deel
from .foundit import Foundit
from .greenhouse import Greenhouse
from .hirist import Hirist
from .instahyre import Instahyre
from .lever import Lever
from .smartrecruiters import SmartRecruiters
from .workable import Workable
from .workday import Workday

PLATFORMS = {p.key: p for p in (Cornerstone, Cutshort, Deel, Foundit, Greenhouse, Hirist, Instahyre, Lever,
                                SmartRecruiters, Workable, Workday)}
__all__ = ['Board', 'PLATFORMS', 'Platform']
