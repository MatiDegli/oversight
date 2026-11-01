# pyright: reportUnknownMemberType=false
# Pydantic leaves ValidationInfo.context untyped; keep the approved authority validator literal.
"""The envelope, event payloads and ServerEvent union (docs/proposal.md, 4.4 to 4.6).

Every event carries seq, ts, type and payload. Lead replies carry bounded, masked model text.
"""

from typing import Annotated, Literal, Self

from pydantic import (
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveInt,
    ValidationInfo,
    model_validator,
)

from oversight_contracts.common import (
    AgentId,
    CaseOutcome,
    ChangeId,
    ContractModel,
    FileStatus,
    MessageText,
    NodeId,
    NonEmptyText,
    PlanId,
    QueuedTaskId,
    RunId,
    Slot,
    Timestamp,
    WorkspacePath,
)
from oversight_contracts.decisions import DecisionRef
from oversight_contracts.examples import TaskExample
from oversight_contracts.grants import GrantCreated, GrantRevoked, GrantUse, rules_satisfied
from oversight_contracts.graph import ChangeGraph, GraphDelta, GraphSnapshot, ImpactCone
from oversight_contracts.plans import Plan, PlanZone
from oversight_contracts.policy import ChangeAssessment, MergeModeState
from oversight_contracts.queue import QueuedTask
from oversight_contracts.review import ChangeFacts, CrossReview, ReviewCard

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
    """Tool calls per round; Request changes starts a new round with the same budget."""
    max_output_tokens: int
    """Per model call."""
    max_wall_clock_s: int
    """Per round; Request changes starts a new round."""
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
    zone: PlanZone | None = None
    """The server-assigned edit zone."""
    started_by: AgentId | None = None
    """The lead that started this agent."""
    queued_task_id: QueuedTaskId | None = None
    """The queue item dispatching this agent."""
    base_commit: NonEmptyText | None = None
    """The agent base used to guard an unproposed rejection."""
    examples: list[TaskExample] | None = None
    """The accepted task examples, retained for live observation and replay."""
    answering: DecisionRef | None = None
    """The originating inbox entry, copied unchanged into every status for this task; null when
    unlinked or absent from older recordings. Producer-side validation verifies the reference.
    """


class CaseResult(ContractModel):
    """One observed pytest node outcome."""

    test_id: NonEmptyText
    """The complete pytest node id."""
    outcome: CaseOutcome
    """The observed per-node outcome."""
    detail: str | None = None
    """A bounded diagnostic without absolute paths."""


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

    cases: list[CaseResult] | None = Field(default=None, max_length=200)
    """At most 200 observed pytest node outcomes."""
    cases_complete: bool | None = None
    """Whether all per-node outcomes were resolved and retained."""

    @model_validator(mode="after")
    def _case_completeness(self) -> Self:
        if self.cases_complete and self.cases is None:
            raise ValueError("complete cases require a list")
        if self.cases is not None and len({case.test_id for case in self.cases}) != len(self.cases):
            raise ValueError("case ids must be unique")
        return self


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

    error_kind: Literal["unknown_tool", "invalid_json", "invalid_arguments"] | None = None
    """Set only for malformed model tool calls; every other tool error stays null."""

    @model_validator(mode="after")
    def _malformed_call_is_error(self) -> Self:
        if self.error_kind is not None and self.result != "error":
            raise ValueError("error_kind requires result error")
        return self


class AgentMessageSent(ContractModel):
    """A message the person sent to a running agent, emitted when the server accepts it (the 202).

    The agent reads queued messages before its next model call; a drive that ends first drops them.
    """

    agent_id: AgentId
    """The recipient: a coding agent or coordination lead."""
    text: MessageText | None = None
    """As stored: runs shaped like a credential are masked as `[redacted]` before the event is written.
    Null only in an export for publication that withheld it."""  # noqa: E501


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
    examples: list[TaskExample] | None = None
    """The task examples and confirmations captured for this proposal."""


