"""Runtime observer adapters, keeping instrumentation out of the tool loop."""

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any
from weakref import WeakSet

from oversight.telemetry import CURRENT, annotate, bind, mark, span
from oversight.workspace import Workspace

_WRAPPED: WeakSet[Workspace] = WeakSet()


def publications[T](publish: Callable[[Any], Awaitable[T]]) -> Callable[[Any], Awaitable[T]]:
    async def observed(payload: Any) -> T:
        with span("publication") as fields:
            result = await publish(payload)
            fields["event_seq"] = getattr(result, "seq", None)
            return result

    return observed


def timed(name: str):
    def decorate(function: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        async def observed(*args: Any, **kwargs: Any) -> Any:
            with span(name):
                return await function(*args, **kwargs)

        return observed

    return decorate


def observe_locks(workspace: Workspace) -> None:
    """Wrap the public lock boundary once; preserve the original reentrant lock manager.

    ContextVars select the caller's observer, so an untraced parallel task is unaffected.
    No workspace internals, lock ordering or ownership are changed.
    """
    if CURRENT.get() is None or workspace in _WRAPPED:
        return
    original = workspace.locks

    @asynccontextmanager
    async def locks(*, main: bool = False, agent_id: str | None = None):
        manager: AbstractAsyncContextManager[None] = original(main=main, agent_id=agent_id)
        with span("lock_wait", lock_kind="main_and_worktree" if main else "worktree"):
            await manager.__aenter__()
        try:
            yield
        except BaseException as error:
            if not await manager.__aexit__(type(error), error, error.__traceback__):
                raise
        else:
            await manager.__aexit__(None, None, None)

    _WRAPPED.add(workspace)
    workspace.locks = locks


def traced_drive[T](drive: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    async def observed(self: Any, *args: Any, **kwargs: Any) -> T:
        from oversight.agents._prompts import PROMPT_VERSION

        token = CURRENT.set(self._observer)
        try:
            observe_locks(self._workspace)
            with (
                bind(agent=self._task.agent_id, prompt_version=PROMPT_VERSION),
                span("agent_attempt"),
            ):
                result = await drive(self, *args, **kwargs)
                if getattr(result, "state", None) == "ready_for_review":
                    mark("proposal_ready")
                return result
        finally:
            CURRENT.reset(token)

    return observed


async def model_turn(self: Any, messages: Any, schemas: Any) -> Any:
    annotate(turn=self._turns, tool_call=None, http_attempt=None)
    profile = self._model.profile
    with span(
        "model_turn",
        requested_model=profile.model,
        reasoning_effort=profile.reasoning,
        output_allowance=self._task.limits.max_output_tokens,
        reported_model=None,
        input_tokens=None,
        output_tokens=None,
        cached_tokens=None,
        reasoning_tokens=None,
        finish_reason=None,
        tool_count=None,
    ) as fields:
        reply = await self._model.complete(messages, schemas)
        fields.update(
            reported_model=reply.model,
            input_tokens=reply.usage.input_tokens,
            output_tokens=reply.usage.output_tokens,
            cached_tokens=reply.usage.cached_tokens,
            reasoning_tokens=reply.usage.reasoning_tokens,
            finish_reason=reply.finish_reason,
            tool_count=len(reply.tool_calls),
        )
        return reply
