"""Run-local lead ownership, admission and committed lifecycle publication."""

# Composition accesses LiveRun's owned settings, registries and durable completion helper.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from oversight.agents import (
    BRAKE_DETAIL,
    LEDGER_DETAIL,
    MUSE_SPARK,
    MUSE_SPARK_OPEN,
    STARTING_DETAIL,
    LeadProposalError,
    LeadRun,
    LeadTools,
    LedgerError,
    ModelClient,
    close_model,
)
from oversight.api import ApiError
from oversight.changes import plan_core, read_queue
from oversight.runtime_accounting import PlanAccounting
from oversight.setup import missing_settings
from oversight_contracts.commands import LeadCreated as LeadCreated
from oversight_contracts.commands import StartLead as StartLead
from oversight_contracts.commands import StopLead as StopLead
from oversight_contracts.events import LeadStatus, ModelProfile
from oversight_contracts.graph import GraphSnapshot
from oversight_contracts.plans import SubtaskDraft

if TYPE_CHECKING:
    from oversight.app import LiveRun


@dataclass
class _Lead:
    status: LeadStatus
    revision: int = 0
    stopping: bool = False
    entered: bool = False
    initializing: asyncio.Task[None] | None = None
    task: asyncio.Task[None] | None = None


class LeadService:
    def __init__(self, live: LiveRun) -> None:
        self.live = live
        self._closing = False
        self.leads: dict[str, _Lead] = {}

    async def start(self, request: StartLead) -> LeadCreated:
        live = self.live
        if self._closing or live._closing or live._manager is None:
            raise ApiError("wrong_state", "the run is not accepting lead commands")
        if not request.task.strip():
            raise ApiError("invalid_request", "the task is empty")
        profile = self._profile(request.model)
        if missing := missing_settings(replace(live._settings, cross_review=False), {"A": profile}):
            raise ApiError("not_configured", "Server setup is incomplete.", missing=missing)
        if profile.provider == "meta" and (reason := live.brake_reason()) is not None:
            raise ApiError("spend_brake", reason)
        if any(lead.status.state in {"starting", "working"} for lead in self.leads.values()):
            raise ApiError("wrong_state", "a lead is already active")
        live._count += 1
        agent_id = f"agent-{live._count}"
        lead = _Lead(
            LeadStatus(
                agent_id=agent_id,
                revision=1,
                task=request.task,
                model=profile.id,
                state="starting",
                step=0,
                detail=STARTING_DETAIL,
            )
        )
        self.leads[agent_id] = lead
        # Registration is synchronous. Retain initialization so shutdown cannot miss a start
        # whose first durable status is still being committed.
        lead.initializing = asyncio.create_task(self._begin(lead, profile))
        await live._finish(lead.initializing)
        return LeadCreated(agent_id=agent_id)

    def _profile(self, model: str | None) -> ModelProfile:
        default = self.live._slot_profiles["A"]
        default = default if default.provider == "fake" else MUSE_SPARK
        profiles = {p.id: p for p in (MUSE_SPARK, MUSE_SPARK_OPEN, default)}
        if model is not None and model not in profiles:
            raise ApiError("invalid_request", "the lead runs on muse-spark or muse-spark-open")
        return profiles[model] if model is not None else default

    async def _begin(self, lead: _Lead, profile: ModelProfile) -> None:
        await self._status(lead, "starting", STARTING_DETAIL)
        lead.task = asyncio.create_task(self._drive(lead, profile))

    async def _status(
        self,
        lead: _Lead,
        state: str,
        detail: str | None,
        *,
        step: int | None = None,
        plan_id: str | None = None,
    ) -> None:
        status = LeadStatus.model_validate(
            {
                **lead.status.model_dump(),
                "revision": lead.revision + 1,
                "state": state,
                "step": lead.status.step if step is None else step,
                "detail": detail,
                "requested_plan_id": plan_id,
            }
        )
        await self.live.store.publish(status)
        lead.status = status
        lead.revision = status.revision

    def _graph(self) -> GraphSnapshot:
        snapshot = self.live.store.graph()[1]
        assert snapshot is not None
        return snapshot

    def _tools(self, lead: _Lead) -> LeadTools:
        async def propose(task: str, drafts: list[SubtaskDraft]) -> str:
            try:
                result = await self.live.plans.create_and_propose(
                    task,
                    drafts,
                    started_by=lead.status.agent_id,
                )
            except ApiError as error:
                # agents cannot import api: adapt only this server-owned error at the boundary.
                raise LeadProposalError(error.message) from None
            return result.plan_id

        return LeadTools(
            graph=self._graph,
            tree=lambda: self.live.get_tree(None),
            read=lambda path: self.live.get_file(path, None),
            queue=lambda: read_queue(self.live.store),
            zone=lambda: plan_core.whole_graph(self._graph()),
            propose=propose,
            slots=len(self.live._slots),
        )

    async def _drive(self, lead: _Lead, profile: ModelProfile) -> None:
        lead.entered = True
        live, client = self.live, None
        accounting = PlanAccounting(live, None, profile, lead.status.agent_id, role="lead")

        def make_client() -> ModelClient:
            nonlocal client
            client = live._client_factory(profile, "A")
            return client

        def admit() -> str | None:
            return BRAKE_DETAIL if profile.provider == "meta" and live.brake_reason() else None

        async def working() -> None:
            await live._finish(self._status(lead, "working", None, step=0))

        async def step(plan_id: str | None) -> None:
            await live._finish(
                self._status(
                    lead,
                    "working",
                    f"Requested {plan_id}." if plan_id else None,
                    step=lead.status.step + 1,
                    plan_id=plan_id,
                )
            )

        run = LeadRun(
            agent_id=lead.status.agent_id,
            task=lead.status.task,
            make_client=make_client,
            tools=self._tools(lead),
            record=accounting.record,
            publish=live.store.publish,
            admit=admit,
            on_working=working,
            on_step=step,
        )
        try:
            try:
                if self._closing or live._closing or lead.stopping:
                    raise asyncio.CancelledError
                end = await run.run()
                state, detail = end.state, end.detail
            except asyncio.CancelledError:
                state = "stopped" if lead.stopping else "halted"
                detail = run.detail(
                    "Stopped by the person." if lead.stopping else "The server shut down."
                )
            except LedgerError:
                await accounting.report_failure()
                state, detail = "halted", run.detail(LEDGER_DETAIL)
            await live._finish(self._status(lead, state, detail, step=run.steps))
        finally:
            if client is not None:
                with contextlib.suppress(Exception):
                    await live._finish(close_model(client))

    def stop(self, agent_id: str, request: StopLead) -> None:
        lead = self.leads.get(agent_id)
        if lead is None:
            raise ApiError("not_found", f"there is no lead {agent_id!r}")
        if lead.status.state not in {"starting", "working"} or lead.stopping:
            raise ApiError("wrong_state", "the lead is terminal or already stopping")
        if request.revision != lead.revision:
            raise ApiError(
                "wrong_state",
                f"stale lead revision {request.revision}; the latest is {lead.revision}",
            )
        lead.stopping = True
        assert lead.task is not None
        if lead.entered:
            lead.task.cancel()

    async def close(self) -> None:
        self._closing = True
        initial = [
            lead.initializing for lead in self.leads.values() if lead.initializing is not None
        ]
        await asyncio.gather(*initial, return_exceptions=True)
        tasks = [lead.task for lead in self.leads.values() if lead.task is not None]
        for lead in self.leads.values():
            if lead.task is not None and lead.entered and not lead.task.done():
                lead.task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
