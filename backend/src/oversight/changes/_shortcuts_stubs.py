"""Find placeholders and tokenized TODO/FIXME comments in touched functions."""

import ast
import io
import tokenize

from oversight.changes._shortcuts_common import (
    Finding,
    FuncNode,
    ShortcutKind,
    dotted_name,
    walk_local,
)


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


def detect_stubs(
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
        for node in walk_local(func):
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
                name = dotted_name(exc.func if isinstance(exc, ast.Call) else exc)
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
