"""Resolve every file's call sites and imports against the whole workspace into edges.

A call is "exact" only when its name resolves to a single function or class; otherwise it is
"possible", with the reason. Calling a class is a call to the class node. Calls through `self` or
`cls` look the method up along the class's bases: exact when that finds one definition and no
subclass in the workspace overrides it, possible with "several_candidates" otherwise. `super()`
looks it up after the class. Imports follow re-exports, so `from shop import Cart` reaches
`shop.cart:Cart` through `shop/__init__.py`.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from oversight.graph._facts import (
    Binding,
    CallRef,
    ClassName,
    DefBinding,
    DynamicRef,
    FileParse,
    FixtureRef,
    GlobalName,
    ImportBinding,
    LocalImport,
    Ref,
    SelfBase,
    UnknownBase,
)
from oversight_contracts.graph import Confidence, EdgeKind, GraphEdge, GraphNode, PossibleReason

_CALLABLE = ("function", "method", "class")
# When one source reaches one target through several call sites, the strongest reason is kept.
_REASON_ORDER: tuple[PossibleReason, ...] = (
    "several_candidates",
    "rebound_name",
    "star_import",
    "dynamic_dispatch",
    "unknown_receiver",
)

type _Target = tuple[str, Confidence, PossibleReason | None]


@dataclass(frozen=True, slots=True)
class _Value:
    kind: Literal["node", "module", "external"]
    ref: str


_EXTERNAL = _Value("external", "")


@dataclass(frozen=True, slots=True)
class _Res:
    """What a name may hold, and why it might hold something else."""

    values: frozenset[_Value] = field(default_factory=frozenset[_Value])
    rebound: bool = False
    star: bool = False

    def __or__(self, other: "_Res") -> "_Res":
        return _Res(
            self.values | other.values, self.rebound or other.rebound, self.star or other.star
        )

    def flagged(self, rebound: bool = False, star: bool = False) -> "_Res":
        return _Res(self.values, self.rebound or rebound, self.star or star)


def link(files: Iterable[FileParse]) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Nodes and edges of a set of parsed files, sorted by id. Each module must appear once."""
    return _Linker(list(files)).run()


