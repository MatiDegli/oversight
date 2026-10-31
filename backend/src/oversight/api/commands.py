"""The command routes of section 4.7: start an agent, stop it, approve, reject or request changes on
a change, Revert a rules merge, read and save a file on main, rescan the graph; 501 for the rest.

A replay takes no commands: every command route answers 501 there, its body not parsed. Live, the
app passes an object that implements `Commands`; this module maps its errors to the contract's.
Archived change and coding-agent reads use the app's committed store in both modes.
"""

import re
from collections.abc import Callable, Coroutine
from typing import Any, Protocol, cast

from fastapi import APIRouter, Request, Response

from oversight.api.errors import ApiError
from oversight.changes import (
    ChangeNotFound,
    GrantNotFound,
    InvalidAnswers,
    InvalidGrant,
    WrongState,
    read_agent_status,
    read_change_view,
    read_plan,
    read_queue,
)
from oversight.events import EventStore
from oversight.telemetry import CURRENT, TRACE_HEADER, evaluation_trace, mark
from oversight.workspace import TREE_TIMEOUT_S, FileTooLarge, InvalidPath, StaleFile, WorkspaceError
from oversight_contracts.commands import (
    AgentCreated,
    AgentMessage,
    ApproveChange,
    ChangeView,
    CreatePlan,
    EnqueueTask,
    FileContent,
    FileSaved,
    PlanCreated,
    PlanRevision,
    QueueRevision,
    RejectChange,
    RejectPlan,
    RequestChanges,
    RevertChange,
    SaveFile,
    SetMergeMode,
    StartAgent,
    TreeResponse,
    UpdatePlan,
)
from oversight_contracts.events import AgentStatus
from oversight_contracts.plans import Plan
from oversight_contracts.policy import MergeModeState
from oversight_contracts.queue import QueuedTask, QueueSnapshot

NOT_BUILT: tuple[tuple[str, str, str], ...] = (
    ("POST", "/dictation", "Reserved /dictation"),
    ("POST", "/leads", "Reserved /leads"),
    ("GET", "/leads/{agent_id}", "Reserved /leads/{agent_id}"),
    ("POST", "/leads/{agent_id}/stop", "Reserved /leads/{agent_id}/stop"),
    ("POST", "/changes/{change_id}/review", "Reserved /changes/{change_id}/review"),
)
"""Routes in the contract that this level does not build: 501, whatever the body."""
LIVE_ONLY: tuple[tuple[str, str, str], ...] = (
    ("POST", "/plans", "Creating a plan"),
    ("PUT", "/plans/{plan_id}", "Updating a plan"),
    ("POST", "/plans/{plan_id}/start", "Starting a plan"),
    ("POST", "/plans/{plan_id}/reject", "Rejecting a plan"),
    ("POST", "/queue", "Queueing work"),
    ("POST", "/queue/{queued_task_id}/cancel", "Cancelling queued work"),
    ("GET", "/tree", "Listing files"),
    ("POST", "/agents", "Starting an agent"),
    ("POST", "/agents/{agent_id}/stop", "Stop"),
    ("POST", "/agents/{agent_id}/message", "Messages to an agent"),
    ("POST", "/agents/{agent_id}/pause", "Pause"),
    ("POST", "/agents/{agent_id}/resume", "Resume"),
    ("POST", "/changes/{change_id}/approve", "Approve"),
    ("POST", "/changes/{change_id}/reject", "Reject"),
    ("POST", "/changes/{change_id}/request-changes", "Request changes"),
    ("GET", "/merge-mode", "Reading the merge mode"),
    ("PUT", "/merge-mode", "Setting the merge mode"),
    ("POST", "/changes/{change_id}/revert", "Revert"),
    ("POST", "/grants/{grant_id}/revoke", "Revoke grant"),
    ("GET", "/files", "Reading files"),
    ("PUT", "/files", "Saving files"),
    ("POST", "/graph/rescan", "Rescanning the graph"),
)


