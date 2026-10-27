"""A pure shortcut detector for agent diffs: stdlib only, no model, no network.

The input is a change set: a list of ``(path, old, new)`` where ``old`` is
``None`` for a new file and ``new`` is ``None`` for a deleted one. Findings
ask for review rather than accusing: a legitimate change can trip several
of these, and a human decides.
"""

import ast
import io
import tokenize
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath


class ShortcutKind(StrEnum):
    TEST_REMOVED = "test_removed"
    ASSERTION_LOOSENED = "assertion_loosened"
    SKIP_ADDED = "skip_added"
    EXPECTED_VALUE_CHANGED = "expected_value_changed"
    LITERAL_BRANCH = "literal_branch"
    STUB_LEFT = "stub_left"
    PARSE_ERROR = "parse_error"


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    line: int
    kind: ShortcutKind
    message: str


type Change = tuple[str, str | None, str | None]
type FuncNode = ast.FunctionDef | ast.AsyncFunctionDef

_TEST_LITERALS_IGNORE: frozenset[object] = frozenset({"", 0, 1, -1})


def _is_test_file(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if not parts:
        return False
    name = parts[-1]
    if name == "conftest.py":
        return True
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith("_test.py"):
        return True
    return "tests" in parts[:-1] and name.endswith(".py")


def _is_test_function(qualname: str) -> bool:
    return qualname.rsplit(".", 1)[-1].startswith("test_")


def _dotted_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base is not None else node.attr
    if isinstance(node, ast.Call):
        return _dotted_name(node.func)
    return None


def _collect_functions(tree: ast.Module) -> dict[str, FuncNode]:
    result: dict[str, FuncNode] = {}

    def walk(node: ast.AST, prefix: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = ".".join([*prefix, child.name])
                key = qual
                suffix = 2
                while key in result:
                    key = f"{qual}#{suffix}"
                    suffix += 1
                result[key] = child
                walk(child, [*prefix, child.name])
            elif isinstance(child, ast.ClassDef):
                walk(child, [*prefix, child.name])
            else:
                walk(child, prefix)

    walk(tree, [])
    return result


def _walk_local(func: FuncNode) -> list[ast.AST]:
    """Nodes owned by ``func`` itself, not by a nested function or class."""
    out: list[ast.AST] = []
    stack: list[ast.AST] = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        out.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return out


def _is_assert_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    name = _dotted_name(node.func)
    if name is None:
        return False
    return name.rsplit(".", 1)[-1].startswith("assert")


def _assert_nodes(func: FuncNode) -> list[ast.Assert | ast.Call]:
    nodes: list[ast.Assert | ast.Call] = []
    for node in _walk_local(func):
        if isinstance(node, ast.Assert) or (isinstance(node, ast.Call) and _is_assert_call(node)):
            nodes.append(node)
    nodes.sort(key=lambda n: (n.lineno, n.col_offset))
    return nodes


def _body_dump(func: FuncNode) -> tuple[str, ...]:
    return tuple(ast.dump(stmt) for stmt in func.body)


def _parse(source: str) -> ast.Module:
    return ast.parse(source)


def _todo_comments(source: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                upper = tok.string.upper()
                if "TODO" in upper or "FIXME" in upper:
                    out.append((tok.start[0], tok.string.strip()))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return out
    return out


def _skip_markers(func: FuncNode) -> list[tuple[str, int]]:
    markers: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for dec in func.decorator_list:
        name = _dotted_name(dec)
        if name is not None and ("skip" in name.lower() or "xfail" in name.lower()):
            marker = (name.lower(), dec.lineno)
            if marker not in seen:
                seen.add(marker)
                markers.append(marker)
    for node in _walk_local(func):
        if isinstance(node, ast.Call):
            name = _dotted_name(node.func)
            if name is not None:
                last = name.rsplit(".", 1)[-1].lower()
                if last in {"skip", "xfail", "skiptest"}:
                    marker = (name.lower(), node.lineno)
                    if marker not in seen:
                        seen.add(marker)
                        markers.append(marker)
    return markers


def _approx_tolerances(node: ast.AST) -> list[tuple[str, float, float]]:
    out: list[tuple[str, float, float]] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        name = _dotted_name(child.func)
        if name is None or name.rsplit(".", 1)[-1] != "approx":
            continue
        if not child.args:
            continue
        expected = ast.dump(child.args[0])
        rel = 1e-6
        abs_tol = 1e-12
        if (
            len(child.args) > 1
            and isinstance(child.args[1], ast.Constant)
            and isinstance(child.args[1].value, (int, float))
        ):
            rel = float(child.args[1].value)
        for kw in child.keywords:
            if (
                kw.arg in {"rel", "abs"}
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, (int, float))
            ):
                if kw.arg == "rel":
                    rel = float(kw.value.value)
                else:
                    abs_tol = float(kw.value.value)
        out.append((expected, rel, abs_tol))
    return out


def _literal_key(value: object) -> tuple[str, object] | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (str, int, float)) and value not in _TEST_LITERALS_IGNORE:
        return (type(value).__name__, value)
    return None


def _test_literals(tree: ast.Module) -> set[tuple[str, object]]:
    literals: set[tuple[str, object]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _is_assert_call(node):
            continue
        name = _dotted_name(node.func)
        if name is not None and name.rsplit(".", 1)[-1] in {"approx", "skip", "xfail", "raises"}:
            continue
        for arg in [*node.args, *[kw.value for kw in node.keywords]]:
            for sub in ast.walk(arg):
                if isinstance(sub, ast.Constant):
                    key = _literal_key(sub.value)
                    if key is not None:
                        literals.add(key)
    return literals


def _detect_test_removed(
    path: str,
    old_funcs: dict[str, FuncNode],
    new_funcs: dict[str, FuncNode],
) -> list[Finding]:
    findings: list[Finding] = []
    old_tests = {q: n for q, n in old_funcs.items() if _is_test_function(q)}
    new_tests = {q: n for q, n in new_funcs.items() if _is_test_function(q)}
    added = {q: n for q, n in new_funcs.items() if q not in old_funcs}
    for qual, node in old_tests.items():
        if qual in new_tests:
            continue
        counterpart: FuncNode | None = None
        for cand in added.values():
            if _body_dump(cand) == _body_dump(node):
                counterpart = cand
                break
        if counterpart is not None and counterpart.name.startswith("test_"):
            continue
        if counterpart is not None:
            # Renamed to a name pytest no longer collects.
            name = counterpart.name
            findings.append(
                Finding(
                    path=path,
                    line=counterpart.lineno,
                    kind=ShortcutKind.TEST_REMOVED,
                    message=(
                        f"Please review: test '{qual}' appears renamed to "
                        f"'{name}', which pytest no longer collects."
                    ),
                )
            )
        else:
            findings.append(
                Finding(
                    path=path,
                    line=node.lineno,
                    kind=ShortcutKind.TEST_REMOVED,
                    message=f"Please review: test '{qual}' was removed.",
                )
            )
    return findings


def _check_pair(
    path: str, old_node: ast.Assert | ast.Call, new_node: ast.Assert | ast.Call
) -> Finding | None:
    # Wider pytest.approx tolerance.
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
        # assert x == v becoming assert x
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
            # Expected value changed.
            if (
                len(old_test.ops) == 1
                and isinstance(old_test.ops[0], ast.Eq)
                and len(new_test.ops) == 1
                and isinstance(new_test.ops[0], ast.Eq)
                and ast.dump(old_test.left) == ast.dump(new_test.left)
                and len(old_test.comparators) == 1
                and len(new_test.comparators) == 1
                and isinstance(old_test.comparators[0], ast.Constant)
                and isinstance(new_test.comparators[0], ast.Constant)
                and old_test.comparators[0].value != new_test.comparators[0].value
            ):
                return Finding(
                    path=path,
                    line=new_node.lineno,
                    kind=ShortcutKind.EXPECTED_VALUE_CHANGED,
                    message=(
                        "Please review: check the new expected value "
                        f"({new_test.comparators[0].value!r}); "
                        "the assertion now expects a different result."
                    ),
                )
    if isinstance(old_node, ast.Call) and isinstance(new_node, ast.Call):
        old_name = _dotted_name(old_node.func)
        new_name = _dotted_name(new_node.func)
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
            and isinstance(old_node.args[1], ast.Constant)
            and isinstance(new_node.args[1], ast.Constant)
            and old_node.args[1].value != new_node.args[1].value
        ):
            return Finding(
                path=path,
                line=new_node.lineno,
                kind=ShortcutKind.EXPECTED_VALUE_CHANGED,
                message=(
                    "Please review: check the new expected value "
                    f"({new_node.args[1].value!r}); "
                    "the assertion now expects a different result."
                ),
            )
    return None


def _detect_assertions(
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
        for old_node, new_node in zip(old_asserts, new_asserts, strict=False):
            found = _check_pair(path, old_node, new_node)
            if found is not None:
                findings.append(found)
        if len(new_asserts) < len(old_asserts):
            for extra in old_asserts[len(new_asserts) :]:
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
    return findings


def _detect_skips(
    path: str,
    old_funcs: dict[str, FuncNode],
    new_funcs: dict[str, FuncNode],
) -> list[Finding]:
    findings: list[Finding] = []
    for qual, new_func in new_funcs.items():
        old_func = old_funcs.get(qual)
        old_keys: set[str] = (
            {k for k, _ in _skip_markers(old_func)} if old_func is not None else set()
        )
        for key, line in _skip_markers(new_func):
            if key not in old_keys:
                findings.append(
                    Finding(
                        path=path,
                        line=line,
                        kind=ShortcutKind.SKIP_ADDED,
                        message=(
                            f"Please review: a skip or xfail ('{key}') was added, "
                            "so this test may no longer run."
                        ),
                    )
                )
    return findings


def _detect_literal_branches(
    path: str,
    old_tree: ast.Module | None,
    new_tree: ast.Module,
    test_literals: set[tuple[str, object]],
) -> list[Finding]:
    if not test_literals:
        return []
    old_dumps: set[str] = set()
    if old_tree is not None:
        old_dumps = {ast.dump(n) for n in ast.walk(old_tree) if isinstance(n, ast.Compare)}
    findings: list[Finding] = []
    for node in ast.walk(new_tree):
        if not isinstance(node, (ast.If, ast.While, ast.IfExp)):
            continue
        for sub in ast.walk(node.test):
            if not isinstance(sub, ast.Compare) or ast.dump(sub) in old_dumps:
                continue
            matched: object = None
            for const in ast.walk(sub):
                if isinstance(const, ast.Constant):
                    key = _literal_key(const.value)
                    if key is not None and key in test_literals:
                        matched = const.value
                        break
            if matched is not None:
                findings.append(
                    Finding(
                        path=path,
                        line=node.lineno,
                        kind=ShortcutKind.LITERAL_BRANCH,
                        message=(
                            f"Please review: this code branches on {matched!r}, "
                            "a literal also used as an input in the tests; "
                            "check it is not special-casing the test."
                        ),
                    )
                )
                break
    return findings


def _detect_stubs(
    path: str,
    old_funcs: dict[str, FuncNode],
    new_funcs: dict[str, FuncNode],
    old_source: str | None,
    new_source: str,
) -> list[Finding]:
    touched: dict[str, FuncNode] = {}
    for qual, func in new_funcs.items():
        old_func = old_funcs.get(qual)
        if (
            old_func is None
            or ast.dump(old_func) != ast.dump(func)
            or (
                old_source is not None
                and ast.get_source_segment(old_source, old_func)
                != ast.get_source_segment(new_source, func)
            )
        ):
            touched[qual] = func
    findings: list[Finding] = []
    for func in touched.values():
        for node in _walk_local(func):
            if isinstance(node, ast.Pass):
                findings.append(
                    Finding(
                        path=path,
                        line=node.lineno,
                        kind=ShortcutKind.STUB_LEFT,
                        message=(
                            "Please review: a 'pass' placeholder was left "
                            "in a function this change touched."
                        ),
                    )
                )
            elif (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and node.value.value is Ellipsis
            ):
                findings.append(
                    Finding(
                        path=path,
                        line=node.lineno,
                        kind=ShortcutKind.STUB_LEFT,
                        message=(
                            "Please review: a '...' placeholder was left "
                            "in a function this change touched."
                        ),
                    )
                )
            elif isinstance(node, ast.Raise) and node.exc is not None:
                exc = node.exc
                name = _dotted_name(exc.func if isinstance(exc, ast.Call) else exc)
                if name is not None and name.rsplit(".", 1)[-1] == "NotImplementedError":
                    findings.append(
                        Finding(
                            path=path,
                            line=node.lineno,
                            kind=ShortcutKind.STUB_LEFT,
                            message=(
                                "Please review: NotImplementedError was left "
                                "in a function this change touched."
                            ),
                        )
                    )
    # TODO/FIXME comments inside a touched function's range.
    comments = _todo_comments(new_source)
    if comments and touched:
        for line, text in comments:
            containing = [
                f for f in touched.values() if f.lineno <= line <= (f.end_lineno or f.lineno)
            ]
            if containing:
                # Innermost touched function wins.
                containing.sort(key=lambda f: (f.end_lineno or f.lineno) - f.lineno)
                findings.append(
                    Finding(
                        path=path,
                        line=line,
                        kind=ShortcutKind.STUB_LEFT,
                        message=(
                            f"Please review: a TODO/FIXME comment ('{text}') was left "
                            "in a function this change touched."
                        ),
                    )
                )
    return findings


def detect_shortcuts(changes: Iterable[Change]) -> list[Finding]:
    """Return findings for a change set, sorted by path, line and kind."""
    change_list = list(changes)
    findings: list[Finding] = []

    parsed: dict[
        str, tuple[str | None, str | None, ast.Module | None, ast.Module | None, bool]
    ] = {}
    for path, old, new in change_list:
        old_tree: ast.Module | None = None
        new_tree: ast.Module | None = None
        parse_failed = False
        for source, is_new in ((old, False), (new, True)):
            if source is None:
                continue
            try:
                tree = _parse(source)
            except SyntaxError as exc:
                if not parse_failed:
                    findings.append(
                        Finding(
                            path=path,
                            line=exc.lineno or 1,
                            kind=ShortcutKind.PARSE_ERROR,
                            message=(
                                "Please review: this file does not parse, "
                                "so the other checks could not run on it."
                            ),
                        )
                    )
                    parse_failed = True
                continue
            if is_new:
                new_tree = tree
            else:
                old_tree = tree
        parsed[path] = (old, new, old_tree, new_tree, parse_failed)

    # Literals used as inputs in the change set's (new) tests.
    test_literals: set[tuple[str, object]] = set()
    for path, (_old, _new, _old_tree, new_tree, _failed) in parsed.items():
        if new_tree is not None and _is_test_file(path):
            test_literals |= _test_literals(new_tree)

    for path, old, new in change_list:
        _o, _n, old_tree, new_tree, parse_failed = parsed[path]
        if parse_failed:
            if new is None and old is not None and _is_test_file(path):
                findings.append(
                    Finding(
                        path=path,
                        line=1,
                        kind=ShortcutKind.TEST_REMOVED,
                        message="Please review: a test file was deleted.",
                    )
                )
            continue
        if new is None:
            if old is not None and _is_test_file(path):
                findings.append(
                    Finding(
                        path=path,
                        line=1,
                        kind=ShortcutKind.TEST_REMOVED,
                        message="Please review: a test file was deleted.",
                    )
                )
            continue
        if new_tree is None:
            continue  # Parse error already reported, or empty change.
        old_funcs = _collect_functions(old_tree) if old_tree is not None else {}
        new_funcs = _collect_functions(new_tree)
        is_test = _is_test_file(path)
        if old is not None and old == new:
            continue
        if is_test and old_tree is not None:
            findings.extend(_detect_test_removed(path, old_funcs, new_funcs))
        if is_test:
            if old_tree is not None:
                findings.extend(_detect_assertions(path, old_funcs, new_funcs))
            findings.extend(_detect_skips(path, old_funcs, new_funcs))
        if not is_test:
            findings.extend(_detect_literal_branches(path, old_tree, new_tree, test_literals))
        findings.extend(_detect_stubs(path, old_funcs, new_funcs, old, new))

    findings.sort(key=lambda f: (f.path, f.line, f.kind, f.message))
    return findings
