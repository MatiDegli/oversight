"""Find newly introduced placeholders, excluding declared interface members."""

import ast
import io
import re
import tokenize
from collections import Counter

from oversight.changes._shortcuts_common import Finding, ShortcutKind, Source, walk_local
from oversight.changes._shortcuts_tests import TestRef


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


def _actionable_todos(source: Source) -> list[tuple[int, str]]:
    """A leading action marker is a placeholder; prose mentioning TODO is not."""
    comments: list[tuple[int, str]] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source.text).readline):
            if token.type == tokenize.COMMENT and re.match(
                r"#\s*(?:TODO|FIXME)\s*:\s*\S", token.string, re.IGNORECASE
            ):
                comments.append((token.start[0], token.string.strip()))
    except (tokenize.TokenError, IndentationError):
        pass
    return comments


def _inventory(source: Source) -> dict[str, list[Stub]]:
    result: dict[str, list[Stub]] = {qual: [] for qual in source.functions}
    for qual, func in source.functions.items():
        if qual in source.tests:
            continue
        body = list(func.body)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body.pop(0)
        parent = source.classes.get(qual.rpartition(".")[0])
        no_op_method = parent is not None and (
            func.name.startswith("visit_")
            or bool(parent.bases)
            or any(
                source.resolve(dec) in {"typing.override", "typing_extensions.override"}
                for dec in func.decorator_list
            )
        )
        for node in walk_local(func):
            label = None
            if isinstance(node, ast.Pass) and body == [node] and not no_op_method:
                label = "'pass'"
            elif (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and node.value.value is Ellipsis
                and body == [node]
                and not no_op_method
            ):
                label = "'...'"
            elif (
                isinstance(node, ast.Raise)
                and node.exc is not None
                and source.resolve(node.exc)
                in {"NotImplementedError", "builtins.NotImplementedError"}
            ):
                label = "NotImplementedError"
            elif (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and source.resolve(node.value.func) in {"TODO", "FIXME"}
            ):
                label = "TODO/FIXME"
            if label is not None and isinstance(node, ast.stmt):
                result[qual].append((label, node.lineno, f"a {label} placeholder"))
    for line, text in _actionable_todos(source):
        owners = [
            (qual, func)
            for qual, func in source.functions.items()
            if func.lineno <= line <= (func.end_lineno or func.lineno)
        ]
        if owners:
            qual, _ = min(
                owners, key=lambda item: (item[1].end_lineno or item[1].lineno) - item[1].lineno
            )
            if qual not in source.tests:
                result[qual].append((text, line, f"an actionable TODO/FIXME marker ('{text}')"))
    return result


def detect_stubs(
    path: str,
    old: Source | None,
    new: Source,
    old_sources: dict[str, Source],
    matches: dict[TestRef, TestRef],
) -> list[Finding]:
    before = _inventory(old) if old else {}
    inventories = {p: _inventory(src) for p, src in old_sources.items()}
    # Preserve placeholders owned by intact renamed/moved tests and their helpers.
    for (new_path, qual), (old_path, old_qual) in matches.items():
        if new_path != path or (old_path == path and qual == old_qual):
            continue
        for owner, stubs in inventories[old_path].items():
            if owner == old_qual or owner.startswith(old_qual + "."):
                before[qual + owner[len(old_qual) :]] = stubs
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
