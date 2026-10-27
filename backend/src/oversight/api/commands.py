"""The command routes of section 4.7: start an agent, stop it, approve, reject or request changes on
a change; 501 for the rest.

A replay takes no commands: every command route answers 501 there, its body not parsed. Live, the
app passes an object that implements `Commands`; this module maps its errors to the contract's.
"""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Body, Response

from oversight.api.errors import ApiError
from oversight.changes import ChangeNotFound, WrongState
from oversight_contracts.commands import (
    AgentCreated,
    ApproveChange,
    RejectChange,
    RequestChanges,
    StartAgent,
)

NOT_BUILT: tuple[tuple[str, str, str], ...] = (
    ("GET", "/impact", "The impact query"),
    ("GET", "/files", "Reading files"),
    ("PUT", "/files", "Saving files"),
    ("POST", "/graph/rescan", "Rescanning the graph"),
    ("POST", "/agents/{agent_id}/message", "Messages to an agent"),
    ("POST", "/agents/{agent_id}/pause", "Pause"),
    ("POST", "/agents/{agent_id}/resume", "Resume"),
)
"""Routes in the contract that this level does not build: 501, whatever the body."""
LIVE_ONLY: tuple[tuple[str, str, str], ...] = (
    ("POST", "/agents", "Starting an agent"),
    ("POST", "/agents/{agent_id}/stop", "Stop"),
    ("POST", "/changes/{change_id}/approve", "Approve"),
    ("POST", "/changes/{change_id}/reject", "Reject"),
    ("POST", "/changes/{change_id}/request-changes", "Request changes"),
)


class Commands(Protocol):
    """What the live app does for the command routes. `start` raises `ApiError` for a refused
    start; `approve`, `reject`, `request_changes` and `stop` take the decision with no `await`, so a
    second one raises `WrongState` at once, and its result arrives as events. All four raise
    `ChangeNotFound` for an id they do not know, and the app's own refusals as `ApiError`."""

    async def start(self, request: StartAgent) -> AgentCreated: ...

    def approve(self, change_id: str, comment: str | None) -> None: ...

    def reject(self, change_id: str, comment: str | None) -> None: ...

    def request_changes(self, change_id: str, comment: str) -> None: ...

    def stop(self, agent_id: str) -> None: ...


def _not_built(what: str, why: str) -> Callable[[], Coroutine[Any, Any, None]]:
    async def answer() -> None:
        raise ApiError("not_implemented", f"{what} {why}")

    return answer


def _decide[A](decision: Callable[[str, A], None], change_id: str, comment: A) -> None:
    try:
        decision(change_id, comment)
    except ChangeNotFound:
        raise ApiError("not_found", f"there is no change {change_id!r}") from None
    except WrongState as error:
        raise ApiError("wrong_state", str(error)) from None


def add_command_routes(router: APIRouter, commands: Commands | None) -> None:
    for method, path, what in NOT_BUILT:
        router.add_api_route(
            path, _not_built(what, "is not built yet"), methods=[method], status_code=501
        )
    if commands is None:
        for method, path, what in LIVE_ONLY:
            router.add_api_route(
                path, _not_built(what, "is not available in a replay"), methods=[method]
            )
        return

    @router.post("/agents", status_code=201)
    async def start_agent(request: StartAgent) -> AgentCreated:  # pyright: ignore[reportUnusedFunction]
        return await commands.start(request)

    @router.post("/changes/{change_id}/approve", status_code=202, response_class=Response)
    async def approve(  # pyright: ignore[reportUnusedFunction]
        change_id: str, body: Annotated[ApproveChange | None, Body()] = None
    ) -> Response:
        _decide(commands.approve, change_id, body.comment if body else None)
        return Response(status_code=202)

    @router.post("/changes/{change_id}/reject", status_code=202, response_class=Response)
    async def reject(  # pyright: ignore[reportUnusedFunction]
        change_id: str, body: Annotated[RejectChange | None, Body()] = None
    ) -> Response:
        _decide(commands.reject, change_id, body.comment if body else None)
        return Response(status_code=202)

    @router.post("/changes/{change_id}/request-changes", status_code=202, response_class=Response)
    async def request_changes(change_id: str, body: RequestChanges) -> Response:  # pyright: ignore[reportUnusedFunction]
        _decide(commands.request_changes, change_id, body.comment)
        return Response(status_code=202)

    @router.post("/agents/{agent_id}/stop", status_code=202, response_class=Response)
    async def stop(agent_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        try:
            commands.stop(agent_id)
        except ChangeNotFound:
            raise ApiError("not_found", f"there is no agent {agent_id!r}") from None
        except WrongState as error:
            raise ApiError("wrong_state", str(error)) from None
        return Response(status_code=202)
