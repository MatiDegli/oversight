"""Run-local lead ownership, admission and committed lifecycle publication."""

# Composition accesses LiveRun's owned settings, registries and durable completion helper.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from oversight.agents import (
    MUSE_SPARK,
    MUSE_SPARK_OPEN,
    UNVERIFIED_PROFILES,
    LeadPolicy,
    LeadProposalError,
    LeadRun,
    LeadTools,
    ServerNotice,
    build_starting_detail,
)
from oversight.api import ApiError
from oversight.changes import plan_core, read_queue
from oversight.runtime_lead_admission import message
from oversight.runtime_lead_turns import LeadTurn
from oversight.runtime_lead_wakes import LeadWatcher, WakeClock
from oversight.setup import missing_settings
from oversight_contracts.commands import LeadCreated as LeadCreated
from oversight_contracts.commands import StartLead as StartLead
from oversight_contracts.commands import StopLead as StopLead
from oversight_contracts.events import LeadLimits, LeadStatus, ModelProfile
from oversight_contracts.graph import GraphSnapshot
from oversight_contracts.plans import SubtaskDraft
from oversight_contracts.views import LeadView

if TYPE_CHECKING:
    from oversight.app import LiveRun


_log = logging.getLogger(__name__)
UNEXPECTED_DETAIL = "The lead failed unexpectedly."


@dataclass
class _Lead:
    status: LeadStatus
    profile: ModelProfile
    limits: LeadLimits
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    phase: str = "starting"
    turns: int = 0
    wakes: int = 0
    humans_waiting: int = 0
    through_seq: int | None = None
    refusals: set[str] = field(default_factory=set[str])
    watcher: LeadWatcher | None = None
    watching: asyncio.Task[None] | None = None
    run: LeadRun | None = None
    revision: int = 0
    stopping: bool = False
    entered: bool = False
    admitting: asyncio.Task[None] | None = None
    initializing: asyncio.Task[None] | None = None
    tasks: list[asyncio.Task[None]] = field(default_factory=list[asyncio.Task[None]])
    task: asyncio.Task[None] | None = None


