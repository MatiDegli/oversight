"""A pure shortcut detector for agent diffs: stdlib only, no model, no network.

The input is a change set: a list of ``(path, old, new)`` where ``old`` is
``None`` for a new file and ``new`` is ``None`` for a deleted one. Findings
ask for review rather than accusing: a legitimate change can trip several
of these, and a human decides.

Known limits: a literal that reaches the code under test only through a
fixture's return value is not tracked as a test input, so a branch on that
literal in non-test code is not reported. Only literals written directly
in a test call (or in a non-expected ``parametrize`` column) count as
inputs.
"""

import ast
import io
import tokenize
from collections import Counter
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

_SIMILARITY_THRESHOLD = 0.65

_DECORATOR_SKIP: frozenset[str] = frozenset(
    {
        "pytest.mark.skip",
        "pytest.mark.skipif",
        "pytest.mark.xfail",
        "unittest.skip",
        "unittest.skipIf",
        "unittest.skipUnless",
        "unittest.expectedFailure",
    }
)
_CALL_SKIP: frozenset[str] = frozenset({"pytest.skip", "pytest.xfail"})


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


def _dotted_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base is not None else node.attr
    if isinstance(node, ast.Call):
        return _dotted_name(node.func)
    return None


def _import_map(tree: ast.Module) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname is not None:
                    mapping[alias.asname] = alias.name
                else:
                    first = alias.name.split(".", 1)[0]
                    mapping[first] = first
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            module = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                mapping[local] = f"{module}.{alias.name}" if module else alias.name
    return mapping


def _resolved_name(node: ast.expr, imports: dict[str, str]) -> str | None:
    raw = _dotted_name(node)
    if raw is None:
        return None
    if raw.startswith("self."):
        return raw
    parts = raw.split(".")
    if parts[0] in imports:
        rest = ".".join(parts[1:])
        return f"{imports[parts[0]]}.{rest}" if rest else imports[parts[0]]
    return raw


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


def _collect_classes(tree: ast.Module) -> dict[str, ast.ClassDef]:
    result: dict[str, ast.ClassDef] = {}

    def walk(node: ast.AST, prefix: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                qual = ".".join([*prefix, child.name])
                key = qual
                suffix = 2
                while key in result:
                    key = f"{qual}#{suffix}"
                    suffix += 1
                result[key] = child
                walk(child, [*prefix, child.name])
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, [*prefix, child.name])
            else:
                walk(child, prefix)

    walk(tree, [])
    return result


def _class_is_collectable(node: ast.ClassDef) -> bool:
    for base in node.bases:
        name = _dotted_name(base)
        if name is not None and name.rsplit(".", 1)[-1] == "TestCase":
            return True
    if not node.name.startswith("Test"):
        return False
    return not any(
        isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == "__init__"
        for child in node.body
    )


def _collect_tests(tree: ast.Module) -> dict[str, FuncNode]:
    """Tests pytest would collect: module ``test_*`` functions and
    ``test_*`` methods of collectable ``Test*`` classes (no ``__init__``).
    A nested ``test_*`` function inside another function is not a test.
    """
    result: dict[str, FuncNode] = {}
    for child in tree.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if child.name.startswith("test_"):
                result[child.name] = child
        elif isinstance(child, ast.ClassDef) and _class_is_collectable(child):
            for item in child.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                    item.name.startswith("test_")
                ):
                    result[f"{child.name}.{item.name}"] = item
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
    raises_ctx: set[int] = set()
    local = _walk_local(func)
    for node in local:
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if isinstance(item.context_expr, ast.Call):
                    name = _dotted_name(item.context_expr.func)
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


# -- body similarity (rename matching) ---------------------------------------


def _body_features(func: FuncNode) -> Counter[str]:
    features: Counter[str] = Counter()
    for stmt in func.body:
        for node in ast.walk(stmt):
            features[type(node).__name__] += 1
            if isinstance(node, ast.Constant):
                features[f"const:{node.value!r}"] += 1
            elif isinstance(node, ast.Call):
                name = _dotted_name(node.func)
                if name is not None:
                    features[f"call:{name}"] += 1
            elif isinstance(node, ast.Attribute):
                features[f"attr:{node.attr}"] += 1
            elif isinstance(node, ast.keyword) and node.arg is not None:
                features[f"kw:{node.arg}"] += 1
    return features


