"""The only typed path from a person's example selection to server-owned pins.

E2 owns the route, the evidence status and resumption; this module never starts a run.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType

from oversight.agents import (
    ExamplesInvalid,
    ExampleTarget,
    canonical_json,
    validate_person_examples,
)
from oversight.changes import (
    ChangeManager,
    ConfirmOutcome,
    ExamplesConfirmation,
    WrongState,
)
from oversight_contracts.commands import ConfirmExamples
from oversight_contracts.common import AgentId, ChangeId, RunId
from oversight_contracts.examples import ExamplePin, TaskExample


class InvalidExamples(ValueError):
    """A precise validation reason; the runtime maps it to invalid_request."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


@dataclass(frozen=True)
class PendingExamples:
    run_id: RunId
    agent_id: AgentId
    change_id: ChangeId
    base_commit: str
    evidence_seq: int
    source_task: str
    offered: tuple[TaskExample, ...]
    meaning_by_example: Mapping[str, str]
    targets: tuple[ExampleTarget, ...]
    steps: int

    def __post_init__(self) -> None:
        if any(example.pin is not None for example in self.offered):
            raise ValueError("Pending examples must be unpinned")
        if self.evidence_seq < 1 or self.steps < 0:
            raise ValueError("Pending evidence seq must be positive and steps nonnegative")
        object.__setattr__(self, "offered", tuple(e.model_copy(deep=True) for e in self.offered))
        object.__setattr__(
            self, "meaning_by_example", MappingProxyType(dict(self.meaning_by_example))
        )
        object.__setattr__(self, "targets", tuple(self.targets))


def _selected(pending: PendingExamples, examples: list[TaskExample]) -> list[TaskExample]:
    offered = {example.example_id: example for example in pending.offered}
    meanings: set[str] = set()
    for example in examples:
        if offered.get(example.example_id) != example:
            raise InvalidExamples("not_offered", "Confirm requires unchanged offered examples")
        meaning = pending.meaning_by_example.get(example.example_id)
        if meaning is None:
            raise InvalidExamples("unknown_meaning", "The selected example has no offered meaning")
        meanings.add(meaning)
    if len(meanings) != 1:
        raise InvalidExamples("mixed_meanings", "Select examples of one meaning")
    return [example.model_copy(deep=True) for example in examples]


def check_confirmation(
    pending: PendingExamples, command: ConfirmExamples, agent_id: AgentId
) -> ExamplesConfirmation:
    """Pure ordered guards; prose, model replies and inbox links confer no authority."""
    if (agent_id, command.run_id, command.evidence_seq, command.base_commit) != (
        pending.agent_id,
        pending.run_id,
        pending.evidence_seq,
        pending.base_commit,
    ):
        raise WrongState("The command does not identify the examples the person saw")
    if any(example.pin is not None for example in command.examples):
        raise InvalidExamples("client_pin", "Only the server builds confirmation pins")
    if not 1 <= len(command.examples) <= 5:
        raise InvalidExamples("case_count", "Expected one to five examples")
    ids = [example.example_id for example in command.examples]
    if len(set(ids)) != len(ids):
        raise InvalidExamples("duplicate_example_id", "Example ids must be unique")
    if command.action == "confirm":
        normalized = _selected(pending, command.examples)
    elif command.action == "edit":
        try:
            normalized = validate_person_examples(command.examples, pending.targets)
        except ExamplesInvalid as error:
            raise InvalidExamples(error.code, str(error)) from error
    else:
        raise InvalidExamples("invalid_action", "Expected confirm or edit")
    digest = sha256(
        canonical_json(
            {"action": command.action, "examples": [e.model_dump(mode="json") for e in normalized]}
        ).encode("ascii")
    ).hexdigest()
    pin = ExamplePin(source_task=pending.source_task, confirmed_by="person")
    return ExamplesConfirmation(
        pending.base_commit,
        tuple(
            e.model_copy(update={"pin": pin.model_copy(deep=True)}, deep=True) for e in normalized
        ),
        pending.steps,
        digest,
    )


async def confirm_examples(
    manager: ChangeManager, agent_id: AgentId, pending: PendingExamples, command: ConfirmExamples
) -> ConfirmOutcome:
    confirmation = check_confirmation(pending, command, agent_id)
    return await manager.confirm_examples(agent_id, pending.change_id, confirmation)
