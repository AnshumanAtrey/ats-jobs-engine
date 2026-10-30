"""Every platform the engine reads."""
from .base import Board, Platform
from .cornerstone import Cornerstone

PLATFORMS = {p.key: p for p in (Cornerstone,)}
__all__ = ['Board', 'PLATFORMS', 'Platform']
