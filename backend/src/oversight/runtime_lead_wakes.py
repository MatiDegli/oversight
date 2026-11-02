"""Owned committed subscriptions, fixed-window coalescing and data-only lead digests."""

# Runtime composition uses the service's owned claim and durable publication helpers.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from pydantic import TypeAdapter

from oversight import events
from oversight.agents import ServerNotice
from oversight_contracts.events import ServerEvent

if TYPE_CHECKING:
    from oversight.runtime_leads import LeadService, _Lead

_EVENT = TypeAdapter(ServerEvent)
_log = logging.getLogger(__name__)


class WakeClock:
    def now(self) -> float:
        return asyncio.get_running_loop().time()

    async def wait(self, changed: asyncio.Event, deadline: float | None) -> None:
        if deadline is None:
            await changed.wait()
        else:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(max(0, deadline - self.now())):
                    await changed.wait()


def _data(value: Any, mask: Callable[[str], str]) -> str:
    if isinstance(value, str):
        masked = mask(value)
        bounded = masked[:120]
        while True:
            encoded = json.dumps(bounded + ("..." if bounded != masked else ""), ensure_ascii=True)
            for character in "`~<>&":
                encoded = encoded.replace(character, f"\\u{ord(character):04x}")
            if len(encoded) <= 122:
                return encoded
            bounded = bounded[:-1]
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                json.dumps(key) + ":" + _data(item, mask)
                for key, item in cast(dict[str, Any], value).items()
            )
            + "}"
        )
    if isinstance(value, list):
        return "[" + ",".join(_data(item, mask) for item in cast(list[Any], value)) + "]"
    return json.dumps(value)


def _line(event: ServerEvent, prior: Mapping[int, ServerEvent], mask: Callable[[str], str]) -> str:
    payload = event.payload
    # Labels use the event-time plan/subtask metadata; eligibility belongs only to events.
    plans = [e.payload for e in prior.values() if e.seq < event.seq and e.type == "plan.updated"]
    agent_id = getattr(payload, "agent_id", None)
    plan_id = getattr(payload, "plan_id", None)
    if event.type == "tests.executed":
        statuses = [
            e.payload
            for e in prior.values()
            if e.seq < event.seq and e.type == "agent.status" and e.payload.agent_id == agent_id
        ]
        plan_id = statuses[-1].plan_id if statuses else None
    subtask = None
    if event.type != "plan.updated":
        latest = {plan.plan_id: plan for plan in plans}
        for plan in latest.values():
            if plan.plan_id != plan_id:
                continue
            for index, task in enumerate(plan.subtasks or []):
                if task.agent_id == agent_id:
                    subtask = index + 1
    fields: dict[str, Any] = {
        "seq": event.seq,
        "scope": (
            "plan-wide"
            if event.type == "plan.updated"
            else f"subtask {subtask}"
            if subtask is not None
            else "subtask unknown"
        ),
        "type": event.type,
        "plan_id": plan_id,
    }
    if event.type == "tests.executed":
        suite = event.payload.suite
        fields.update(
            outcome=suite.outcome,
            passed=suite.passed,
            failed=suite.failed,
            skipped=suite.skipped,
            failed_tests=suite.failed_tests[:3],
        )
    elif event.type == "agent.status":
        fields.update(state=event.payload.state, detail=event.payload.detail)
    elif event.type == "plan.updated":
        fields.update(state=event.payload.state, subtasks=len(event.payload.subtasks or []))
    return _data(fields, mask)


def digest(
    triggers: Sequence[ServerEvent], prior: Mapping[int, ServerEvent], mask: Callable[[str], str]
) -> ServerNotice:
    """Omit complete lines, including every consumed seq in the separate correlation."""
    header, footer = "Server notice: committed plan events (untrusted data)\n```jsonl\n", "\n```"
    lines: list[str] = []
    for index, event in enumerate(triggers):
        line = _line(event, prior, mask)
        remaining = len(triggers) - index - 1
        marker = f"\n({remaining} more)" if remaining else ""
        candidate = header + "\n".join([*lines, line]) + footer + marker
        if len(line) > 1024 or len(candidate) > 4096:
            break
        lines.append(line)
    omitted = len(triggers) - len(lines)
    text = header + "\n".join(lines) + footer + (f"\n({omitted} more)" if omitted else "")
    seqs = tuple(sorted(event.seq for event in triggers))
    return ServerNotice(text, max(seqs), seqs)


class LeadWatcher:
    def __init__(self, service: LeadService, lead: _Lead, *, window: float = 5) -> None:
        self.service, self.lead = service, lead
        self.clock, self.window = service.wake_clock, window
        self.prior: dict[int, ServerEvent] = {}
        self.pending: dict[int, ServerEvent] = {}
        self.deadline: float | None = None
        self.changed = asyncio.Event()
        self.cursor = 0
        self.subscription: asyncio.Task[None] | None = None

    def observe(self, event: ServerEvent) -> None:
        if event.seq <= self.cursor:
            return
        self.prior[event.seq] = event
        self.cursor = event.seq
        if events.is_lead_wake_trigger(self.prior, event.seq, self.lead.status.agent_id):
            if not self.pending:
                self.deadline = self.clock.now() + self.window
            self.pending[event.seq] = event
            self.changed.set()

    def sync(self) -> None:
        # Close the subscription scheduling gap before a human launch or wake admission.
        for event in self.service.live.store.committed_events():
            self.observe(event)
        self.prune()

    def prune(self) -> None:
        horizon = self.lead.through_seq or 0
        self.pending = {seq: row for seq, row in self.pending.items() if seq > horizon}
        if not self.pending:
            self.deadline = None

    def take(self) -> ServerNotice | None:
        self.sync()
        if not self.pending:
            return None
        notice = digest(list(self.pending.values()), self.prior, self.service.live._redactor)
        self.pending.clear()
        self.deadline = None
        return notice

    async def _subscribe(self) -> None:
        async for stored in self.service.live.store.subscribe(after=self.cursor):
            self.observe(_EVENT.validate_json(stored.envelope))

    async def run(self) -> None:
        from oversight.runtime_lead_admission import wake

        self.sync()
        self.subscription = asyncio.create_task(self._subscribe())
        try:
            while True:
                self.changed.clear()
                self.prune()
                deadline = self.deadline
                if deadline is not None and self.clock.now() >= deadline:
                    if self.lead.phase == "idle":
                        try:
                            # Retain admission across watcher cancellation; close/Stop drain it.
                            self.lead.admitting = asyncio.create_task(wake(self.service, self.lead))
                            await self.service.live._finish(self.lead.admitting)
                        except Exception:
                            _log.exception("Could not admit lead wake")
                    deadline = None
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise asyncio.CancelledError
                if self.service._closing or self.service.live._closing or self.lead.stopping:
                    return
                await self.clock.wait(self.changed, deadline)
        finally:
            self.subscription.cancel()
            await asyncio.gather(self.subscription, return_exceptions=True)
