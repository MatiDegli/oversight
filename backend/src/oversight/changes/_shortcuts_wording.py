"""Cancel reworded string checks only when explicit numeric constraints survive."""

import ast
import re
from collections import Counter

from oversight.changes._shortcuts_common import check_deadline


def _groups(nodes: list[ast.Assert | ast.Call]) -> dict[str, list[tuple[int, str]]]:
    result: dict[str, list[tuple[int, str]]] = {}
    for index, node in enumerate(nodes):
        if not isinstance(node, ast.Assert) or not isinstance(node.test, ast.Compare):
            continue
        check = node.test
        if (
            len(check.ops) == 1
            and isinstance(check.ops[0], ast.In)
            and isinstance(check.left, ast.Constant)
            and isinstance(check.left.value, str)
        ):
            result.setdefault(ast.dump(check.comparators[0]), []).append((index, check.left.value))
    return result


def _components(
    left: list[tuple[int, str]], right: list[tuple[int, str]], deadline: float
) -> list[tuple[list[tuple[int, str]], list[tuple[int, str]]]]:
    """Join only checks sharing words across versions, never just a receiver.

    Several checks may become one message check. An unrelated check on the
    same result must survive cancellation even when the numeric checks agree.
    """
    entries = [(0, index, text) for index, text in left] + [
        (1, index, text) for index, text in right
    ]
    words = [set(re.findall(r"[^\W\d_]+", text.lower())) for _, _, text in entries]
    pending = set(range(len(entries)))
    result: list[tuple[list[tuple[int, str]], list[tuple[int, str]]]] = []
    while pending:
        queue = [min(pending)]
        pending.remove(queue[0])
        before: list[tuple[int, str]] = []
        after: list[tuple[int, str]] = []
        for current in queue:
            check_deadline(deadline)
            side, index, text = entries[current]
            (before if side == 0 else after).append((index, text))
            neighbors = sorted(
                other
                for other in pending
                if entries[other][0] != side and words[current] & words[other]
            )
            pending.difference_update(neighbors)
            queue.extend(neighbors)
        result.append((before, after))
    return result


def _same_constraints(before: list[tuple[int, str]], after: list[tuple[int, str]]) -> bool:
    old_text = " ".join(text for _, text in before).lower()
    new_text = " ".join(text for _, text in after).lower()
    numbers = Counter(re.findall(r"\b\d+(?:\.\d+)?\b", old_text))
    words = set(re.findall(r"[^\W\d_]+", old_text)) & set(re.findall(r"[^\W\d_]+", new_text))
    weaker = any(
        phrase in new_text and phrase not in old_text
        for phrase in (
            "at least",
            "at most",
            "more than",
            "less than",
            "up to",
            "minimum",
            "maximum",
            "roughly",
            "approximately",
        )
    )
    return bool(
        numbers
        and numbers == Counter(re.findall(r"\b\d+(?:\.\d+)?\b", new_text))
        and words
        and not weaker
    )


def _direct_match(before: str, after: str) -> bool:
    """Every old check needs its own evidence in one surviving message.

    A check without numbers may be part of a split message, but a numeric
    constraint must survive in that message, not across a component's links.
    """
    before, after = before.lower(), after.lower()
    numbers = Counter(re.findall(r"\b\d+(?:\.\d+)?\b", before))
    old_words = set(re.findall(r"[^\W\d_]+", before))
    words = old_words & set(re.findall(r"[^\W\d_]+", after))
    # One word can anchor punctuation-only wording or a numeric comparison
    # with two explicit operands. A single number cannot replace a lost word.
    anchored = len(words) >= 2 or bool(words and (words == old_words or sum(numbers.values()) >= 2))
    return anchored and numbers <= Counter(re.findall(r"\b\d+(?:\.\d+)?\b", after))


def cancel_reworded(
    before: list[ast.Assert | ast.Call],
    after: list[ast.Assert | ast.Call],
    *,
    deadline: float = float("inf"),
) -> tuple[list[ast.Assert | ast.Call], list[ast.Assert | ast.Call]]:
    left, right = _groups(before), _groups(after)
    matched_old: set[int] = set()
    matched_new: set[int] = set()
    for key in sorted(left.keys() & right.keys()):
        for old_entries, new_entries in _components(left[key], right[key], deadline):
            old_entries = [
                entry
                for entry in old_entries
                if any(_direct_match(entry[1], text) for _, text in new_entries)
            ]
            if _same_constraints(old_entries, new_entries):
                matched_old.update(i for i, _ in old_entries)
                matched_new.update(i for i, _ in new_entries)
    return (
        [node for i, node in enumerate(before) if i not in matched_old],
        [node for i, node in enumerate(after) if i not in matched_new],
    )
