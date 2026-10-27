"""Compare checks, ignoring diagnostic messages and checked-call arguments in raises blocks."""

import ast

from oversight.changes._shortcuts_approx import approx_signature
from oversight.changes._shortcuts_common import (
    Finding,
    FuncNode,
    ShortcutKind,
    Source,
    is_literal_like,
    walk_local,
)
from oversight.changes._shortcuts_equivalence import (
    align_parameters,
    body_key,
    singleton_tightening,
)
from oversight.changes._shortcuts_normalize import inline_assertion_calls, normalize_assertions


def is_assert_call(node: ast.AST, source: Source) -> bool:
    if not isinstance(node, ast.Call):
        return False
    name = source.resolve(node.func)
    if name is None:
        return False
    return name.rsplit(".", 1)[-1].startswith("assert")


def _assert_nodes(func: FuncNode, source: Source) -> list[ast.Assert | ast.Call]:
    local = walk_local(func)
    nodes: list[ast.Assert | ast.Call] = []
    for node in local:
        if isinstance(node, ast.Call) and _raises_key(node, source) is not None:
            continue
        if isinstance(node, ast.Assert) or (
            isinstance(node, ast.Call) and is_assert_call(node, source)
        ):
            nodes.append(node)
    nodes.sort(key=lambda n: (n.lineno, n.col_offset))
    return nodes


def _check_pair(
    path: str,
    old_node: ast.Assert | ast.Call,
    new_node: ast.Assert | ast.Call,
    old: Source,
    new: Source,
) -> Finding | None:
    old_shape, old_tolerances = approx_signature(old_node, old)
    new_shape, new_tolerances = approx_signature(new_node, new)
    if old_shape == new_shape and any(
        before is not None
        and after is not None
        and any(right > left for left, right in zip(before, after, strict=True))
        for before, after in zip(old_tolerances, new_tolerances, strict=False)
    ):
        return Finding(
            path,
            new_node.lineno,
            ShortcutKind.ASSERTION_LOOSENED,
            "Please review: a pytest.approx tolerance was widened, "
            "so this assertion checks less precisely than before.",
        )
    if isinstance(old_node, ast.Assert) and isinstance(new_node, ast.Assert):
        old_test, new_test = old_node.test, new_node.test
        if (
            isinstance(old_test, ast.Compare)
            and len(old_test.ops) == 1
            and isinstance(old_test.ops[0], ast.Eq)
            and not isinstance(new_test, ast.Compare)
            and ast.dump(new_test) == ast.dump(old_test.left)
        ):
            return Finding(
                path=path,
                line=new_node.lineno,
                kind=ShortcutKind.ASSERTION_LOOSENED,
                message=(
                    "Please review: 'assert x == v' became 'assert x', "
                    "so the expected value is no longer checked."
                ),
            )
        if isinstance(old_test, ast.Compare) and isinstance(new_test, ast.Compare):
            if (
                len(old_test.ops) == 1
                and isinstance(old_test.ops[0], ast.Eq)
                and len(new_test.ops) == 1
                and isinstance(new_test.ops[0], (ast.In, ast.GtE, ast.LtE))
                and ast.dump(old_test.left) == ast.dump(new_test.left)
            ):
                return Finding(
                    path=path,
                    line=new_node.lineno,
                    kind=ShortcutKind.ASSERTION_LOOSENED,
                    message=(
                        "Please review: '==' became 'in', '>=' or '<=', "
                        "so this assertion is looser than before."
                    ),
                )
            if (
                len(old_test.ops) == 1
                and isinstance(old_test.ops[0], ast.Eq)
                and len(new_test.ops) == 1
                and isinstance(new_test.ops[0], ast.Eq)
                and ast.dump(old_test.left) == ast.dump(new_test.left)
                and len(old_test.comparators) == 1
                and len(new_test.comparators) == 1
                and is_literal_like(old_test.comparators[0])
                and is_literal_like(new_test.comparators[0])
                and ast.dump(old_test.comparators[0]) != ast.dump(new_test.comparators[0])
            ):
                return Finding(
                    path=path,
                    line=new_node.lineno,
                    kind=ShortcutKind.EXPECTED_VALUE_CHANGED,
                    message=(
                        "Please review: check the new expected value "
                        f"({ast.unparse(new_test.comparators[0])}); "
                        "the assertion now expects a different result."
                    ),
                )
    if isinstance(old_node, ast.Call) and isinstance(new_node, ast.Call):
        old_name = old.resolve(old_node.func)
        new_name = new.resolve(new_node.func)
        old_last = old_name.rsplit(".", 1)[-1] if old_name else ""
        new_last = new_name.rsplit(".", 1)[-1] if new_name else ""
        if old_last == "assertEqual" and new_last == "assertTrue":
            return Finding(
                path=path,
                line=new_node.lineno,
                kind=ShortcutKind.ASSERTION_LOOSENED,
                message=(
                    "Please review: assertEqual became assertTrue, "
                    "so the expected value is no longer checked."
                ),
            )
        if (
            old_last == "assertEqual"
            and new_last == "assertEqual"
            and len(old_node.args) >= 2
            and len(new_node.args) >= 2
            and ast.dump(old_node.args[0]) == ast.dump(new_node.args[0])
            and is_literal_like(old_node.args[1])
            and is_literal_like(new_node.args[1])
            and ast.dump(old_node.args[1]) != ast.dump(new_node.args[1])
        ):
            return Finding(
                path=path,
                line=new_node.lineno,
                kind=ShortcutKind.EXPECTED_VALUE_CHANGED,
                message=(
                    "Please review: check the new expected value "
                    f"({ast.unparse(new_node.args[1])}); "
                    "the assertion now expects a different result."
                ),
            )
    return None