def _body_similarity(old: FuncNode, new: FuncNode) -> float:
    if _body_dump(old) == _body_dump(new):
        return 1.0
    old_f = _body_features(old)
    new_f = _body_features(new)
    total = sum(old_f.values()) + sum(new_f.values())
    if total == 0:
        return 1.0
    common = sum((old_f & new_f).values())
    return 2.0 * common / total


@dataclass
class _Candidate:
    path: str
    qual: str
    node: FuncNode
    collected: bool


def _detect_test_removed_global(
    old_tests: dict[str, dict[str, FuncNode]],
    new_tests: dict[str, dict[str, FuncNode]],
    old_funcs: dict[str, dict[str, FuncNode]],
    new_funcs: dict[str, dict[str, FuncNode]],
) -> list[Finding]:
    pool: list[_Candidate] = []
    for path in sorted(new_funcs):
        for qual, node in sorted(new_funcs[path].items(), key=lambda kv: (kv[1].lineno, kv[0])):
            if qual in old_funcs.get(path, {}):
                continue
            pool.append(
                _Candidate(
                    path=path, qual=qual, node=node, collected=qual in new_tests.get(path, {})
                )
            )
    findings: list[Finding] = []
    removed: list[tuple[str, str, FuncNode]] = []
    for path in sorted(old_tests):
        for qual, node in sorted(old_tests[path].items(), key=lambda kv: (kv[1].lineno, kv[0])):
            if qual in new_tests.get(path, {}):
                continue
            removed.append((path, qual, node))
    removed.sort(key=lambda item: (item[0], item[2].lineno, item[1]))
    for path, qual, node in removed:
        best: _Candidate | None = None
        best_sim = -1.0
        for cand in pool:
            sim = _body_similarity(node, cand.node)
            if sim < _SIMILARITY_THRESHOLD:
                continue
            better = sim > best_sim + 1e-9 or (
                abs(sim - best_sim) <= 1e-9
                and best is not None
                and cand.collected
                and not best.collected
            )
            if best is None or better:
                best = cand
                best_sim = sim
        if best is not None:
            pool.remove(best)
            if best.collected:
                continue
            findings.append(
                Finding(
                    path=best.path,
                    line=best.node.lineno,
                    kind=ShortcutKind.TEST_REMOVED,
                    message=(
                        f"Please review: test '{qual}' appears renamed to "
                        f"'{best.node.name}', which pytest no longer collects."
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


# -- skip markers --------------------------------------------------------------


def _skip_decorators(decorators: list[ast.expr], imports: dict[str, str]) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for dec in decorators:
        resolved = _resolved_name(dec, imports)
        if resolved is not None and resolved in _DECORATOR_SKIP:
            marker = (resolved, dec.lineno)
            if marker not in seen:
                seen.add(marker)
                out.append(marker)
    return out


def _skip_markers(func: FuncNode, imports: dict[str, str]) -> list[tuple[str, int]]:
    markers: list[tuple[str, int]] = list(_skip_decorators(func.decorator_list, imports))
    seen: set[tuple[str, int]] = set(markers)
    for node in _walk_local(func):
        if isinstance(node, ast.Call):
            resolved = _resolved_name(node.func, imports)
            if resolved is not None and resolved in _CALL_SKIP:
                marker = (resolved, node.lineno)
                if marker not in seen:
                    seen.add(marker)
                    markers.append(marker)
    return markers


def _module_skip_markers(tree: ast.Module, imports: dict[str, str]) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()

    def add(resolved: str, line: int) -> None:
        marker = (resolved, line)
        if marker not in seen:
            seen.add(marker)
            out.append(marker)

    for stmt in tree.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
            resolved = _resolved_name(call.func, imports)
            if resolved is not None and resolved in _CALL_SKIP:
                for kw in call.keywords:
                    if (
                        kw.arg == "allow_module_level"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                    ):
                        add(resolved, call.lineno)
        target_is_pytestmark = False
        value: ast.expr | None = None
        if isinstance(stmt, ast.Assign):
            target_is_pytestmark = any(
                isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets
            )
            value = stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            target_is_pytestmark = isinstance(stmt.target, ast.Name) and (
                stmt.target.id == "pytestmark"
            )
            value = stmt.value
        if target_is_pytestmark and value is not None:
            for sub in ast.walk(value):
                if isinstance(sub, (ast.Call, ast.Attribute, ast.Name)):
                    resolved = _resolved_name(sub, imports)
                    if resolved is not None and resolved in _DECORATOR_SKIP:
                        add(resolved, sub.lineno)
    return out


def _detect_skips(
    path: str,
    old_tree: ast.Module | None,
    new_tree: ast.Module,
    old_funcs: dict[str, FuncNode],
    new_funcs: dict[str, FuncNode],
    old_classes: dict[str, ast.ClassDef],
    new_classes: dict[str, ast.ClassDef],
) -> list[Finding]:
    findings: list[Finding] = []
    old_imports = _import_map(old_tree) if old_tree is not None else {}
    new_imports = _import_map(new_tree)
    for qual, new_func in new_funcs.items():
        old_func = old_funcs.get(qual)
        old_keys: set[str] = (
            {k for k, _ in _skip_markers(old_func, old_imports)}
            if old_func is not None
            else set[str]()
        )
        for key, line in _skip_markers(new_func, new_imports):
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
    for qual, new_class in new_classes.items():
        old_class = old_classes.get(qual)
        old_keys = (
            {k for k, _ in _skip_decorators(old_class.decorator_list, old_imports)}
            if old_class is not None
            else set[str]()
        )
        for key, line in _skip_decorators(new_class.decorator_list, new_imports):
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
    old_module: set[str] = (
        {k for k, _ in _module_skip_markers(old_tree, old_imports)}
        if old_tree is not None
        else set[str]()
    )
    for key, line in _module_skip_markers(new_tree, new_imports):
        if key not in old_module:
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


# -- literals, approx, expected values ----------------------------------------


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
        name = _dotted_name(child.func)
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


def _literal_key(value: object) -> tuple[str, object] | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (str, int, float)):
        return (type(value).__name__, value)
    return None


def _literal_value_of(node: ast.expr) -> tuple[str, object] | None:
    if isinstance(node, ast.Constant):
        return _literal_key(node.value)
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.USub, ast.UAdd))
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float))
    ):
        if isinstance(node.operand.value, bool):
            return None
        value = node.operand.value
        signed = -value if isinstance(node.op, ast.USub) else value
        return (type(signed).__name__, signed)
    return None


