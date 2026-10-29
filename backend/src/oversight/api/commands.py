"""The command routes of section 4.7: start an agent, stop it, approve, reject or request changes on
a change, read and save a file on main, rescan the graph; 501 for the rest.

A replay takes no commands: every command route answers 501 there, its body not parsed. Live, the
app passes an object that implements `Commands`; this module maps its errors to the contract's.
"""

import re
from collections.abc import Callable, Coroutine
from typing import Any, Protocol

from fastapi import APIRouter, Request, Response

from oversight.api.errors import ApiError
from oversight.changes import (
    ChangeNotFound,
    GrantNotFound,
    InvalidAnswers,
    InvalidGrant,
    WrongState,
)
from oversight.telemetry import CURRENT, TRACE_HEADER, evaluation_trace, mark
from oversight.workspace import TREE_TIMEOUT_S, FileTooLarge, InvalidPath, StaleFile, WorkspaceError
from oversight_contracts.commands import (
    AgentCreated,
    ApproveChange,
    FileContent,
    FileSaved,
    RejectChange,
    RequestChanges,
    SaveFile,
    StartAgent,
    TreeResponse,
)

NOT_BUILT: tuple[tuple[str, str, str], ...] = (
    ("POST", "/dictation", "Reserved /dictation"),
    ("POST", "/queue", "Reserved /queue"),
    ("GET", "/queue", "Reserved /queue"),
    ("GET", "/queue/{queued_task_id}", "Reserved /queue/{queued_task_id}"),
    ("POST", "/queue/{queued_task_id}/cancel", "Reserved /queue/{queued_task_id}/cancel"),
    ("POST", "/leads", "Reserved /leads"),
    ("GET", "/leads/{agent_id}", "Reserved /leads/{agent_id}"),
    ("POST", "/leads/{agent_id}/stop", "Reserved /leads/{agent_id}/stop"),
    ("POST", "/changes/{change_id}/review", "Reserved /changes/{change_id}/review"),
    ("POST", "/changes/{change_id}/revert", "Reserved /changes/{change_id}/revert"),
    ("GET", "/changes/{change_id}", "Reserved /changes/{change_id}"),
    ("GET", "/agents/{agent_id}", "Reserved /agents/{agent_id}"),
    ("GET", "/impact", "The impact query"),
    ("GET", "/merge-mode", "Reading the merge mode"),
    ("PUT", "/merge-mode", "Setting the merge mode"),
    ("POST", "/plans", "Creating a plan"),
    ("GET", "/plans/{plan_id}", "Reading a plan"),
    ("PUT", "/plans/{plan_id}", "Updating a plan"),
    ("POST", "/plans/{plan_id}/start", "Starting a plan"),
    ("POST", "/plans/{plan_id}/reject", "Rejecting a plan"),
    ("POST", "/agents/{agent_id}/message", "Messages to an agent"),
    ("POST", "/agents/{agent_id}/pause", "Pause"),
    ("POST", "/agents/{agent_id}/resume", "Resume"),
)
"""Routes in the contract that this level does not build: 501, whatever the body."""
LIVE_ONLY: tuple[tuple[str, str, str], ...] = (
    ("GET", "/tree", "Listing files"),
    ("POST", "/agents", "Starting an agent"),
    ("POST", "/agents/{agent_id}/stop", "Stop"),
    ("POST", "/changes/{change_id}/approve", "Approve"),
    ("POST", "/changes/{change_id}/reject", "Reject"),
    ("POST", "/changes/{change_id}/request-changes", "Request changes"),
    ("POST", "/grants/{grant_id}/revoke", "Revoke grant"),
    ("GET", "/files", "Reading files"),
    ("PUT", "/files", "Saving files"),
    ("POST", "/graph/rescan", "Rescanning the graph"),
)


class Commands(Protocol):
    """What the live app does for the command routes. `start` raises `ApiError` for a refused
    start; `approve`, `reject`, `request_changes` and `stop` take the decision with no `await`, so a
    second one raises `WrongState` at once, and its result arrives as events. All four raise
    `ChangeNotFound` for an id they do not know, and the app's own refusals as `ApiError`."""

    async def start(self, request: StartAgent) -> AgentCreated: ...

    def approve(self, change_id: str, request: ApproveChange) -> None: ...

    async def revoke_grant(self, grant_id: str) -> None: ...

    def reject(self, change_id: str, request: RejectChange) -> None: ...

    def request_changes(self, change_id: str, request: RequestChanges) -> None: ...

    def stop(self, agent_id: str) -> None: ...

    async def get_file(self, path: str, agent_id: str | None) -> FileContent:
        """Raises `ChangeNotFound` (no worktree for that agent), `FileNotFoundError` and
        `InvalidPath`."""
        ...

    async def save_file(self, request: SaveFile) -> FileSaved:
        """Raises `ChangeNotFound`, `InvalidPath`, `StaleFile`, `WrongState`, and
        `WorkspaceError` for a save that failed; review edits may remain in the worktree."""
        ...

    async def rescan(self) -> None: ...

    async def get_tree(self, agent_id: str | None) -> TreeResponse: ...


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
    except (InvalidAnswers, InvalidGrant) as error:
        raise ApiError("invalid_request", str(error)) from None


