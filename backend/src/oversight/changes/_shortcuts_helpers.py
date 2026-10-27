"""Retain checks in local helpers and calls that execute those helpers."""

import ast
from collections import Counter

from oversight.changes._shortcuts_common import (
    Finding,
    FuncNode,
    ShortcutKind,
    Source,
    is_test_file,
    walk_local,
)
from oversight.changes._shortcuts_tests import TestRef, same_test_body


def _has_checks(func: FuncNode, source: Source) -> bool:
    return any(
        isinstance(n, ast.Assert)
        or (
            isinstance(n, ast.Call)
            and (source.resolve(n.func) or "").rsplit(".", 1)[-1].startswith("assert")
        )
        for n in walk_local(func)
    )


def _calls(func: FuncNode, helpers: dict[str, str]) -> Counter[str]:
    calls: Counter[str] = Counter()
    for node in walk_local(func):
        if isinstance(node, ast.Call):
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in {"self", "cls"}
                    else ""
                )
            )
            if name in helpers:
                calls[helpers[name]] += 1
    return calls


def _is_helper(qual: str, source: Source) -> bool:
    parent = qual.rpartition(".")[0]
    return qual not in source.tests and (
        not parent
        or (parent in source.classes and any(q.startswith(parent + ".") for q in source.tests))
    )


def detect_helpers(
    old: dict[str, Source], new: dict[str, Source], matches: dict[TestRef, TestRef]
) -> list[Finding]:
    findings: list[Finding] = []
    helpers = [
        (p, q, f)
        for p, src in old.items()
        if is_test_file(p)
        for q, f in src.functions.items()
        if _is_helper(q, src) and _has_checks(f, src)
    ]
    targets = [
        (p, q, f)
        for p, src in new.items()
        if is_test_file(p)
        for q, f in src.functions.items()
        if _is_helper(q, src)
    ]
    identities: dict[TestRef, TestRef] = {}
    consumed: set[TestRef] = set()
    for p, q, func in helpers:
        target = next(((np, nq) for np, nq, _ in targets if (np, nq) == (p, q)), None)
        if target is None:
            target = next(
                (
                    (np, nq)
                    for np, nq, nf in targets
                    if (np, nq) not in consumed and same_test_body(old[p], func, new[np], nf)
                ),
                None,
            )
        if target is None:
            findings.append(
                Finding(
                    p,
                    func.lineno,
                    ShortcutKind.ASSERTION_LOOSENED,
                    f"Please review: asserting helper '{q}' was removed or replaced.",
                )
            )
        else:
            consumed.add(target)
            identities[target] = (p, q)
    for (np, nq), (op, oq) in matches.items():
        if nq not in new[np].tests:
            continue
        before = {f.name: q for p, q, f in helpers if p == op}
        after = {
            new[p].functions[q].name: old_q
            for (p, q), (old_p, old_q) in identities.items()
            if p == np and old_p == op
        }
        if _calls(old[op].tests[oq], before) - _calls(new[np].tests[nq], after):
            findings.append(
                Finding(
                    np,
                    new[np].tests[nq].lineno,
                    ShortcutKind.ASSERTION_LOOSENED,
                    "Please review: a call to an asserting helper was removed.",
                )
            )
    return findings
