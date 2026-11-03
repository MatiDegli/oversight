"""Reference-only view requests grounded in committed run state."""

from typing import Annotated, Literal, Self

from pydantic import Field, NonNegativeInt, PositiveInt, StringConstraints, model_validator

from oversight_contracts.common import AgentId, ChangeId, ContractModel

type ViewPath = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=512,
        pattern=r"^(?:\.?[A-Za-z0-9_-][A-Za-z0-9_.-]*/)*\.?[A-Za-z0-9_-][A-Za-z0-9_.-]*$",
    ),
]
"""Canonical relative ASCII path; producer also rejects .git and secret paths."""

type ViewCaption = Annotated[str, StringConstraints(min_length=1, max_length=160, pattern=r"\S")]
"""Optional masked speech, never evidence."""


class AgentRef(ContractModel):
    """One committed agent reference."""

    kind: Literal["agent"]
    """Reference discriminator."""
    agent_id: AgentId
    """Run-local agent identifier."""


class PathRef(ContractModel):
    """One repository path reference."""

    kind: Literal["path"]
    """Reference discriminator."""
    path: ViewPath
    """Canonical repository-relative path."""


class ChangeFileRef(ContractModel):
    """One exact recorded change/file pair."""

    change_id: ChangeId
    """Run-local change identifier."""
    path: ViewPath
    """Recorded file path within that change."""


type HighlightRef = Annotated[AgentRef | PathRef, Field(discriminator="kind")]
"""Tagged agent or path identity."""


class HighlightView(ContractModel):
    """References to highlight on a person's click."""

    kind: Literal["highlight"]
    """View discriminator."""
    references: Annotated[list[HighlightRef], Field(min_length=1, max_length=8)]
    """One to eight distinct references."""

    @model_validator(mode="after")
    def _distinct_references(self) -> Self:
        identities = [ref.model_dump_json() for ref in self.references]
        if len(set(identities)) != len(identities):
            raise ValueError("references must be distinct")
        return self


class FilesView(ContractModel):
    """Exact recorded files, including resolved changes."""

    kind: Literal["files"]
    """View discriminator."""
    references: Annotated[list[ChangeFileRef], Field(min_length=1, max_length=8)]
    """One to eight distinct change/file pairs."""

    @model_validator(mode="after")
    def _distinct_references(self) -> Self:
        identities = [ref.model_dump_json() for ref in self.references]
        if len(set(identities)) != len(identities):
            raise ValueError("references must be distinct")
        return self


class BehaviorView(ContractModel):
    """Measured behavior for the displayed candidate."""

    kind: Literal["behavior"]
    """View discriminator."""
    change_id: ChangeId
    """Change whose current revision/head supplies evidence."""


class MomentView(ContractModel):
    """One committed event; navigation is replay-only."""

    kind: Literal["moment"]
    """View discriminator."""
    event_seq: PositiveInt
    """Referenced committed sequence, no later than through_seq."""


type LeadViewKind = Annotated[
    HighlightView | FilesView | BehaviorView | MomentView, Field(discriminator="kind")
]
"""Closed union of grounded view requests."""


class LeadView(ContractModel):
    """A lead's reference-only view request."""

    agent_id: AgentId
    """Server-assigned requesting lead."""
    through_seq: NonNegativeInt
    """Successful status horizon, earlier than this event."""
    caption: ViewCaption | None = None
    """Masked optional speech; null when omitted or withheld."""
    view: LeadViewKind
    """Tagged references; contains no model-supplied facts."""
