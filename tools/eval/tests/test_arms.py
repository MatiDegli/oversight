"""Same-profile comparisons and repetitions use real HTTP/SSE with scripted models only."""

import json

import pytest
from live_helpers import Scripts, live_server, live_settings  # pyright: ignore[reportMissingImports]
from pydantic import ValidationError
from tools.eval.arms import Arm
from tools.eval.grade import Result
from tools.eval.ledger import Ledger
from tools.eval.run import execute
from tools.eval.tasks import BenchError, load_tasks
from tools.eval.tests.test_runner import FREE, TASK, script

OFF = Arm(arm_id="plain", profile="fake", reasoning=None, harness_pack="off", tariff=FREE)
ON = OFF.model_copy(update={"arm_id": "packed", "harness_pack": "on"})


@pytest.mark.anyio
async def test_same_profile_arms_repetitions_and_resume_have_distinct_keys(tmp_path):
    out = tmp_path / "results.jsonl"
    configurations = [(OFF, 1), (ON, 1), (OFF, 2)]
    for index, (arm, repetition) in enumerate(configurations):
        factory = Scripts([script_with_tests()])
        async with live_server(
            live_settings(tmp_path / str(index), harness_pack=arm.harness_pack == "on"), factory
        ) as server:
            await execute(
                str(server.client.base_url),
                "fake",
                1,
                out,
                [TASK],
                FREE,
                arm=arm,
                repetition=repetition,
            )
            assert factory.calls == 1
    # Resume the fourth key; all of the previous keys must skip before server preflight.
    factory = Scripts([script_with_tests()])
    async with live_server(
        live_settings(tmp_path / "resume", harness_pack=True), factory
    ) as server:
        for arm, repetition in [*configurations, (ON, 2)]:
            await execute(
                str(server.client.base_url),
                "fake",
                1,
                out,
                [TASK],
                FREE,
                arm=arm,
                repetition=repetition,
            )
        assert factory.calls == 1
    rows = [Result.model_validate_json(line) for line in out.read_text().splitlines()]
    assert {(r.arm_id, r.repetition) for r in rows} == {
        ("plain", 1),
        ("packed", 1),
        ("plain", 2),
        ("packed", 2),
    }
    assert all(r.outcome == "solved" and r.turns == 4 for r in rows)
    assert len({r.attempt_id for r in rows}) == 4
    assert len({r.config_hash for r in rows}) == 2
    ledger = Ledger(out, 1, FREE)
    try:
        for record, row in zip(ledger.state.records, rows, strict=True):
            for key in ("arm_id", "repetition", "config_hash", "attempt_id"):
                assert getattr(record, key) == getattr(row, key)
                for obj in (record, row):
                    with pytest.raises(ValidationError, match="frozen"):
                        setattr(obj, key, getattr(obj, key))
        with pytest.raises(BenchError, match="configuration changed"):
            ledger.completed(TASK, "fake", OFF.arm_id, 3, ON.config_hash())
    finally:
        ledger.close()


@pytest.mark.parametrize(
    "status,unknown", [("finished", False), ("reserved", False), ("finished", True)]
)
def test_version_one_migration_keeps_every_charge_reservation_and_unknown(
    tmp_path, status, unknown
):
    out = tmp_path / "results.jsonl"
    old_result = {
        "task_id": TASK.id,
        "agent_model": "fake-scripted",
        "outcome": "failed",
        "steps": 2,
        "duration_s": 1,
        "tokens": {"input": 100, "output": 20},
        "cost_usd": "unknown" if unknown else 0.25,
        "test_result": {"baseline": "not_run", "hidden": "not_run", "policy": "not_run"},
    }
    old_record = {
        "task_id": TASK.id,
        "task_hash": TASK.hash,
        "profile": "fake",
        "run_id": "old",
        "allowance_usd": 0.4,
        "charged_usd": 0.25,
        "unknown": unknown,
        "status": status,
        "result": old_result,
    }
    (tmp_path / "ledger.json").write_text(
        json.dumps(
            {
                "version": 1,
                "cap_usd": 1,
                "results_file": out.name,
                "tariff": None,
                "records": [old_record],
            }
        )
    )
    ledger = Ledger(out, 1, None)
    try:
        record = ledger.state.records[0]
        assert ledger.state.version == 2
        assert ledger.spent == 0.25 and record.allowance_usd == 0.4
        assert record.status == status and record.unknown == unknown
        assert record.arm_id == "fake" and record.repetition == 1
        assert record.result is not None and record.result.steps == 2
        assert record.result.turns == "unknown"
        if unknown or status == "reserved":
            with pytest.raises(BenchError, match=r"unknown cost|unsettled reservation"):
                ledger.ready()
        else:
            assert ledger.completed(TASK, "fake", "fake", 1, "legacy")
        identity = record.attempt_id
    finally:
        ledger.close()
    ledger = Ledger(out, 1, None)
    assert ledger.state.records[0].attempt_id == identity
    migrated = Result.model_validate_json(out.read_text().strip())
    assert migrated.turns == "unknown"
    from tools.eval.report import report

    arm = report(ledger)["arms"][record.arm_id]
    assert arm["aggregate"]["turns"] == "unknown"
    assert arm["repetitions"]["1"]["turns"] == "unknown"
    ledger.close()


