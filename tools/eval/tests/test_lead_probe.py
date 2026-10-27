"""Scripted clients only: no provider transport or credentials are used."""

import json
from dataclasses import replace

import pytest
from tools.eval import lead_probe as probe
from tools.eval.grade import Tariff
from tools.eval.ledger import Ledger, Record
from tools.eval.tasks import BenchError

from oversight.agents.client import MUSE_SPARK, ModelReply, ToolCall, Usage
from oversight.settings import Settings
from oversight_contracts.graph import GraphSnapshot


@pytest.fixture
async def graph():
    return await probe.demo_graph()


@pytest.fixture
def snapshot():
    # Only validation/accounting tests use this small graph; integration scans the real template.
    return GraphSnapshot(
        version="v1", commit="demo", nodes=[], edges=[], parse_errors=[], positions={}
    )


def plan(graph):
    paths = sorted({n.path for n in graph.nodes if not n.is_test})[:2]
    return {
        "subtasks": [
            {"task": f"Improve {path}", "zone": {"node_ids": [], "paths": [path]}} for path in paths
        ]
    }


def reply(name, args, usage=None):
    return ModelReply(None, [ToolCall("tool-1", name, json.dumps(args))], usage or Usage(100, 50))


def reaction(scenario):
    calls = []
    for change in scenario["changes"]:
        paths = [
            s.split(" ", 1)[1].split(":", 1)[0]
            for s in change["review"]["findings"]
            if s.startswith(("BLOCKING ", "SHOULD-FIX "))
        ]
        if paths:
            calls.append(
                ToolCall(
                    f"t-{len(calls)}",
                    "request_changes",
                    json.dumps(
                        {
                            "change_id": change["change_id"],
                            "comment": "Please fix " + ", ".join(paths),
                        }
                    ),
                )
            )
    calls.append(ToolCall("wait", "wait", "{}"))
    return ModelReply(None, calls, Usage(100, 50))


class Script:
    profile = MUSE_SPARK

    def __init__(self, *turns):
        self.turns = list(turns)
        self.calls = []

    async def complete(self, messages, tools):
        self.calls.append((list(messages), tools))
        assert self.turns, "Unexpected extra model call"
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn


@pytest.mark.anyio
async def test_five_run_gate_scans_real_demo_and_records_all_outputs(tmp_path, graph):
    selected = probe.scenarios()
    script = Script(
        *(turn for s in selected for turn in [reply("propose_plan", plan(graph)), reaction(s)])
    )
    summary = await probe.execute(script, tmp_path, selected, graph, Tariff(input=1, output=2))
    assert summary["verdict"] == "PASS"
    assert summary["plans_valid"] == summary["reactions_correct"] == 5
    assert len(script.calls) == 10
    assert summary["total_cost_usd"] == pytest.approx(0.002)
    outputs = [json.loads(p.read_text()) for p in tmp_path.glob("run-*.json")]
    assert len(outputs) == 5
    assert all(r["tokens"] == {"input": 200, "output": 100} for r in outputs)
    assert all(r["cost_usd"] == pytest.approx(0.0004) for r in outputs)
    assert not (probe.REPO / "demo-repo" / ".git").exists()
    assert all(c[1] == probe.PLAN_TOOLS for c in script.calls[::2])
    assert all(c[1] == probe.REACTION_TOOLS for c in script.calls[1::2])
    assert graph.nodes and any(e.kind == "calls" for e in graph.edges)


@pytest.mark.anyio
async def test_plans_valid_overlap_arity_and_unparseable(graph):
    valid = plan(graph)
    assert probe.validate_plan(json.dumps(valid), graph)[1] == []
    valid["subtasks"][1]["zone"] = valid["subtasks"][0]["zone"]
    assert "path_overlap" in {p["code"] for p in probe.validate_plan(json.dumps(valid), graph)[1]}
    for invalid in (
        "{",
        "null",
        "[]",
        json.dumps({"subtasks": []}),
        json.dumps({"subtasks": plan(graph)["subtasks"][:1]}),
        json.dumps({"subtasks": plan(graph)["subtasks"] * 2}),
    ):
        assert probe.validate_plan(invalid, graph) == (None, [{"code": "invalid_arguments"}])


@pytest.mark.anyio
async def test_invalid_plan_can_be_corrected_in_tool_loop(tmp_path, graph):
    scenario = probe.scenarios()[0]
    script = Script(
        ModelReply(None, [ToolCall("bad", "propose_plan", "{")], Usage(10, 10)),
        reply("propose_plan", plan(graph)),
        reaction(scenario),
    )
    await probe.execute(script, tmp_path, [scenario], graph, Tariff(input=1, output=1))
    result = json.loads(next(tmp_path.glob("run-*.json")).read_text())
    assert result["plan_valid"] and result["reaction"]["correct"]
    assert script.calls[1][0][-1]["role"] == "tool"
    assert "invalid_arguments" in script.calls[1][0][-1]["content"]


