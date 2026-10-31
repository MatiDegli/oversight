"""The routes: the event stream, the graph and the health check (sections 4.6 and 4.7), and the
commands, from `commands.py`."""

import re
from collections.abc import AsyncIterator, Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent

from oversight.api.commands import Commands, add_command_routes
from oversight.api.errors import ApiError
from oversight.changes import ChangeNotFound
from oversight.events import (
    EventStore,
    HistoryTooLarge,
    InvalidHistoryRequest,
    build_usage_view,
    read_history,
)
from oversight.graph import NodeNotFoundError, node_cone
from oversight_contracts import CONTRACT_VERSION
from oversight_contracts.commands import GraphResponse, Health, MissingSetting
from oversight_contracts.events import Limits
from oversight_contracts.graph import GraphSnapshot, ImpactCone
from oversight_contracts.usage import UsageView

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


def _cone_depth(value: str | None, bound: int) -> int:
    if value is None:
        return bound
    if not re.fullmatch(r"[0-9]+", value) or not (digits := value.lstrip("0")):
        raise ApiError("invalid_request", "depth must be a positive integer")
    # Clamp before conversion, including integers beyond Python's digit conversion limit.
    return bound if len(digits) > len(str(bound)) else min(int(digits), bound)


def create_router(
    store: EventStore,
    mode: Literal["live", "replay"],
    commands: Commands | None = None,
    *,
    missing_settings: list[MissingSetting] | None = None,
    change_graph: Callable[[str], GraphSnapshot] | None = None,
) -> APIRouter:
    """Live mode passes `commands`; without it, as in a replay, every command answers 501."""
    router = APIRouter()
    add_command_routes(router, commands, run_id=store.run_id)
    run_limits: Limits | None = None

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

    @router.get("/history")
    async def history(  # pyright: ignore[reportUnusedFunction]
        after_seq: str = "0",
        limit: str = "50",
        through_seq: str | None = None,
    ) -> Response:
        after = _seq(after_seq, "after_seq")
        count = _seq(limit, "limit")
        through = None if through_seq is None else _seq(through_seq, "through_seq")
        try:
            body = await read_history(store, after_seq=after, limit=count, through_seq=through)
        except InvalidHistoryRequest as error:
            raise ApiError("invalid_request", str(error)) from error
        except HistoryTooLarge as error:
            raise ApiError("too_large", str(error)) from error
        return Response(body, media_type="application/json")

    @router.get("/usage")
    async def usage() -> UsageView:  # pyright: ignore[reportUnusedFunction]
        return build_usage_view(store.run_id, store.committed_events())

    @router.get("/graph")
    async def graph() -> GraphResponse:  # pyright: ignore[reportUnusedFunction]
        seq, snapshot = store.graph()
        if snapshot is None:
            raise ApiError("not_found", "no graph snapshot has been published in this run yet")
        return GraphResponse(run_id=store.run_id, seq=seq, graph=snapshot)

    @router.get("/impact")
    async def impact(  # pyright: ignore[reportUnusedFunction]
        node: str, depth: str | None = None, change: str | None = None
    ) -> ImpactCone:
        nonlocal run_limits
        # Validate the query before looking up resources, even before a run has a snapshot.
        _cone_depth(depth, 1)
        _, snapshot = store.graph()
        if snapshot is None:
            raise ApiError("not_found", "no graph snapshot has been published in this run yet")
        if run_limits is None:
            run_limits = next(
                (e.payload.limits for e in store.committed_events() if e.type == "run.started"),
                None,
            )
        if run_limits is None:
            raise ApiError("not_found", "no run limits have been published yet")
        if change is not None:
            try:
                if change_graph is None:
                    raise ChangeNotFound(change)
                snapshot = change_graph(change)
            except ChangeNotFound:
                raise ApiError("not_found", f"there is no change {change!r}") from None
        try:
            return node_cone(
                snapshot,
                node,
                max_depth=_cone_depth(depth, run_limits.cone_max_depth),
                max_nodes=run_limits.cone_max_nodes,
            )
        except NodeNotFoundError:
            raise ApiError("not_found", f"there is no node {node!r} in this graph") from None

    @router.get("/health")
    async def health() -> Health:  # pyright: ignore[reportUnusedFunction]
        return Health(
            ok=True,
            run_id=store.run_id,
            contract_version=CONTRACT_VERSION,
            mode=mode,
            missing_settings=missing_settings or [],
        )

    return router