def add_command_routes(router: APIRouter, commands: Commands | None, *, run_id: str) -> None:
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
    async def start_agent(request: StartAgent, http: Request) -> AgentCreated:  # pyright: ignore[reportUnusedFunction]
        # request.zone is reserved and ignored until zones are delivered in 1.8.0.
        attempt_id = http.headers.get(TRACE_HEADER)
        if attempt_id is not None and not re.fullmatch(r"[a-f0-9]{32}", attempt_id):
            raise ApiError(
                "invalid_request", f"invalid {TRACE_HEADER}: expected 32 lowercase hex characters"
            )
        with evaluation_trace(run_id, attempt_id):
            if attempt_id is not None and CURRENT.get() is None:
                raise ApiError(
                    "invalid_request", "evaluation tracing requires OVERSIGHT_EVAL_TRACE_DIR"
                )
            mark("server_attempt_start")
            return await commands.start(request)

    @router.post("/changes/{change_id}/approve", status_code=202, response_class=Response)
    async def approve(  # pyright: ignore[reportUnusedFunction]
        change_id: str, body: ApproveChange
    ) -> Response:
        _decide(commands.approve, change_id, body)
        return Response(status_code=202)

    @router.post("/changes/{change_id}/reject", status_code=202, response_class=Response)
    async def reject(  # pyright: ignore[reportUnusedFunction]
        change_id: str, body: RejectChange
    ) -> Response:
        _decide(commands.reject, change_id, body)
        return Response(status_code=202)

    @router.post("/grants/{grant_id}/revoke", status_code=202, response_class=Response)
    async def revoke_grant(grant_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        try:
            await commands.revoke_grant(grant_id)
        except GrantNotFound:
            raise ApiError("not_found", f"there is no grant {grant_id!r}") from None
        except WrongState as error:
            raise ApiError("wrong_state", str(error)) from None
        return Response(status_code=202)

    @router.post("/changes/{change_id}/request-changes", status_code=202, response_class=Response)
    async def request_changes(change_id: str, body: RequestChanges) -> Response:  # pyright: ignore[reportUnusedFunction]
        _decide(commands.request_changes, change_id, body)
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

    @router.get("/files")
    async def get_file(path: str, agent: str | None = None) -> FileContent:  # pyright: ignore[reportUnusedFunction]
        try:
            return await commands.get_file(path, agent)
        except ChangeNotFound:
            raise ApiError("not_found", f"there is no live worktree for {agent!r}") from None
        except FileNotFoundError:
            raise ApiError("not_found", f"there is no file {path[:80]!r}") from None
        except FileTooLarge as error:
            raise ApiError("too_large", str(error)) from None
        except InvalidPath as error:
            raise ApiError("invalid_path", str(error)) from None

    @router.get("/tree")
    async def get_tree(agent: str | None = None) -> TreeResponse:  # pyright: ignore[reportUnusedFunction]
        try:
            return await commands.get_tree(agent)
        except InvalidPath as error:
            raise ApiError("invalid_path", str(error)) from None
        except TimeoutError:
            raise ApiError(
                "internal",
                f"listing files exceeded {TREE_TIMEOUT_S:g} seconds; retry the Files sidebar",
            ) from None

    @router.put("/files")
    async def save_file(request: SaveFile) -> FileSaved:  # pyright: ignore[reportUnusedFunction]
        try:
            return await commands.save_file(request)
        except ChangeNotFound:
            raise ApiError(
                "not_found", f"there is no live worktree for {request.agent_id!r}"
            ) from None
        except FileTooLarge as error:
            raise ApiError("too_large", str(error)) from None
        except InvalidPath as error:
            raise ApiError("invalid_path", str(error)) from None
        except StaleFile as error:
            raise ApiError("stale_file", str(error)) from None
        except WrongState as error:
            raise ApiError("wrong_state", str(error)) from None
        except WorkspaceError as error:
            # Review save failures may leave edits in the worktree without an error event.
            raise ApiError("internal", str(error)) from None

    @router.post("/graph/rescan", status_code=202, response_class=Response)
    async def rescan() -> Response:  # pyright: ignore[reportUnusedFunction]
        await commands.rescan()
        return Response(status_code=202)
