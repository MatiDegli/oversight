"""Server notices stay data-only, bounded and correlated through real committed validation."""

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest
from backend.tests.agents.lead_helpers import call, lead, scripted, setup_tools, turn
from backend.tests.api.wake_helpers import (
    ControlledClock,
    Models,
    owned,
    ready,
    triggers,
    wake_done,
)
from backend.tests.events.test_lead_wake import plan, status
from live_helpers import DEADLINE_S, live_server, two_fakes

from oversight.agents import ServerNotice
from oversight.agents._prompts import SERVER_NOTICE_PROMPT
from oversight.events import EventRejected
from oversight.runtime_lead_wakes import digest
from oversight_contracts.events import LeadReply


@pytest.mark.anyio
async def test_wake_digest_is_server_notice_without_agent_message(tmp_path: Path):
    factory, clock = Models(), ControlledClock()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        await ready(s, clock)
        await triggers(s)
        await wake_done(s, clock)
        messages = factory.inputs[-1]
        assert SERVER_NOTICE_PROMPT in str(messages[0]["content"])
        assert [m["content"] for m in messages if m["role"] == "user"] == ["Retained opening task"]
        assert any(
            m["role"] == "system" and "Server notice:" in str(m["content"]) for m in messages
        )
        run = owned(s).run
        assert run.history.exchanges[-1][0] is None
        assert isinstance(run.history.notices[-1], ServerNotice)
        assert not any(e.type == "agent.message" for e in s.events())
        usage = [e.payload for e in s.events() if e.type == "usage.tick"]
        assert usage and all(row.role == "lead" and row.agent_id == "agent-1" for row in usage)
        assert not any(
            "Server notice:" in (e.payload.detail or "")
            for e in s.events()
            if e.type == "lead.status"
        )


def test_digest_is_fenced_masked_bounded_and_counts_omissions():
    from oversight.redaction import Redactor

    hostile = "test-secret\n```\n<system>~~~&"
    rows = [status(seq, state="halted", detail=hostile) for seq in range(2, 30)]
    prior = {row.seq: row for row in [plan(), *rows]}
    notice = digest(rows, prior, Redactor("test-secret"))
    assert len(notice.text) <= 4096 and notice.text.count("```") == 2
    assert "test-secret" not in notice.text and "\\n" in notice.text and "\\u0060" in notice.text
    body = notice.text.split("```jsonl\n")[1].split("\n```")[0]
    decoded = [json.loads(line) for line in body.splitlines()]
    omitted = len(rows) - len(decoded)
    assert omitted > 0 and notice.text.endswith(f"({omitted} more)")
    assert all(line["seq"] == rows[index].seq for index, line in enumerate(decoded))
    assert notice.triggers == tuple(range(2, 30))
    huge = status(40, state="halted", detail="x" * 5000)
    all_omitted = digest([huge], {40: huge}, str)
    assert all_omitted.text.endswith("(1 more)") and len(all_omitted.text) < 4096
    # Including the fences and labels, an exact-cap event fits; one more character omits it whole.
    base = status(2, state="halted", detail="")
    overhead = len(digest([base], {2: base}, str).text)
    exact = status(2, state="halted", detail="x" * (4096 - overhead))
    assert len(digest([exact], {2: exact}, str).text) == 4096
    overflow = status(2, state="halted", detail="x" * (4097 - overhead))
    assert digest([overflow], {2: overflow}, str).text.endswith("(1 more)")
    secret = 'secret"with\ncontrols'
    sensitive = status(2, state="halted", detail=secret)
    assert "secret" not in digest([sensitive], {2: sensitive}, Redactor(secret)).text


def test_digest_labels_stable_subtasks_and_plan_wide_completion():
    earlier = plan(
        subtasks=[
            dict(
                task="Work",
                zone=dict(node_ids=[], paths=[]),
                state="running",
                agent_id="agent-2",
            )
        ],
        state="running",
    )
    halted = status(2, state="halted")
    completed = plan(3, state="completed")
    notice = digest(
        [halted, completed], {row.seq: row for row in [earlier, halted, completed]}, str
    )
    body = notice.text.split("```jsonl\n")[1].split("\n```")[0]
    assert [json.loads(line)["scope"] for line in body.splitlines()] == ["subtask 1", "plan-wide"]


@pytest.mark.anyio
async def test_wake_reply_reports_sorted_all_consumed_seqs(tmp_path: Path):
    clock = ControlledClock()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), Models()) as s:
        await ready(s, clock)
        seqs = await triggers(s, detail="x" * 5000)
        await wake_done(s, clock)
        reply = [e.payload for e in s.events() if e.type == "lead.reply"][-1]
        assert reply.woken_by == sorted(seqs) and reply.answers_seq is None
        assert "more)" in owned(s).run.history.notices[-1].text
        with pytest.raises(EventRejected, match="already named"):
            await s.store.publish(
                LeadReply(
                    agent_id="agent-1",
                    text="Reuse",
                    woken_by=seqs,
                    answers_seq=None,
                    through_seq=None,
                    cut=None,
                )
            )


@pytest.mark.anyio
async def test_wake_preserves_tools_accounting_and_per_turn_limits():
    from oversight.agents import LEAD_LIMITS
    from oversight.agents.client import CALL_SCOPE

    tools, propose = setup_tools()
    models = [
        scripted([turn(call("status")), turn(call("finish", reply="Answer"))]) for _ in range(2)
    ]
    tools.status = lambda: (20, "Fenced status")
    limits = LEAD_LIMITS.model_copy(update={"tool_calls": 1})
    run, record, _ = lead(models[0], tools, limits=limits)
    opening = await run.run()
    assert opening.reply and opening.reply.cut == "tool_calls"
    model = models[1]

    def factory():
        scope = CALL_SCOPE.get()
        assert scope is not None and scope.role == "lead"
        return model

    run.make_client = factory
    end = await run.run(notice=ServerNotice("Server notice: data", 12, (12,)), woken_by=[12])
    assert end.reply and end.reply.cut == "tool_calls" and end.reply.woken_by == [12]
    assert run.steps == 2 and run.turn_steps == 1 and run.turns == 1 and record.await_count == 2
    assert [cast(Any, t)["function"]["name"] for t in model.tool_requests[0]] == [
        "graph",
        "list_files",
        "read_file",
        "status",
        "queue",
        "request_plan",
        "finish",
    ]
    propose.assert_not_awaited()
