"""Review helpers that are not wired into the product yet.

Other modules import only what this file exports; everything else here is
internal. The shortcut detector is a pure function over a change set and
imports only the standard library.
"""

from oversight.review._detector import Change, Finding, ShortcutKind, detect_shortcuts

__all__ = ["Change", "Finding", "ShortcutKind", "detect_shortcuts"]
