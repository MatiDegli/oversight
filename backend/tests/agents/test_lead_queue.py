"""Queue reads preserve a valid, bounded JSON prefix without changing server state."""

import json

import pytest
from backend.tests.agents.lead_helpers import setup_tools

from oversight_contracts.plans import PlanZone
from oversight_contracts.queue import QueuedAgentTask, QueuedTask, QueueSnapshot

pytestmark = pytest.mark.anyio


def snapshot(*texts: str) -> QueueSnapshot:
    return QueueSnapshot(
        run_id="run-20261031-170000",
        seq=42,
        tasks=[
            QueuedTask(
                agent=QueuedAgentTask(
                    task=text, slot="A", model="scripted", zone=PlanZone(node_ids=[], paths=[])
                ),
                queued_task_id=f"queued-task-{index}",
                revision=1,
                state="queued",
                agent_ids=[],
                detail="Waiting for a slot.",
            )
            for index, text in enumerate(texts, 1)
        ],
    )


async def read_queue(source: QueueSnapshot) -> tuple[str, QueueSnapshot]:
    before = source.model_copy(deep=True)
    tasks, entries = source.tasks, tuple(source.tasks)
    tools, _ = setup_tools(queue=lambda: source)
    outcome = await tools.call("queue", "{}")
    assert outcome.ok and not outcome.finish and outcome.plan_id is None
    content = outcome.content.split("\n", 1)[0]
    shown = QueueSnapshot.model_validate_json(content)
    assert len(content) <= 20_000
    assert content == shown.model_dump_json(exclude_none=True)
    assert (shown.run_id, shown.seq) == (source.run_id, source.seq)
    assert source == before and source.tasks is tasks
    assert all(a is b for a, b in zip(source.tasks, entries, strict=True))
    return outcome.content, shown


async def test_empty_queue_is_unchanged() -> None:
    source = snapshot()
    content, shown = await read_queue(source)
    assert content == source.model_dump_json(exclude_none=True)
    assert shown.tasks == []


async def test_queue_that_fits_is_unchanged_in_source_order() -> None:
    source = snapshot("First task", "Second task")
    source.tasks.append(
        QueuedTask(
            plan_id="plan-1",
            plan_revision=2,
            queued_task_id="queued-task-3",
            revision=3,
            state="queued",
            agent_ids=[],
            detail="Waiting for a slot.",
        )
    )
    content, shown = await read_queue(source)
    assert content == source.model_dump_json(exclude_none=True)
    assert shown == source
    assert '"started_by"' not in content


@pytest.mark.parametrize("overflow", [False, True])
async def test_null_fields_keep_the_existing_exclude_none_serialization(overflow: bool) -> None:
    source = snapshot("Task", "x" * 20_000) if overflow else snapshot("Task")
    source.tasks[0] = source.tasks[0].model_copy(
        update={
            "agent": QueuedAgentTask(task="Task", slot=None, model=None, zone=None),
            "detail": None,
        }
    )
    before = source.model_copy(deep=True)
    tools, _ = setup_tools(queue=lambda: source)
    outcome = await tools.call("queue", "{}")
    prefix = source.model_copy(update={"tasks": source.tasks[:1]})
    suffix = "\n(Queue truncated: 1 entries omitted.)" if overflow else ""
    assert outcome.content == prefix.model_dump_json(exclude_none=True) + suffix
    parsed = json.loads(outcome.content.split("\n", 1)[0])
    assert parsed["tasks"][0]["agent"] == {"task": "Task"}
    assert "detail" not in parsed["tasks"][0]
    assert source == before


async def test_exact_20000_character_boundary_is_inclusive() -> None:
    source = snapshot("x", "Last entry")
    padding = 20_000 - len(source.model_dump_json(exclude_none=True))
    source = snapshot("x" * (padding + 1), "Last entry")
    content, shown = await read_queue(source)
    assert len(content) == 20_000
    assert content == source.model_dump_json(exclude_none=True) and shown == source
    source = snapshot("x" * (padding + 1), "Last entry!")
    content, shown = await read_queue(source)
    assert shown.tasks == source.tasks[:1]
    assert content.endswith("\n(Queue truncated: 1 entries omitted.)")


async def test_overflow_keeps_longest_prefix_with_exact_omission_count() -> None:
    source = snapshot("x" * 8000, "y" * 8000, "z" * 8000, "later small entry")
    content, shown = await read_queue(source)
    expected = source.model_copy(update={"tasks": source.tasks[:2]})
    assert shown == expected
    assert content == expected.model_dump_json(exclude_none=True) + (
        "\n(Queue truncated: 2 entries omitted.)"
    )
    assert len(source.model_copy(update={"tasks": source.tasks[:3]}).model_dump_json()) > 20_000


async def test_first_entry_too_large_returns_empty_envelope_without_skipping() -> None:
    source = snapshot("x" * 20_000, "small task")
    content, shown = await read_queue(source)
    empty = source.model_copy(update={"tasks": []})
    assert shown == empty
    assert content == empty.model_dump_json(exclude_none=True) + (
        "\n(Queue truncated: 2 entries omitted.)"
    )


async def test_escaped_and_non_ascii_values_use_serialized_character_lengths() -> None:
    text = 'Café 🧉 "quoted" \\path\nnext\trow'
    source = snapshot(text, "last")
    padding = 20_000 - len(source.model_dump_json(exclude_none=True))
    source = snapshot(text + "é" * padding, "last")
    content, shown = await read_queue(source)
    assert len(content) == 20_000 and len(content.encode()) > 20_000
    assert shown == source and shown.tasks[0].agent is not None
    assert shown.tasks[0].agent.task == text + "é" * padding
    source = snapshot(text + "é" * padding + "\n", "last")
    content, shown = await read_queue(source)
    assert shown.tasks == source.tasks[:1]
    assert content.endswith("\n(Queue truncated: 1 entries omitted.)")
