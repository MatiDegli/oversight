"""Projection and accounting tests never construct a paid model client."""

import json

import pytest
from tools.eval import arms as arm_module
from tools.eval import bench
from tools.eval.arms import Arm, output_path
from tools.eval.grade import Result, Tariff, Tokens
from tools.eval.grade import Tests as SuiteResults
from tools.eval.ledger import Ledger
from tools.eval.planning import project, schedule
from tools.eval.report import report
from tools.eval.server import environment, runtime_directory
from tools.eval.tasks import BenchError, load_tasks
from tools.eval.tests.test_arms import OFF, ON
from tools.eval.tests.test_runner import TASK, script


def pilot(ledger, arm=OFF, *, cost=0.1, tokens=True, unknown=False, task=TASK):
    record = ledger.reserve(
        task,
        arm.profile,
        "pilot",
        0.8,
        arm_id=arm.arm_id,
        config_hash=arm.config_hash(),
        tariff=arm.tariff,
        pilot=True,
    )
    ledger.charge(record, cost)
    if unknown:
        ledger.charge(record, "unknown")
    result = Result(
        arm_id=record.arm_id,
        repetition=record.repetition,
        config_hash=record.config_hash,
        attempt_id=record.attempt_id,
        profile=record.profile,
        task_id=task.id,
        agent_model="fake-scripted",
        outcome="solved",
        turns=3,
        steps=3,
        duration_s=2,
        tokens=Tokens(input=100 if tokens else "unknown", output=20),
        cost_usd="unknown" if unknown else cost,
        test_result=SuiteResults(baseline="passed", hidden="passed", policy="passed"),
    )
    ledger.finish(record, result, cleaned=True)
    return record


def test_projection_counts_all_prior_spend_and_refuses_over_cap(tmp_path):
    ledger = Ledger(tmp_path / "results.jsonl", 1, None)
    try:
        pilot(ledger, cost=0.2)
        pilot(ledger, ON, cost=0.3)
        jobs = schedule([OFF, ON], [TASK], 2, 21)
        projection = project(ledger, jobs)
        assert projection["spent_usd"] == 0.5
        assert projection["remaining_usd"] == 0.5
        assert projection["projected_total_usd"] == 1
        with pytest.raises(BenchError, match="exceeds cap"):
            project(ledger, schedule([OFF, ON], [TASK], 3, 21))
        assert len(ledger.state.records) == 2
    finally:
        ledger.close()


def test_projection_unknown_cost_requires_explicit_tariff_and_known_tokens(tmp_path):
    ledger = Ledger(tmp_path / "results.jsonl", 1, None)
    # Start without a tariff; later explicit pricing informs the projection, preserving history.
    arm = OFF.model_copy(update={"tariff": None})
    record = pilot(ledger, arm, cost=0.01, unknown=True)
    before = ledger.path.read_bytes()
    try:
        with pytest.raises(BenchError, match="unknown tariff/cost"):
            project(ledger, schedule([arm], [TASK], 2, 0))
        priced_arm = arm.model_copy(update={"tariff": Tariff(input=100, output=200)})
        estimate = project(ledger, schedule([priced_arm], [TASK], 2, 0))
        assert estimate["projected_total_usd"] == pytest.approx(0.028)
        assert record.unknown and record.charged_usd == 0.01
        assert ledger.path.read_bytes() == before
        assert record.result is not None
        record.result.tokens.input = "unknown"
        with pytest.raises(BenchError, match="explicit tariff and tokens"):
            project(ledger, schedule([priced_arm], [TASK], 2, 0))
        with pytest.raises(BenchError, match="unknown cost"):
            ledger.ready()
    finally:
        ledger.close()


def test_baseten_projection_never_invents_a_tariff(tmp_path):
    ledger = Ledger(tmp_path / "results.jsonl", 1, None)
    arm = Arm(arm_id="open", profile="muse-spark-open", reasoning="low", harness_pack="off")
    try:
        pilot(ledger, arm, cost=0.1)
        with pytest.raises(BenchError, match="unknown tariff"):
            project(ledger, schedule([arm], [TASK], 2, 7))
        priced_arm = arm.model_copy(update={"tariff": Tariff(input=1, output=2)})
        assert project(ledger, schedule([priced_arm], [TASK], 2, 7))["remaining_usd"] == 0.1
    finally:
        ledger.close()


