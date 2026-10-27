"""Test identities, strict renames and parametrized expected values."""

import ast
import copy
import time
from dataclasses import dataclass
from difflib import SequenceMatcher

from oversight.changes._shortcuts_common import (
    Finding,
    FuncNode,
    ShortcutKind,
    Source,
    is_collected_file,
    is_literal_like,
    is_test_file,
    walk_local,
)


class _LocalNames(ast.NodeTransformer):
    def __init__(self, names: dict[str, str]) -> None:
        self.names = names

    def visit_Name(self, node: ast.Name) -> ast.Name:
        node.id = self.names.get(node.id, node.id)
        return node


def _callee_identity(source: Source, expr: ast.expr) -> str:
    resolved = source.resolve(expr)
    root = expr
    while isinstance(root, ast.Attribute):
        root = root.value
    if isinstance(root, ast.Name) and root.id in {"self", "cls"}:
        return ast.dump(expr)
    imported = (
        isinstance(root, ast.Name)
        and root.id not in {"self", "cls"}
        and source.scopes.get(root, {}).get(root.id) is not None
    )
    if imported and resolved is not None:
        return f"@import:{resolved.rsplit('.', 1)[-1]}"
    return resolved or ast.dump(expr)


def _rename_key(source: Source, func: FuncNode) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Only alpha-equivalent bodies with identical final called names are renames.

    No similarity scores, extraction/inlining or call substitutions. Nested
    scopes are left exact: uncertain matches become a neutral removal finding.
    """
    local: dict[str, str] = {}
    for arg in [*func.args.posonlyargs, *func.args.args, *func.args.kwonlyargs]:
        local.setdefault(arg.arg, f"@{len(local)}")
    for arg in (func.args.vararg, func.args.kwarg):
        if arg:
            local.setdefault(arg.arg, f"@{len(local)}")
    for stmt in func.body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                local.setdefault(node.id, f"@{len(local)}")
    # Binding changes in nested scopes are ambiguous; require an exact body there.
    if any(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda))
        for stmt in func.body
        for n in ast.walk(stmt)
    ):
        local = {}
    normalizer = _LocalNames(local)
    body: list[str] = []
    for index, stmt in enumerate(func.body):
        if (
            index == 0
            and isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        ):
            continue
        copied = copy.deepcopy(stmt)
        # Import paths can change during a move. Preserve the final callee name
        # (add versus sub still differs), rather than the module or import alias.
        for original, clone in zip(list(ast.walk(stmt)), list(ast.walk(copied)), strict=True):
            if isinstance(original, ast.Call) and isinstance(clone, ast.Call):
                identity = _callee_identity(source, original.func)
                if identity.startswith("@import:"):
                    clone.func = ast.Name(id=identity, ctx=ast.Load())
        body.append(ast.dump(normalizer.visit(copied)))
    calls = tuple(
        _callee_identity(source, node.func)
        for stmt in func.body
        for node in ast.walk(stmt)
        if isinstance(node, ast.Call)
    )
    return tuple(body), calls


type TestRef = tuple[str, str]


def same_test_body(old: Source, before: FuncNode, new: Source, after: FuncNode) -> bool:
    return _rename_key(old, before) == _rename_key(new, after)


def _expression_checks(expr: ast.expr) -> int:
    if isinstance(expr, ast.BoolOp) and isinstance(expr.op, ast.And):
        return sum(_expression_checks(value) for value in expr.values)
    if (
        isinstance(expr, ast.Compare)
        and len(expr.ops) == 1
        and isinstance(expr.ops[0], ast.Eq)
        and isinstance(expr.left, (ast.Tuple, ast.List))
        and isinstance(expr.comparators[0], (ast.Tuple, ast.List))
        and len(expr.left.elts) == len(expr.comparators[0].elts)
    ):
        return len(expr.left.elts)
    return 1


def _check_count(source: Source, func: FuncNode) -> int:
    count = 0
    for node in walk_local(func):
        if isinstance(node, ast.Assert):
            count += _expression_checks(node.test)
        elif isinstance(node, ast.Call):
            name = source.resolve(node.func) or ""
            if name.rsplit(".", 1)[-1].startswith("assert") or name == "pytest.raises":
                count += 1
    return count


def _similarity(old: Source, before: FuncNode, new: Source, after: FuncNode) -> float:
    """A collected replacement must retain check count and share called behavior.

    Exact matches are consumed first. Fuzzy matches need substantial body overlap
    as well as a shared callee; unrelated tests cannot pair by check count alone.
    """
    if _check_count(new, after) < _check_count(old, before):
        return 0

    def expectations(func: FuncNode) -> dict[str, set[str]]:
        values: dict[str, set[str]] = {}
        for node in walk_local(func):
            if isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare):
                check = node.test
                if (
                    len(check.ops) == 1
                    and isinstance(check.ops[0], ast.Eq)
                    and is_literal_like(check.comparators[0])
                ):
                    values.setdefault(ast.dump(check.left), set()).add(
                        ast.dump(check.comparators[0])
                    )
        return values

    previous, current = expectations(before), expectations(after)
    if any(not values & current[key] for key, values in previous.items() if key in current):
        return 0
    left, old_calls = _rename_key(old, before)
    right, new_calls = _rename_key(new, after)
    old_set, new_set = set(old_calls), set(new_calls)
    if old_set and not old_set & new_set:
        return 0
    ratio = SequenceMatcher(None, "\n".join(left), "\n".join(right), autojunk=False).ratio()
    overlap = len(old_set & new_set) / max(1, len(old_set | new_set))
    old_checks = {ast.dump(n.test) for n in walk_local(before) if isinstance(n, ast.Assert)}
    new_checks = {ast.dump(n.test) for n in walk_local(after) if isinstance(n, ast.Assert)}
    if old_checks and old_checks <= new_checks and (overlap >= 0.5 or not old_set | new_set):
        return 1
    return ratio if ratio >= 0.65 and (overlap >= 0.5 or not old_set | new_set) else 0


def match_tests(
    old: dict[str, Source], new: dict[str, Source], *, deadline: float = float("inf")
) -> tuple[list[Finding], dict[TestRef, TestRef]]:
    if sum(len(s.functions) for s in [*old.values(), *new.values()]) > 1200:
        raise TimeoutError("test matching function cap (1200 versions)")
    pool = [
        (path, qual, func)
        for path, src in sorted(new.items())
        if is_test_file(path)
        for qual, func in src.functions.items()
        if path not in old or qual not in old[path].functions
    ]
    # Prefer collected destinations, then stable path and source order.
    pool.sort(key=lambda item: (item[1] not in new[item[0]].tests, item[0], item[2].lineno))
    findings: list[Finding] = []
    matches: dict[TestRef, TestRef] = {}
    # Compute structural keys once, rather than for every old/new pair.
    keys = {(p, q): _rename_key(new[p], f) for p, q, f in pool}
    comparisons = 0
    for path, src in sorted(old.items()):
        if (
            not is_collected_file(path)
            and not path.endswith("/conftest.py")
            and path != "conftest.py"
        ):
            continue
        for qual, node in src.tests.items():
            if time.monotonic() >= deadline:
                raise TimeoutError("test matching timeout")
            if path in new and qual in new[path].tests:
                matches[path, qual] = (path, qual)
                continue
            key = _rename_key(src, node)
            match = next((i for i, (p, _q, _f) in enumerate(pool) if keys[p, _q] == key), None)
            if match is None:
                comparisons += len(pool)
                if comparisons > 400:
                    raise TimeoutError("test matching comparison cap (400 pairs)")
                scores = [
                    (_similarity(src, node, new[p], f), p == path, -i, i)
                    for i, (p, q, f) in enumerate(pool)
                    if is_collected_file(p) and q in new[p].tests
                ]
                if scores and (best := max(scores))[0] > 0:
                    match = best[3]
            target_path, target_line = path, node.lineno
            explanation = f"test '{qual}' was removed or replaced."
            if match is not None:
                p, q, f = pool.pop(match)
                matches[p, q] = (path, qual)
                if is_collected_file(p) and q in new[p].tests:
                    continue
                target_path, target_line = p, f.lineno
                explanation = (
                    f"test '{qual}' appears renamed to '{q}', which pytest no longer collects."
                )
            findings.append(
                Finding(
                    target_path,
                    target_line,
                    ShortcutKind.TEST_REMOVED,
                    f"Please review: {explanation}",
                )
            )
    return findings, matches


@dataclass(frozen=True)
class Parameters:
    names: tuple[str, ...]
    expected: frozenset[int]
    cases: ast.List | ast.Tuple
    # A string with one name takes a whole collection as one value. A names
    # sequence, including ['expected'], requires an outer row wrapper in pytest.
    single_string: bool

    def elements(self, case: ast.expr, source: Source) -> list[ast.expr]:
        if isinstance(case, ast.Call) and source.resolve(case.func) == "pytest.param":
            return list(case.args)
        if self.single_string:
            return [case]
        return list(case.elts) if isinstance(case, (ast.Tuple, ast.List)) else [case]


def parametrize_parts(call: ast.Call, source: Source) -> Parameters | None:
    if source.resolve(call.func) != "pytest.mark.parametrize" or len(call.args) < 2:
        return None
    names_node, cases = call.args[:2]
    single_string = False
    if isinstance(names_node, ast.Constant) and isinstance(names_node.value, str):
        names = tuple(n.strip() for n in names_node.value.split(",") if n.strip())
        single_string = len(names) == 1
    elif isinstance(names_node, (ast.List, ast.Tuple)):
        names = tuple(
            n.value
            for n in names_node.elts
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        )
    else:
        return None
    if not names or not isinstance(cases, (ast.List, ast.Tuple)):
        return None
    expected_names = {
        node.test.comparators[0].id
        for definition in source.definitions.values()
        if call in definition.decorator_list
        for node in ast.walk(definition)
        if isinstance(node, ast.Assert)
        and isinstance(node.test, ast.Compare)
        and len(node.test.ops) == 1
        and isinstance(node.test.ops[0], ast.Eq)
        and isinstance(node.test.comparators[0], ast.Name)
        and isinstance(node.test.left, ast.Call)
    }
    expected = frozenset(
        i for i, name in enumerate(names) if "expected" in name.lower() or name in expected_names
    )
    return Parameters(names, expected, cases, single_string)


@dataclass(frozen=True)
class _Case:
    inputs: tuple[str, ...]
    expected: tuple[str, ...]
    display: str
    line: int


def _collect_parametrize(source: Source) -> dict[tuple[str, tuple[str, ...]], list[_Case]]:
    result: dict[tuple[str, tuple[str, ...]], list[_Case]] = {}
    for owner, definition in source.definitions.items():
        for dec in definition.decorator_list:
            parts = parametrize_parts(dec, source) if isinstance(dec, ast.Call) else None
            if parts is None:
                continue
            entries = result.setdefault((owner, parts.names), [])
            for case in parts.cases.elts:
                values = parts.elements(case, source)
                entries.append(
                    _Case(
                        tuple(ast.dump(v) for i, v in enumerate(values) if i not in parts.expected),
                        tuple(ast.dump(v) for i, v in enumerate(values) if i in parts.expected),
                        ", ".join(
                            ast.unparse(v) for i, v in enumerate(values) if i in parts.expected
                        ),
                        case.lineno,
                    )
                )
    return result


class _InlineParameters(ast.NodeTransformer):
    def __init__(self, values: dict[str, ast.expr]) -> None:
        self.values = values

    def visit_Name(self, node: ast.Name) -> ast.expr:
        if isinstance(node.ctx, ast.Load) and node.id in self.values:
            return copy.deepcopy(self.values[node.id])
        return node


def single_case_inlined(old: Source, before: str, new: Source, after: str) -> bool:
    left, right = old.functions.get(before), new.functions.get(after)
    if left is None or right is None:
        return False
    if any(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef))
        for stmt in left.body
        for n in ast.walk(stmt)
    ):
        return False
    for dec in left.decorator_list:
        parts = parametrize_parts(dec, old) if isinstance(dec, ast.Call) else None
        if parts is None or len(parts.cases.elts) != 1:
            continue
        values = parts.elements(parts.cases.elts[0], old)
        if len(values) != len(parts.names) or any(not is_literal_like(v) for v in values):
            continue
        if any(
            arg.arg in parts.names
            for arg in [*right.args.posonlyargs, *right.args.args, *right.args.kwonlyargs]
        ):
            continue
        # No stores or nested scopes may shadow the substituted parameters.
        if any(
            isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id in parts.names
            for n in walk_local(left)
        ):
            continue
        inline = _InlineParameters(dict(zip(parts.names, values, strict=True)))
        body = [ast.dump(inline.visit(copy.deepcopy(stmt))) for stmt in left.body]
        if body == [ast.dump(stmt) for stmt in right.body]:
            return True
    return False


def _compare_rows(path: str, old_entries: list[_Case], new_entries: list[_Case]) -> list[Finding]:
    remaining = list(new_entries)
    unmatched: list[_Case] = []
    # Cancel the complete multiset first, including duplicate inputs/rows.
    for entry in old_entries:
        idx = next(
            (
                i
                for i, c in enumerate(remaining)
                if (c.inputs, c.expected) == (entry.inputs, entry.expected)
            ),
            None,
        )
        if idx is not None:
            remaining.pop(idx)
        else:
            unmatched.append(entry)
    findings: list[Finding] = []
    for entry in unmatched:
        idx = next((i for i, c in enumerate(remaining) if c.inputs == entry.inputs), None)
        if idx is None:
            findings.append(
                Finding(
                    path,
                    entry.line,
                    ShortcutKind.ASSERTION_LOOSENED,
                    "Please review: a parametrize row was removed.",
                )
            )
            continue
        changed = remaining.pop(idx)
        findings.append(
            Finding(
                path,
                changed.line,
                ShortcutKind.EXPECTED_VALUE_CHANGED,
                f"Please review: check the new expected value ({changed.display}); "
                "the assertion now expects a different result.",
            )
        )
    return findings


def detect_parametrize_expected(
    path: str,
    old: Source,
    new: Source,
    pairs: list[tuple[str, str]] | None = None,
) -> list[Finding]:
    old_map, new_map = _collect_parametrize(old), _collect_parametrize(new)
    owners = dict(pairs) if pairs is not None else None
    if owners is not None:
        for before, after in pairs or []:
            # Class decorators supply parameters to every method. Follow all
            # enclosing owners when a matched method is renamed or moved.
            while "." in before and "." in after:
                before, after = before.rsplit(".", 1)[0], after.rsplit(".", 1)[0]
                if before in old.classes and after in new.classes:
                    owners[before] = after
    findings: list[Finding] = []
    for key, old_entries in old_map.items():
        owner, names = key
        if owners is not None:
            if owner not in owners:
                continue
            owner = owners[owner]
        if owner not in new.definitions:
            continue  # Test removals/renames are handled by match_tests and pairs.
        if (owner, names) not in new_map:
            if any(
                other_owner == owner
                and len(other_names) == len(names)
                and [(c.inputs, c.expected) for c in entries]
                == [(c.inputs, c.expected) for c in old_entries]
                for (other_owner, other_names), entries in new_map.items()
            ):
                continue  # Identical rows with renamed columns retain the checks.
            if single_case_inlined(old, key[0], new, owner):
                continue
            definition = new.definitions[owner]
            if any(
                isinstance(dec, ast.Call)
                and new.resolve(dec.func) == "pytest.mark.parametrize"
                and parametrize_parts(dec, new) is None
                for dec in definition.decorator_list
            ):
                continue  # The table is opaque: detect_unknown supplies the reason.
        findings.extend(_compare_rows(path, old_entries, new_map.get((owner, names), [])))
    return findings
