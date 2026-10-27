"""The envelope, the eleven event payloads and the ServerEvent union (docs/proposal.md, 4.4 to 4.6).

Every event carries seq, ts, type and payload. No event carries an absolute path or model text.
"""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from oversight_contracts.common import (
    AgentId,
    ChangeId,
    ContractModel,
    FileStatus,
    NodeId,
    PlanId,
    RunId,
    Slot,
    Timestamp,
    WorkspacePath,
)
from oversight_contracts.graph import ChangeGraph, GraphDelta, GraphSnapshot, ImpactCone
from oversight_contracts.plans import Plan
from oversight_contracts.policy import ChangeAssessment, MergeModeState

# run.started


class ModelProfile(ContractModel):
    id: str
    """"muse-spark", "muse-spark-open", "fake"."""
    label: str
    """"Muse Spark"."""
    provider: Literal["meta", "baseten", "fake"]
    model: str
    """The exact id sent to the API, such as "meta-models/muse-spark-open-1026"."""
    reasoning: str | None
    """The reasoning setting sent on every call; null if the API has none."""


class SlotInfo(ContractModel):
    slot: Slot
    default_model: str
    """A ModelProfile id."""


class Limits(ContractModel):
    max_steps: int
    """Tool calls per task."""
    max_output_tokens: int
    """Per model call."""
    max_wall_clock_s: int
    """Per task."""
    test_timeout_s: int
    cone_max_depth: int
    cone_max_nodes: int


class Budget(ContractModel):
    budget_usd: float
    spent_usd: float
    """From the ledger, before this run."""
    warn_at: list[float]
    """[0.5, 0.8]."""
    block_at: float
    """0.9: above it the server refuses new agents."""


class RunStarted(ContractModel):
    run_id: RunId
    contract_version: str
    prompt_version: str
    """A name and a hash of the system prompt and tool schemas."""
    workspace: str
    """"demo-repo"."""
    base_commit: str
    """Main's first commit in the runtime workspace."""
    models: list[ModelProfile]
    slots: list[SlotInfo]
    limits: Limits
    budget: Budget
    merge_mode: MergeModeState | None = None
    """Initial server merge mode; null in recordings without mode metadata."""


# agent.status, agent.step, agent.focus

type AgentState = Literal[
    "starting", "working", "ready_for_review", "revising", "paused", "halted", "done", "stopped"
]
"""`paused` is level 3. `halted`: the run ended on its own, at the step limit, the no-progress
cutoff or an error; its change stays open for review."""


class AgentStatus(ContractModel):
    agent_id: AgentId
    slot: Slot
    change_id: ChangeId
    task: str
    model: str
    """A ModelProfile id."""
    state: AgentState
    step: int
    """Tool calls finished so far."""
    max_steps: int
    """Grows when a revision starts."""
    detail: str | None
    """Why it halted or stopped, in one sentence."""
    plan_id: PlanId | None = None
    """The originating plan; null for standalone agents or older recordings."""


class SuiteResult(ContractModel):
    """A test run's result. Not "TestResult", which pytest would try to collect."""

    outcome: Literal["passed", "failed", "error", "timeout"]
    passed: int
    failed: int
    """Failures and errors."""
    skipped: int
    duration_ms: int
    failed_tests: list[str]
    """Pytest node ids, 20 at most."""


class AgentStep(ContractModel):
    agent_id: AgentId
    step: int
    """1-based."""
    tool: str
    """The tool the model named, even one that does not exist."""
    summary: str
    """Built by the server from the tool and its arguments, never from model text."""
    result: Literal["ok", "error"]
    error: str | None
    """The error returned to the model, shortened."""
    duration_ms: int
    tests: SuiteResult | None
    """Set for run_tests."""


class FocusRange(ContractModel):
    path: WorkspacePath
    start_line: int | None
    """Null: the whole file."""
    end_line: int | None


class AgentFocus(ContractModel):
    agent_id: AgentId
    mode: Literal["read", "edit"]
    ranges: list[FocusRange]
    """A search may cover several files."""
    node_ids: list[NodeId]
    """The innermost nodes over the ranges, resolved on the agent's own copy. A search covers only
    its first 5 matching nodes, in both lists."""


# change.updated, change.proposed, change.resolved


class FileDiff(ContractModel):
    path: WorkspacePath
    status: FileStatus
    original: str | None
    """The whole file at the change's base; null if added."""
    modified: str | None
    """The whole file in the change's version; null if deleted."""
    lines_added: int
    lines_removed: int


class ChangeUpdated(ContractModel):
    change_id: ChangeId
    agent_id: AgentId
    author: Literal["agent", "user"]
    file: FileDiff
    """The file this edit touched."""
    node_ids: list[NodeId]
    """Nodes of that file the change adds, modifies or removes."""
    graph: ChangeGraph
    """The whole change against its base, after this edit."""
    files_changed: int
    """Totals for the whole change, here and below."""
    lines_added: int
    lines_removed: int


class FileStat(ContractModel):
    path: WorkspacePath
    status: FileStatus
    lines_added: int
    lines_removed: int


class ChangeProposed(ContractModel):
    change_id: ChangeId
    agent_id: AgentId
    revision: int
    """1, then +1 after each Request changes."""
    task: str
    summary: str | None
    """The agent's note, from finish; null when the run halted."""
    base_commit: str
    head_commit: str
    """The commit on the agent's branch that holds this revision."""
    files: list[FileStat]
    lines_added: int
    lines_removed: int
    graph: ChangeGraph
    tests: SuiteResult
    """Run by the server on head_commit, through run_tests' runner and timeout."""
    impact: ImpactCone
    """Computed on the change's version."""