@pytest.mark.anyio
@pytest.mark.parametrize("index", range(5))
@pytest.mark.parametrize("wrong", [False, True])
async def test_each_reaction_right_and_wrong(index, wrong, tmp_path, graph):
    scenario = probe.scenarios()[index]
    correct = reaction(scenario)
    if wrong:
        # Missing required requests, or an unsolicited request in the NIT-only case.
        correct = (
            reply("request_changes", {"change_id": "A", "comment": "shop/models.py"})
            if index == 3
            else reply("wait", {})
        )
    script = Script(reply("propose_plan", plan(graph)), correct, reply("wait", {}))
    await probe.execute(script, tmp_path, [scenario], graph, Tariff(input=1, output=1))
    result = json.loads(next(tmp_path.glob("run-*.json")).read_text())
    assert result["reaction"]["correct"] is not wrong


@pytest.mark.anyio
@pytest.mark.parametrize(
    "calls",
    [
        [("request_changes", {"change_id": "A", "comment": "shop/pricing.py"})],
        [
            (
                "request_changes",
                {"change_id": "A", "comment": "shop/pricing.py tests/test_pricing.py"},
            )
        ]
        * 2,
        [("request_changes", {"change_id": "unknown", "comment": "all fixed"})],
        [("approve", {"change_id": "A"})],
        [("request_changes", {"change_id": "A"})],
    ],
)
async def test_reaction_rejects_missing_path_duplicates_and_invalid_calls(calls, tmp_path, graph):
    scenario = probe.scenarios()[0]
    turn = ModelReply(
        None,
        [ToolCall(str(i), name, json.dumps(args)) for i, (name, args) in enumerate(calls)]
        + [ToolCall("wait", "wait", "{}")],
        Usage(100, 50),
    )
    await probe.execute(
        Script(reply("propose_plan", plan(graph)), turn),
        tmp_path,
        [scenario],
        graph,
        Tariff(input=1, output=1),
    )
    assert not json.loads(next(tmp_path.glob("run-*.json")).read_text())["reaction"]["correct"]


@pytest.mark.anyio
async def test_read_change_returns_fixed_review(tmp_path, graph):
    scenario = probe.scenarios()[0]
    script = Script(
        reply("propose_plan", plan(graph)),
        reply("read_change", {"change_id": "A"}),
        reaction(scenario),
    )
    await probe.execute(script, tmp_path, [scenario], graph, Tariff(input=1, output=1))
    content = json.loads(script.calls[2][0][-1]["content"])
    assert content == scenario["changes"][0]


@pytest.mark.anyio
async def test_six_calls_per_part(tmp_path, graph):
    scenario = probe.scenarios()[0]
    script = Script(
        *([reply("propose_plan", {})] * 6), *([reply("read_change", {"change_id": "A"})] * 6)
    )
    summary = await probe.execute(
        script, tmp_path, [scenario], graph, Tariff(input=0.01, output=0.01)
    )
    assert len(script.calls) == 12
    assert summary["verdict"] == "FAIL"


@pytest.mark.anyio
async def test_run_cap_stops_before_call_and_next_run_can_start(tmp_path, snapshot):
    ledger = Ledger(tmp_path / "accounting.jsonl", probe.TOTAL_CAP, Tariff(input=1, output=1))
    try:
        budget = probe.Budget(ledger, "run")
        budget.cost = probe.RUN_CAP - 0.0001
        script = Script()
        with pytest.raises(BenchError, match="per-run cap"):
            await budget.complete(script, [], [])
        assert not script.calls and not ledger.state.records
        probe.Budget(ledger, "next").reserve([], [])
    finally:
        ledger.close()


@pytest.mark.anyio
@pytest.mark.parametrize("existing", [False, True])
async def test_total_cap_including_existing_ledger(existing, tmp_path):
    tariff = Tariff(input=1, output=1)
    ledger = Ledger(tmp_path / "accounting.jsonl", probe.TOTAL_CAP, tariff)
    ledger.state.records.append(
        Record(
            task_id="prior",
            task_hash="hash",
            profile="muse-spark",
            run_id="old",
            allowance_usd=0.499,
            charged_usd=0.499,
            status="finished",
        )
    )
    ledger.save()
    if existing:
        ledger.close()
        ledger = Ledger(tmp_path / "accounting.jsonl", probe.TOTAL_CAP, tariff)
    try:
        script = Script()
        with pytest.raises(BenchError, match="total cap"):
            await probe.Budget(ledger, "new").complete(script, [], [])
        assert not script.calls
        assert ledger.spent == 0.499
    finally:
        ledger.close()


