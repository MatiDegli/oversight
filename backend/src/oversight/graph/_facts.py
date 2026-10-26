"""What one file's parse leaves behind: its nodes, and the facts name resolution reads.

A `FileParse` depends on that file's bytes alone, so it is cached by content hash and reused until
the file changes. Everything that needs other files, such as which node a call reaches, is worked
out later by the linker from these facts.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from oversight_contracts.graph import GraphNode

# Bindings: what a name holds in one scope. A name keeps every binding the scope gives it.


@dataclass(frozen=True, slots=True)
class DefBinding:
    """A `def` or `class` that is a node."""

    node_id: str


@dataclass(frozen=True, slots=True)
class ImportBinding:
    """`from module import attr`, or the module itself when `attr` is None."""

    module: str
    attr: str | None


@dataclass(frozen=True, slots=True)
class ParamBinding:
    """A parameter of a function or lambda."""


@dataclass(frozen=True, slots=True)
class OtherBinding:
    """Any other binding: an assignment, a loop target, a definition that is not a node."""


type Binding = DefBinding | ImportBinding | ParamBinding | OtherBinding

# Bases: where a callee expression starts, once the file's own scopes are taken into account.


@dataclass(frozen=True, slots=True)
class GlobalName:
    """A name looked up in the module's own scope (or, failing that, its star imports)."""

    name: str


@dataclass(frozen=True, slots=True)
class LocalImport:
    """A name a function imports for itself: every import of it in that scope, and whether the
    scope also binds it some other way."""

    bindings: tuple[ImportBinding, ...]
    rebound: bool


@dataclass(frozen=True, slots=True)
class ClassName:
    """A name bound in a class body, used from that same body."""

    class_id: str
    name: str


@dataclass(frozen=True, slots=True)
class SelfBase:
    """`self` or `cls` in a method of `class_id`, or `super()` when `via_super`."""

    class_id: str
    via_super: bool


@dataclass(frozen=True, slots=True)
class UnknownBase:
    """A receiver of unknown type: a parameter, a local, a call result."""


type Base = GlobalName | LocalImport | ClassName | SelfBase | UnknownBase

# Call references: what a call site names.


@dataclass(frozen=True, slots=True)
class CallRef:
    """A base followed by attributes: `pricing.compute_price` is `pricing`, `("compute_price",)`."""

    base: Base
    attrs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DynamicRef:
    """`getattr(obj, "name")(...)`: a call by name at run time."""

    name: str


@dataclass(frozen=True, slots=True)
class FixtureRef:
    """A test or fixture parameter that pytest fills by calling the fixture of that name."""

    name: str


type Ref = CallRef | DynamicRef | FixtureRef


@dataclass(frozen=True, slots=True)
class CallFact:
    """A call site: the nearest enclosing node runs it."""

    owner: str
    ref: Ref


@dataclass(frozen=True, slots=True)
class ImportFact:
    """`import module` (no names), or `from module import names`; `*` is a name."""

    module: str
    names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ClassFacts:
    bindings: Mapping[str, tuple[Binding, ...]]
    bases: tuple[CallRef, ...]
    assigned: frozenset[str]
    """Attributes its methods set or delete on `self` or `cls`, which may replace a method."""


@dataclass(frozen=True, slots=True)
class FileParse:
    path: str
    module: str
    is_package: bool
    nodes: tuple[GraphNode, ...]
    """In source order, the module first."""
    own_hashes: Mapping[str, str]
    """Per node: the hash of its own source, without the nodes nested in it."""
    bindings: Mapping[str, tuple[Binding, ...]]
    """The module scope."""
    star_imports: tuple[str, ...]
    all_names: frozenset[str] | None
    """`__all__` when it is a literal list or tuple of strings."""
    classes: Mapping[str, ClassFacts]
    calls: tuple[CallFact, ...]
    imports: tuple[ImportFact, ...]
    fixtures: Mapping[str, str]
    """Module-level pytest fixtures, by name."""
