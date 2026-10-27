"""Cancel reworded string checks only when explicit numeric constraints survive."""

import ast
import re
from collections import Counter


def cancel_reworded(
    before: list[ast.Assert | ast.Call], after: list[ast.Assert | ast.Call]
) -> tuple[list[ast.Assert | ast.Call], list[ast.Assert | ast.Call]]:
    def groups(nodes: list[ast.Assert | ast.Call]) -> dict[str, list[tuple[int, str]]]:
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
                result.setdefault(ast.dump(check.comparators[0]), []).append(
                    (index, check.left.value)
                )
        return result

    left, right = groups(before), groups(after)
    matched_old: set[int] = set()
    matched_new: set[int] = set()
    for key in left.keys() & right.keys():
        old_text = " ".join(text for _, text in left[key]).lower()
        new_text = " ".join(text for _, text in right[key]).lower()
        numbers = Counter(re.findall(r"\b\d+(?:\.\d+)?\b", old_text))
        words = set(re.findall(r"\w+", old_text)) & set(re.findall(r"\w+", new_text))
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
        if (
            numbers
            and numbers == Counter(re.findall(r"\b\d+(?:\.\d+)?\b", new_text))
            and len(words) >= 2
            and not weaker
        ):
            matched_old.update(i for i, _ in left[key])
            matched_new.update(i for i, _ in right[key])
    return (
        [node for i, node in enumerate(before) if i not in matched_old],
        [node for i, node in enumerate(after) if i not in matched_new],
    )