class Conflict(ContractModel):
    kind: Literal["merge", "tests"]
    paths: list[WorkspacePath]
    """Conflicting files, for a merge conflict."""
    tests: SuiteResult | None
    """The combined result's tests, for a test failure."""


class ChangeResolved(ContractModel):
    change_id: ChangeId
    agent_id: AgentId
    revision: NonNegativeInt
    """Zero when the change was never proposed."""
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

    decided_by: Literal["person", "rules", "cross_review", "lead_agent"] | None = None
    """Recorded decision or revision-request provenance."""
    review_seq: PositiveInt | None = None
    """The review triggering this revision request."""
    requested_by: AgentId | None = None
    """The lead requesting this revision."""
    head_commit: NonEmptyText | None = None
    """The head receiving this resolution, or the agent base at revision zero."""
    reasons: list[str] = Field(default_factory=list)
    """Recorded server-authored reasons for this decision."""
    assessment: ChangeAssessment | None = None
    """The immutable assessment used for this decision, when available."""
    answers: list[ReviewCard] | None = Field(default=None, max_length=8)
    """The accepted human answers, copied by the server from a decision command."""
    grants: list[GrantUse] | None = Field(default=None, exclude_if=lambda value: value is None)
    """Immutable authorization and measured callers waived by a rules approval."""

    @model_validator(mode="after")
    def _authority(self, info: ValidationInfo) -> Self:
        actor = self.decided_by
        if actor in {"cross_review", "lead_agent"}:
            if (
                self.decision != "changes_requested"
                or self.reason is not None
                or self.revision < 1
                or not self.head_commit
                or not self.comment
                or not self.comment.strip()
            ):
                raise ValueError("model actors may only request changes with a target and comment")
            if actor == "cross_review" and self.review_seq is None:
                raise ValueError("cross-review requests require review_seq")
        if (actor == "lead_agent") != (self.requested_by is not None):
            raise ValueError("requested_by is set exactly for lead revision requests")
        if self.decision == "approved" and actor not in {"person", "rules", None}:
            raise ValueError("only person or rules may approve")
        if (info.context or {}).get("live") and actor is None:
            raise ValueError("live resolutions require explicit provenance")
        return self

    @model_validator(mode="after")
    def _zero_resolution(self) -> Self:
        if self.revision == 0 and (
            self.decision != "rejected"
            or self.merge is not None
            or self.decided_by not in {"person", None}
        ):
            raise ValueError("revision zero requires a personal or historical rejection")
        return self

    @model_validator(mode="after")
    def _rules_decision(self) -> Self:
        if self.grants is not None and self.decided_by != "rules":
            raise ValueError("grant uses belong only to rules approvals")
        if self.decided_by != "rules":
            return self
        assessment = self.assessment
        if (
            self.decision != "approved"
            or assessment is None
            or assessment.mode != "merge_when_safe"
            or not rules_satisfied(assessment, self.grants or ())
            or (self.change_id, self.agent_id, self.revision)
            != (assessment.change_id, assessment.agent_id, assessment.revision)
            or (self.grants and self.head_commit != assessment.head_commit)
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

    role: Literal["agent", "review", "plan", "lead"] | None = None
    """The purpose of this model call."""
    change_id: ChangeId | None = None
    """The change associated with this call."""
    plan_id: PlanId | None = None
    """The plan associated with this call."""


class ModelAttemptFailed(ContractModel):
    """Emitted by the server once per failed attempt, before the retry wait and for the final
    failure; never written by the model. The card reads "Waiting for model (retry n/5)" from
    an attempt with wait_s set, until that agent's next agent.step, usage.tick or
    model.attempt_failed.
    """

    agent_id: AgentId
    """The author for review calls, as in usage.tick."""
    change_id: ChangeId | None = None
    role: Literal["agent", "review", "plan", "lead"] | None = None
    model: NonEmptyText
    """The profile id sent; the API reports no model on a failure."""
    provider: Literal["meta", "baseten", "fake"]
    attempt: PositiveInt
    """The 1-based number of the attempt that failed."""
    max_attempts: PositiveInt
    """The retry policy limit, currently 5."""
    cause: Literal["rate_limited", "server_error", "connection", "timeout", "refused"]
    status: int | None = None
    """HTTP status when the provider answered."""
    retry_after_s: NonNegativeFloat | None = None
    """Provider Retry-After, when sent."""
    wait_s: NonNegativeFloat | None = None
    """Wait before the next attempt; null when none follows."""
    final: Literal["attempts_used", "not_retryable", "deadline"] | None = None

    @model_validator(mode="after")
    def _failed_attempt_shape(self) -> Self:
        if (self.final is not None) != (self.wait_s is None):
            raise ValueError("final is set exactly when wait_s is null")
        if self.attempt > self.max_attempts:
            raise ValueError("attempt must not exceed max_attempts")
        if self.final == "attempts_used" and self.attempt != self.max_attempts:
            raise ValueError("attempts_used requires attempt equal to max_attempts")
        if self.cause in {"connection", "timeout"}:
            if self.status is not None:
                raise ValueError("connection and timeout have no HTTP status")
        elif self.status is None:
            raise ValueError("provider responses require HTTP status")
        elif self.cause == "rate_limited" and self.status != 429:
            raise ValueError("rate_limited requires HTTP 429")
        elif self.cause == "server_error" and not 500 <= self.status <= 599:
            raise ValueError("server_error requires HTTP 500-599")
        elif self.cause == "refused" and (self.status == 429 or 500 <= self.status <= 599):
            raise ValueError("refused excludes HTTP 429 and 500-599")
        return self


class ErrorReport(ContractModel):
    """A failure shown without breaking the demo."""

    source: Literal["model_api", "agent", "tools", "git", "tests", "graph", "server"]
    message: str
    agent_id: AgentId | None
    change_id: ChangeId | None
    fatal: bool
    """The agent's run halted because of it."""


class SuiteExecution(ContractModel):
    """The server-observed execution of a specific Git tree."""

    change_id: ChangeId
    """The change owning this execution."""
    agent_id: AgentId
    """The agent owning this execution."""
    revision: PositiveInt | None
    """The proposed revision, if already assigned."""
    phase: Literal["agent", "proposal", "combined", "base_probe", "head_probe"]
    """The purpose of this execution."""
    tested_commit: NonEmptyText | None
    """The tested commit, when one exists."""
    tested_tree: NonEmptyText
    """The exact tested Git tree."""
    suite: SuiteResult
    """The server-observed test result."""


class LeadStatus(ContractModel):
    """The server-owned coordination lead state."""

    agent_id: AgentId
    """The lead identifier."""
    revision: PositiveInt
    """The monotonically increasing lead-state revision."""
    task: NonEmptyText
    """The coordination task."""
    model: NonEmptyText
    """The offered model profile."""
    state: Literal["starting", "working", "idle", "halted", "stopped", "done"]
    """Idle waits between turns; done is retained for historical log compatibility."""
    step: NonNegativeInt
    """Completed public-command tool calls."""
    detail: str | None
    """The lifecycle explanation."""
    requested_plan_id: PlanId | None = None
    """The plan this revision's step requested, or null."""


class LeadReply(ContractModel):
    """One lead turn's reply; L2 produces it and L3 presents the thread."""

    agent_id: AgentId
    """The lead replying."""
    answers_seq: PositiveInt | None
    """An earlier agent.message to this lead; null answers the opening task."""
    text: Annotated[str, Field(min_length=1, max_length=4096, pattern=r"\S")] | None
    """Nonblank text, masked before storage like messages; null with a cut or withheld export."""
    through_seq: PositiveInt | None
    """The observed horizon, strictly lower than this reply's envelope seq, or null."""
    cut: Literal["tool_calls", "model_calls", "time", "spend_brake"] | None
    """The turn limit that cut the reply short, or null."""


class RevertResult(ContractModel):
    """The outcome of a person-authorized rules merge inverse."""

    change_id: ChangeId
    """The original change."""
    revision: PositiveInt
    """The original merged revision."""
    resolution_seq: PositiveInt
    """The original rules resolution."""
    merge_commit: NonEmptyText
    """The original merge commit."""
    decided_by: Literal["person"]
    """The principal requesting this revert."""
    result: Literal["reverted", "needs_you"]
    """The revert outcome."""
    main_commit: NonEmptyText
    """Main when this result was finalized."""
    revert_commit: NonEmptyText | None
    """The committed inverse, if successful."""
    paths: list[WorkspacePath]
    """Conflicting paths, when known."""
    reason: NonEmptyText | None
    """Why intervention is required."""
    tests: SuiteResult | None
    """The inverse candidate's test result."""

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if self.result == "reverted":
            if (
                not self.revert_commit
                or self.main_commit != self.revert_commit
                or self.tests is None
                or self.tests.outcome != "passed"
            ):
                raise ValueError("reverted requires the committed and tested inverse")
        elif not self.reason or self.revert_commit is not None:
            raise ValueError("needs_you requires a reason and no inverse commit")
        return self


# The envelope


class EventBase(ContractModel):
    seq: int = Field(ge=1)
    """From 1, +1 per event within a run, no gaps; assigned by the single writer."""
    ts: Timestamp


class GrantCreatedEvent(EventBase):
    type: Literal["grant.created"]
    payload: GrantCreated


class GrantRevokedEvent(EventBase):
    type: Literal["grant.revoked"]
    payload: GrantRevoked


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


class AgentMessageEvent(EventBase):
    type: Literal["agent.message"]
    payload: AgentMessageSent


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


class ChangeReviewedEvent(EventBase):
    """The change.reviewed envelope."""

    type: Literal["change.reviewed"]
    """The event discriminator."""
    payload: CrossReview
    """The complete event payload."""


class ChangeFactsRecordedEvent(EventBase):
    """The change.facts_recorded envelope."""

    type: Literal["change.facts_recorded"]
    """The event discriminator."""
    payload: ChangeFacts
    """The complete event payload."""


class TestsExecutedEvent(EventBase):
    """The tests.executed envelope."""

    type: Literal["tests.executed"]
    """The event discriminator."""
    payload: SuiteExecution
    """The complete event payload."""


class RevertResolvedEvent(EventBase):
    """The revert.resolved envelope."""

    type: Literal["revert.resolved"]
    """The event discriminator."""
    payload: RevertResult
    """The complete event payload."""


class QueueUpdatedEvent(EventBase):
    """The queue.updated envelope."""

    type: Literal["queue.updated"]
    """The event discriminator."""
    payload: QueuedTask
    """The complete event payload."""


class LeadStatusEvent(EventBase):
    """The lead.status envelope."""

    type: Literal["lead.status"]
    """The event discriminator."""
    payload: LeadStatus
    """The complete event payload."""


class LeadReplyEvent(EventBase):
    """The lead.reply envelope."""

    type: Literal["lead.reply"]
    payload: LeadReply


class ModelAttemptFailedEvent(EventBase):
    """The server-owned model.attempt_failed envelope."""

    type: Literal["model.attempt_failed"]
    payload: ModelAttemptFailed


type ServerEvent = Annotated[
    RunStartedEvent
    | GraphSnapshotEvent
    | GraphDeltaEvent
    | AgentStatusEvent
    | AgentStepEvent
    | AgentMessageEvent
    | AgentFocusEvent
    | ChangeUpdatedEvent
    | ChangeProposedEvent
    | ChangeResolvedEvent
    | UsageTickEvent
    | ModelAttemptFailedEvent
    | ErrorReportEvent
    | ChangeAssessedEvent
    | MergeModeChangedEvent
    | PlanUpdatedEvent
    | ChangeReviewedEvent
    | ChangeFactsRecordedEvent
    | TestsExecutedEvent
    | RevertResolvedEvent
    | QueueUpdatedEvent
    | LeadStatusEvent
    | LeadReplyEvent
    | GrantCreatedEvent
    | GrantRevokedEvent,
    Field(discriminator="type"),
]
"""Every event the server sends; validate one with `TypeAdapter(ServerEvent)`."""
