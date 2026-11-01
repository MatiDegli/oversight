"""Human-confirmed examples: admission and one durable return to working."""

from dataclasses import dataclass, replace

from oversight.agents import AgentTask, task_examples, validate_agent_status
from oversight.changes._state import ChangeRecord, WrongState
from oversight.changes._support import ChangeContext
from oversight_contracts.events import AgentStatus
from oversight_contracts.examples import TaskExample


@dataclass(frozen=True)
class ExamplesConfirmation:
    """Server-validated criteria, detached from the submitted command."""

    base_commit: str
    examples: tuple[TaskExample, ...]
    steps: int
    digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "examples", tuple(e.model_copy(deep=True) for e in self.examples))


@dataclass(frozen=True)
class ConfirmOutcome:
    """Repeated means these examples were already confirmed, regardless of whether a run exists."""

    task: AgentTask
    repeated: bool


def check(rec: ChangeRecord, confirmation: ExamplesConfirmation) -> AgentTask:
    """All record guards and task construction precede the manager's synchronous reservation."""
    if (
        rec.state != "unproposed"
        or not rec.review_ready
        or rec.revision != 0
        or rec.head_commit is not None
        or rec.task.examples != ()
        or rec.base_commit != confirmation.base_commit
    ):
        raise WrongState("Examples need a completed, unproposed examples phase on the same base")
    if not confirmation.examples:
        raise ValueError("Confirmation requires pinned examples")
    return replace(rec.task, examples=confirmation.examples, prior_steps=confirmation.steps)


async def confirmed(
    ctx: ChangeContext, rec: ChangeRecord, confirmation: ExamplesConfirmation, task: AgentTask
) -> ConfirmOutcome:
    """The store receipt and its record update are indivisible through caller cancellation."""
    payload = AgentStatus(
        agent_id=task.agent_id,
        slot=task.slot,
        change_id=task.change_id,
        task=task.task,
        model=rec.model_id,
        base_commit=rec.base_commit,
        state="starting",
        detail=None,
        step=task.prior_steps,
        max_steps=task.prior_steps + task.limits.max_steps,
        examples=task_examples(task.examples),
        answering=task.answering,
    )
    validate_agent_status(payload, base_commit=rec.base_commit, answering=task.answering)

    async def publish_and_record() -> ConfirmOutcome:
        await ctx.publish(payload)
        rec.task = task
        rec.state = "working"
        rec.proposed_now = False
        rec.review_ready = False
        rec.examples_digest = confirmation.digest
        return ConfirmOutcome(task, repeated=False)

    return await ctx.whole(publish_and_record())


async def repeated(rec: ChangeRecord) -> ConfirmOutcome:
    return ConfirmOutcome(rec.task, repeated=True)
