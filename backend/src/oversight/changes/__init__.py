"""Each change from its first edit to merge or discard, and the user's edits.

Other modules import only what this file exports; everything else here is internal.
"""

from oversight.changes._manager import ChangeManager, ChangeNotFound, Revision, WrongState
from oversight.changes._shortcut_detector import (
    DetectionResult,
    Finding,
    ShortcutKind,
    detect_shortcuts,
)

__all__ = [
    "ChangeManager",
    "ChangeNotFound",
    "DetectionResult",
    "Finding",
    "Revision",
    "ShortcutKind",
    "WrongState",
    "detect_shortcuts",
]
