"""The read routes: the event stream, the graph and the health check (sections 4.6 and 4.7)."""

from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent

from oversight.api.errors import ApiError
from oversight.events import EventStore
from oversight_contracts import CONTRACT_VERSION
from oversight_contracts.commands import GraphResponse, Health


def _no_transform(response: Response) -> None:
    # FastAPI's SSE response sets `Cache-Control: no-cache` and appends the headers set here, so
    # the stream carries both directives: `no-cache, no-transform`, as section 4.6 asks.
    response.headers["Cache-Control"] = "no-transform"


def create_router(store: EventStore, mode: Literal["live", "replay"]) -> APIRouter:
    router = APIRouter()

    def resume_point(
        after: Annotated[int, Query(ge=0)] = 0,
        run: str | None = None,
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> int:
        """`Last-Event-ID`, else `after`, else 0; from the start when `run` names another run."""
        if run is not None and run != store.run_id:
            return 0
        if last_event_id is None:
            return after
        if not last_event_id.isdigit():
            raise ApiError("invalid_request", "Last-Event-ID must be a seq: a whole number")
        return int(last_event_id)

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