@pytest.mark.anyio
@pytest.mark.parametrize("usage", [Usage(), Usage(None, 50), Usage(100, None)])
async def test_unknown_usage_stops_whole_probe_and_blocks_rerun(usage, tmp_path, graph):
    script = Script(reply("propose_plan", plan(graph), usage))
    summary = await probe.execute(
        script, tmp_path, probe.scenarios(), graph, Tariff(input=1, output=1)
    )
    assert len(script.calls) == 1
    assert summary["verdict"] == "FAIL" and summary["total_cost_usd"] == "unknown"
    result = json.loads(next(tmp_path.glob("run-*.json")).read_text())
    assert result["cost_usd"] == "unknown"
    assert "unknown" in result["tokens"].values()
    rerun = Script()
    await probe.execute(rerun, tmp_path, probe.scenarios(), graph, Tariff(input=1, output=1))
    assert not rerun.calls


@pytest.mark.anyio
async def test_call_error_and_crash_reservation_block_reruns(tmp_path, snapshot):
    ledger = Ledger(tmp_path / "accounting.jsonl", probe.TOTAL_CAP, Tariff(input=1, output=1))
    probe.Budget(ledger, "interrupted").reserve([], [])
    ledger.close()
    script = Script()
    summary = await probe.execute(
        script, tmp_path, probe.scenarios(), snapshot, Tariff(input=1, output=1)
    )
    assert not script.calls and summary["total_cost_usd"] == "unknown"


def test_dry_run_creates_no_client_or_settings(monkeypatch, tmp_path, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("dry-run loaded settings or created a client")

    monkeypatch.setattr(probe, "make_client", forbidden)
    monkeypatch.setattr(probe.Settings, "load", forbidden)
    assert probe.main(["--dry-run", "--out", str(tmp_path / "out")]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert len(printed) == 5 and printed[0]["prompts"]["plan"]
    assert not (tmp_path / "out").exists()


def test_product_client_explicit_reasoning_and_no_hidden_retries(monkeypatch):
    received = {}

    def capture(profile, **kwargs):
        received.update(profile=profile, **kwargs)

    monkeypatch.setattr(probe, "OpenAIClient", capture)
    probe.make_client(replace(Settings(), meta_api_key="test-placeholder"))
    assert received["profile"].reasoning == "low"
    assert received["retry"].max_attempts == 1
    assert received["max_output_tokens"] == probe.MAX_OUTPUT


@pytest.mark.anyio
@pytest.mark.parametrize("failed", [1, 2])
async def test_gate_requires_four_of_five_plans_and_reactions(failed, tmp_path, graph):
    turns = []
    for index, scenario in enumerate(probe.scenarios()):
        turns.extend(
            [ModelReply("No plan", [], Usage(100, 50)), reply("wait", {})]
            if index < failed
            else [reply("propose_plan", plan(graph)), reaction(scenario)]
        )
    summary = await probe.execute(
        Script(*turns), tmp_path, probe.scenarios(), graph, Tariff(input=1, output=1)
    )
    assert summary["plans_valid"] == summary["reactions_correct"] == 5 - failed
    assert summary["verdict"] == ("PASS" if failed == 1 else "FAIL")


@pytest.mark.anyio
async def test_unknown_call_error_never_records_zero_tokens(tmp_path, graph):
    script = Script(RuntimeError("provider failure"))
    summary = await probe.execute(
        script, tmp_path, probe.scenarios(), graph, Tariff(input=1, output=1)
    )
    assert len(script.calls) == 1 and summary["total_cost_usd"] == "unknown"
    result = json.loads(next(tmp_path.glob("run-*.json")).read_text())
    assert result["tokens"] == {"input": "unknown", "output": "unknown"}
    assert result["cost_usd"] == "unknown"


@pytest.mark.anyio
@pytest.mark.parametrize("cap", ["run", "total"])
async def test_call_reaching_cap_is_refused(cap, tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / "accounting.jsonl", probe.TOTAL_CAP, Tariff(input=1, output=1))
    try:
        wire = json.dumps({"messages": [], "tools": []}, ensure_ascii=False)
        allowance = (len(wire.encode()) + 4096 + probe.MAX_OUTPUT) / 1e6
        budget = probe.Budget(ledger, "boundary")
        monkeypatch.setattr(probe, "RUN_CAP" if cap == "run" else "TOTAL_CAP", allowance)
        with pytest.raises(BenchError, match="cap cannot reserve"):
            budget.reserve([], [])
        assert not ledger.state.records
    finally:
        ledger.close()


@pytest.mark.anyio
async def test_provider_exceeding_reservation_stops_probe(tmp_path, graph):
    script = Script(reply("propose_plan", plan(graph), Usage(100, probe.MAX_OUTPUT + 1)))
    summary = await probe.execute(
        script, tmp_path, probe.scenarios(), graph, Tariff(input=1, output=1)
    )
    assert len(script.calls) == 1 and summary["total_cost_usd"] == "unknown"
    assert summary["verdict"] == "FAIL"