def _raises_key(call: ast.Call, source: Source) -> str | None:
    name = source.resolve(call.func)
    last = name.rsplit(".", 1)[-1] if name else ""
    if last not in {"raises", "assertRaises", "assertRaisesRegex"} or not call.args:
        return None
    match = next((kw.value for kw in call.keywords if kw.arg == "match"), None)
    if last == "assertRaisesRegex" and len(call.args) >= 2:
        match = call.args[1]
    return ast.dump(call.args[0]) + ":" + (ast.dump(match) if match is not None else "")


def _raises_wrappers(func: FuncNode, source: Source) -> list[tuple[str, int]]:
    # Callable and context-manager forms check the same exception. The called
    # body/arguments may change without removing the exception expectation.
    return [
        (key, node.lineno)
        for node in walk_local(func)
        if isinstance(node, ast.Call) and (key := _raises_key(node, source)) is not None
    ]


def _assert_key(node: ast.Assert | ast.Call) -> str:
    return ast.dump(node.test if isinstance(node, ast.Assert) else node)


def detect_assertions(
    path: str,
    old: Source,
    new: Source,
) -> list[Finding]:
    old, new = normalize_assertions(old), normalize_assertions(new)
    findings: list[Finding] = []
    for qual, old_func in old.functions.items():
        new_func = new.functions.get(qual)
        if new_func is None:
            continue
        align_parameters(old, new, old_func, new_func)
        if body_key(old_func) == body_key(new_func):
            continue
        old_asserts = _assert_nodes(old_func, old)
        new_asserts = _assert_nodes(new_func, new)
        # Match by content first: swapping asserts is clean.
        unmatched_new: list[ast.Assert | ast.Call] = list(new_asserts)
        remaining_old: list[ast.Assert | ast.Call] = []
        for old_node in old_asserts:
            dump = _assert_key(old_node)
            match_idx: int | None = None
            for idx, cand in enumerate(unmatched_new):
                if _assert_key(cand) == dump:
                    match_idx = idx
                    break
            if match_idx is not None:
                unmatched_new.pop(match_idx)
            else:
                remaining_old.append(old_node)
        # Inlining is a fallback: a call edited before an unchanged assertion
        # does not mean that the assertion itself was removed.
        inline_assertion_calls(old_func)
        inline_assertion_calls(new_func)
        # Pair remaining by a loosening / expected-value relation.
        still_old: list[ast.Assert | ast.Call] = []
        for old_node in remaining_old:
            equivalent = next(
                (
                    i
                    for i, cand in enumerate(unmatched_new)
                    if _assert_key(cand) == _assert_key(old_node)
                ),
                None,
            )
            if equivalent is not None:
                unmatched_new.pop(equivalent)
                continue
            paired: Finding | None = None
            paired_idx: int | None = None
            for idx, cand in enumerate(unmatched_new):
                found = _check_pair(path, old_node, cand, old, new)
                if found is not None:
                    paired = found
                    paired_idx = idx
                    break
            # Same approx check with an equal, tighter or unknowable effective
            # tolerance is preserved, rather than treated as an assertion removal.
            if paired is None:
                old_shape, tolerances = approx_signature(old_node, old)
                approx_idx = next(
                    (
                        i
                        for i, cand in enumerate(unmatched_new)
                        if tolerances and approx_signature(cand, new)[0] == old_shape
                    ),
                    None,
                )
                if approx_idx is not None:
                    unmatched_new.pop(approx_idx)
                    continue
            if paired is not None and paired_idx is not None:
                findings.append(paired)
                unmatched_new.pop(paired_idx)
                continue
            # A tightening (the reverse of a loosening) is clean.
            tight_idx: int | None = None
            for idx, cand in enumerate(unmatched_new):
                reverse = _check_pair(path, cand, old_node, new, old)
                if (
                    reverse is not None and reverse.kind == ShortcutKind.ASSERTION_LOOSENED
                ) or singleton_tightening(old_node, cand):
                    tight_idx = idx
                    break
            if tight_idx is not None:
                unmatched_new.pop(tight_idx)
            else:
                still_old.append(old_node)
        for extra in still_old:
            findings.append(
                Finding(
                    path=path,
                    line=extra.lineno,
                    kind=ShortcutKind.ASSERTION_LOOSENED,
                    message=(
                        "Please review: an assertion was removed, "
                        "so this test checks less than before."
                    ),
                )
            )
        # Removing a raises wrapper around a call.
        old_wrappers = _raises_wrappers(old_func, old)
        new_wrappers = _raises_wrappers(new_func, new)
        unmatched_wrappers: list[tuple[str, int]] = list(new_wrappers)
        for key, line in old_wrappers:
            match_idx = None
            for idx, cand in enumerate(unmatched_wrappers):
                if cand[0] == key:
                    match_idx = idx
                    break
            if match_idx is not None:
                unmatched_wrappers.pop(match_idx)
            else:
                findings.append(
                    Finding(
                        path=path,
                        line=line,
                        kind=ShortcutKind.ASSERTION_LOOSENED,
                        message=(
                            "Please review: a 'pytest.raises' / 'assertRaises' "
                            "check was removed, so this test checks less than before."
                        ),
                    )
                )
    return findings
