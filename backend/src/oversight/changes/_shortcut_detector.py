"""A pure, deterministic shortcut detector for agent diffs; stdlib only.

Each change is ``(path, old, new)``; None denotes a missing version. Findings
ask for review: legitimate changes can trigger them, and a human decides.

Known limit: literals reaching the code under test only through a fixture's
return value are not tracked. Only explicit call inputs and non-expected
parametrize columns count. This module is not wired into the change manager.
"""

from collections.abc import Iterable

from oversight.changes._shortcuts_asserts import detect_assertions
from oversight.changes._shortcuts_common import Change, Source, is_test_file
from oversight.changes._shortcuts_common import Finding as Finding
from oversight.changes._shortcuts_common import ShortcutKind as ShortcutKind
from oversight.changes._shortcuts_literals import collect_test_literals, detect_literal_branches
from oversight.changes._shortcuts_skips import detect_skips
from oversight.changes._shortcuts_stubs import detect_stubs
from oversight.changes._shortcuts_tests import detect_parametrize_expected, match_tests


def detect_shortcuts(changes: Iterable[Change]) -> list[Finding]:
    """Return findings sorted by path, line and kind, without side effects."""
    change_list = sorted(changes, key=lambda change: change[0])
    findings: list[Finding] = []
    old_sources: dict[str, Source] = {}
    new_sources: dict[str, Source] = {}
    failed: set[str] = set()
    for path, old, new in change_list:
        for text, sources in ((old, old_sources), (new, new_sources)):
            if text is None:
                continue
            try:
                sources[path] = Source(text)
            except (SyntaxError, ValueError) as exc:
                if path not in failed:
                    findings.append(
                        Finding(
                            path,
                            (exc.lineno or 1) if isinstance(exc, SyntaxError) else 1,
                            ShortcutKind.PARSE_ERROR,
                            "Please review: this file does not parse, "
                            "so the other checks could not run on it.",
                        )
                    )
                    failed.add(path)
        if path in failed:
            old_sources.pop(path, None)
            new_sources.pop(path, None)

    # Exact moves preserve the baseline for every per-file check, including
    # class/module skip markers and comments in functions. Consume origins once.
    origins = [
        path
        for path, old, new in change_list
        if old is not None and new is None and path in old_sources
    ]
    moved: dict[str, str] = {}
    for path, old, new in change_list:
        if old is not None or new is None or path not in new_sources:
            continue
        origin = next(
            (
                p
                for p in origins
                if old_sources[p].text == new and is_test_file(p) == is_test_file(path)
            ),
            None,
        )
        if origin is not None:
            moved[path] = origin
            origins.remove(origin)

    removed, matches = match_tests(old_sources, new_sources)
    findings.extend(removed)
    for path, old, new in change_list:
        if (
            new is None
            and old is not None
            and is_test_file(path)
            and path not in moved.values()
            and path not in failed
            and not old_sources[path].tests
        ):
            findings.append(
                Finding(
                    path,
                    1,
                    ShortcutKind.TEST_REMOVED,
                    "Please review: a test file was deleted.",
                )
            )

    inputs: set[tuple[str, object]] = set()
    for path, source in new_sources.items():
        if is_test_file(path):
            inputs.update(collect_test_literals(source))

    for path, new in new_sources.items():
        old = old_sources.get(moved.get(path, path))
        if old is not None and old.text == new.text:
            continue
        if is_test_file(path):
            if old:
                findings.extend(detect_assertions(path, old, new))
                findings.extend(detect_parametrize_expected(path, old, new))
            findings.extend(detect_skips(path, old, new, old_sources, matches))
        else:
            findings.extend(
                detect_literal_branches(
                    path,
                    old.tree if old else None,
                    new.tree,
                    inputs,
                )
            )
        findings.extend(detect_stubs(path, old, new))
    return sorted(findings, key=lambda f: (f.path, f.line, f.kind, f.message))
