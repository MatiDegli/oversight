"""Compare checks, ignoring diagnostic messages and checked-call arguments in raises blocks."""

import ast

from oversight.changes._shortcuts_common import (
    Finding,
    FuncNode,
    ShortcutKind,
    dotted_name,
    is_literal_like,
    walk_local,
)


def is_assert_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    name = dotted_name(node.func)
    if name is None:
        return False
    return name.rsplit(".", 1)[-1].startswith("assert")


def _assert_nodes(func: FuncNode) -> list[ast.Assert | ast.Call]:
    raises_ctx: set[int] = set()
    local = walk_local(func)
    for node in local:
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if isinstance(item.context_expr, ast.Call):
                    name = dotted_name(item.context_expr.func)
                    if name is not None and name.rsplit(".", 1)[-1] in {
                        "raises",
                        "assertRaises",
                        "assertRaisesRegex",
                    }:
                        raises_ctx.add(id(item.context_expr))
    nodes: list[ast.Assert | ast.Call] = []
    for node in local:
        if id(node) in raises_ctx:
            continue
        if isinstance(node, ast.Assert) or (isinstance(node, ast.Call) and is_assert_call(node)):
            nodes.append(node)
    nodes.sort(key=lambda n: (n.lineno, n.col_offset))
    return nodes


def _const_number(node: ast.expr) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        if isinstance(node.value, bool):
            return None
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        inner = _const_number(node.operand)
        if inner is not None:
            return -inner if isinstance(node.op, ast.USub) else inner
    return None


def _approx_tolerances(node: ast.AST) -> list[tuple[str, float, float]]:
    out: list[tuple[str, float, float]] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        name = dotted_name(child.func)
        if name is None or name.rsplit(".", 1)[-1] != "approx":
            continue
        if not child.args:
            continue
        expected = ast.dump(child.args[0])
        rel = 1e-6
        abs_tol = 1e-12
        if len(child.args) > 1:
            value = _const_number(child.args[1])
            if value is not None:
                rel = value
        if len(child.args) > 2:
            value = _const_number(child.args[2])
            if value is not None:
                abs_tol = value
        for kw in child.keywords:
            if kw.arg in {"rel", "abs"}:
                value = _const_number(kw.value)
                if value is not None:
                    if kw.arg == "rel":
                        rel = value
                    else:
                        abs_tol = value
        out.append((expected, rel, abs_tol))
    return out


def _check_pair(
    path: str, old_node: ast.Assert | ast.Call, new_node: ast.Assert | ast.Call
) -> Finding | None:
    old_approx = _approx_tolerances(old_node)
    new_approx = _approx_tolerances(new_node)
    if old_approx and new_approx:
        new_by_expected = {exp: (rel, ab) for exp, rel, ab in new_approx}
        for exp, rel, ab in old_approx:
            if exp in new_by_expected:
                new_rel, new_abs = new_by_expected[exp]
                if new_rel > rel or new_abs > ab:
                    return Finding(
                        path=path,
                        line=new_node.lineno,
                        kind=ShortcutKind.ASSERTION_LOOSENED,
                        message=(
                            "Please review: a pytest.approx tolerance was widened, "
                            "so this assertion checks less precisely than before."
                        ),
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
        old_name = dotted_name(old_node.func)
        new_name = dotted_name(new_node.func)
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


def _raises_wrappers(func: FuncNode) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for node in walk_local(func):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            ctx = item.context_expr
            if not isinstance(ctx, ast.Call):
                continue
            name = dotted_name(ctx.func)
            if name is None:
                continue
            last = name.rsplit(".", 1)[-1]
            if last not in {"raises", "assertRaises", "assertRaisesRegex"}:
                continue
            out.append((ast.dump(ctx), node.lineno))
    return out


def _assert_key(node: ast.Assert | ast.Call) -> str:
    return ast.dump(node.test if isinstance(node, ast.Assert) else node)


def detect_assertions(
    path: str,
    old_funcs: dict[str, FuncNode],
    new_funcs: dict[str, FuncNode],
) -> list[Finding]:
    findings: list[Finding] = []
    for qual, old_func in old_funcs.items():
        new_func = new_funcs.get(qual)
        if new_func is None:
            continue
        old_asserts = _assert_nodes(old_func)
        new_asserts = _assert_nodes(new_func)
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
        # Pair remaining by a loosening / expected-value relation.
        still_old: list[ast.Assert | ast.Call] = []
        for old_node in remaining_old:
            paired: Finding | None = None
            paired_idx: int | None = None
            for idx, cand in enumerate(unmatched_new):
                found = _check_pair(path, old_node, cand)
                if found is not None:
                    paired = found
                    paired_idx = idx
                    break
            if paired is not None and paired_idx is not None:
                findings.append(paired)
                unmatched_new.pop(paired_idx)
                continue
            # A tightening (the reverse of a loosening) is clean.
            tight_idx: int | None = None
            for idx, cand in enumerate(unmatched_new):
                reverse = _check_pair(path, cand, old_node)
                if reverse is not None and reverse.kind == ShortcutKind.ASSERTION_LOOSENED:
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
        old_wrappers = _raises_wrappers(old_func)
        new_wrappers = _raises_wrappers(new_func)
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
