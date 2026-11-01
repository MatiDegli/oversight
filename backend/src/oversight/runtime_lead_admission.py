"""Claim an idle lead synchronously, then own publication independently of the HTTP caller."""

# These helpers compose run-owned services; they do not grant product-agent authority.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from oversight.api import ApiError
from oversight_contracts.events import AgentMessageSent

if TYPE_CHECKING:
    from oversight.runtime_leads import LeadService, _Lead


def _open(service: LeadService, lead: _Lead) -> bool:
    return not (service._closing or service.live._closing or lead.stopping)


def _eligible(service: LeadService, lead: _Lead) -> None:
    if not _open(service, lead) or service.live._manager is None or lead.phase != "idle":
        raise ApiError("wrong_state", "the lead is not accepting messages")


async def message(service: LeadService, agent_id: str, text: str) -> None:
    lead = service.leads.get(agent_id)
    if lead is None:
        raise ApiError("not_found", f"there is no lead {agent_id!r}")
    _eligible(service, lead)
    async with lead.lock:
        _eligible(service, lead)
        if lead.profile.provider == "meta" and (reason := service.live.brake_reason()):
            raise ApiError("spend_brake", reason)
        masked = service.live._redactor(text)
        if not masked.strip() or len(masked) > 4096 or len(text) > 4096:
            raise ApiError(
                "invalid_request", "message must be nonblank and at most 4,096 characters"
            )
        # No publication await may precede this claim. Competing HTTP sends see working.
        lead.phase = "working"
        lead.admitting = asyncio.create_task(_publish(service, lead, masked))
    await service.live._finish(lead.admitting)


async def _publish(service: LeadService, lead: _Lead, masked: str) -> None:
    try:
        committed = await service.live.store.publish(
            AgentMessageSent(agent_id=lead.status.agent_id, text=masked)
        )
        async with lead.lock:
            # Stop and shutdown own terminalization. A committed message is never removed.
            if not _open(service, lead):
                return
            await service._status(lead, "working", None)
            if not _open(service, lead):
                return
            service._launch(lead, masked, committed.seq)
    except Exception:
        async with lead.lock:
            if _open(service, lead):
                lead.phase = "idle"
        raise ApiError("internal", "could not record the lead message") from None