def test_projection_requires_pilot_and_refuses_unsettled_reservations(tmp_path):
    ledger = Ledger(tmp_path / "results.jsonl", 1, None)
    try:
        with pytest.raises(BenchError, match="missing pilot"):
            project(ledger, schedule([OFF], [TASK], 2, 1))
        ledger.reserve(TASK, "fake", "interrupted", 0.1)
        with pytest.raises(BenchError, match="unsettled reservation"):
            project(ledger, schedule([OFF], [TASK], 2, 1))
        assert len(ledger.state.records) == 1
    finally:
        ledger.close()


def test_schedule_has_reproducible_shuffled_arm_task_repetition_order():
    tasks = load_tasks()[:3]
    order = schedule([OFF, ON], tasks, 3, 23)
    assert order == schedule([OFF, ON], tasks, 3, 23)
    assert order != schedule([OFF, ON], tasks, 3, 24)
    assert len({(j.task.id, j.arm.arm_id, j.repetition) for j in order}) == 18


def test_report_keeps_repetitions_and_propagates_unknown_costs_and_tokens(tmp_path):
    ledger = Ledger(tmp_path / "results.jsonl", 1, None)
    try:
        first = pilot(ledger)
        assert first.result is not None
        second = first.model_copy(
            update={
                "repetition": 2,
                "attempt_id": "second",
                "unknown": True,
                "result": first.result.model_copy(
                    update={
                        "repetition": 2,
                        "attempt_id": "second",
                        "cost_usd": "unknown",
                        "tokens": Tokens(input="unknown", output=20),
                        "outcome": "failed",
                    }
                ),
            }
        )
        ledger.state.records.append(second)
        output = report(ledger)
        arm = output["arms"][OFF.arm_id]
        assert arm["aggregate"]["tasks_solved"] == 1
        assert arm["aggregate"]["turns"] == 6 and arm["aggregate"]["wall_time_s"] == 4
        assert arm["aggregate"]["cost_usd"] == "unknown"
        assert arm["aggregate"]["tokens"] == {"input": "unknown", "output": "unknown"}
        assert arm["repetitions"]["1"]["tasks_solved"] == 1
        assert arm["repetitions"]["1"]["cost_usd"] == 0.1
        assert arm["repetitions"]["1"]["tokens"] == {"input": 100, "output": 20}
        assert arm["repetitions"]["2"]["tasks_solved"] == 0
        assert arm["repetitions"]["2"]["cost_usd"] == "unknown"
        assert arm["repetitions"]["2"]["tokens"] == {"input": "unknown", "output": "unknown"}
        assert arm["mean_tasks_solved"] == 0.5 and len(arm["attempts"]) == 2
        assert output["cost_usd"] == "unknown"
    finally:
        ledger.close()


def test_paid_output_is_pinned_and_legacy_ledgers_cannot_be_ignored(tmp_path, monkeypatch):
    canonical = tmp_path / ".eval-output" / "bench" / "results.jsonl"
    monkeypatch.setattr(arm_module, "PAID_OUTPUT", canonical)
    for new in (tmp_path / "fresh" / "results.jsonl", canonical.parent / "other.jsonl"):
        with pytest.raises(BenchError, match="paid --out"):
            output_path(new, paid=True)
        assert not new.exists()
    assert output_path(canonical, paid=True) == canonical
    product_evidence = canonical.parent / "server-retained" / ".runtime" / "ledger.json"
    product_evidence.parent.mkdir(parents=True)
    product_evidence.write_text('{"spent_usd": 0.5}')
    assert output_path(canonical, paid=True) == canonical
    old = canonical.parent.parent / "meta" / "ledger.json"
    old.parent.mkdir(parents=True)
    old.write_text("{}")
    with pytest.raises(BenchError, match="consolidation"):
        output_path(canonical, paid=True)


