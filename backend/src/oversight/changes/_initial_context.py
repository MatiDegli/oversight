"""Pure, bounded source/one-hop context; adoption and task wiring are separate."""

import ast
import re
import symtable
from collections import Counter
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import cast

from oversight.changes._neighbors import LIMIT
from oversight_contracts.common import NodeId
from oversight_contracts.graph import GraphNode, GraphSnapshot

TOKEN_BUDGET = 1500
BYTES_PER_TOKEN = 3
SIGNATURE_CHARS = 400
CONSTANT_CHARS = 120
_HEADING = (
    "Context for your task, built from the repository's graph and source; not a complete list."
)
_CATEGORIES = ("targets", "callers", "callees", "constants", "callers", "callees")
type _Literal = (
    bool
    | int
    | float
    | complex
    | str
    | bytes
    | list[_Literal]
    | tuple[_Literal, ...]
    | set[_Literal]
    | dict[_Literal, _Literal]
    | None
)


@dataclass(frozen=True)
class InitialContext:
    text: str
    tokens: int
    omitted: tuple[str, ...]


@dataclass(frozen=True)
class _Entry:
    target: NodeId
    section: int
    text: str
    omission: str


def _tokens(text: str) -> int:
    return (len(text.encode()) + BYTES_PER_TOKEN - 1) // BYTES_PER_TOKEN


def _global_loads(table: symtable.SymbolTable) -> set[str]:
    names = {s.get_name() for s in table.get_symbols() if s.is_global() and s.is_referenced()}
    for child in table.get_children():
        names.update(_global_loads(child))
    return names


def _literal_repr(value: _Literal) -> str:
    """Use Python representations, with set members ordered independently of hash seeds."""
    if isinstance(value, set):
        return "{" + ", ".join(sorted(map(_literal_repr, value))) + "}" if value else "set()"
    if isinstance(value, dict):
        return (
            "{"
            + ", ".join(f"{_literal_repr(k)}: {_literal_repr(v)}" for k, v in value.items())
            + "}"
        )
    if isinstance(value, (list, tuple)):
        items = ", ".join(map(_literal_repr, value))
        if isinstance(value, list):
            return f"[{items}]"
        return f"({items}{',' if len(value) == 1 else ''})"
    return repr(value)


def _module_constants(tree: ast.Module) -> dict[str, str]:
    # Walk control-flow suites too, but never descend into another lexical scope.
    bindings: Counter[str] = Counter()
    candidates: dict[str, ast.expr] = {}
    pending: list[ast.AST] = list(tree.body)
    while pending:
        item = pending.pop()
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings[item.name] += 1
            # Defaults, decorators and bases execute in the enclosing scope.
            pending.extend(c for c in ast.iter_child_nodes(item) if not isinstance(c, ast.stmt))
            continue
        if isinstance(item, ast.Lambda):
            pending.extend(item.args.defaults)
            pending.extend(d for d in item.args.kw_defaults if d is not None)
            continue
        if isinstance(item, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            # Comprehension targets are local; walrus targets bind in the outer scope.
            pending.extend(n for n in ast.walk(item) if isinstance(n, ast.NamedExpr))
            continue
        if isinstance(item, (ast.Import, ast.ImportFrom)):
            bindings.update(a.asname or a.name.split(".")[0] for a in item.names)
        if isinstance(item, ast.ExceptHandler) and item.name:
            bindings[item.name] += 1
        if isinstance(item, (ast.MatchAs, ast.MatchStar)) and item.name:
            bindings[item.name] += 1
        if isinstance(item, ast.MatchMapping) and item.rest:
            bindings[item.rest] += 1
        if isinstance(item, ast.AnnAssign) and item.value is None:
            continue
        if isinstance(item, ast.Name) and isinstance(item.ctx, (ast.Store, ast.Del)):
            bindings[item.id] += 1
        if (
            isinstance(item, ast.Assign)
            and len(item.targets) == 1
            and isinstance(item.targets[0], ast.Name)
        ):
            candidates[item.targets[0].id] = item.value
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.value:
            candidates[item.target.id] = item.value
        pending.extend(ast.iter_child_nodes(item))
    constants: dict[str, str] = {}
    for name, value in sorted(candidates.items()):
        if bindings[name] == 1 and re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            with suppress(ValueError, TypeError, SyntaxError, RecursionError):
                constants[name] = _literal_repr(cast(_Literal, ast.literal_eval(value)))
    return constants


def _source(node: GraphNode, source: str) -> tuple[str, dict[str, str]]:
    tree = ast.parse(source)
    definition = next(
        d
        for d in ast.walk(tree)
        if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef))
        and min([d.lineno] + [a.lineno for a in d.decorator_list]) == node.start_line
    )
    pending = [symtable.symtable(source, node.path, "exec")]
    table = pending[0]
    while pending:
        table = pending.pop()
        if table.get_name() == definition.name and table.get_lineno() == definition.lineno:
            break
        pending.extend(table.get_children())
    signature = "\n".join(
        [f"@{ast.unparse(d)}" for d in definition.decorator_list]
        + [
            f"{'async ' if isinstance(definition, ast.AsyncFunctionDef) else ''}"
            f"def {definition.name}({ast.unparse(definition.args)})"
            + (f" -> {ast.unparse(definition.returns)}" if definition.returns else "")
        ]
    )
    loads = _global_loads(table)
    return signature, {n: v for n, v in _module_constants(tree).items() if n in loads}


