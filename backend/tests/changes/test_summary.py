"""Server summaries use captured observations, never an author's finish prose."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from changes_helpers import EDIT, TESTS, Harness
from live_helpers import Scripts, is_type, live_server, live_settings
from pydantic import TypeAdapter

from oversight.changes._facts import FactsEvidence, build_change_facts, validate_change_facts
from oversight.workspace import SuiteRun
from oversight_contracts.events import CaseResult, ServerEvent, SuiteExecution, SuiteResult
from oversight_contracts.review import ChangeFacts, ChangeTarget, FinishDeclaration


def evidence() -> FactsEvidence:
    return FactsEvidence(
        "agent-1",
        ChangeTarget(
            change_id="change-1",
            revision=1,
            base_commit="base",
            head_commit="head",
            main_commit="base",
            candidate_tree="tree",
        ),
        FinishDeclaration(tests_run=True, tests=["quasar-finishing-claim"]),
        (),
        (),
        ("Unavailable producers",),
        {},
        {},
    )


def test_unknowns_and_no_declared_text() -> None:
    captured = evidence()
    facts = build_change_facts(captured)
    assert facts.summary is not None and len(facts.summary) == 3
    assert "cost unknown" in facts.summary[2]
    assert "not compared" in facts.summary[2]
    assert "quasar-finishing-claim" not in " ".join(facts.summary)
    assert "tests added: unknown" in facts.summary[1]
    assert "tests run: unknown" in facts.summary[1]
    other = replace(captured, declared=FinishDeclaration(tests_run=False))
    assert build_change_facts(other).summary == facts.summary


@pytest.mark.parametrize("summary", [None, ["Invented result"], ["Task: altered", "Edits", "$0"]])
def test_validator_rejects_missing_or_forged_summary(summary) -> None:
    captured = evidence()
    facts = build_change_facts(captured).model_copy(update={"summary": summary})
    with pytest.raises(ValueError, match="summary"):
        validate_change_facts(facts, captured)


@pytest.mark.anyio
async def test_actual_edits_and_execution_counts_ignore_claims(h: Harness) -> None:
    tree = await h.open(1)
    assert await h.edit(1, *EDIT)
    # Restored paths remain in edited_paths, but are not actual candidate edits.
    assert await h.edit(1, "shop/mod.py", "return 3", "return 1")
    old = (await tree.read_text(TESTS)).content
    assert await h.edit(1, TESTS, old, old + "\n\ndef test_added():\n    assert True\n")
    declaration = FinishDeclaration(tests_run=False, tests=["quasar-finishing-claim"])
    assert await h.manager.proposed("agent-1", "nebula-report-only", declaration=declaration)
    [facts] = h.payloads(ChangeFacts)
    assert facts.summary is not None
    assert facts.summary[0] == "Task: Do it."
    assert "1 files, 1 nodes" in facts.summary[1]
    assert "tests added: 1" in facts.summary[1]
    assert "tests run: 2 (2 passed, 0 failed, 0 skipped; passed)" in facts.summary[1]
    assert "not compared" in facts.summary[2] and "cost unknown" in facts.summary[2]
    assert not any(
        word in " ".join(facts.summary)
        for word in (
            "quasar-finishing-claim",
            "nebula-report-only",
        )
    )
    assert facts.behavior_diff is None and facts.measured_cone is None


@pytest.mark.anyio
async def test_fake_model_proposal_has_contract_valid_summary(tmp_path: Path) -> None:
    async with live_server(live_settings(tmp_path), Scripts()) as server:
        response = await server.client.post("/agents", json={"task": "Document apply_coupon."})
        assert response.status_code == 201
        agent_id = response.json()["agent_id"]
        await server.until(is_type("agent.status", agent_id=agent_id, state="ready_for_review"))
        [event] = [e for e in server.events() if e.type == "change.facts_recorded"]
        facts = event.payload
        assert facts.summary is not None and 1 <= len(facts.summary) <= 3
        assert facts.summary[0] == "Task: Document apply_coupon."
        assert "cost unknown" in facts.summary[2]
        assert TypeAdapter(ServerEvent).validate_json(event.model_dump_json()) == event


def test_long_task_and_many_files_keep_caps_and_line_order() -> None:
    from oversight.changes._summary import SummaryEvidence

    captured = replace(
        evidence(),
        summary_evidence=SummaryEvidence(
            task="Requested task\r\n" + "long task " * 1000,
            changed_paths=tuple(f"{'x' * 200}/{i}.py" for i in range(1000)),
            changed_nodes=tuple(f"module:function_{i}" for i in range(2000)),
            added_test_ids=(),
        ),
    )
    facts = build_change_facts(captured)
    assert facts.summary is not None and len(facts.summary) == 3
    task, edits, results = facts.summary
    assert task.startswith("Task: Requested task ") and task.endswith("…")
    assert edits.startswith("Changed: 1000 files, 2000 nodes;")
    assert results.startswith("Other results: not compared;")
    assert all(
        0 < len(line) <= 240 and "\n" not in line and "\r" not in line for line in facts.summary
    )
    assert sum(map(len, facts.summary)) <= 720
    assert ChangeFacts.model_validate(facts.model_dump()) == facts
    assert build_change_facts(captured).summary == facts.summary


@pytest.mark.parametrize("outcome", ["passed", "failed", "error", "timeout"])
def test_tests_run_use_execution_outcome(outcome) -> None:
    from oversight.changes._summary import SummaryEvidence

    cases = [CaseResult(test_id="tests/test_a.py::test_a", outcome="failed")]
    suite = SuiteResult(
        outcome=outcome,
        passed=0,
        failed=1,
        skipped=0,
        duration_ms=10,
        failed_tests=[cases[0].test_id],
        cases=cases,
        cases_complete=True,
    )
    execution = SuiteExecution(
        change_id="change-1",
        agent_id="agent-1",
        revision=1,
        phase="proposal",
        tested_commit="head",
        tested_tree="tree",
        suite=suite,
    )
    run = SuiteRun(suite, "", tuple(cases), (), "tree", "head")
    captured = replace(
        evidence(),
        executions={10: execution},
        runs={10: run},
        summary_evidence=SummaryEvidence(added_test_ids=()),
    )
    facts = build_change_facts(captured)
    assert facts.summary is not None
    assert f"tests run: 1 (0 passed, 1 failed, 0 skipped; {outcome})" in facts.summary[1]


@pytest.mark.parametrize("costs", [(0.12, 0.03), (0.0,), (0.12, None)])
@pytest.mark.anyio
async def test_usage_receipts_are_attributed_and_unknown_is_not_zero(h: Harness, costs) -> None:
    from oversight_contracts.events import UsageTick

    await h.open(1)
    assert await h.edit(1, *EDIT)
    tick = None
    for index, cost in enumerate(costs):
        tick = UsageTick(
            agent_id="agent-1",
            role="agent" if index == 0 else "review",
            change_id="change-1",
            model="fake",
            input_tokens=None,
            output_tokens=None,
            cached_tokens=None,
            cost_usd=cost,
            spent_usd=999,
            budget_usd=1000,
            brake="ok",
        )
        receipt = await h.publish(tick)
        h.manager.usage_recorded(receipt)
        h.manager.usage_recorded(receipt)  # Receipt redelivery is idempotent.
    assert tick is not None
    foreign = tick.model_copy(update={"change_id": "change-2", "cost_usd": 99})
    h.manager.usage_recorded(await h.publish(foreign))
    assert await h.finish(1)
    [facts] = h.payloads(ChangeFacts)
    assert facts.summary is not None
    expected = "cost unknown" if None in costs else f"cost ${sum(costs):.6g}"
    assert expected in facts.summary[2]
    if None in costs:
        assert "$0" not in facts.summary[2]


@pytest.mark.anyio
async def test_fake_model_known_usage_reaches_summary(tmp_path: Path, monkeypatch) -> None:
    from live_helpers import script_json

    from oversight.agents import Pricing
    from oversight.app import LiveRun

    monkeypatch.setattr(LiveRun, "_price_for", lambda *_: Pricing(1, 1, 1))
    turns: list[dict[str, Any]] = [
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "shop/pricing.py"}}]},
        {
            "tool_calls": [
                {
                    "name": "edit_file",
                    "arguments": {
                        "path": "shop/pricing.py",
                        "old_text": "base = unit_price * quantity",
                        "new_text": "base = quantity * unit_price",
                    },
                }
            ]
        },
        {"tool_calls": [{"name": "finish", "arguments": {"summary": "nebula-report-only"}}]},
    ]
    for turn in turns:
        turn["usage"] = {"input_tokens": 1000, "output_tokens": 100, "cached_tokens": 0}
    scripts = Scripts([script_json("Reorder multiplication.", turns)])
    async with live_server(live_settings(tmp_path), scripts) as server:
        response = await server.client.post("/agents", json={"task": "Reorder multiplication."})
        assert response.status_code == 201
        await server.until(is_type("agent.status", state="ready_for_review"))
        [event] = [e for e in server.events() if e.type == "change.facts_recorded"]
        assert event.payload.summary[2] == "Other results: not compared; cost $0.0033"
        assert "nebula-report-only" not in " ".join(event.payload.summary)