class Commands(Protocol):
    """What the live app does for the command routes. `start` raises `ApiError` for a refused
    start; `approve`, `reject`, `request_changes` and `stop` take the decision with no `await`, so a
    second one raises `WrongState` at once, and its result arrives as events. Those four, along
    with `message`, `pause` and `resume`, raise `ChangeNotFound` for an id they do not know,
    and the app's own refusals as `ApiError`."""

    async def start(self, request: StartAgent) -> AgentCreated: ...

    async def enqueue(self, request: EnqueueTask) -> QueuedTask: ...

    async def create_plan(self, request: CreatePlan) -> PlanCreated: ...

    async def update_plan(self, plan_id: str, request: UpdatePlan) -> None: ...

    async def start_plan(self, plan_id: str, request: PlanRevision) -> None: ...

    async def reject_plan(self, plan_id: str, request: RejectPlan) -> None: ...

    async def cancel_queued(self, queued_task_id: str, request: QueueRevision) -> None: ...

    def approve(self, change_id: str, request: ApproveChange) -> None: ...

    async def revert(self, change_id: str, request: RevertChange) -> None:
        """Await only the committed resolution read, then admit with no await.

        Raises ChangeNotFound and WrongState for a refused command.
        """
        ...

    async def revoke_grant(self, grant_id: str) -> None: ...

    def reject(self, change_id: str, request: RejectChange) -> None: ...

    def request_changes(self, change_id: str, request: RequestChanges) -> None: ...

    def stop(self, agent_id: str) -> None: ...

    async def message(self, agent_id: str, request: AgentMessage) -> None: ...

    def pause(self, agent_id: str) -> None: ...

    def resume(self, agent_id: str) -> None: ...

    async def get_file(self, path: str, agent_id: str | None) -> FileContent:
        """Raises `ChangeNotFound` (no worktree for that agent), `FileNotFoundError` and
        `InvalidPath`."""
        ...

    async def save_file(self, request: SaveFile) -> FileSaved:
        """Raises `ChangeNotFound`, `InvalidPath`, `StaleFile`, `WrongState`, and
        `WorkspaceError` for a save that failed; review edits may remain in the worktree."""
        ...

    async def rescan(self) -> None: ...

    def merge_mode(self) -> MergeModeState: ...

    async def set_merge_mode(self, request: SetMergeMode) -> MergeModeState: ...

    async def get_tree(self, agent_id: str | None) -> TreeResponse: ...


def _not_built(what: str, why: str) -> Callable[[], Coroutine[Any, Any, None]]:
    async def answer() -> None:
        raise ApiError("not_implemented", f"{what} {why}")

    return answer


def _decide[A](
    decision: Callable[[str, A], None], change_id: str, comment: A, *, kind: str = "change"
) -> None:
    try:
        decision(change_id, comment)
    except ChangeNotFound:
        raise ApiError("not_found", f"there is no {kind} {change_id!r}") from None
    except WrongState as error:
        raise ApiError("wrong_state", str(error)) from None
    except (InvalidAnswers, InvalidGrant) as error:
        raise ApiError("invalid_request", str(error)) from None


