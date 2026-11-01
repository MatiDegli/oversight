"""One lead turn's client, accounting and serialized finalization."""

# Composition owns the service and LiveRun's private operations.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from oversight.agents import (
    BRAKE_DETAIL,
    LEDGER_DETAIL,
    LeadEnd,
    LeadRun,
    LedgerError,
    ModelClient,
    close_model,
)
from oversight.runtime_accounting import PlanAccounting

if TYPE_CHECKING:
    from oversight.runtime_leads import LeadService, _Lead


class LeadTurn:
    def __init__(self, service: LeadService, lead: _Lead) -> None:
        self.service, self.lead, self.live = service, lead, service.live
        self.client: ModelClient | None = None
        self.accounting = PlanAccounting(
            self.live, None, lead.profile, lead.status.agent_id, role="lead"
        )
        self.run = lead.run or LeadRun(
            agent_id=lead.status.agent_id,
            task=lead.status.task,
            make_client=self.make_client,
            tools=service._tools(lead),
            record=self.accounting.record,
            publish=self.live.store.publish,
            admit=self.admit,
            on_working=self.working,
            on_step=self.step,
            mask=self.live._redactor,
        )
        lead.run = self.run
        self.run.make_client, self.run.record = self.make_client, self.accounting.record
        self.run.on_working, self.run.on_step = self.working, self.step

    def check(self) -> None:
        if self.service._closing or self.live._closing or self.lead.stopping:
            raise asyncio.CancelledError

    def make_client(self) -> ModelClient:
        self.check()
        self.client = self.live._client_factory(self.lead.profile, "A")
        return self.client

    def admit(self) -> str | None:
        self.check()
        return (
            BRAKE_DETAIL
            if self.lead.profile.provider == "meta" and self.live.brake_reason()
            else None
        )

    async def working(self) -> None:
        self.check()
        if self.lead.status.state != "working":
            await self.live._finish(self.service._status(self.lead, "working", None))
        self.check()

    async def step(self, plan_id: str | None) -> None:
        await self.live._finish(
            self.service._status(
                self.lead,
                "working",
                f"Requested {plan_id}." if plan_id else None,
                step=self.run.steps,
                plan_id=plan_id,
            )
        )

    async def drive(self, human: str | None, answers_seq: int | None) -> None:
        lead, service, live = self.lead, self.service, self.live
        lead.entered = True
        lead.turns += 1
        end = None
        try:
            try:
                self.check()
                end = await self.run.run(human, answers_seq)
                state, detail = end.state, end.detail
            except asyncio.CancelledError:
                state, detail = service._ending(lead)
            except LedgerError:
                state, detail = "halted", self.run.detail(LEDGER_DETAIL)
            if self.accounting.failure is not None:
                await self.accounting.report_failure()
            await self.finalize(end, state, detail)
        except Exception as error:
            await service._halt_unexpected(lead, error, self.run)
        finally:
            if self.client is not None:
                with contextlib.suppress(Exception):
                    await live._finish(close_model(self.client))

    async def finalize(self, end: LeadEnd | None, state: str, detail: str) -> None:
        lead, service, live = self.lead, self.service, self.live
        async with lead.lock:
            if lead.stopping or service._closing or live._closing:
                state, detail = service._ending(lead)
            elif end is not None and end.reply is not None:
                await live._finish(live.store.publish(end.reply))
                if lead.turns >= 50:
                    state, detail = "halted", "Reached the limit of 50 turns."
            await live._finish(service._status(lead, state, detail, step=self.run.steps))
