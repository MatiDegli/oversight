"""H1 preflight and evidence tests use synthetic histories and fake children only."""

import json
from contextlib import nullcontext
from unittest.mock import AsyncMock

import pytest
from tools.eval import arms as arm_module
from tools.eval import bench, planning, server
from tools.eval.arms import Arm, load_arms
from tools.eval.grade import Tariff
from tools.eval.ledger import Ledger, State
from tools.eval.planning import estimate, schedule
from tools.eval.report import report
from tools.eval.session import Session, allowance
from tools.eval.tasks import BenchError, digest, load_tasks
from tools.eval.tests.test_arms import OFF, ON
from tools.eval.tests.test_detection_labels import synthetic_labels
from tools.eval.tests.test_detection_report import fake_pass
from tools.eval.tests.test_planning import pilot
from tools.eval.tests.test_runner import TASK, script
from tools.eval.transport import API, Events

PAID = Arm(arm_id="paid", profile="muse-spark", reasoning="low", harness_pack="on")


def recorded_plan(ledger, *, pilot_start):
    specification = {
        "arms": [PAID.model_dump(mode="json")],
        "task_hashes": {TASK.id: TASK.hash},
        "repetitions": 1,
        "pilot": pilot_start,
    }
    plan_id = digest(json.dumps(specification, sort_keys=True))
    ledger.state.plans[plan_id] = {**specification, "seed": 17}
    ledger.save()
    return plan_id


@pytest.mark.parametrize("pack", ["on", "off"])
@pytest.mark.parametrize("profile", ["fake", "muse-spark"])
def test_both_child_settings_read_pack(tmp_path, monkeypatch, pack, profile):
    import uvicorn

    import oversight.app as product

    arm = PAID.model_copy(
        update={
            "profile": profile,
            "harness_pack": pack,
            "reasoning": None if profile == "fake" else "low",
        }
    )
    for key, value in server.environment(arm, tmp_path, 12345, 20).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("OVERSIGHT_FAKE_SCRIPT", str(tmp_path / "script.json"))
    # Exercise the real Settings.load parser, replacing credential-file I/O entirely.
    monkeypatch.setattr("oversight.settings.read_env_file", lambda path: {})
    captured = []
    monkeypatch.setattr(product, "create_app", lambda settings: captured.append(settings))
    monkeypatch.setattr(uvicorn, "Config", lambda *a, **k: None)
    monkeypatch.setattr(uvicorn.Server, "run", lambda *a, **k: None)
    monkeypatch.setattr(server.socket, "socket", lambda **k: nullcontext())
    monkeypatch.setattr(product, "PROFILES", dict(product.PROFILES))
    server.serve(arm, 0)
    assert captured[0].harness_pack is (pack == "on")
    assert (
        server.environment(arm, tmp_path, 12345, 20)["OVERSIGHT_HARNESS_PACK"]
        == str(pack == "on").lower()
    )


@pytest.mark.anyio
@pytest.mark.parametrize("arm", [OFF, ON], ids=["off", "on"])
async def test_fake_child_advertisement_matches_planned_hash(tmp_path, arm):
    script_path = tmp_path / "script.json"
    script_path.write_text(script())
    async with server.dedicated(arm, tmp_path, 20, script_path) as url:
        api = API(url)
        health = await api.health()
        events = Events(api, health.run_id, tmp_path / "events.jsonl")
        try:
            session = Session(api, events)
            await session.preflight("fake", None)
            assert (
                arm.config_hash(session.run)
                == schedule([arm], [TASK], 1, 0)[0].identity()["config_hash"]
            )
            assert session.run.prompt_version == arm.configuration()["prompt_version"]
        finally:
            await events.close()
            await api.client.aclose()


@pytest.fixture
def gate_inputs(tmp_path, monkeypatch):
    out = tmp_path / "accounting" / "paid" / "results.jsonl"
    monkeypatch.setattr(arm_module, "PAID_OUTPUT", out)
    labels, _ = synthetic_labels(tmp_path)
    pin = digest(labels.read_text())
    paths = []
    for index, choice in enumerate(("asked_for", "not_asked_for")):
        directory = tmp_path / f"pass-{index}"
        directory.mkdir()
        rows = fake_pass(choice, unavailable=0)
        for row in rows:
            row.result.detection.labels_sha256 = pin
        state = State(
            cap_usd=20,
            results_file="results.jsonl",
            tariff=None,
            records=rows,
            plans={"fake": {"kind": "detection", "pilot": False, "labels_sha256": pin}},
        )
        path = directory / "ledger.json"
        path.write_text(state.model_dump_json())
        paths.append(path)
    arms = tmp_path / "arms.json"
    arms.write_text(json.dumps([PAID.model_dump(mode="json")]))
    return out, arms, [*paths, labels], pin