def test_cli_refuses_projection_before_starting_any_process(tmp_path, monkeypatch, capsys):
    out = tmp_path / "results.jsonl"
    ledger = Ledger(out, 1, None)
    pilot(ledger, cost=0.6)
    ledger.close()
    path = tmp_path / "arms.json"
    path.write_text(json.dumps([OFF.model_dump(mode="json")]))

    def forbidden(*args, **kwargs):
        pytest.fail("projection refusal must precede process creation")

    monkeypatch.setattr(bench, "dedicated", forbidden)
    assert (
        bench.main(
            [
                "plan",
                "--arms",
                str(path),
                "--out",
                str(out),
                "--cap-usd",
                "1",
                "--task",
                TASK.id,
                "--repetitions",
                "2",
            ]
        )
        == 1
    )
    assert "exceeds cap" in capsys.readouterr().err


@pytest.mark.anyio
async def test_owned_fake_process_pilot_plan_and_resume_record_seed_and_order(tmp_path):
    out = tmp_path / "results.jsonl"
    script_path = tmp_path / "script.json"
    script_path.write_text(script())
    ledger = Ledger(out, 1, None)
    try:
        first = await bench.run_plan(
            ledger,
            [OFF, ON],
            [TASK],
            1,
            13,
            pilot=True,
            fake_script=script_path,
        )
        assert len(ledger.state.records) == 2 and first["seed"] == 13
        planned = await bench.run_plan(
            ledger,
            [OFF, ON],
            [TASK],
            2,
            27,
            pilot=False,
            fake_script=script_path,
        )
        assert len(ledger.state.records) == 4
        order = ledger.state.plans[planned["plan_id"]]["order"]
        resumed = await bench.run_plan(
            ledger,
            [OFF, ON],
            [TASK],
            2,
            None,
            pilot=False,
            fake_script=script_path,
        )
        assert resumed["seed"] == 27 and len(ledger.state.records) == 4
        assert ledger.state.plans[planned["plan_id"]]["order"] == order
        with pytest.raises(BenchError, match="recorded seed"):
            await bench.run_plan(
                ledger,
                [OFF, ON],
                [TASK],
                2,
                28,
                pilot=False,
                fake_script=script_path,
            )
        rows = [json.loads(line) for line in out.read_text().splitlines()]
        assert all(r["outcome"] == "solved" for r in rows)
        assert len({r["attempt_id"] for r in rows}) == 4
    finally:
        ledger.close()


def test_child_environment_passes_harness_and_never_exports_provider_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("META_API_KEY", "do-not-inherit")
    monkeypatch.setenv("BASETEN_API_KEY", "do-not-inherit")
    monkeypatch.setenv("OVERSIGHT_HARNESS_PACK", "ambient-value")
    for arm in (ON, OFF):
        env = environment(arm, tmp_path / ".runtime", 12345, 20)
        assert env["OVERSIGHT_HARNESS_PACK"] == arm.harness_pack
        assert env["OVERSIGHT_AGENT_MODEL"] == "fake"
        assert "META_API_KEY" not in env and "BASETEN_API_KEY" not in env


def test_report_retains_an_unsettled_reservation_without_inventing_zero_cost(tmp_path):
    ledger = Ledger(tmp_path / "results.jsonl", 1, None)
    try:
        record = ledger.reserve(TASK, "fake", "lost-start-response", 0.1)
        output = report(ledger)
        assert output["cost_usd"] == "unknown" and output["unsettled_reservations"] == 1
        group = output["arms"][record.arm_id]
        assert group["aggregate"]["cost_usd"] == "unknown"
        assert group["reservations"][0]["allowance_usd"] == 0.1
    finally:
        ledger.close()


def test_paid_runtime_preserves_product_billing_evidence_without_a_provider_call(tmp_path):
    paid = Arm(arm_id="retention", profile="muse-spark", reasoning="low", harness_pack="off")
    with runtime_directory(paid, tmp_path) as directory:
        evidence = directory / ".runtime" / "ledger.json"
        evidence.parent.mkdir()
        evidence.write_text('{"spent_usd": 0.5}')
    assert evidence.read_text() == '{"spent_usd": 0.5}'
    with runtime_directory(OFF, tmp_path) as fake_directory:
        assert fake_directory.exists()
    assert not fake_directory.exists()
