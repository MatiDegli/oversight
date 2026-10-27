import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from oversight.agents import AgentTask
from oversight.graph import (
    CodeVersion,
)
from oversight.workspace import (
    Tree,
)
from oversight_contracts.common import AgentId, ChangeId
from oversight_contracts.events import (
    ChangeResolved,
    Conflict,
)
from oversight_contracts.graph import ChangeGraph, GraphDelta, GraphSnapshot

_log = logging.getLogger("oversight.changes._manager")

type State = Literal[
    "working", "ready", "unproposed", "deciding", "conflict", "closing", "closed", "broken"
]
type Source = Literal["git", "tests", "graph", "server"]
type By = Literal["agent", "user"]
"""Who wrote a file version that a `change.updated` shows."""

APPROVABLE: frozenset[State] = frozenset({"ready"})
REJECTABLE: frozenset[State] = frozenset({"ready", "conflict", "unproposed", "broken"})
"""`broken`: the merge failed and main's state is unknown; Reject still closes the change."""
REVISABLE: frozenset[State] = frozenset({"ready", "conflict"})
STOPPABLE: frozenset[State] = frozenset({*REJECTABLE, "working"})
"""`working`: the run is cancelled first, and the change's outcome waits for its end."""
STOPPED = "Stopped by the user."
SUSPECT = "Main's state is unknown; reject the change and restart the server"
CLOSE_OUT_ATTEMPTS = 2
"""Tries per event on each occasion (a decision, a retry before the next decision or `open`, the
drain)."""


class ChangeNotFound(LookupError):
    """No change has that id: the route answers not_found."""


class WrongState(Exception):
    """The change cannot take that decision now: the route answers wrong_state."""


@dataclass(frozen=True)
class Revision:
    """What the agent resumes with after Request changes."""

    comment: str
    note: str | None
    """Set when the base moved: main merged in, and the files with conflict markers or the tests
    that failed with main."""


@dataclass
class Move:
    """A base move's facts as read so far, for its recovery."""

    main_head: str | None = None
    old_head: str | None = None


@dataclass
class ChangeRecord:
    task: AgentTask
    model_id: str
    tree: Tree
    base_commit: str
    base_files: dict[str, str]
    base: CodeVersion
    head_files: dict[str, str]
    head: CodeVersion
    graph: ChangeGraph
    state: State = "working"
    revision: int = 0
    head_commit: str | None = None
    """The head of the last revision the store took."""
    proposed_now: bool = False
    """The current run (the first, or a revision's) proposed; `run_ended` reads it."""
    summary: str | None = None
    """The agent's note on the last revision the store took."""
    steps: int = 0
    requests: int = 0
    """Request changes taken so far; each grows the step budget by `limits.max_steps`."""
    requested: ChangeResolved | None = None
    """A `changes_requested` the store has not taken yet: sent before the next proposal, or
    before a Reject's close-out."""
    conflict: Conflict | None = None
    """The last approval's conflict, for the note of a redo on the new base."""
    merged: bool = False
    touched: set[str] = field(default_factory=lambda: set[str]())
    published: dict[str, str | None] = field(default_factory=lambda: dict[str, str | None]())
    """Per path, the `modified` last sent in a `change.updated`."""
    unpublished: set[str] = field(default_factory=lambda: set[str]())
    resolution: ChangeResolved | None = None
    delta: GraphDelta | None = None
    next_snapshot: GraphSnapshot | None = None
    sent: set[str] = field(default_factory=lambda: set[str]())
    """Close-out events already sent: "resolved", "delta", "done"."""
    sending: asyncio.Task[None] | None = None
    """The close-out publish in flight, kept until it ends, even when its close-out was cancelled;
    the next close-out, and any merge, waits for it first."""
    cleaned: bool = False
    """The worktree and branch are gone."""
    fallback: State = "ready"
    """Where a decision that ends without settling leaves the change: the state it was taken from,
    `broken` once the merge may have touched main, or `conflict` once the approval's conflict is
    sent. The change stays `deciding` until then, so nothing is decided while it is settled."""
    decision: int = 0
    """Counts the decisions taken; only the latest one's task may end `deciding`."""
    taken: int = 0
    """The revision Approve was clicked on (R1): a later one is reviewed before any merge."""
    stopped: bool = False
    """The decision is a Stop: its close-out sends `stopped` before the resolution, no `done`."""

    @property
    def agent_id(self) -> AgentId:
        return self.task.agent_id

    @property
    def change_id(self) -> ChangeId:
        return self.task.change_id


@dataclass(frozen=True)
class Facts:
    """Main as git reports it after a merge attempt."""

    head: str
    clean: bool
    merging: bool
    contains: bool
    """The change's head commit is an ancestor of main's HEAD."""

    @property
    def merged(self) -> bool:
        return self.contains and self.clean and not self.merging

    def unchanged(self, pre: str | None) -> bool:
        # No `pre`: the first git call failed, so the merge never ran.
        return (pre is None or self.head == pre) and self.clean and not self.merging


@dataclass
class ChangeState:
    """The single owner of mutable manager state, with no I/O."""

    snapshot: GraphSnapshot
    changes: dict[ChangeId, ChangeRecord] = field(
        default_factory=lambda: dict[ChangeId, ChangeRecord]()
    )
    agents: dict[AgentId, ChangeRecord] = field(
        default_factory=lambda: dict[AgentId, ChangeRecord]()
    )
    bases: dict[str, tuple[dict[str, str], CodeVersion]] = field(
        default_factory=lambda: dict[str, tuple[dict[str, str], CodeVersion]]()
    )
    pending: dict[AgentId, ChangeRecord] = field(
        default_factory=lambda: dict[AgentId, ChangeRecord]()
    )
    """Changes whose close-out or cleanup is unfinished; retried before a decision or open."""
    tasks: set[asyncio.Task[Any]] = field(default_factory=lambda: set[asyncio.Task[Any]]())
    suspect: bool = False

    def break_change(self, rec: ChangeRecord) -> None:
        """Main's state is unknown: the change is `broken`, and main suspect until restart."""
        rec.state = "broken"
        self.suspect = True

    def restore(self, rec: ChangeRecord, decision: int) -> None:
        if rec.state == "deciding" and rec.decision == decision:
            if rec.fallback == "broken":
                self.break_change(rec)
            else:
                rec.state = rec.fallback
