"""One bounded, tool-free plan call; unusable output preserves the person's task verbatim."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from oversight.agents._prompts import PLAN_PROMPT
from oversight.agents.client import (
    CALL_SCOPE,
    CallScope,
    EmptyChoicesError,
    ModelClient,
    ModelReply,
)
from oversight_contracts.graph import GraphSnapshot

MAX_GRAPH_CHARS = 20_000


class _Task(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    task: str = Field(min_length=1, max_length=4_000)


@dataclass(frozen=True)
class PlanOutcome:
    task: str
    detail: str


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> object:
    raise ValueError(f"invalid JSON constant: {value}")


def graph_context(snapshot: GraphSnapshot) -> str:
    """Only modules and definitions directly contained by modules, from the captured snapshot."""
    modules = {node.id for node in snapshot.nodes if node.kind == "module"}
    rows = [
        f"{node.path}: {node.kind} {node.qualname}"
        for node in sorted(snapshot.nodes, key=lambda node: (node.path, node.qualname, node.id))
        if node.kind == "module" or node.parent_id in modules
    ]
    context = "\n".join(rows)
    if len(context) <= MAX_GRAPH_CHARS:
        return context
    notice = "\n[Graph context truncated at 20,000 characters.]"
    return context[: MAX_GRAPH_CHARS - len(notice)] + notice


async def run_plan_call(
    client_factory: Callable[[], ModelClient],
    task: str,
    snapshot: GraphSnapshot,
    *,
    ready_time: float,
    record: Callable[[ModelReply], Awaitable[None]],
) -> PlanOutcome:
    deadline = ready_time + 90.0
    token = CALL_SCOPE.set(CallScope(deadline, record, role="plan"))
    try:
        try:
            async with asyncio.timeout_at(deadline):
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError
                client = client_factory()
                reply = await client.complete(
                    [
                        {"role": "system", "content": PLAN_PROMPT},
                        {"role": "user", "content": task},
                        {
                            "role": "user",
                            "content": f"Main graph version: {snapshot.version}\n"
                            + graph_context(snapshot),
                        },
                    ],
                    [],
                )
                await record(reply)
            if reply.tool_calls or reply.finish_reason == "length":
                raise ValueError("unexpected tools or truncated output")
            if reply.model is not None and reply.model != client.profile.model:
                raise ValueError("reported model differs from its profile")
            parsed = _Task.model_validate(
                json.loads(
                    reply.text or "",
                    object_pairs_hook=_unique_object,
                    parse_constant=_invalid_constant,
                )
            )
            if not parsed.task.strip():
                raise ValueError("empty subtask")
            return PlanOutcome(parsed.task, "The model wrote the subtask text.")
        except TimeoutError:
            reason = "the model call exceeded its 90-second bound"
        except EmptyChoicesError:
            # The SDK client already records this response through the scope callback.
            reason = "the model returned no choices"
        except ValueError:
            reason = "the model reply was invalid (expected only a nonempty task up to 4,000 chars)"
        except Exception as error:
            reason = f"the model call failed ({type(error).__name__})"
        return PlanOutcome(task, f"Verbatim fallback: {reason}.")
    finally:
        CALL_SCOPE.reset(token)
