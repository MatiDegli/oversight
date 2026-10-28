"""Close retained task clients at resolution, failed starts, and server shutdown."""

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from oversight.agents.client import ModelClient
from oversight_contracts.common import Slot
from oversight_contracts.events import ModelProfile


@runtime_checkable
class Closable(Protocol):
    async def aclose(self) -> None: ...


async def close_model(model: ModelClient) -> None:
    if isinstance(model, Closable):
        await model.aclose()


class TaskClients:
    """Also retain clients from starts that fail before an AgentRun can own them."""

    def __init__(self, factory: Callable[[ModelProfile, Slot], ModelClient]) -> None:
        self._factory = factory
        self._clients: list[Closable] = []

    def __call__(self, profile: ModelProfile, slot: Slot) -> ModelClient:
        model = self._factory(profile, slot)
        if isinstance(model, Closable):
            self._clients.append(model)
        return model

    async def aclose(self) -> None:
        # Attempt every close even when one transport cannot shut down cleanly.
        failure: Exception | None = None
        for model in self._clients:
            try:
                await model.aclose()
            except Exception as error:
                failure = failure or error
        self._clients.clear()
        if failure is not None:
            raise failure