def _is_literal_like(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return _is_literal_like(node.operand)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_literal_like(elt) for elt in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            (key is None or _is_literal_like(key)) and _is_literal_like(value)
            for key, value in zip(node.keys, node.values, strict=True)
        )
    if isinstance(node, ast.Starred):
        return _is_literal_like(node.value)
    return False


def _parametrize_parts(
    call: ast.Call, imports: dict[str, str]
) -> tuple[list[str], set[int], ast.expr | None] | None:
    resolved = _resolved_name(call.func, imports)
    if resolved is None:
        raw = _dotted_name(call.func)
        if raw is None or raw.rsplit(".", 1)[-1] != "parametrize":
            return None
    elif resolved.rsplit(".", 1)[-1] != "parametrize":
        return None
    if len(call.args) < 2:
        return None
    names_node = call.args[0]
    argnames: list[str] = []
    if isinstance(names_node, ast.Constant) and isinstance(names_node.value, str):
        argnames = [part.strip() for part in names_node.value.split(",") if part.strip()]
    elif isinstance(names_node, (ast.List, ast.Tuple)):
        for elt in names_node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                argnames.append(elt.value)
    if not argnames:
        return None
    expected_idx = {i for i, n in enumerate(argnames) if "expected" in n.lower()}
    return (argnames, expected_idx, call.args[1])