def test_configuration_hash_covers_model_reasoning_limits_prompt_and_harness():
    from oversight.app import LIMITS
    from oversight_contracts.events import RunStarted

    # A typed shell is sufficient: only the configuration's two advertised fields are read.
    run = RunStarted.model_construct(limits=LIMITS, prompt_version="test-v1")
    arm = Arm(arm_id="meta", profile="muse-spark", reasoning="low", harness_pack="off")
    baseline = arm.config_hash(run)
    variants = [
        arm.model_copy(update={"reasoning": "high"}).config_hash(run),
        arm.model_copy(update={"harness_pack": "on"}).config_hash(run),
        arm.model_copy(update={"profile": "muse-spark-open"}).config_hash(run),
        arm.config_hash(run.model_copy(update={"prompt_version": "test-v2"})),
        arm.config_hash(run.model_copy(update={"limits": replace_limits(LIMITS)})),
    ]
    assert all(h != baseline for h in variants)
    assert len(set(variants)) == len(variants)


def replace_limits(limits):
    return limits.model_copy(update={"max_steps": limits.max_steps - 1})


@pytest.mark.anyio
async def test_repetitions_alone_are_independent_attempts(tmp_path):
    out = tmp_path / "results.jsonl"
    for repetition in (1, 2):
        factory = Scripts([script_with_tests()])
        async with live_server(live_settings(tmp_path / str(repetition)), factory) as server:
            await execute(
                str(server.client.base_url),
                "fake",
                1,
                out,
                [TASK],
                FREE,
                arm=OFF,
                repetition=repetition,
            )
            assert factory.calls == 1
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["repetition"] for r in rows] == [1, 2]
    assert len({r["attempt_id"] for r in rows}) == 2


def test_completion_skips_only_the_finished_four_part_key(tmp_path):
    ledger = Ledger(tmp_path / "results.jsonl", 1, None)
    try:
        record = ledger.reserve(
            TASK,
            "fake",
            "run",
            0.1,
            arm_id=OFF.arm_id,
            config_hash=OFF.config_hash(),
        )
        assert not ledger.completed(TASK, "fake", OFF.arm_id, 1, OFF.config_hash())
        # Cleanup confirmation, rather than the mere existence of a reservation, completes a key.
        record.status = "finished"
        assert ledger.completed(TASK, "fake", OFF.arm_id, 1, OFF.config_hash())
        assert not ledger.completed(TASK, "fake", OFF.arm_id, 2, OFF.config_hash())
        assert not ledger.completed(TASK, "fake", ON.arm_id, 1, ON.config_hash())
        assert not ledger.completed(TASK, "other-profile", OFF.arm_id, 1, OFF.config_hash())
        assert not ledger.completed(load_tasks()[1], "fake", OFF.arm_id, 1, OFF.config_hash())
    finally:
        ledger.close()


def script_with_tests():
    data = json.loads(script())
    data["turns"].insert(
        -1,
        {
            "tool_calls": [{"name": "run_tests", "arguments": {}}],
            "usage": {"input_tokens": 100, "output_tokens": 20},
        },
    )
    return json.dumps(data)
