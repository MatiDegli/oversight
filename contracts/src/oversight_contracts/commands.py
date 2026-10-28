"""Request and response bodies, and the single error format (docs/proposal.md, section 4.7).

A 202 means only that a command was accepted; its results arrive as events. Request bodies may
omit their optional keys; responses always carry every key.
"""

from typing import Literal, Self

from pydantic import Field, NonNegativeInt, PositiveInt, model_validator

from oversight_contracts.common import (
    AgentId,
    ChangeId,
    ContractModel,
    MergeMode,
    PlanId,
    RunId,
    Slot,
    WorkspacePath,
)
from oversight_contracts.common import (
    NonEmptyText as NonEmptyText,
)
from oversight_contracts.decisions import DecisionRef
from oversight_contracts.events import ChangeProposed, ChangeResolved, FileDiff, ServerEvent
from oversight_contracts.examples import TaskExample
from oversight_contracts.graph import GraphSnapshot
from oversight_contracts.plans import PlanZone, SubtaskDraft
from oversight_contracts.policy import ChangeAssessment
from oversight_contracts.queue import QueueSpec
from oversight_contracts.review import ChangeFacts, ChangeUsage, CrossReview, ReviewCard


class StartAgent(ContractModel):
    """`POST /agents`."""

    task: NonEmptyText
    slot: Slot | None = None
    """The free slot when omitted."""
    model: str | None = None
    """A ModelProfile id; the slot's default model when omitted."""
    zone: PlanZone | None = None
    """The requested edit zone."""
    examples: list[TaskExample] | None = Field(default=None, max_length=5)
    """Examples submitted with the task before code is changed."""
    examples_only: bool | None = None
    """True requests one model call without edit/test tools; null or false uses normal execution."""
    answering: DecisionRef | None = None
    """The originating inbox entry, copied unchanged into every status for this task; null when
    unlinked or absent from older recordings. Producer-side validation verifies the reference.
    """


class AgentCreated(ContractModel):
    """201 from `POST /agents`."""

    agent_id: AgentId
    change_id: ChangeId
    slot: Slot


class AgentMessage(ContractModel):
    """`POST /agents/{id}/message`, level 3: answers 501 until then."""

    text: NonEmptyText


class ApproveChange(ContractModel):
    """`POST /changes/{id}/approve`: approve the observed revision and head."""

    comment: str | None = None
    answers: list[ReviewCard] | None = Field(default=None, max_length=8)
    """The answered review cards for the observed revision and head."""

    revision: PositiveInt
    """The proposed revision the caller observed."""
    head_commit: NonEmptyText
    """The proposed head the caller observed."""


class RequestChanges(ContractModel):
    """`POST /changes/{id}/request-changes`: the comment goes back to the agent."""

    comment: NonEmptyText
    answers: list[ReviewCard] | None = Field(default=None, max_length=8)
    """The answered review cards accompanying the revision request."""

    revision: PositiveInt
    """The proposed revision the caller observed."""
    head_commit: NonEmptyText
    """The proposed head the caller observed."""


class RejectChange(ContractModel):
    """`POST /changes/{id}/reject`."""

    comment: str | None = None
    answers: list[ReviewCard] | None = Field(default=None, max_length=8)
    """The answered review cards accompanying rejection."""

    revision: NonNegativeInt
    """The observed revision, including zero before any proposal."""
    head_commit: NonEmptyText
    """The observed proposed head, or agent base at revision zero."""


class GraphResponse(ContractModel):
    """200 from `GET /graph`: main's graph and the seq it reflects, read together."""

    run_id: RunId
    seq: int
    graph: GraphSnapshot


class FileContent(ContractModel):
    """200 from `GET /files?path=&agent=`."""

    path: WorkspacePath
    content: str
    hash: str
    """SHA-256 of the content, in hex."""
    agent_id: AgentId | None
    """Whose worktree it was read from; null for main."""


class SaveFile(ContractModel):
    """`PUT /files`: writes main, or the named agent's worktree while it is idle in review."""

    path: WorkspacePath
    content: str
    base_hash: str
    """SHA-256 of the content the user started from; the save fails with stale_file if the file
    changed since."""
    agent_id: AgentId | None = None


class FileSaved(ContractModel):
    """200 from `PUT /files`."""

    path: WorkspacePath
    hash: str


