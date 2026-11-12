"""Every platform the engine reads."""
from .ashby import Ashby
from .base import Board, Platform
from .cornerstone import Cornerstone
from .cutshort import Cutshort
from .deel import Deel
from .efinancialcareers import EFinancialCareers
from .foundit import Foundit
from .greenhouse import Greenhouse
from .hiringcafe import HiringCafe
from .hirist import Hirist
from .instahyre import Instahyre
from .lever import Lever
from .naukri import Naukri
from .smartrecruiters import SmartRecruiters
from .welcometothejungle import WelcomeToTheJungle
from .wellfound import Wellfound
from .workable import Workable
from .workday import Workday
from .ycombinator import YCombinator

PLATFORMS = {p.key: p for p in (Ashby, Cornerstone, Cutshort, Deel, EFinancialCareers, Foundit, Greenhouse, HiringCafe, Hirist,
                                Instahyre, Lever, Naukri, SmartRecruiters, WelcomeToTheJungle, Wellfound, Workable, Workday,
                                YCombinator)}
__all__ = ['Board', 'PLATFORMS', 'Platform']
