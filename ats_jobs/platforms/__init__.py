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
from .naukri import Naukri
from .smartrecruiters import SmartRecruiters
from .welcometothejungle import WelcomeToTheJungle
from .workable import Workable
from .workday import Workday

PLATFORMS = {p.key: p for p in (Cornerstone, Cutshort, Deel, Foundit, Greenhouse, Hirist, Instahyre, Lever, Naukri,
                                SmartRecruiters, WelcomeToTheJungle, Workable, Workday)}
__all__ = ['Board', 'PLATFORMS', 'Platform']
