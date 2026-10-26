"""Request and response bodies, and the single error format (docs/proposal.md, section 4.7).

A 202 means only that a command was accepted; its results arrive as events. Request bodies may
omit their optional keys; responses always carry every key.
"""

from typing import Annotated, Literal

from pydantic import StringConstraints

from oversight_contracts.common import (
    AgentId,
    ChangeId,
    ContractModel,
    RunId,
    Slot,
    WorkspacePath,
)
from oversight_contracts.graph import GraphSnapshot

type NonEmptyText = Annotated[str, StringConstraints(min_length=1)]


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


class Health(ContractModel):
    """200 from `GET /health`."""

    ok: bool
    run_id: RunId
    contract_version: str
    mode: Literal["live", "replay"]


type ErrorCode = Literal[
    "not_found",
    "wrong_state",
    "slot_busy",
    "spend_brake",
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


class ErrorResponse(ContractModel):
    """Every error, from every command."""

    error: ErrorDetail
