"""Retain checks in local helpers and calls that execute those helpers."""

import ast
from collections import Counter
from pathlib import PurePosixPath

from oversight.changes._shortcuts_common import (
    Finding,
    FuncNode,
    ShortcutKind,
    Source,
    check_deadline,
    is_test_file,
    walk_local,
)
from oversight.changes._shortcuts_tests import TestRef, same_test_body


def _always_true(node: ast.expr) -> bool:
    try:
        return bool(ast.literal_eval(node))
    except (ValueError, TypeError, SyntaxError):
        return False


def _has_checks(func: FuncNode, source: Source) -> bool:
    # Schema validation alone is data preparation, not an asserting helper.
    return any(
        (isinstance(n, ast.Assert) and not _always_true(n.test))
        or (
            isinstance(n, ast.Call)
            and (source.resolve(n.func) or "").rsplit(".", 1)[-1].startswith("assert")
        )
        for n in walk_local(func)
    )


def _validations(func: FuncNode, source: Source) -> Counter[str]:
    return Counter(
        source.resolve(n.func) or ast.dump(n.func)
        for n in walk_local(func)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in {"model_validate", "model_validate_json"}
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


def _module_index(sources: dict[str, Source], deadline: float) -> dict[str, list[Source]]:
    index: dict[str, list[Source]] = {}
    for path, source in sources.items():
        check_deadline(deadline)
        parts = list(PurePosixPath(path).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        for start in range(len(parts)):
            index.setdefault(".".join(parts[start:]), []).append(source)
    return index


def _imported_target(
    resolved: str, path: str, index: dict[str, list[Source]], deadline: float
) -> tuple[Source, FuncNode | None] | None:
    if resolved.startswith("."):
        level = len(resolved) - len(resolved.lstrip("."))
        parent = PurePosixPath(path).parent
        for _ in range(level - 1):
            parent = parent.parent
        resolved = ".".join((*parent.parts, resolved.lstrip(".")))
    candidates: list[tuple[Source, FuncNode | None]] = []
    parts = resolved.split(".")
    for split in range(1, len(parts)):
        check_deadline(deadline)
        for target in index.get(".".join(parts[:split]), []):
            check_deadline(deadline)
            name = ".".join(parts[split:])
            func = target.functions.get(name)
            # A visible re-export has an opaque destination. A module without
            # either the function or an imported binding is not a candidate.
            binding = target.scopes[target.tree].get(parts[split])
            if func is not None or binding is not None:
                candidates.append((target, func))
    return candidates[0] if len(candidates) == 1 else None


def _imported_calls(
    func: FuncNode,
    source: Source,
    helpers: dict[str, str],
    path: str,
    index: dict[str, list[Source]],
    validations: dict[str, Counter[str]],
) -> tuple[Counter[str], Counter[str]]:
    calls: Counter[str] = Counter()
    opaque: Counter[str] = Counter()
    for node in walk_local(func):
        check_deadline(source.deadline)
        if not isinstance(node, ast.Call):
            continue
        root = node.func
        while isinstance(root, ast.Attribute):
            root = root.value
        resolved = source.resolve(node.func)
        if (
            isinstance(root, ast.Name)
            and root.id not in {"self", "cls"}
            and source.scopes.get(root, {}).get(root.id) is not None
            and resolved is not None
            and (name := resolved.rsplit(".", 1)[-1]) in helpers
        ):
            target = _imported_target(resolved, path, index, source.deadline)
            if target is None or target[1] is None:
                opaque[helpers[name]] += 1
            elif _has_checks(target[1], target[0]) or (
                # Preserve the closed extraction case: validation already in an
                # asserting helper may survive extraction (683fe05). Validation
                # does not make an otherwise non-asserting helper into a check.
                validations[helpers[name]]
                and not (validations[helpers[name]] - _validations(target[1], target[0]))
            ):
                calls[helpers[name]] += 1
    return calls, opaque


def _unknown_target(path: str, line: int) -> Finding:
    return Finding(
        path,
        line,
        ShortcutKind.UNKNOWN,
        "Unknown: imported asserting helper target is not visible or is ambiguous "
        "in this change; checks cannot be compared. Please review.",
    )


def _match_helpers(
    helpers: list[tuple[str, str, FuncNode]],
    targets: list[tuple[str, str, FuncNode]],
    old: dict[str, Source],
    new: dict[str, Source],
    matches: dict[TestRef, TestRef],
    imported: dict[TestRef, tuple[Counter[str], Counter[str]]],
) -> tuple[dict[TestRef, TestRef], list[Finding]]:
    identities: dict[TestRef, TestRef] = {}
    findings: list[Finding] = []
    consumed: set[TestRef] = set()
    for p, q, func in helpers:
        target = next(((np, nq) for np, nq, _ in targets if (np, nq) == (p, q)), None)
        opaque_refs = [
            ref
            for ref, (old_path, _) in matches.items()
            if old_path == p and imported.get(ref, (Counter(), Counter()))[1][q]
        ]
        if target is None and not opaque_refs:
            target = next(
                (
                    (np, nq)
                    for np, nq, nf in targets
                    if (np, nq) not in consumed and same_test_body(old[p], func, new[np], nf)
                ),
                None,
            )
        if target is None:
            for np, nq in opaque_refs:
                findings.append(_unknown_target(np, new[np].tests[nq].lineno))
            if any(
                old_path == p
                and any(counts[q] for counts in imported.get(ref, (Counter(), Counter())))
                for ref, (old_path, _old_qual) in matches.items()
            ):
                continue  # Checks are visible, or the extraction is explicitly unknown.
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
    return identities, findings


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
    deadline = min((s.deadline for s in new.values()), default=float("inf"))
    index = _module_index(new, deadline)
    imported = {
        (np, nq): _imported_calls(
            new[np].tests[nq],
            new[np],
            {f.name: q for p, q, f in helpers if p == op},
            np,
            index,
            {q: _validations(f, old[p]) for p, q, f in helpers if p == op},
        )
        for (np, nq), (op, _oq) in matches.items()
        if nq in new[np].tests
    }
    identities, helper_findings = _match_helpers(helpers, targets, old, new, matches, imported)
    findings.extend(helper_findings)
    for (np, nq), (op, oq) in matches.items():
        if nq not in new[np].tests:
            continue
        before = {f.name: q for p, q, f in helpers if p == op}
        after = {
            new[p].functions[q].name: old_q
            for (p, q), (old_p, old_q) in identities.items()
            if p == np and old_p == op
        }
        if _calls(old[op].tests[oq], before) - (
            _calls(new[np].tests[nq], after) + sum(imported.get((np, nq), ()), Counter[str]())
        ):
            findings.append(
                Finding(
                    np,
                    new[np].tests[nq].lineno,
                    ShortcutKind.ASSERTION_LOOSENED,
                    "Please review: a call to an asserting helper was removed.",
                )
            )
    return findings
