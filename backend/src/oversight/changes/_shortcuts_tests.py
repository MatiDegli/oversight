"""Test identities, strict renames and parametrized expected values."""

import ast
import copy
from dataclasses import dataclass

from oversight.changes._shortcuts_common import (
    Finding,
    FuncNode,
    ShortcutKind,
    Source,
    is_test_file,
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


def match_tests(
    old: dict[str, Source], new: dict[str, Source]
) -> tuple[list[Finding], dict[TestRef, TestRef]]:
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
    for path, src in sorted(old.items()):
        if not is_test_file(path):
            continue
        for qual, node in src.tests.items():
            if path in new and qual in new[path].tests:
                matches[path, qual] = (path, qual)
                continue
            key = _rename_key(src, node)
            match = next(
                (i for i, (p, _q, f) in enumerate(pool) if _rename_key(new[p], f) == key), None
            )
            target_path, target_line = path, node.lineno
            explanation = f"test '{qual}' was removed or replaced."
            if match is not None:
                p, q, f = pool.pop(match)
                matches[p, q] = (path, qual)
                if q in new[p].tests:
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
    expected = frozenset(i for i, name in enumerate(names) if "expected" in name.lower())
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
            if parts is None or not parts.expected:
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


def detect_parametrize_expected(path: str, old: Source, new: Source) -> list[Finding]:
    old_map, new_map = _collect_parametrize(old), _collect_parametrize(new)
    findings: list[Finding] = []
    for key, old_entries in old_map.items():
        remaining = list(new_map.get(key, []))
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
        for entry in unmatched:
            idx = next((i for i, c in enumerate(remaining) if c.inputs == entry.inputs), None)
            if idx is None:
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
