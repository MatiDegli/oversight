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
from oversight_contracts.graph import GraphSnapshot
from oversight_contracts.plans import SubtaskDraft


class StartAgent(ContractModel):
    """`POST /agents`."""

    task: NonEmptyText
    slot: Slot | None = None
    """The free slot when omitted."""
    model: str | None = None
    """A ModelProfile id; the slot's default model when omitted."""


class AgentCreated(ContractModel):
    """201 from `POST /agents`."""

    agent_id: AgentId
    change_id: ChangeId
    slot: Slot


class AgentMessage(ContractModel):
    """`POST /agents/{id}/message`, level 3: answers 501 until then."""

    text: NonEmptyText


class ApproveChange(ContractModel):
    """`POST /changes/{id}/approve`. It takes no hash: in review the agent is idle."""

    comment: str | None = None


class RequestChanges(ContractModel):
    """`POST /changes/{id}/request-changes`: the comment goes back to the agent."""

    comment: NonEmptyText


class RejectChange(ContractModel):
    """`POST /changes/{id}/reject`."""

    comment: str | None = None


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
}
"""The HTTP status of each error code. FastAPI's own validation error becomes invalid_request; an
unexpected server-side failure becomes internal (contract 1.1)."""


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
