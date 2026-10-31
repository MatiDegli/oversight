from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from oversight.changes._measured import MeasuredEvidence
    from oversight.changes._preemption import PersonIntent
    from oversight.changes._review_port import ReviewCapture
    from oversight.changes._run_findings import EditReceipt, SuiteReceipt

from oversight.agents import AgentTask
from oversight.changes._behavior import BehaviorDiff
from oversight.changes._evidence import WriteEvidence
from oversight.graph import (
    CodeVersion,
)
from oversight.workspace import (
    SuiteRun,
    Tree,
)
from oversight_contracts.common import AgentId, ChangeId
from oversight_contracts.events import (
    ChangeResolved,
    Conflict,
    SuiteExecution,
)
from oversight_contracts.graph import ChangeGraph, GraphDelta, GraphSnapshot
from oversight_contracts.policy import MergeModeState
from oversight_contracts.review import FinishDeclaration, RoleUsage

type State = Literal[
    "working",
    "ready",
    "unproposed",
    "deciding",
    "conflict",
    "closing",
    "closed",
    "broken",
    "delivery_failed",
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
    """The person's unchanged comment, followed by graph context when available; only for the
    agent's next message. The resolution event carries the person's comment alone."""
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
    proposal_mode: MergeModeState | None = None
    rules_consumed: set[int] = field(default_factory=set[int])
    head_commit: str | None = None
    """The head of the last revision the store took."""
    proposed_now: bool = False
    """The current run (the first, or a revision's) proposed; `run_ended` reads it."""
    summary: str | None = None
    """The agent's note on the last revision the store took."""
    steps: int = 0
    requests: int = 0
    """Request changes taken so far; each grows the step budget by `limits.max_steps`."""
    requested_evidence: WriteEvidence | None = None
    requested: ChangeResolved | None = None
    """A `changes_requested` the store has not taken yet: sent before the next proposal, or
    before a Reject's close-out."""
    conflict: Conflict | None = None
    """The last approval's conflict, for the note of a redo on the new base."""
    merged: bool = False
    observed_paths: set[str] = field(default_factory=set[str])
    touched: set[str] = field(default_factory=lambda: set[str]())
    published: dict[str, str | None] = field(default_factory=lambda: dict[str, str | None]())
    """Per path, the `modified` last sent in a `change.updated`."""
    unpublished: set[str] = field(default_factory=lambda: set[str]())
    run_edits: list[EditReceipt] = field(default_factory=lambda: list["EditReceipt"]())
    run_suites: list[SuiteReceipt] = field(default_factory=lambda: list["SuiteReceipt"]())
    executions: dict[int, SuiteExecution] = field(default_factory=dict[int, SuiteExecution])
    summary_usage: dict[int, RoleUsage] = field(default_factory=dict[int, RoleUsage])
    suite_runs: dict[int, SuiteRun] = field(default_factory=dict[int, SuiteRun])
    behavior_diffs: dict[int, BehaviorDiff] = field(default_factory=dict[int, BehaviorDiff])
    """Private per-revision computation; public facts capture an independent explicit projection."""
    measured_cones: dict[int, MeasuredEvidence] = field(
        default_factory=lambda: dict[int, "MeasuredEvidence"]()
    )
    declared: FinishDeclaration | None = None
    delivery_rejected: str | None = None
    evidence: WriteEvidence | None = None
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
    rules_task: asyncio.Task[None] | None = None
    rules_phase: (
        Literal["queued", "preflight", "suite", "authorizing", "effect", "settling", "finished"]
        | None
    ) = None
    person_intent: PersonIntent | None = None
    person_acted: bool = False
    """Permanent per change, including all future re-proposals."""
    review_ready: bool = False
    """The author run ended ready_for_review; a request clears it before resumption."""
    review_task: asyncio.Task[Any] | None = None
    card_generation: int = 0
    card_task: asyncio.Task[None] | None = None
    review_capture: ReviewCapture | None = None
    review_reserved: bool = False
    review_consumed: bool = False
    review_attempts: set[int] = field(default_factory=set[int])
    """An unsuccessful preflight releases the reservation but never retries its review_seq."""
    stopped: bool = False
    """The decision is a Stop: its close-out sends `stopped` before the resolution, no `done`."""

    def person_intervened(self) -> None:
        """Admission is synchronous; an identical or subsequently failed save still counts."""
        self.person_acted = True
        self.invalidate_cards()
        if self.review_task is not None:
            self.review_task.cancel()

    def invalidate_cards(self) -> None:
        self.card_generation += 1
        if self.card_task is not None:
            self.card_task.cancel()

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
    closing: bool = False
    suspect: bool = False

    def break_change(self, rec: ChangeRecord) -> None:
        """Main's state is unknown: the change is `broken`, and main suspect until restart."""
        rec.state = "broken"
        self.suspect = True

    def restore(self, rec: ChangeRecord, decision: int) -> None:
        if not rec.delivery_rejected and rec.state == "deciding" and rec.decision == decision:
            if rec.fallback == "broken":
                self.break_change(rec)
            else:
                rec.state = rec.fallback