@pytest.mark.parametrize("command", ["pilot", "plan"])
@pytest.mark.parametrize("resume", [False, True])
def test_paid_start_and_resume_require_gate(gate_inputs, monkeypatch, capsys, command, resume):
    out, arms, gate, pin = gate_inputs
    monkeypatch.setattr(bench, "dedicated", lambda *a, **k: pytest.fail("server start"))
    monkeypatch.setattr(bench, "execute", lambda *a, **k: pytest.fail("attempt start"))
    plan_id = None
    if resume:
        ledger = Ledger(out, 20, None)
        try:
            plan_id = recorded_plan(ledger, pilot_start=command == "pilot")
        finally:
            ledger.close()
    runner = AsyncMock(return_value={"accepted": True})
    monkeypatch.setattr(bench, "run_plan", runner)
    args = [command, "--arms", str(arms), "--out", str(out), "--task", TASK.id]
    assert bench.main(args) == 1
    assert "require --gate" in capsys.readouterr().err
    runner.assert_not_called()
    assert bench.main([*args, "--gate", *map(str, gate), "--labels-sha256", pin]) == 0
    runner.assert_awaited_once()
    if resume:
        assert plan_id is not None
        assert plan_id in State.model_validate_json((out.parent / "ledger.json").read_bytes()).plans


@pytest.mark.parametrize(
    "defect,reason",
    [
        ("threshold", "failed_conditions"),
        ("sha", "SHA-256 mismatch"),
        ("pin", "labels pin"),
        ("plan_pin", "labels pin"),
        ("missing", "existing"),
        ("same", "distinct"),
        ("inside", "outside the paid directory"),
        ("cap", "at most 20"),
        ("pinned_cap", "pinned above 20"),
    ],
)
def test_gate_refuses_before_any_start(gate_inputs, monkeypatch, capsys, defect, reason):
    out, arms, gate, pin = gate_inputs
    cap = "20"
    if defect in {"threshold", "pin", "plan_pin"}:
        state = State.model_validate_json(gate[0].read_bytes())
        assert state.records[0].result is not None
        assert state.records[0].result.detection is not None
        assert state.records[-1].result is not None
        assert state.records[-1].result.detection is not None
        if defect == "threshold":
            state.records[-1].result.detection.let_through = False
        elif defect == "pin":
            state.records[0].result.detection.labels_sha256 = "wrong"
        else:
            state.plans["fake"]["labels_sha256"] = "wrong"
        gate[0].write_text(state.model_dump_json())
    elif defect == "sha":
        pin = "0" * 64
    elif defect == "missing":
        gate[0].unlink()
    elif defect == "same":
        gate[1] = gate[0]
    elif defect == "inside":
        out.parent.mkdir(parents=True)
        moved = out.parent / "nested" / "ledger.json"
        moved.parent.mkdir()
        moved.write_bytes(gate[0].read_bytes())
        gate[0] = moved
    elif defect == "cap":
        cap = "21"
    else:
        out.parent.mkdir(parents=True)
        (out.parent / "ledger.json").write_text(
            State(cap_usd=25, results_file=out.name, tariff=None).model_dump_json()
        )
    runner = AsyncMock()
    monkeypatch.setattr(bench, "run_plan", runner)
    assert (
        bench.main(
            [
                "pilot",
                "--arms",
                str(arms),
                "--out",
                str(out),
                "--cap-usd",
                cap,
                "--gate",
                *map(str, gate),
                "--labels-sha256",
                pin,
            ]
        )
        == 1
    )
    assert reason in capsys.readouterr().err
    runner.assert_not_called()


@pytest.mark.parametrize("arm", [OFF, ON, PAID.model_copy(update={"harness_pack": "off"})])
def test_fake_and_pack_off_start_need_no_gate(gate_inputs, monkeypatch, arm):
    out, arms, _, _ = gate_inputs
    arms.write_text(json.dumps([arm.model_dump(mode="json")]))
    runner = AsyncMock(return_value={})
    monkeypatch.setattr(bench, "run_plan", runner)
    assert bench.main(["pilot", "--arms", str(arms), "--out", str(out)]) == 0
    runner.assert_awaited_once()