class _Linker:
    def __init__(self, files: list[FileParse]) -> None:
        self.files = files
        self.by_path = {f.path: f for f in files}
        self.by_module = {f.module: f for f in files}
        self.prefixes = {
            m.rsplit(".", n)[0] for m in self.by_module for n in range(1, m.count(".") + 1)
        }
        self.nodes = {n.id: n for f in files for n in f.nodes}
        self.classes = {cid: facts for f in files for cid, facts in f.classes.items()}
        self.class_file = {cid: f for f in files for cid in f.classes}
        self.by_name: dict[str, list[str]] = {}
        self.methods_by_name: dict[str, list[str]] = {}
        for node in self.nodes.values():
            if node.kind in ("function", "method"):
                self.by_name.setdefault(node.name, []).append(node.id)
            if node.kind == "method":
                self.methods_by_name.setdefault(node.name, []).append(node.id)
        self._mro: dict[str, list[str]] = {}
        self._subclasses: dict[str, set[str]] | None = None

    def run(self) -> tuple[list[GraphNode], list[GraphEdge]]:
        edges: dict[str, tuple[EdgeKind, str, str, Confidence, PossibleReason | None]] = {}

        def add(
            kind: EdgeKind, src: str, dst: str, conf: Confidence, reason: PossibleReason | None
        ):
            edge_id = f"{kind}:{src}->{dst}"
            old = edges.get(edge_id)
            if old is not None and _stronger(old[3], old[4], conf, reason):
                return
            edges[edge_id] = (kind, src, dst, conf, reason)

        for file in self.files:
            for target in self._imported(file):
                add("imports", file.module, target, "exact", None)
            for fact in file.calls:
                for dst, conf, reason in self._resolve(file, fact.ref):
                    add("calls", fact.owner, dst, conf, reason)
        graph_edges = [
            GraphEdge(id=eid, kind=k, src=s, dst=d, confidence=c, reason=r)
            for eid, (k, s, d, c, r) in sorted(edges.items())
        ]
        return sorted(self.nodes.values(), key=lambda n: n.id), graph_edges

    # Imports -------------------------------------------------------------------------------

    def _imported(self, file: FileParse) -> set[str]:
        targets: set[str] = set()
        for fact in file.imports:
            source = self.by_module.get(fact.module)
            for name in fact.names or (None,):
                target = fact.module
                sub = f"{fact.module}.{name}"
                if (
                    name
                    and sub in self.by_module
                    and (source is None or name not in source.bindings)
                ):
                    target = sub
                if target in self.by_module and target != file.module:
                    targets.add(target)
        return targets

    # Names ---------------------------------------------------------------------------------

    def _module_value(self, module: str) -> _Value:
        if module in self.by_module or module in self.prefixes:
            return _Value("module", module)
        return _EXTERNAL

    def _name_in_module(self, module: str, name: str, seen: frozenset[tuple[str, str]]) -> _Res:
        if (module, name) in seen:
            return _Res()
        seen = seen | {(module, name)}
        file = self.by_module.get(module)
        sub = self._module_value(f"{module}.{name}")
        if file is None:
            if sub.kind == "module":
                return _Res(frozenset({sub}))
            return _Res(frozenset({_EXTERNAL})) if module not in self.prefixes else _Res()
        bindings = file.bindings.get(name)
        if bindings:
            return self._bindings(bindings, seen)
        if sub.kind == "module":
            return _Res(frozenset({sub}))
        res = _Res()
        for source in file.star_imports:
            if self._exports(source, name):
                res = res | self._name_in_module(source, name, seen).flagged(star=True)
        return res

    def _exports(self, module: str, name: str) -> bool:
        file = self.by_module.get(module)
        if file is None:
            return False
        if file.all_names is not None:
            return name in file.all_names
        return not name.startswith("_")

    def _bindings(self, bindings: Iterable[Binding], seen: frozenset[tuple[str, str]]) -> _Res:
        res = _Res()
        for binding in bindings:
            res = res | self._binding(binding, seen)
        return res

    def _binding(self, binding: Binding, seen: frozenset[tuple[str, str]]) -> _Res:
        if isinstance(binding, DefBinding):
            return _Res(frozenset({_Value("node", binding.node_id)}))
        if isinstance(binding, ImportBinding):
            if binding.attr is None:
                return _Res(frozenset({self._module_value(binding.module)}))
            return self._name_in_module(binding.module, binding.attr, seen)
        return _Res(rebound=True)

    # Classes -------------------------------------------------------------------------------

    def _bases(self, class_id: str) -> list[str]:
        facts = self.classes.get(class_id)
        if facts is None:
            return []
        file = self.class_file[class_id]
        found: list[str] = []
        for ref in facts.bases:
            res = self._navigate(file, ref)
            for value in sorted(res.values, key=lambda v: v.ref) if res is not None else ():
                if value.kind == "node" and self.nodes[value.ref].kind == "class":
                    found.append(value.ref)
        return found

    def _mro_of(self, class_id: str) -> list[str]:
        if class_id in self._mro:
            return self._mro[class_id]
        self._mro[class_id] = [class_id]  # guards against cycles while it is computed
        order = [class_id]
        for base in self._bases(class_id):
            order.extend(c for c in self._mro_of(base) if c not in order)
        self._mro[class_id] = order
        return order

    def _subclasses_of(self, class_id: str) -> set[str]:
        if self._subclasses is None:
            direct: dict[str, set[str]] = {}
            for cid in self.classes:
                for base in self._bases(cid):
                    direct.setdefault(base, set()).add(cid)
            self._subclasses = direct
        found: set[str] = set()
        pending = [class_id]
        while pending:
            for sub in self._subclasses.get(pending.pop(), ()):
                if sub not in found:
                    found.add(sub)
                    pending.append(sub)
        return found

    def _class_attr(self, mro: list[str], name: str) -> _Res:
        for cid in mro:
            facts = self.classes.get(cid)
            bindings = facts.bindings.get(name) if facts else None
            if bindings:
                return self._bindings(bindings, frozenset())
        return _Res()

    # Calls ---------------------------------------------------------------------------------

    def _resolve(self, file: FileParse, ref: Ref) -> list[_Target]:
        if isinstance(ref, DynamicRef):
            return [(n, "possible", "dynamic_dispatch") for n in self.by_name.get(ref.name, ())]
        if isinstance(ref, FixtureRef):
            fixture = self._fixture(file, ref.name)
            return [(fixture, "possible", "dynamic_dispatch")] if fixture else []
        res = self._navigate(file, ref)
        if res is None:
            name = ref.attrs[-1] if ref.attrs else ""
            return [(m, "possible", "unknown_receiver") for m in self.methods_by_name.get(name, ())]
        return _classify(res, self.nodes)

    def _navigate(self, file: FileParse, ref: CallRef) -> _Res | None:
        """What the reference holds; None when an attribute's receiver is of unknown type."""
        base = ref.base
        attrs = ref.attrs
        res: _Res | None
        if isinstance(base, SelfBase):
            if not attrs:
                return _Res()
            res = self._self_attr(base, attrs[0])
            attrs = attrs[1:]
        elif isinstance(base, GlobalName):
            res = self._name_in_module(file.module, base.name, frozenset())
        elif isinstance(base, LocalImport):
            res = self._binding(base.binding, frozenset()).flagged(rebound=base.rebound)
        elif isinstance(base, ClassName):
            facts = self.classes.get(base.class_id)
            bindings = facts.bindings.get(base.name, ()) if facts else ()
            res = self._bindings(bindings, frozenset())
        else:
            assert isinstance(base, UnknownBase)
            res = None
        for attr in attrs:
            if res is None or len(res.values) != 1:
                return None
            (value,) = res.values
            if value.kind == "external":
                return _Res()
            if value.kind == "module":
                res = self._name_in_module(value.ref, attr, frozenset()).flagged(
                    res.rebound, res.star
                )
            elif self.nodes[value.ref].kind == "class":
                res = self._class_attr(self._mro_of(value.ref), attr).flagged(res.rebound, res.star)
            else:
                return None
        return res

    def _self_attr(self, base: SelfBase, name: str) -> _Res:
        mro = self._mro_of(base.class_id)
        if base.via_super:
            return self._class_attr(mro[1:], name)
        res = self._class_attr(mro, name)
        for sub in sorted(self._subclasses_of(base.class_id)):
            facts = self.classes[sub]
            res = res | self._bindings(facts.bindings.get(name, ()), frozenset())
        return res

    def _fixture(self, file: FileParse, name: str) -> str | None:
        if name in file.fixtures:
            return file.fixtures[name]
        parts = file.path.split("/")[:-1]
        for depth in range(len(parts), -1, -1):
            conftest = self.by_path.get("/".join([*parts[:depth], "conftest.py"]))
            if conftest is not None and name in conftest.fixtures:
                return conftest.fixtures[name]
        return None


def _classify(res: _Res, nodes: Mapping[str, GraphNode]) -> list[_Target]:
    callables = sorted(
        v.ref for v in res.values if v.kind == "node" and nodes[v.ref].kind in _CALLABLE
    )
    if not callables:
        return []
    others = len(res.values) - len(callables)
    reason: PossibleReason | None = None
    if res.star:
        reason = "star_import"
    elif res.rebound:
        reason = "rebound_name"
    elif len(callables) + others > 1:
        reason = "several_candidates"
    confidence: Confidence = "exact" if reason is None else "possible"
    return [(c, confidence, reason) for c in callables]


def _stronger(
    old_conf: Confidence,
    old_reason: PossibleReason | None,
    conf: Confidence,
    reason: PossibleReason | None,
) -> bool:
    """Whether the edge already recorded wins over a new call site with the same target."""
    if old_conf == "exact" or conf == "exact":
        return old_conf == "exact"
    assert old_reason is not None and reason is not None
    return _REASON_ORDER.index(old_reason) <= _REASON_ORDER.index(reason)
