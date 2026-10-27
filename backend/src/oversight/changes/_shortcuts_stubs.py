"""Find newly introduced placeholders, excluding declared interface members."""

import ast
import io
import tokenize
from collections import Counter

from oversight.changes._shortcuts_common import Finding, ShortcutKind, Source, walk_local


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


def _interfaces(source: Source) -> set[str]:
    interfaces: set[str] = set()
    changed = True
    while changed:
        changed = False
        for qual, cls in source.classes.items():
            bases = [base.value if isinstance(base, ast.Subscript) else base for base in cls.bases]
            declared = any(
                source.resolve(base) in {"typing.Protocol", "typing_extensions.Protocol", "abc.ABC"}
                for base in bases
            ) or any(
                kw.arg == "metaclass" and source.resolve(kw.value) == "abc.ABCMeta"
                for kw in cls.keywords
            )
            # Local class names resolve conservatively to unknown. Match them
            # only against interface classes in the enclosing lexical scope.
            parent = qual.rpartition(".")[0]
            inherited = any(
                isinstance(base, ast.Name)
                and (f"{parent}.{base.id}" if parent else base.id) in interfaces
                for base in bases
            )
            if qual not in interfaces and (declared or inherited):
                interfaces.add(qual)
                changed = True
    return interfaces


type Stub = tuple[str, int, str]


def _inventory(source: Source) -> dict[str, list[Stub]]:
    result: dict[str, list[Stub]] = {qual: [] for qual in source.functions}
    for qual, func in source.functions.items():
        for node in walk_local(func):
            label = None
            if isinstance(node, ast.Pass):
                label = "'pass'"
            elif (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and node.value.value is Ellipsis
            ):
                label = "'...'"
            elif (
                isinstance(node, ast.Raise)
                and node.exc is not None
                and source.resolve(node.exc)
                in {"NotImplementedError", "builtins.NotImplementedError"}
            ):
                label = "NotImplementedError"
            if label is not None and isinstance(node, ast.stmt):
                result[qual].append((label, node.lineno, f"a {label} placeholder"))
    for line, text in _todo_comments(source.text):
        enclosing = [
            (qual, func)
            for qual, func in source.functions.items()
            if func.lineno <= line <= (func.end_lineno or func.lineno)
        ]
        if enclosing:
            qual, _ = min(
                enclosing, key=lambda item: (item[1].end_lineno or item[1].lineno) - item[1].lineno
            )
            result[qual].append((text, line, f"a TODO/FIXME comment ('{text}')"))
    return result


def detect_stubs(path: str, old: Source | None, new: Source) -> list[Finding]:
    before = _inventory(old) if old else {}
    interfaces = _interfaces(new)
    findings: list[Finding] = []
    for qual, stubs in _inventory(new).items():
        func = new.functions[qual]
        if qual.rpartition(".")[0] in interfaces or any(
            new.resolve(dec)
            in {"typing.overload", "typing_extensions.overload", "abc.abstractmethod"}
            for dec in func.decorator_list
        ):
            continue
        previous = Counter(key for key, _, _ in before.get(qual, []))
        reported: set[int] = set()
        for key, line, description in sorted(stubs, key=lambda stub: stub[1]):
            if previous[key]:
                previous[key] -= 1
                continue
            if line in reported:
                continue
            reported.add(line)
            findings.append(
                Finding(
                    path,
                    line,
                    ShortcutKind.STUB_LEFT,
                    f"Please review: {description} was introduced in this function.",
                )
            )
    return findings
