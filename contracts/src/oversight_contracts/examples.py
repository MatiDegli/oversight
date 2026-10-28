"""Task and node examples with explicit human confirmation and source provenance."""

from typing import Literal, Self

from pydantic import model_validator

from oversight_contracts.common import ContractModel, NonEmptyText
from oversight_contracts.policy import EvidenceRef


class ExamplePin(ContractModel):
    """Human confirmation of the containing task example."""

    source_task: NonEmptyText
    """The original task text, retained because there is no shared TaskId."""
    confirmed_by: Literal["person"] = "person"
    """The existing single-user human principal; the server verifies confirmation."""


class TaskExample(ContractModel):
    """An example supplied with a task before editing begins."""

    example_id: NonEmptyText
    """A stable identifier within the originating task."""
    inputs: NonEmptyText
    """Rendered input, never executable code."""
    expected_output: str | None = None
    """Expected rendered output; the empty string is a valid expectation."""
    expected_behavior: NonEmptyText | None = None
    """Expected behavior when output alone does not describe the example."""
    note: str | None = None
    """An optional free-text explanation."""
    pin: ExamplePin | None = None
    """Human confirmation and originating task; null means unconfirmed."""

    @model_validator(mode="after")
    def _has_expectation(self) -> Self:
        if self.expected_output is None and self.expected_behavior is None:
            raise ValueError("an example requires expected output or behavior")
        return self


class NodeExample(TaskExample):
    """A captured node-card example with explicit provenance."""

    source: Literal["test_literal", "recorded_run", "pinned_example", "behavior_diff"]
    """How the example was obtained; a test literal is not execution evidence."""
    source_id: NonEmptyText
    """Pytest/call-site id, recorded invocation id, task/example id, or node/row index."""
    evidence: EvidenceRef
    """The source event or Git object, narrowed to a path where applicable."""
