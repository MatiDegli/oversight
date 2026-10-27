"""The graph of main, a change's overlay on it, and the impact cone (docs/proposal.md, section 4.3).

Containment is `parent_id`; there are no `contains` edges. Pending changes stay out of main's graph:
each change carries its own `ChangeGraph` against its base commit.
"""

from typing import Literal, Self

from pydantic import model_validator

from oversight_contracts.common import ContractModel, EdgeId, NodeId, WorkspacePath

type NodeKind = Literal["module", "class", "function", "method"]
type EdgeKind = Literal["imports", "calls", "inherits"]
type Confidence = Literal["exact", "possible"]
type PossibleReason = Literal[
    "unknown_receiver",
    "several_candidates",
    "rebound_name",
    "star_import",
    "dynamic_dispatch",
]
"""Why a call is only possible: an attribute call on a receiver of unknown type; a name bound to
more than one definition; a reassigned name; a name that may come from a star import; a call by name
at run time, such as getattr or a framework hook."""


class GraphNode(ContractModel):
    id: NodeId
    kind: NodeKind
    name: str
    """`total`; a module's last segment."""
    qualname: str
    """`Product.total`; a module's dotted path."""
    path: WorkspacePath
    start_line: int
    """The first decorator's line, if any."""
    end_line: int
    parent_id: NodeId | None
    """The module for top-level definitions, the class for methods; null for modules."""
    is_test: bool
    """In a test module: hidden by default, never listed in a cone."""


class GraphEdge(ContractModel):
    id: EdgeId
    kind: EdgeKind
    src: NodeId
    dst: NodeId
    confidence: Confidence
    reason: PossibleReason | None
    """Set exactly when the confidence is "possible"."""

    @model_validator(mode="after")
    def _reason_matches_confidence(self) -> Self:
        if (self.confidence == "possible") != (self.reason is not None):
            raise ValueError('reason is set exactly when confidence is "possible"')
        return self


class Point(ContractModel):
    x: float
    y: float


class ParseError(ContractModel):
    path: WorkspacePath
    line: int | None
    message: str


class GraphSnapshot(ContractModel):
    version: str
    """A hash of every file's hash."""
    commit: str
    """Main's HEAD, which the graph was parsed from."""
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    parse_errors: list[ParseError]
    """Files kept at their last good parse."""
    positions: dict[NodeId, Point]
    """The saved preset layout; empty when there is none."""


class GraphDelta(ContractModel):
    from_version: str
    to_version: str
    commit: str
    cause: Literal["merge", "user_edit", "rescan", "revert"]
    nodes_added: list[GraphNode]
    nodes_changed: list[GraphNode]
    """Same id, other fields."""
    nodes_removed: list[NodeId]
    edges_added: list[GraphEdge]
    edges_changed: list[GraphEdge]
    """Same id, other confidence or reason."""
    edges_removed: list[EdgeId]
    parse_errors: list[ParseError]
    """The full current list."""


class ChangeGraph(ContractModel):
    """One change against its own base commit."""

    nodes_added: list[GraphNode]
    """In the change's version, not in its base."""
    nodes_modified: list[NodeId]
    """The node's own source differs from the base, excluding the bodies of nodes nested in it."""
    nodes_removed: list[NodeId]
    """In the base, gone from the change's version."""
    edges_added: list[GraphEdge]
    """To draw the added nodes' calls."""
    edges_removed: list[EdgeId]


class ImpactEntry(ContractModel):
    node_id: NodeId
    distance: int
    """1: it calls a seed directly."""


class ImpactCone(ContractModel):
    """The functions possibly affected by a change; never presented as a complete list."""

    seeds: list[NodeId]
    """The change's added and modified nodes."""
    entries: list[ImpactEntry]
    """By distance, then id; seeds and tests left out."""
    edges_followed: Literal["exact", "exact_and_possible"]
    """"exact" in v1."""
    max_depth: int
    max_nodes: int
    omitted_by_depth: int
    """Reachable beyond max_depth."""
    omitted_by_limit: int
    """Cut by max_nodes."""
    tests_reaching: int
    """Test functions whose exact calls reach a seed, at any depth."""
