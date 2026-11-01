"""Closed lead tools and bounded scripted coordination, without model transport."""

import json
from typing import Any
from unittest.mock import AsyncMock

from oversight.agents import (
    LeadRun,
    LeadTools,
    ScriptedModel,
    parse_script,
)
from oversight.agents.client import CALL_SCOPE
from oversight_contracts.commands import FileContent, TreeResponse
from oversight_contracts.graph import GraphSnapshot
from oversight_contracts.plans import PlanZone
from oversight_contracts.queue import QueueSnapshot


def graph() -> GraphSnapshot:
    return GraphSnapshot(
        version="main-now", commit="base", nodes=[], edges=[], parse_errors=[], positions={}
    )


def setup_tools(slots: int = 2, **overrides: Any) -> tuple[LeadTools, AsyncMock]:
    propose = AsyncMock(side_effect=["plan-1", "plan-2", "plan-3"])
    values: dict[str, Any] = dict(
        graph=graph,
        tree=AsyncMock(
            return_value=TreeResponse(
                run_id="run-20261031-170000",
                agent_id=None,
                directories=[],
                files=[],
                graph_version="main-now",
                truncated=False,
            )
        ),
        read=AsyncMock(
            return_value=FileContent(
                path="shop/pricing.py",
                content="\n".join(f"line {i}" for i in range(1, 451)),
                hash="base",
                agent_id=None,
            )
        ),
        queue=lambda: QueueSnapshot(run_id="run-20261031-170000", seq=0, tasks=[]),
        zone=lambda: PlanZone(node_ids=[], paths=["shop/pricing.py"]),
        propose=propose,
        slots=slots,
    )
    return LeadTools(**(values | overrides)), propose


def scripted(turns: list[dict[str, Any]], delay: float = 0) -> ScriptedModel:
    return ScriptedModel(
        parse_script(json.dumps(dict(task="Coordinate", turns=turns, turn_delay_s=delay)))
    )


def call(name: str, **arguments: Any) -> dict[str, Any]:
    return dict(name=name, arguments=arguments)


def turn(*calls: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return dict(text=None, tool_calls=list(calls), usage=dict(input_tokens=1), **extra)


def plan(task: str = "Document pricing", **extra: Any) -> dict[str, Any]:
    return dict(task=task, subtasks=[dict(task="Document shop/pricing.py")]) | extra


def lead(model: Any, tools: LeadTools, **overrides: Any) -> tuple[LeadRun, AsyncMock, AsyncMock]:
    record, step = AsyncMock(), AsyncMock()

    def factory() -> Any:
        scope = CALL_SCOPE.get()
        assert scope is not None and scope.role == "lead"
        return model

    values: dict[str, Any] = dict(
        agent_id="agent-1",
        task="Coordinate",
        make_client=factory,
        tools=tools,
        record=record,
        publish=AsyncMock(),
        admit=lambda: None,
        on_working=AsyncMock(),
        on_step=step,
    )
    return LeadRun(**(values | overrides)), record, step