class LeadService:
    def __init__(self, live: LiveRun) -> None:
        self.live = live
        self._closing = False
        self.leads: dict[str, _Lead] = {}
        self.policy = LeadPolicy(
            live._lead_limits, live._lead_history_chars, live._settings.lead_wakes
        )
        self.wake_clock = WakeClock()

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
        active = sum(lead.phase in {"starting", "working", "idle"} for lead in self.leads.values())
        if active >= live._max_leads:
            raise ApiError(
                "wrong_state",
                f"OVERSIGHT_MAX_LEADS is {live._max_leads} for this run. "
                "Stop a lead to free capacity, or change the setting for the next run.",
            )
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
                detail=build_starting_detail(live._lead_limits),
                limits=live._lead_limits,
            ),
            profile,
            live._lead_limits,
        )
        self.leads[agent_id] = lead
        # Registration is synchronous. Retain initialization so shutdown cannot miss a start
        # whose first durable status is still being committed.
        lead.initializing = asyncio.create_task(self._begin(lead, profile))
        await live._finish(lead.initializing)
        return LeadCreated(agent_id=agent_id)

    async def message(self, agent_id: str, text: str) -> None:
        await message(self, agent_id, text)

    def _profile(self, model: str | None) -> ModelProfile:
        default = self.live._slot_profiles["A"]
        default = default if default.provider == "fake" else MUSE_SPARK
        profiles = {p.id: p for p in (MUSE_SPARK, MUSE_SPARK_OPEN, default)}
        name = model or default.id
        if name in UNVERIFIED_PROFILES:
            raise ApiError("invalid_request", f"{name} cannot run agents yet: unverified")
        if model is not None and model not in profiles:
            raise ApiError("invalid_request", "the lead runs on muse-spark or muse-spark-open")
        return profiles[model] if model is not None else default

    async def _begin(self, lead: _Lead, profile: ModelProfile) -> None:
        if lead.stopping or self._closing or self.live._closing:
            return
        try:
            await self._status(lead, "starting", build_starting_detail(lead.limits))
        except Exception as error:
            await self._halt_unexpected(lead, error)
            return
        if lead.stopping or self._closing or self.live._closing:
            return
        lead.watcher = LeadWatcher(self, lead)
        lead.watching = asyncio.create_task(lead.watcher.run())
        self._launch(lead)

    def _launch(
        self,
        lead: _Lead,
        human: str | None = None,
        answers_seq: int | None = None,
        *,
        notice: ServerNotice | None = None,
        woken_by: list[int] | None = None,
    ) -> None:
        lead.entered = False
        lead.refusals.clear()
        lead.task = asyncio.create_task(
            self._drive(lead, lead.profile, human, answers_seq, notice, woken_by)
        )
        lead.tasks.append(lead.task)

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
                "limits": lead.limits,
            }
        )
        await self.live.store.publish(status)
        lead.status = status
        lead.revision = status.revision
        lead.phase = state
        if state not in {"starting", "working", "idle"}:
            if lead.watching is not None:
                lead.watching.cancel()
        elif state == "idle" and lead.watcher is not None:
            lead.watcher.changed.set()

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

        async def publish_view(view: LeadView) -> None:
            async with lead.lock:
                if lead.stopping or self._closing or self.live._closing:
                    raise LeadProposalError("The lead is no longer accepting views.")
                await self.live._finish(self.live.store.publish(view))

        return LeadTools(
            graph=self._graph,
            tree=lambda: self.live.get_tree(None),
            read=lambda path: self.live.get_file(path, None),
            queue=lambda: read_queue(self.live.store),
            zone=lambda: plan_core.whole_graph(self._graph()),
            propose=propose,
            slots=len(self.live._slots),
            mask=self.live._redactor,
            status=lambda: self.live.store.lead_status(include_views=True),
            status_without_views=self.live.store.lead_status,
            agent_id=lead.status.agent_id,
            resolve_views=self.live.store.view_resolver,
            publish_view=publish_view,
            limits=lead.limits,
        )

    async def _drive(
        self,
        lead: _Lead,
        profile: ModelProfile,
        human: str | None = None,
        answers_seq: int | None = None,
        notice: ServerNotice | None = None,
        woken_by: list[int] | None = None,
    ) -> None:
        await LeadTurn(self, lead).drive(human, answers_seq, notice, woken_by)

    async def _halt_unexpected(
        self,
        lead: _Lead,
        error: Exception,
        run: LeadRun | None = None,
    ) -> None:
        _log.error("Lead %s failed unexpectedly", lead.status.agent_id, exc_info=error)
        await self.live._finish(
            self.live._report(
                "server",
                f"Lead {lead.status.agent_id} failed ({type(error).__name__}); see the server log.",
                agent_id=lead.status.agent_id,
                fatal=True,
            )
        )
        detail = run.detail(UNEXPECTED_DETAIL) if run is not None else UNEXPECTED_DETAIL
        await self.live._finish(
            self._status(
                lead,
                "halted",
                detail,
                step=run.steps if run is not None else 0,
            )
        )

    def stop(self, agent_id: str, request: StopLead) -> None:
        lead = self.leads.get(agent_id)
        if lead is None:
            raise ApiError("not_found", f"there is no lead {agent_id!r}")
        if lead.phase not in {"starting", "working", "idle"} or lead.stopping:
            raise ApiError("wrong_state", "the lead is terminal or already stopping")
        if request.revision != lead.revision:
            raise ApiError(
                "wrong_state",
                f"stale lead revision {request.revision}; the latest is {lead.revision}",
            )
        lead.stopping = True
        if lead.watching is not None:
            lead.watching.cancel()
        previous = lead.task
        admitting = lead.admitting
        if (
            previous is not None
            and not previous.done()
            and lead.phase != "idle"
            and lead.entered
            and (admitting is None or admitting.done())
        ):
            previous.cancel()
        lead.entered = False
        lead.task = asyncio.create_task(self._stop_admitting(lead, admitting, previous))
        lead.tasks.append(lead.task)

    async def _stop_admitting(
        self,
        lead: _Lead,
        admitting: asyncio.Task[None] | None,
        previous: asyncio.Task[None] | None,
    ) -> None:
        await asyncio.gather(
            *(t for t in (lead.initializing, admitting, previous, lead.watching) if t),
            return_exceptions=True,
        )
        await self._finish_open(lead)

    def _ending(self, lead: _Lead) -> tuple[str, str]:
        return (
            ("stopped", "Stopped by the person.")
            if lead.stopping
            else ("halted", "The server shut down.")
        )

    async def _finish_open(self, lead: _Lead) -> None:
        async with lead.lock:
            if lead.phase in {"starting", "working", "idle"}:
                state, detail = self._ending(lead)
                await self.live._finish(self._status(lead, state, detail))

    async def close(self) -> None:
        self._closing = True
        watchers = [lead.watching for lead in self.leads.values() if lead.watching is not None]
        for watcher in watchers:
            watcher.cancel()
        await asyncio.gather(*watchers, return_exceptions=True)
        initial = [
            lead.initializing for lead in self.leads.values() if lead.initializing is not None
        ]
        await asyncio.gather(*initial, return_exceptions=True)
        admissions = [lead.admitting for lead in self.leads.values() if lead.admitting is not None]
        await asyncio.gather(*admissions, return_exceptions=True)
        tasks = {task for lead in self.leads.values() for task in lead.tasks}
        tasks |= set(watchers)
        tasks |= {lead.task for lead in self.leads.values() if lead.task is not None}
        for lead in self.leads.values():
            if lead.task is not None and lead.entered and not lead.task.done():
                lead.task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for lead in self.leads.values():
            await self._finish_open(lead)