def _relations(graph: GraphSnapshot, node: GraphNode, callers: bool) -> list[_Entry]:
    nodes = {n.id: n for n in graph.nodes if n.kind in ("function", "method")}
    ranks: dict[str, int] = {}
    for edge in graph.edges:
        end, other = (edge.dst, edge.src) if callers else (edge.src, edge.dst)
        if edge.kind == "calls" and end == node.id and other in nodes:
            rank = 0 if edge.confidence == "exact" else 2
            ranks[other] = min(ranks.get(other, rank), rank)
    ordered = sorted(ranks, key=lambda i: (ranks[i], nodes[i].qualname, nodes[i].path, i))
    label = "caller" if callers else "callee"
    return [
        _Entry(
            node.id,
            (1 if callers else 2) + (3 if ranks[i] else 0),
            f"{nodes[i].qualname} ({nodes[i].path}; {'possible' if ranks[i] else 'exact'})"
            if index < LIMIT
            else "",
            f"{label} {i}: {'budget' if index < LIMIT else 'list limit'}",
        )
        for index, i in enumerate(ordered)
    ]


def _render(
    entries: list[_Entry], chosen: set[int], notes: list[tuple[str, str]]
) -> InitialContext:
    active = [e.target for i, e in enumerate(entries) if e.section == 0 and i in chosen]
    omitted = list(notes)
    for i, entry in enumerate(entries):
        if i not in chosen and (entry.section == 0 or entry.target in active):
            omitted.append((_CATEGORIES[entry.section], entry.omission))
    blocks: list[str] = []
    for target in active:
        selected = [e for i, e in enumerate(entries) if e.target == target and i in chosen]
        lines = [e.text for e in selected if e.section == 0]
        for label, sections in (("Callers", (1, 4)), ("Callees", (2, 5)), ("Constants", (3,))):
            values = [e.text for e in selected if e.section in sections]
            if values:
                lines.append(f"{label}: {', '.join(values)}")
        blocks.append("\n".join(lines))
    text = _HEADING + "\n" + "\n\n".join(blocks) if blocks else ""
    counts = Counter(category for category, _ in omitted)
    summary = ", ".join(
        f"{counts[c]} {c}" for c in ("callers", "callees", "constants", "targets") if counts[c]
    )
    if text and summary:
        text += f"\nOmitted: {summary}"
    return InitialContext(text, _tokens(text), tuple(reason for _, reason in omitted))


def initial_context(
    graph: GraphSnapshot,
    files: Mapping[str, str],
    targets: Iterable[NodeId],
    *,
    budget: int = TOKEN_BUDGET,
) -> InitialContext:
    """Allocate signatures, exact callers/callees, constants, then possible callers/callees.

    Each trial includes all omissions and formatting bytes. A rejected entry never stops
    later, smaller entries. A missing signature excludes the target's remaining sections.
    """
    if budget < 0:
        raise ValueError("budget must be non-negative")
    if graph.parse_errors:
        paths = ", ".join(sorted({e.path for e in graph.parse_errors}))
        return InitialContext("", 0, (f"graph has parse errors in {paths}",))
    nodes = {n.id: n for n in graph.nodes if n.kind in ("function", "method")}
    ids = set(targets)
    notes = [
        ("targets", f"target {i}: not a function or method in the graph")
        for i in sorted(ids - nodes.keys())
    ]
    entries: list[_Entry] = []
    for i in sorted(ids & nodes.keys(), key=lambda i: (nodes[i].qualname, nodes[i].path, i)):
        node = nodes[i]
        try:
            signature, constants = _source(node, files[node.path])
        except (KeyError, SyntaxError, ValueError, StopIteration, RecursionError):
            notes.append(("targets", f"target {i}: source unavailable"))
            continue
        if len(signature) > SIGNATURE_CHARS:
            signature = signature[: SIGNATURE_CHARS - 4] + " ..."
            notes.append(("", f"signature of {i} cut"))
        entries.append(
            _Entry(i, 0, f"{node.qualname} ({node.path})\n{signature}", f"target {i}: budget")
        )
        entries.extend(_relations(graph, node, True))
        entries.extend(_relations(graph, node, False))
        for name, value in constants.items():
            long = len(value) > CONSTANT_CHARS
            entries.append(
                _Entry(
                    i,
                    3,
                    "" if long else f"{name} = {value}",
                    f"constant {name}: {'too long' if long else 'budget'}",
                )
            )
    # Stable sort preserves target order and each relation's rank/name/path/id order.
    entries.sort(key=lambda e: e.section)
    chosen: set[int] = set()
    active: set[str] = set()
    for index, entry in enumerate(entries):
        if not entry.text or (entry.section and entry.target not in active):
            continue
        trial = chosen | {index}
        if _render(entries, trial, notes).tokens <= budget:
            chosen = trial
            if entry.section == 0:
                active.add(entry.target)
    return _render(entries, chosen, notes)