class Conflict(ContractModel):
    kind: Literal["merge", "tests"]
    paths: list[WorkspacePath]
    """Conflicting files, for a merge conflict."""
    tests: SuiteResult | None
    """The combined result's tests, for a test failure."""


class ChangeResolved(ContractModel):
    change_id: ChangeId
    agent_id: AgentId
    revision: int
    """0 when the change was never proposed."""
    decision: Literal["approved", "changes_requested", "rejected"]
    reason: Literal["stopped"] | None
    """"stopped": the agent was stopped and its change discarded."""
    comment: str | None
    merge: Literal["merged", "conflict"] | None
    """Set exactly when approved."""
    merge_commit: str | None
    """Main's new HEAD, set exactly when merged."""
    conflict: Conflict | None
    """Set exactly when the merge is "conflict"."""

    decided_by: Literal["person", "rules"] | None = None
    """Decision provenance; null when it was not recorded."""
    reasons: list[str] = Field(default_factory=list)
    """Recorded server-authored reasons for this decision."""
    assessment: ChangeAssessment | None = None
    """The immutable assessment used for this decision, when available."""

    @model_validator(mode="after")
    def _rules_decision(self) -> Self:
        if self.decided_by != "rules":
            return self
        assessment = self.assessment
        if (
            self.decision != "approved"
            or assessment is None
            or assessment.mode != "merge_when_safe"
            or any(rule.result != "pass" for rule in assessment.rules)
            or (self.change_id, self.agent_id, self.revision)
            != (assessment.change_id, assessment.agent_id, assessment.revision)
            or not self.reasons
            or any(not reason.strip() for reason in self.reasons)
        ):
            raise ValueError("rules decisions require an approved matching assessment and reasons")
        return self

    @model_validator(mode="after")
    def _merge_fields_agree(self) -> Self:
        if (self.decision == "approved") != (self.merge is not None):
            raise ValueError("merge is set exactly when the decision is approved")
        if (self.merge == "merged") != (self.merge_commit is not None):
            raise ValueError('merge_commit is set exactly when merge is "merged"')
        if (self.merge == "conflict") != (self.conflict is not None):
            raise ValueError('conflict is set exactly when merge is "conflict"')
        return self


# usage.tick, error


class UsageTick(ContractModel):
    agent_id: AgentId
    model: str | None
    """The model id the API reported for this call."""
    input_tokens: int | None
    """Null: not reported. Never 0 for unknown."""
    output_tokens: int | None
    cached_tokens: int | None
    cost_usd: float | None
    """Null: unknown."""
    spent_usd: float
    """The ledger's total, this call included."""
    budget_usd: float
    brake: Literal["ok", "warn_50", "warn_80", "blocked"]


class ErrorReport(ContractModel):
    """A failure shown without breaking the demo."""

    source: Literal["model_api", "agent", "tools", "git", "tests", "graph", "server"]
    message: str
    agent_id: AgentId | None
    change_id: ChangeId | None
    fatal: bool
    """The agent's run halted because of it."""


# The envelope


class EventBase(ContractModel):
    seq: int = Field(ge=1)
    """From 1, +1 per event within a run, no gaps; assigned by the single writer."""
    ts: Timestamp


class RunStartedEvent(EventBase):
    type: Literal["run.started"]
    payload: RunStarted


class GraphSnapshotEvent(EventBase):
    type: Literal["graph.snapshot"]
    payload: GraphSnapshot


class GraphDeltaEvent(EventBase):
    type: Literal["graph.delta"]
    payload: GraphDelta


class AgentStatusEvent(EventBase):
    type: Literal["agent.status"]
    payload: AgentStatus


class AgentStepEvent(EventBase):
    type: Literal["agent.step"]
    payload: AgentStep


class AgentFocusEvent(EventBase):
    type: Literal["agent.focus"]
    payload: AgentFocus


class ChangeUpdatedEvent(EventBase):
    type: Literal["change.updated"]
    payload: ChangeUpdated


class ChangeProposedEvent(EventBase):
    type: Literal["change.proposed"]
    payload: ChangeProposed


class ChangeResolvedEvent(EventBase):
    type: Literal["change.resolved"]
    payload: ChangeResolved


class UsageTickEvent(EventBase):
    type: Literal["usage.tick"]
    payload: UsageTick


class ErrorReportEvent(EventBase):
    type: Literal["error"]
    payload: ErrorReport


class ChangeAssessedEvent(EventBase):
    """One recorded change assessment."""

    type: Literal["change.assessed"]
    payload: ChangeAssessment


class MergeModeChangedEvent(EventBase):
    """The new server merge mode."""

    type: Literal["merge_mode.changed"]
    payload: MergeModeState


class PlanUpdatedEvent(EventBase):
    """The complete new plan revision."""

    type: Literal["plan.updated"]
    payload: Plan


type ServerEvent = Annotated[
    RunStartedEvent
    | GraphSnapshotEvent
    | GraphDeltaEvent
    | AgentStatusEvent
    | AgentStepEvent
    | AgentFocusEvent
    | ChangeUpdatedEvent
    | ChangeProposedEvent
    | ChangeResolvedEvent
    | UsageTickEvent
    | ErrorReportEvent
    | ChangeAssessedEvent
    | MergeModeChangedEvent
    | PlanUpdatedEvent,
    Field(discriminator="type"),
]
"""Every event the server sends; validate one with `TypeAdapter(ServerEvent)`."""
