"""F2 lifecycle tests use a fast invalid planner reply and retain their author scripts."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from live_helpers import LiveServer, Scripts, live_server

from oversight.agents import ModelClient, ScriptedModel, parse_script
from oversight.agents.client import CALL_SCOPE
from oversight.app import ClientFactory
from oversight.settings import Settings
from oversight_contracts.common import Slot
from oversight_contracts.events import ModelProfile


@asynccontextmanager
async def plan_server(
    settings: Settings, client_factory: ClientFactory | None = None
) -> AsyncIterator[LiveServer]:
    author_factory = client_factory or Scripts()

    def factory(profile: ModelProfile, slot: Slot) -> ModelClient:
        scope = CALL_SCOPE.get()
        if scope is not None and scope.role == "plan":
            return ScriptedModel(
                parse_script('{"task":"plan","turns":[{"text":"invalid plan reply"}]}', profile),
                profile,
            )
        return author_factory(profile, slot)

    async with live_server(settings, factory) as server:
        yield server
