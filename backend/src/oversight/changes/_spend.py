"""Fresh spend truth, owner-scoped provenance and bounded accounting readiness."""

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING

from oversight.changes._policy import PolicyInputs, assess
from oversight.changes._support import RulesFailure
from oversight.events import StoredEvent
from oversight_contracts.common import RuleId
from oversight_contracts.policy import EvidenceRef, RuleAssessment

if TYPE_CHECKING:
    from oversight.changes._support import ChangeContext


class SpendNotReady(Exception):
    """Synchronous capture must retry after the next accounting notification."""


def admits(ctx: "ChangeContext", rule: RuleId, ref: EvidenceRef, pin: StoredEvent | None) -> bool:
    if rule == "known_spend":
        return (
            pin is not None
            and ref.event_seq == pin.seq
            and ref.git_object is None
            and ref.path is None
        )
    return ref.event_seq is None or ref.event_seq in ctx.assessment.events


def sample(
    ctx: "ChangeContext", inputs: PolicyInputs
) -> tuple[PolicyInputs, StoredEvent | None, str | None]:
    """Accept only spend from the producer; only this stage can attach provenance."""
    supplied = ctx.ports.rules_evidence(inputs) if ctx.ports.rules_evidence else None
    pin = ctx.assessment.spend_receipt
    total = ctx.assessment.spend_receipt_total
    spend = supplied.inputs.spend if supplied else None
    if spend is not None and (
        spend.spent_usd is None or (pin is not None and (total is None or total > spend.spent_usd))
    ):
        # A committed receipt is a lower bound, never a substitute for durable truth.
        spend = replace(spend, ledger_ok=False)
    return (
        replace(
            inputs,
            spend=spend,
            evidence={
                **inputs.evidence,
                "known_spend": (EvidenceRef(event_seq=pin.seq),) if pin else (),
            },
        ),
        pin,
        supplied.unavailable if supplied else None,
    )


def guard(ctx: "ChangeContext", inputs: PolicyInputs) -> tuple[RuleAssessment, StoredEvent | None]:
    sampled, pin, unavailable = sample(ctx, inputs)
    rule = next(r for r in assess(sampled).rules if r.rule == "known_spend")
    if unavailable or rule.result != "pass":
        raise RulesFailure("policy_invalid", "Current Meta spend is not confirmed below the limit.")
    return rule, pin


async def ready(ctx: "ChangeContext", *, deadline: float | None = None) -> None:
    """Wait only for an active write, never for publication or missing observations."""
    ledger = ctx.ports.spend_ledger
    if ledger is None:
        return
    deadline = deadline if deadline is not None else asyncio.get_running_loop().time() + ctx.timeout
    while True:
        revision = ledger.change_revision
        if ledger.state is not None or not ledger.writing or ctx.ports.spend_lost():
            return
        try:
            await ledger.wait_changed(revision, deadline=deadline)
        except TimeoutError as error:
            raise RulesFailure("policy_invalid", "Meta spend write readiness timed out.") from error