def add_command_routes(router: APIRouter, commands: Commands | None, *, run_id: str) -> None:
    @router.get("/plans/{plan_id}")
    async def plan_view(plan_id: str, http: Request) -> Plan:  # pyright: ignore[reportUnusedFunction]
        try:
            return read_plan(cast(EventStore, http.app.state.store), plan_id)
        except ChangeNotFound:
            raise ApiError("not_found", f"there is no plan {plan_id!r}") from None

    @router.get("/queue")
    async def queue(http: Request) -> QueueSnapshot:  # pyright: ignore[reportUnusedFunction]
        return read_queue(cast(EventStore, http.app.state.store))

    @router.get("/queue/{queued_task_id}")
    async def queue_item(queued_task_id: str, http: Request) -> QueuedTask:  # pyright: ignore[reportUnusedFunction]
        snapshot = read_queue(cast(EventStore, http.app.state.store))
        for item in snapshot.tasks:
            if item.queued_task_id == queued_task_id:
                return item
        raise ApiError("not_found", f"there is no queue item {queued_task_id!r}")

    @router.get("/changes/{change_id}")
    async def change_view(change_id: str, http: Request) -> ChangeView:  # pyright: ignore[reportUnusedFunction]
        try:
            return read_change_view(cast(EventStore, http.app.state.store), change_id)
        except ChangeNotFound:
            raise ApiError("not_found", f"there is no change {change_id!r}") from None

    @router.get("/agents/{agent_id}")
    async def agent_status(agent_id: str, http: Request) -> AgentStatus:  # pyright: ignore[reportUnusedFunction]
        try:
            return read_agent_status(cast(EventStore, http.app.state.store), agent_id)
        except ChangeNotFound:
            raise ApiError("not_found", f"there is no agent {agent_id!r}") from None

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

    @router.get("/merge-mode")
    async def merge_mode() -> MergeModeState:  # pyright: ignore[reportUnusedFunction]
        return commands.merge_mode()

    @router.put("/merge-mode")
    async def set_merge_mode(request: SetMergeMode) -> MergeModeState:  # pyright: ignore[reportUnusedFunction]
        try:
            return await commands.set_merge_mode(request)
        except WrongState as error:
            raise ApiError("wrong_state", str(error)) from None

    @router.post("/plans", status_code=201)
    async def create_plan(request: CreatePlan) -> PlanCreated:  # pyright: ignore[reportUnusedFunction]
        return await commands.create_plan(request)

    @router.put("/plans/{plan_id}", status_code=202, response_class=Response)
    async def update_plan(plan_id: str, request: UpdatePlan) -> Response:  # pyright: ignore[reportUnusedFunction]
        await commands.update_plan(plan_id, request)
        return Response(status_code=202)

    @router.post("/plans/{plan_id}/start", status_code=202, response_class=Response)
    async def start_plan(plan_id: str, request: PlanRevision) -> Response:  # pyright: ignore[reportUnusedFunction]
        await commands.start_plan(plan_id, request)
        return Response(status_code=202)

    @router.post("/plans/{plan_id}/reject", status_code=202, response_class=Response)
    async def reject_plan(plan_id: str, request: RejectPlan) -> Response:  # pyright: ignore[reportUnusedFunction]
        await commands.reject_plan(plan_id, request)
        return Response(status_code=202)

    @router.post("/queue", status_code=201)
    async def enqueue(request: EnqueueTask) -> QueuedTask:  # pyright: ignore[reportUnusedFunction]
        return await commands.enqueue(request)

    @router.post("/queue/{queued_task_id}/cancel", status_code=202, response_class=Response)
    async def cancel_queued(  # pyright: ignore[reportUnusedFunction]
        queued_task_id: str, request: QueueRevision
    ) -> Response:
        await commands.cancel_queued(queued_task_id, request)
        return Response(status_code=202)

    @router.post("/agents", status_code=201)
    async def start_agent(request: StartAgent, http: Request) -> AgentCreated:  # pyright: ignore[reportUnusedFunction]
        # request.zone is reserved and ignored until zones are delivered in 1.11.0.
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

    @router.post("/changes/{change_id}/revert", status_code=202, response_class=Response)
    async def revert(change_id: str, request: RevertChange) -> Response:  # pyright: ignore[reportUnusedFunction]
        try:
            await commands.revert(change_id, request)
        except ChangeNotFound:
            raise ApiError(
                "not_found",
                f"there is no rules resolution {request.resolution_seq} for change {change_id!r}",
            ) from None
        except WrongState as error:
            raise ApiError("wrong_state", str(error)) from None
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

    @router.post("/agents/{agent_id}/message", status_code=202, response_class=Response)
    async def message(agent_id: str, body: AgentMessage) -> Response:  # pyright: ignore[reportUnusedFunction]
        try:
            await commands.message(agent_id, body)
        except ChangeNotFound:
            raise ApiError("not_found", f"there is no agent {agent_id!r}") from None
        except WrongState as error:
            raise ApiError("wrong_state", str(error)) from None
        return Response(status_code=202)

    @router.post("/agents/{agent_id}/pause", status_code=202, response_class=Response)
    async def pause(agent_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        _decide(lambda target, _: commands.pause(target), agent_id, None, kind="agent")
        return Response(status_code=202)

    @router.post("/agents/{agent_id}/resume", status_code=202, response_class=Response)
    async def resume(agent_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        _decide(lambda target, _: commands.resume(target), agent_id, None, kind="agent")
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
