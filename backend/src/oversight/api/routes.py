"""The routes: the event stream, the graph and the health check (sections 4.6 and 4.7), and the
commands, from `commands.py`."""

import re
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent

from oversight.api.commands import Commands, add_command_routes
from oversight.api.errors import ApiError
from oversight.events import EventStore
from oversight_contracts import CONTRACT_VERSION
from oversight_contracts.commands import GraphResponse, Health

_SEQ = re.compile(r"[0-9]{1,18}")


def _no_transform(response: Response) -> None:
    # FastAPI's SSE response sets `Cache-Control: no-cache` and appends the headers set here, so
    # the stream carries both directives: `no-cache, no-transform`, as section 4.6 asks.
    response.headers["Cache-Control"] = "no-transform"


def _seq(value: str, name: str) -> int:
    # ASCII digits only: `str.isdigit` also accepts characters `int` cannot read, such as "²".
    if not _SEQ.fullmatch(value):
        raise ApiError("invalid_request", f"{name} must be a seq: a whole number, not {value!r}")
    return int(value)


def create_router(
    store: EventStore, mode: Literal["live", "replay"], commands: Commands | None = None
) -> APIRouter:
    """Live mode passes `commands`; without it, as in a replay, every command answers 501."""
    router = APIRouter()
    add_command_routes(router, commands)

    def resume_point(
        after: Annotated[str | None, Query()] = None,
        run: str | None = None,
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> int:
        """Where to resume, by section 4.6's precedence, checking only the value that applies.

        Another run's `run` wins over everything, so a bad `after` or `Last-Event-ID` is not an
        error there; then `Last-Event-ID`, then `after`, then 0.
        """
        if run is not None and run != store.run_id:
            return 0
        if last_event_id is not None:
            return _seq(last_event_id, "Last-Event-ID")
        if after is not None:
            return _seq(after, "after")
        return 0

    @router.get(
        "/events", response_class=EventSourceResponse, dependencies=[Depends(_no_transform)]
    )
    async def events(  # pyright: ignore[reportUnusedFunction]
        after: Annotated[int, Depends(resume_point)],
    ) -> AsyncIterator[ServerSentEvent]:
        # No `event:` field, so one onmessage handler gets every type; FastAPI sends `: ping`
        # after 15 seconds without an event.
        async for event in store.subscribe(after):
            yield ServerSentEvent(raw_data=event.envelope, id=str(event.seq))

    @router.get("/graph")
    async def graph() -> GraphResponse:  # pyright: ignore[reportUnusedFunction]
        seq, snapshot = store.graph()
        if snapshot is None:
            raise ApiError("not_found", "no graph snapshot has been published in this run yet")
        return GraphResponse(run_id=store.run_id, seq=seq, graph=snapshot)

    @router.get("/health")
    async def health() -> Health:  # pyright: ignore[reportUnusedFunction]
        return Health(ok=True, run_id=store.run_id, contract_version=CONTRACT_VERSION, mode=mode)

    return router