def _case_elements(case: ast.expr) -> list[ast.expr]:
    if isinstance(case, (ast.Tuple, ast.List)):
        return list(case.elts)
    if isinstance(case, ast.Call):
        name = _dotted_name(case.func)
        if name is not None and name.rsplit(".", 1)[-1] == "param":
            return list(case.args)
        return [case]
    return [case]


def _expected_node_ids(tree: ast.Module, imports: dict[str, str]) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            for sub in ast.walk(node.test):
                if isinstance(sub, ast.Compare):
                    for comparator in sub.comparators:
                        for inner in ast.walk(comparator):
                            ids.add(id(inner))
        elif isinstance(node, ast.Call) and _is_assert_call(node):
            if len(node.args) >= 2:
                for inner in ast.walk(node.args[1]):
                    ids.add(id(inner))
        elif isinstance(node, ast.Call):
            parts = _parametrize_parts(node, imports)
            if parts is not None:
                _names, expected_idx, cases_node = parts
                if expected_idx and isinstance(cases_node, (ast.List, ast.Tuple)):
                    for case in cases_node.elts:
                        elements = _case_elements(case)
                        for idx in expected_idx:
                            if idx < len(elements):
                                for inner in ast.walk(elements[idx]):
                                    ids.add(id(inner))
    return ids


def _collect_literal_from(
    node: ast.AST, expected_ids: set[int], out: set[tuple[str, object]]
) -> None:
    if id(node) in expected_ids:
        return
    if isinstance(node, ast.expr):
        value = _literal_value_of(node)
        if value is not None:
            out.add(value)
            return
    for child in ast.iter_child_nodes(node):
        _collect_literal_from(child, expected_ids, out)


