"""Each change from its first edit to merge or discard, and the user's edits.

Other modules import only what this file exports; everything else here is internal.
"""

from oversight.changes._cross_review import ReviewCapture
from oversight.changes._manager import ChangeManager
from oversight.changes._shortcut_detector import (
    DetectionResult,
    Finding,
    ShortcutKind,
    detect_shortcuts,
)
from oversight.changes._state import ChangeNotFound, Revision, WrongState

__all__ = [
    "ChangeManager",
    "ChangeNotFound",
    "DetectionResult",
    "Finding",
    "ReviewCapture",
    "Revision",
    "ShortcutKind",
    "WrongState",
    "detect_shortcuts",
]