@pytest.mark.parametrize(
    "field,value",
    [("profile", "muse-spark-open"), ("reasoning", "high"), ("tariff", {"input": 1, "output": 2})],
)
def test_pair_identity_names_differing_field(tmp_path, field, value):
    plain = PAID.model_copy(update={"arm_id": "plain", "harness_pack": "off"})
    other = PAID.model_dump(mode="json") | {field: value}
    path = tmp_path / "arms.json"
    path.write_text(json.dumps([plain.model_dump(mode="json"), other]))
    with pytest.raises(BenchError, match=field):
        load_arms(path, [])
    assert load_arms(path, [plain.arm_id]) == [plain]


def test_failure_table_uses_only_events_and_preserves_detail(tmp_path):
    ledger = Ledger(tmp_path / "results.jsonl", 20, None)
    try:
        left, right = pilot(ledger), pilot(ledger, ON, task=load_tasks()[1])
        left.agent_id, right.agent_id = "agent-a", "agent-b"
        ledger.state.plans["pair"] = {"arms": [a.model_dump(mode="json") for a in (OFF, ON)]}
        steps = [
            ("finish", "error", None),
            ("edit_file", "error", None),
            ("run_tests", "ok", {"outcome": "failed"}),
            ("run_tests", "ok", {"outcome": "passed"}),
            ("finish", "ok", None),
        ]
        events = [
            {
                "type": "agent.step",
                "payload": {
                    "agent_id": "agent-a",
                    "tool": tool,
                    "result": result,
                    "tests": tests,
                    "summary": "passed",
                    "error": "failed",
                },
            }
            for tool, result, tests in steps
        ]
        detail = "Exact reason: a novel failure.\nDo not classify this."
        events += [
            {
                "type": "agent.status",
                "payload": {"agent_id": "agent-a", "state": "halted", "detail": detail},
            },
            {
                "type": "agent.status",
                "payload": {"agent_id": "agent-a", "state": "done", "detail": None},
            },
            {
                "type": "agent.step",
                "payload": {
                    "agent_id": "other",
                    "tool": "edit_file",
                    "result": "error",
                    "tests": None,
                },
            },
        ]
        (tmp_path / "events-pilot.jsonl").write_text(
            "".join(json.dumps({"run_id": "pilot", "event": e}) + "\n" for e in events)
        )
        table = report(ledger)["failure_table"]
        assert table["measurement"] == "error kind: not measured"
        row = table["arms"][OFF.arm_id][TASK.id][0]
        assert row["pairing"] == "unpaired"
        assert table["arms"][ON.arm_id][TASK.id] == "unpaired"
        assert row["tool_calls"]["finish"] == {"ok": 1, "error": 1}
        assert row["edit_file_errors"] == row["run_tests_failed"] == 1
        assert row["finish_with_passing_tests"] == row["finish_without_passing_tests"] == 1
        assert row["terminal_status"] == {"state": "halted", "detail": detail}
        assert "error_kind" not in json.dumps(table)
        ledger.state.records[-1] = right.model_copy(
            update={"task_id": left.task_id, "task_hash": left.task_hash}
        )
        assert (
            report(ledger)["failure_table"]["arms"][OFF.arm_id][TASK.id][0]["pairing"] == "paired"
        )
    finally:
        ledger.close()


def test_estimate_is_read_only_and_combines_pending_once(tmp_path, monkeypatch, capsys):
    out = tmp_path / "accounting" / "paid" / "results.jsonl"
    ledger = Ledger(out, 20, None)
    pilot(ledger, PAID, cost=2)
    state = ledger.state
    ledger.close()
    before = {p.name: p.read_bytes() for p in out.parent.iterdir()}
    arm = PAID.model_copy(update={"harness_pack": "off", "arm_id": "plain"})
    jobs = schedule([arm], [TASK], 2, 0)
    detection = tmp_path / "detection.json"
    detection.write_text(
        json.dumps(
            {"ledger": str(out.parent / "ledger.json"), "spent_usd": 2, "estimated_total_usd": 5}
        )
    )
    result = estimate(out, state, jobs, detection)
    assert result["attempts"] == 2
    assert result["rate_basis"][arm.arm_id] == "default planning rates 0.1/0.2 USD per M, assumed"
    assert result["pending_total_usd"] == pytest.approx(2 * allowance(arm.profile, None))
    assert result["combined_total_usd"] == pytest.approx(2 + result["pending_total_usd"] + 3)
    assert result["within_usd20"] and result["remaining_usd"] == 18
    assert estimate(out, state, schedule([PAID], [TASK], 2, 0), None)[
        "pending_total_usd"
    ] == allowance(PAID.profile, None)
    assert (
        estimate(out, None, schedule([OFF], [TASK], 1, 0), None)["rate_basis"][OFF.arm_id]
        == "arm tariff, floored at the default-rate allowance"
    )
    detection.write_text(
        json.dumps({"ledger": str(tmp_path / "foreign"), "spent_usd": 2, "estimated_total_usd": 5})
    )
    with pytest.raises(BenchError, match="foreign ledger"):
        estimate(out, state, jobs, detection)
    assert {name: (out.parent / name).read_bytes() for name in before} == before
    arms = tmp_path / "arms.json"
    arms.write_text(json.dumps([arm.model_dump(mode="json")]))
    monkeypatch.setattr(arm_module, "PAID_OUTPUT", out)
    monkeypatch.setattr(bench, "dedicated", lambda *a, **k: pytest.fail("server start"))
    monkeypatch.setattr(bench, "Ledger", lambda *a, **k: pytest.fail("ledger write"))
    from tools.eval.run import main

    assert main(["estimate", "--arms", str(arms), "--out", str(out), "--task", TASK.id]) == 0
    assert json.loads(capsys.readouterr().out)["method"] == "estimate, not a billing ceiling"


