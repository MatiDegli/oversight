"""Each change from its first edit to merge or discard, and the user's edits.

Other modules import only what this file exports; everything else here is internal.
"""

from oversight.changes._manager import ChangeManager
from oversight.changes._mode import ModeController
from oversight.changes._review_port import ReviewCapture, ReviewPort
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
    "ModeController",
    "ReviewCapture",
    "ReviewPort",
    "Revision",
    "ShortcutKind",
    "WrongState",
    "detect_shortcuts",
]