def _test_literals(tree: ast.Module) -> set[tuple[str, object]]:
    imports = _import_map(tree)
    expected_ids = _expected_node_ids(tree, imports)
    literals: set[tuple[str, object]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _is_assert_call(node):
            continue
        resolved = _resolved_name(node.func, imports)
        last = resolved.rsplit(".", 1)[-1] if resolved else ""
        if last in {"approx", "skip", "xfail", "raises", "assertRaises", "assertRaisesRegex"}:
            continue
        if resolved is not None and (resolved in _DECORATOR_SKIP or resolved in _CALL_SKIP):
            continue
        if last == "parametrize":
            parts = _parametrize_parts(node, imports)
            if parts is None:
                continue
            _names, expected_idx, cases_node = parts
            if isinstance(cases_node, (ast.List, ast.Tuple)):
                for case in cases_node.elts:
                    elements = _case_elements(case)
                    for idx, element in enumerate(elements):
                        if idx in expected_idx:
                            continue
                        _collect_literal_from(element, expected_ids, literals)
            continue
        for arg in [*node.args, *[kw.value for kw in node.keywords]]:
            _collect_literal_from(arg, expected_ids, literals)
    return literals


def _literals_in_expr(node: ast.AST) -> list[tuple[str, object]]:
    found: list[tuple[str, object]] = []
    seen: set[tuple[str, object]] = set()

    def visit(current: ast.AST) -> None:
        if isinstance(current, ast.expr):
            value = _literal_value_of(current)
            if value is not None:
                if value not in seen:
                    seen.add(value)
                    found.append(value)
                return
        for child in ast.iter_child_nodes(current):
            visit(child)

    visit(node)
    return found


# -- assertions ----------------------------------------------------------------


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
                and _is_literal_like(old_test.comparators[0])
                and _is_literal_like(new_test.comparators[0])
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
            and _is_literal_like(old_node.args[1])
            and _is_literal_like(new_node.args[1])
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


def _raises_wrappers(func: FuncNode) -> list[tuple[str, tuple[str, ...], int]]:
    out: list[tuple[str, tuple[str, ...], int]] = []
    for node in _walk_local(func):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            ctx = item.context_expr
            if not isinstance(ctx, ast.Call):
                continue
            name = _dotted_name(ctx.func)
            if name is None:
                continue
            last = name.rsplit(".", 1)[-1]
            if last not in {"raises", "assertRaises", "assertRaisesRegex"}:
                continue
            exc = ast.dump(ctx.args[0]) if ctx.args else ""
            body = tuple(ast.dump(stmt) for stmt in node.body)
            out.append((exc, body, node.lineno))
    return out


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
        # Match by content first: swapping asserts is clean.
        unmatched_new: list[ast.Assert | ast.Call] = list(new_asserts)
        remaining_old: list[ast.Assert | ast.Call] = []
        for old_node in old_asserts:
            dump = ast.dump(old_node)
            match_idx: int | None = None
            for idx, cand in enumerate(unmatched_new):
                if ast.dump(cand) == dump:
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
        unmatched_wrappers: list[tuple[str, tuple[str, ...], int]] = list(new_wrappers)
        for exc, body, line in old_wrappers:
            key = (exc, body)
            match_idx = None
            for idx, cand in enumerate(unmatched_wrappers):
                if (cand[0], cand[1]) == key:
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


def _collect_parametrize(
    tree: ast.Module, imports: dict[str, str]
) -> dict[tuple[str, tuple[str, ...]], list[tuple[tuple[str, ...], tuple[str, ...], str, int]]]:
    result: dict[
        tuple[str, tuple[str, ...]], list[tuple[tuple[str, ...], tuple[str, ...], str, int]]
    ] = {}

    def record(owner: str, decorators: list[ast.expr]) -> None:
        for dec in decorators:
            if not isinstance(dec, ast.Call):
                continue
            parts = _parametrize_parts(dec, imports)
            if parts is None:
                continue
            argnames, expected_idx, cases_node = parts
            if not expected_idx or not isinstance(cases_node, (ast.List, ast.Tuple)):
                continue
            entries: list[tuple[tuple[str, ...], tuple[str, ...], str, int]] = []
            for position, case in enumerate(cases_node.elts):
                elements = _case_elements(case)
                input_parts: list[str] = []
                expected_parts: list[str] = []
                expected_repr_parts: list[str] = []
                for idx, element in enumerate(elements):
                    if idx in expected_idx:
                        expected_parts.append(ast.dump(element))
                        expected_repr_parts.append(ast.unparse(element))
                    else:
                        input_parts.append(ast.dump(element))
                input_key: tuple[str, ...] = (
                    tuple(input_parts) if input_parts else (f"#{position}",)
                )
                entries.append(
                    (input_key, tuple(expected_parts), ", ".join(expected_repr_parts), case.lineno)
                )
            result.setdefault((owner, tuple(argnames)), []).extend(entries)

    def walk(node: ast.AST, prefix: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = ".".join([*prefix, child.name])
                record(qual, child.decorator_list)
                walk(child, [*prefix, child.name])
            else:
                walk(child, prefix)

    walk(tree, [])
    return result


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
            matched: tuple[str, object] | None = None
            for key in _literals_in_expr(sub):
                if key in test_literals:
                    matched = key
                    break
            if matched is not None:
                findings.append(
                    Finding(
                        path=path,
                        line=node.lineno,
                        kind=ShortcutKind.LITERAL_BRANCH,
                        message=(
                            f"Please review: this code branches on {matched[1]!r}, "
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
    comments = _todo_comments(new_source)
    if comments:
        for line, text in comments:
            enclosing: list[tuple[str, FuncNode]] = [
                (qual, func)
                for qual, func in new_funcs.items()
                if func.lineno <= line <= (func.end_lineno or func.lineno)
            ]
            if not enclosing:
                continue
            enclosing.sort(key=lambda item: (item[1].end_lineno or item[1].lineno) - item[1].lineno)
            innermost_qual, _innermost = enclosing[0]
            if innermost_qual not in touched:
                continue
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


def _detect_parametrize_expected(
    path: str, old_tree: ast.Module, new_tree: ast.Module
) -> list[Finding]:
    old_map = _collect_parametrize(old_tree, _import_map(old_tree))
    new_map = _collect_parametrize(new_tree, _import_map(new_tree))
    findings: list[Finding] = []
    for key, old_entries in old_map.items():
        new_entries = new_map.get(key)
        if not new_entries:
            continue
        remaining = list(new_entries)
        for input_key, expected_dump, _repr, _line in old_entries:
            match_idx: int | None = None
            for idx, cand in enumerate(remaining):
                if cand[0] == input_key:
                    match_idx = idx
                    break
            if match_idx is None:
                continue
            cand = remaining.pop(match_idx)
            if cand[1] != expected_dump:
                findings.append(
                    Finding(
                        path=path,
                        line=cand[3],
                        kind=ShortcutKind.EXPECTED_VALUE_CHANGED,
                        message=(
                            "Please review: check the new expected value "
                            f"({cand[2]}); "
                            "the assertion now expects a different result."
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

    # Per-file function / test maps for the cross-file test-removed match.
    old_funcs_by: dict[str, dict[str, FuncNode]] = {}
    new_funcs_by: dict[str, dict[str, FuncNode]] = {}
    old_tests_by: dict[str, dict[str, FuncNode]] = {}
    new_tests_by: dict[str, dict[str, FuncNode]] = {}
    for path, (_old, _new, old_tree, new_tree, _failed) in parsed.items():
        if not _is_test_file(path):
            continue
        if old_tree is not None:
            old_funcs_by[path] = _collect_functions(old_tree)
            old_tests_by[path] = _collect_tests(old_tree)
        else:
            old_funcs_by[path] = {}
            old_tests_by[path] = {}
        if new_tree is not None:
            new_funcs_by[path] = _collect_functions(new_tree)
            new_tests_by[path] = _collect_tests(new_tree)
        else:
            new_funcs_by[path] = {}
            new_tests_by[path] = {}

    findings.extend(
        _detect_test_removed_global(old_tests_by, new_tests_by, old_funcs_by, new_funcs_by)
    )
    # A deleted test file with no collected test of its own still counts.
    for path, old, new in change_list:
        if new is not None or old is None or not _is_test_file(path):
            continue
        _o, _n, old_tree, _new_tree, parse_failed = parsed[path]
        if parse_failed or (old_tree is not None and not old_tests_by.get(path)):
            findings.append(
                Finding(
                    path=path,
                    line=1,
                    kind=ShortcutKind.TEST_REMOVED,
                    message="Please review: a test file was deleted.",
                )
            )

    test_literals: set[tuple[str, object]] = set()
    for path, (_old, _new, _old_tree, new_tree, _failed) in parsed.items():
        if new_tree is not None and _is_test_file(path):
            test_literals |= _test_literals(new_tree)

    for path, old, new in change_list:
        _o, _n, old_tree, new_tree, parse_failed = parsed[path]
        if parse_failed:
            continue
        if new is None:
            continue
        if new_tree is None:
            continue
        old_funcs = _collect_functions(old_tree) if old_tree is not None else {}
        new_funcs = _collect_functions(new_tree)
        old_classes = _collect_classes(old_tree) if old_tree is not None else {}
        new_classes = _collect_classes(new_tree)
        is_test = _is_test_file(path)
        if old is not None and old == new:
            continue
        if is_test:
            if old_tree is not None:
                findings.extend(_detect_assertions(path, old_funcs, new_funcs))
                findings.extend(_detect_parametrize_expected(path, old_tree, new_tree))
            findings.extend(
                _detect_skips(
                    path, old_tree, new_tree, old_funcs, new_funcs, old_classes, new_classes
                )
            )
        if not is_test:
            findings.extend(_detect_literal_branches(path, old_tree, new_tree, test_literals))
        findings.extend(_detect_stubs(path, old_funcs, new_funcs, old, new))

    findings.sort(key=lambda f: (f.path, f.line, f.kind, f.message))
    return findings