type SettingName = Literal[
    "META_API_KEY",
    "BASETEN_API_KEY",
    "OVERSIGHT_BUDGET_USD",
    "MUSE_SPARK_INPUT_USD_PER_M",
    "MUSE_SPARK_OUTPUT_USD_PER_M",
]


class MissingSetting(ContractModel):
    """A setup problem: names and affected slots only, never configuration values."""

    name: SettingName
    problem: Literal["missing", "invalid"]
    slots: list[Slot]


class Health(ContractModel):
    """200 from `GET /health`."""

    ok: bool
    run_id: RunId
    contract_version: str
    mode: Literal["live", "replay"]
    missing_settings: list[MissingSetting] = Field(default_factory=list[MissingSetting])


type ErrorCode = Literal[
    "not_found",
    "wrong_state",
    "slot_busy",
    "spend_brake",
    "not_configured",
    "invalid_path",
    "stale_file",
    "not_implemented",
    "invalid_request",
    "internal",
    "forbidden",
    "too_large",
]

ERROR_STATUS: dict[ErrorCode, int] = {
    "not_found": 404,
    "wrong_state": 409,
    "slot_busy": 409,
    "spend_brake": 403,
    "not_configured": 503,
    "invalid_path": 422,
    "stale_file": 409,
    "not_implemented": 501,
    "invalid_request": 422,
    "internal": 500,
    "forbidden": 403,
    "too_large": 413,
}
"""The HTTP status of each error code. FastAPI's own validation error becomes invalid_request; an
unexpected server-side failure becomes internal (contract 1.1). forbidden refuses a request whose
Host or Origin is not allowed (contract 1.4.0). too_large refuses a request body or a file over
the server's size limit."""


class ErrorDetail(ContractModel):
    code: ErrorCode
    message: str
    missing: list[MissingSetting] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    """Present only for not_configured; other errors keep their existing wire shape."""

    @model_validator(mode="after")
    def setup_details(self) -> Self:
        if (self.code == "not_configured") != (self.missing is not None):
            raise ValueError("missing is set exactly for not_configured")
        return self


class ErrorResponse(ContractModel):
    """Every error, from every command."""

    error: ErrorDetail


class SetMergeMode(ContractModel):
    """Select a merge mode using the last observed server revision."""

    mode: MergeMode
    revision: NonNegativeInt


class CreatePlan(ContractModel):
    """Request a proposed split without starting coding agents."""

    task: NonEmptyText


class PlanCreated(ContractModel):
    """The created plan identifier; generation continues through events."""

    plan_id: PlanId


class UpdatePlan(ContractModel):
    """Replace both proposed subtasks using the expected plan revision."""

    revision: PositiveInt
    graph_version: NonEmptyText
    subtasks: tuple[SubtaskDraft, SubtaskDraft]


class PlanRevision(ContractModel):
    """The expected revision for a plan command."""

    revision: PositiveInt


class RejectPlan(PlanRevision):
    """Refuse a proposed plan without starting its agents."""

    comment: str | None = None


class EnqueueTask(QueueSpec):
    """Queue work without starting it immediately."""


class QueueRevision(ContractModel):
    """The queue item revision observed by the person."""

    revision: PositiveInt
    """The queue revision observed by the caller."""


class StartLead(ContractModel):
    """Request a coordination lead."""

    task: NonEmptyText
    """The lead's coordination task."""
    model: NonEmptyText | None = None
    """The offered Meta profile, or default."""


class LeadCreated(ContractModel):
    """The created coordination lead."""

    agent_id: AgentId
    """The created lead identifier."""


class StopLead(ContractModel):
    """Stop the observed lead state."""

    revision: PositiveInt
    """The lead-state revision observed by the person."""


class ReviewChange(ContractModel):
    """Request advisory review of an exact candidate."""

    revision: PositiveInt
    """The revision to review."""
    head_commit: NonEmptyText
    """The proposed head to review."""
    main_commit: NonEmptyText
    """Main used to prepare this review."""


class RevertChange(ContractModel):
    """Request an inverse of a rules merge."""

    resolution_seq: PositiveInt
    """The original rules resolution."""
    merge_commit: NonEmptyText
    """The rules merge to revert."""
    main_commit: NonEmptyText
    """The current main observed by the caller."""


