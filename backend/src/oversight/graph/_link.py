"""Resolve every file's call sites and imports against the whole workspace into edges.

A call is "exact" only when its name resolves to a single function or class; otherwise it is
"possible", with the reason. Calling a class is a call to the class node. Imports follow
re-exports, so `from shop import Cart` reaches `shop.cart:Cart` through `shop/__init__.py`.

Attribute lookup on a class does not emulate the C3 order. It takes every class among the bases
that defines the name and drops those a subclass among them overrides, which holds in any valid
order since a class always precedes its bases. One definition left is exact; several are all
candidates. A base name with several definitions, or one rebound, makes the lookup possible, and
then no definition is dropped: the overrider may be on a branch the class does not take.
Calls through `self` or `cls` take the union of the lookups from the class and from every
subclass, since the receiver may be any of them, and become "rebound_name" when a method assigns
that attribute on `self`. `super()` looks the name up after the class; a subclass
that adds other bases can put more classes after it, so their definitions are candidates too.
An attribute of a receiver with several values, such as a module imported under one name in two
branches, is looked up on each value; the results are all candidates.
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
    unknown: bool = False
    """One of the values had an attribute looked up on it whose type is unknown."""

    def __or__(self, other: "_Res") -> "_Res":
        return _Res(
            self.values | other.values,
            self.rebound or other.rebound,
            self.star or other.star,
            self.unknown or other.unknown,
        )

    def flagged(self, rebound: bool = False, star: bool = False, unknown: bool = False) -> "_Res":
        return _Res(
            self.values, self.rebound or rebound, self.star or star, self.unknown or unknown
        )

    @property
    def doubtful(self) -> bool:
        return bool(self.values) or self.rebound or self.star or self.unknown


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
        self._ancestry: dict[str, tuple[frozenset[str], _Res]] = {}
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
        starred = _Res()
        for source in file.star_imports:
            if self._exports(source, name):
                starred = starred | self._name_in_module(source, name, seen).flagged(star=True)
        bindings = file.bindings.get(name)
        if bindings:  # a star import may come later and replace the binding
            res = self._bindings(bindings, seen)
            return res | starred if starred.values else res
        if sub.kind == "module":
            return _Res(frozenset({sub}))
        return starred

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

    def _bases(self, class_id: str) -> tuple[list[str], _Res]:
        """The classes a class's bases may name, and the doubt about them: a marker value when a
        base may hold more than one thing, and the flags of a rebound or star-imported base."""
        facts = self.classes.get(class_id)
        if facts is None:
            return [], _Res()
        file = self.class_file[class_id]
        found: list[str] = []
        doubt = _Res()
        for ref in facts.bases:
            res = self._navigate(file, ref)
            if res is None:
                continue
            for value in sorted(res.values, key=lambda v: v.ref):
                if value.kind == "node" and self.nodes[value.ref].kind == "class":
                    found.append(value.ref)
            marker = frozenset({_EXTERNAL}) if len(res.values) > 1 else frozenset[_Value]()
            doubt = doubt | _Res(marker, res.rebound, res.star)
        return found, doubt

    def _ancestors(self, class_id: str) -> tuple[frozenset[str], _Res]:
        """The class and every class among its bases, with the doubt gathered on the way."""
        if class_id in self._ancestry:
            return self._ancestry[class_id]
        self._ancestry[class_id] = (frozenset({class_id}), _Res())  # guards against cycles
        bases, doubt = self._bases(class_id)
        found = {class_id}
        for base in bases:
            classes, base_doubt = self._ancestors(base)
            found |= classes
            doubt = doubt | base_doubt
        self._ancestry[class_id] = (frozenset(found), doubt)
        return self._ancestry[class_id]

    def _subclasses_of(self, class_id: str) -> set[str]:
        if self._subclasses is None:
            direct: dict[str, set[str]] = {}
            for cid in self.classes:
                for base in self._bases(cid)[0]:
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

    def _defines(self, class_id: str, name: str) -> bool:
        facts = self.classes.get(class_id)
        return facts is not None and bool(facts.bindings.get(name))

    def _pick(self, classes: Iterable[str], name: str, doubt: _Res) -> _Res:
        """What `name` may hold when looked up along `classes`, all the bases of one class: each
        definition no subclass among them overrides, or every definition when the bases are in
        doubt, since the overrider may be an alternative the class does not inherit from."""
        definers = [c for c in sorted(classes) if self._defines(c, name)]
        res = _Res()
        for cid in definers:
            overridden = not doubt.doubtful and any(
                other != cid and cid in self._ancestors(other)[0] for other in definers
            )
            if not overridden:
                res = res | self._bindings(self.classes[cid].bindings[name], frozenset())
        return res | doubt if res.values else res

    def _class_attr(self, class_id: str, name: str) -> _Res:
        classes, doubt = self._ancestors(class_id)
        return self._pick(classes, name, doubt)

    # Calls ---------------------------------------------------------------------------------

    def _resolve(self, file: FileParse, ref: Ref) -> list[_Target]:
        if isinstance(ref, DynamicRef):
            return [(n, "possible", "dynamic_dispatch") for n in self.by_name.get(ref.name, ())]
        if isinstance(ref, FixtureRef):
            fixture = self._fixture(file, ref.name)
            return [(fixture, "possible", "dynamic_dispatch")] if fixture else []
        res = self._navigate(file, ref)
        name = ref.attrs[-1] if ref.attrs else ""
        unknown: list[_Target] = [
            (m, "possible", "unknown_receiver") for m in self.methods_by_name.get(name, ())
        ]
        if res is None:
            return unknown
        return _classify(res, self.nodes) + (unknown if res.unknown else [])

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
            res = self._bindings(base.bindings, frozenset()).flagged(rebound=base.rebound)
        elif isinstance(base, ClassName):
            facts = self.classes.get(base.class_id)
            bindings = facts.bindings.get(base.name, ()) if facts else ()
            res = self._bindings(bindings, frozenset())
        else:
            assert isinstance(base, UnknownBase)
            res = None
        for attr in attrs:
            if res is None or not res.values:
                return None
            # A receiver with several values may be any of them: a marker keeps the call possible.
            several = frozenset({_EXTERNAL}) if len(res.values) > 1 else frozenset[_Value]()
            found = _Res(several, res.rebound, res.star, res.unknown)
            steps = [self._attr_of(v, attr) for v in sorted(res.values, key=lambda v: v.ref)]
            if all(step is None for step in steps):
                return None
            for step in steps:
                found = found.flagged(unknown=True) if step is None else found | step
            res = found
        return res

    def _attr_of(self, value: _Value, attr: str) -> _Res | None:
        """What `attr` holds on one value; None when that value is of a type we do not follow."""
        if value.kind == "external":
            return _Res(frozenset({_EXTERNAL}))
        if value.kind == "module":
            return self._name_in_module(value.ref, attr, frozenset())
        if self.nodes[value.ref].kind == "class":
            return self._class_attr(value.ref, attr)
        return None

    def _self_attr(self, base: SelfBase, name: str) -> _Res:
        if base.via_super:
            return self._super_attr(base.class_id, name)
        res = _Res()
        seen: set[str] = set()
        for cid in [base.class_id, *sorted(self._subclasses_of(base.class_id))]:
            res = res | self._class_attr(cid, name)
            seen |= self._ancestors(cid)[0]
        assigned = any(name in self.classes[c].assigned for c in seen if c in self.classes)
        return res.flagged(rebound=assigned)

    def _super_attr(self, class_id: str, name: str) -> _Res:
        classes, doubt = self._ancestors(class_id)
        res = self._pick(classes - {class_id}, name, doubt)
        subclasses = self._subclasses_of(class_id)
        others: set[str] = set()
        for sub in subclasses:  # `class D(B, C)` may put C between B and B's own bases
            others |= self._ancestors(sub)[0] - classes - subclasses
        extra = [c for c in sorted(others) if self._defines(c, name)]
        for cid in extra:
            res = res | self._bindings(self.classes[cid].bindings[name], frozenset())
        return res | _Res(frozenset({_EXTERNAL})) if extra else res

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