@pytest.mark.anyio
@pytest.mark.parametrize("pilot_start", [True, False])
async def test_run_plan_itself_requires_gate_before_resume(tmp_path, monkeypatch, pilot_start):
    monkeypatch.setattr(bench, "dedicated", lambda *a, **k: pytest.fail("server start"))
    monkeypatch.setattr(bench, "execute", lambda *a, **k: pytest.fail("attempt start"))
    ledger = Ledger(tmp_path / "results.jsonl", 20, None)
    try:
        plan_id = recorded_plan(ledger, pilot_start=pilot_start)
        before = ledger.path.read_bytes()
        with pytest.raises(BenchError, match="require --gate"):
            await bench.run_plan(
                ledger, [PAID], [TASK], 1, None, pilot=pilot_start, fake_script=None
            )
        assert plan_id in ledger.state.plans and not ledger.state.records
        assert ledger.path.read_bytes() == before
    finally:
        ledger.close()


@pytest.mark.parametrize("unknown,unsettled", [(True, False), (False, True), (True, True)])
def test_estimate_never_certifies_incomplete_costs(tmp_path, unknown, unsettled):
    out = tmp_path / "results.jsonl"
    ledger = Ledger(out, 20, None)
    try:
        record = pilot(ledger)
        record.unknown = unknown
        record.status = "reserved" if unsettled else "finished"
        detection = tmp_path / "detection.json"
        detection.write_text(
            json.dumps({"ledger": str(ledger.path), "spent_usd": 0, "estimated_total_usd": 1})
        )
        result = estimate(out, ledger.state, schedule([OFF], [TASK], 2, 0), detection)
        assert result["within_usd20"] == "unknown"
        assert result["unknown_costs"] == int(unknown)
        assert result["unsettled_reservations"] == int(unsettled)
        assert result["spent_usd"] == record.charged_usd
    finally:
        ledger.close()


@pytest.mark.parametrize(
    "rates,floored", [(None, False), ((0, 0), True), ((0.05, 0.1), True), ((1, 2), False)]
)
def test_estimate_labels_the_reservation_rate_basis(tmp_path, rates, floored):
    tariff = None if rates is None else Tariff(input=rates[0], output=rates[1])
    arm = OFF.model_copy(update={"tariff": tariff})
    result = estimate(tmp_path / "results.jsonl", None, schedule([arm], [TASK], 1, 0), None)
    basis = result["rate_basis"][arm.arm_id]
    assert ("floored at the default-rate allowance" in basis) is floored
    if rates is None:
        assert basis == "default planning rates 0.1/0.2 USD per M, assumed"
    elif not floored:
        assert basis == "arm tariff"


@pytest.mark.parametrize("rates,floored", [((0.2, 0), True), ((0, 0.3), False)])
def test_estimate_rate_basis_follows_allowance_assumptions(tmp_path, monkeypatch, rates, floored):
    floor = allowance("fake", Tariff(input=0, output=0))
    # Model a changed token mix: only output contributes above the reservation floor.
    monkeypatch.setattr(
        planning, "allowance", lambda model, tariff: max(floor, tariff.output if tariff else 0)
    )
    arm = PAID.model_copy(update={"tariff": Tariff(input=rates[0], output=rates[1])})
    result = estimate(tmp_path / "results.jsonl", None, schedule([arm], [TASK], 1, 0), None)
    basis = result["rate_basis"][arm.arm_id]
    assert ("floored at the default-rate allowance" in basis) is floored
    reservation = result["reservations"][0]["reservation_usd"]
    assert (reservation == floor) is floored