class ChangeView(ContractModel):
    """An archived view of one change."""

    change_id: ChangeId
    """The requested change."""
    seq: NonNegativeInt
    """The event position read with this view."""
    proposal: ChangeProposed | None
    """The latest proposal and its diff statistics."""
    files: list[FileDiff]
    """Archived full diff contents."""
    facts: ChangeFacts | None
    """Facts matching the current target."""
    review: CrossReview | None
    """The latest review matching the current candidate."""
    assessment: ChangeAssessment | None
    """The current candidate assessment."""
    resolution: ChangeResolved | None
    """The latest change resolution."""
    usage: ChangeUsage | None = None
    """Per-change accounting reconstructed through this view's seq, or unavailable."""
    revision_rounds: NonNegativeInt | None = None
    """Confirmed changes_requested resolutions through seq; null for incomplete history."""


class DictateTask(ContractModel):
    """Reserved JSON audio upload; feeds a task draft, never a decision command."""

    audio_base64: NonEmptyText | None = None
    """Base64-encoded audio; required by the future implemented endpoint."""
    media_type: NonEmptyText | None = None
    """The audio MIME type; required by the future implemented endpoint."""
    language: NonEmptyText | None = None
    """An optional transcription language hint."""


class TaskDictated(ContractModel):
    """Reserved future 200 response; never emitted while the endpoint answers 501."""

    text: NonEmptyText | None = None
    """The transcribed task draft; required for a successful future response."""


class HistoryPage(ContractModel):
    """One bounded page of historical event envelopes."""

    run_id: RunId
    """The current run only."""
    through_seq: NonNegativeInt
    """The fixed pagination upper bound."""
    events: list[ServerEvent] = Field(max_length=100)
    """The allowed historical event envelopes."""
    next_seq: PositiveInt | None
    """The next after_seq cursor, or null."""

    @model_validator(mode="after")
    def _history_envelopes(self) -> Self:
        allowed = {
            "change.proposed",
            "change.assessed",
            "change.reviewed",
            "change.facts_recorded",
            "tests.executed",
            "change.resolved",
            "revert.resolved",
            "merge_mode.changed",
            "plan.updated",
            "queue.updated",
            "lead.status",
        }
        if any(event.type not in allowed or event.seq > self.through_seq for event in self.events):
            raise ValueError("history contains a disallowed envelope or exceeds its horizon")
        seqs = [event.seq for event in self.events]
        if seqs != sorted(set(seqs)):
            raise ValueError("history envelopes must be chronological and unique")
        return self


class TreeFile(ContractModel):
    """One regular file in the selected runtime tree."""

    path: WorkspacePath
    """Workspace-relative path with forward slashes."""
    hash: str | None
    """Lowercase hex SHA-256 of exact file bytes, or null when hashing was not completed."""

    @model_validator(mode="after")
    def _hash_shape(self) -> Self:
        if self.hash is not None and (
            len(self.hash) != 64 or any(char not in "0123456789abcdef" for char in self.hash)
        ):
            raise ValueError("file hash must be lowercase hex SHA-256")
        return self


class TreeResponse(ContractModel):
    """200 from `GET /tree?agent=`: a plain, bounded runtime listing."""

    run_id: RunId
    """The current run."""
    agent_id: AgentId | None
    """Whose worktree was listed; null for main."""
    directories: list[WorkspacePath]
    """Sorted unique directory paths; excludes the root and trailing slashes."""
    files: list[TreeFile]
    """Regular files, sorted by unique workspace-relative path."""
    graph_version: str | None
    """Matching published main graph version; null for agents or unverified consistency."""
    truncated: bool
    """True exactly when eligible entries were omitted by the 2,000-entry cap."""

    @model_validator(mode="after")
    def _entry_limit(self) -> Self:
        count = len(self.directories) + len(self.files)
        if count > 2000:
            raise ValueError("tree listing exceeds 2,000 entries")
        if self.truncated and count != 2000:
            raise ValueError("a truncated tree listing must contain exactly 2,000 entries")
        return self

    @model_validator(mode="after")
    def _ordered_unique(self) -> Self:
        paths = [file.path for file in self.files]
        if self.directories != sorted(set(self.directories)) or paths != sorted(set(paths)):
            raise ValueError("tree paths must be sorted and unique")
        if set(self.directories).intersection(paths):
            raise ValueError("a path cannot be both a directory and a file")
        return self
