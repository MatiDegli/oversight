"""Identity preservation and provider-free v2 request, pilot and budget guarantees."""

import asyncio
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import httpx2 as httpx
import openai
import pytest
from decision_client_helpers import INPUT, FakeApi, body, ok  # pyright: ignore[reportMissingImports]
from tools.eval.detection import __main__ as cli
from tools.eval.detection.accounting import pilot_projection
from tools.eval.detection.adapters import Adapter
from tools.eval.detection.config import ARMS, Assumptions, fingerprint, projection
from tools.eval.detection.diagnostics import DetectionClient
from tools.eval.detection.engine import jobs_for, run_plan
from tools.eval.detection.product_limits import completion_limit
from tools.eval.detection.reporting import summarize
from tools.eval.grade import Tariff
from tools.eval.ledger import Ledger
from tools.eval.tasks import BenchError
from tools.eval.tests.test_detection_adapters import budget as budget
from tools.eval.tests.test_detection_plan import simulated_measure

from oversight import decision_config
from oversight.agents import MUSE_SPARK_OPEN, DecisionSelected, RetryPolicy
from oversight.changes import ReviewCardLimits
from oversight.settings import Settings

RATES = {"meta": Tariff(input=1, output=2), "baseten": Tariff(input=3, output=4)}
ASSUMPTIONS = Assumptions(input_tokens=1000, output_tokens=512, calls=8)
# Computed on main 0e5305ad565368bce1f1208183677d641ca22616, before editing.
LEGACY_CONFIG = {
    "version": "detection-d6-v1",
    "arm_id": "detect-1",
    "fake": False,
    "profile": "none",
    "model": "none",
    "reasoning": None,
    "behavior": False,
    "prompt_version": "detection-d6-v1",
    "prompt_sha256": "a92734f85fa57e783a474e3162a260f457c10e2dd3ed14459138aea3d2213278",
    "system_prompt_sha256": "355fa87c3d2a7256d44a04dfe7c515826b65a5178f786084e502359c6863009e",
    "response_format_sha256": None,
    "input_fields": [],
    "no_behavior": None,
    "max_output_tokens": 512,
    "retry_attempts": 5,
    "call_s": 30,
    "total_s": 120,
    "classifications": 8,
    "test_timeout_s": 30,
    "measured_cone": False,
    "cross_review": False,
    "tariff": None,
    "protocol": (
        "Arms 2 and 3 use the D6 decision prompt and schema on Muse Spark, "
        "not product cross-review."
    ),
}
LEGACY_FINGERPRINT = "8d6af6b92b60d8650eb8d1d20e74c140af155e999549bdb23991091d2d169cf3"


def test_tests_only_configuration_is_byte_identical_to_main():
    assert ARMS[0].configuration(False, RATES) == LEGACY_CONFIG
    assert fingerprint(ARMS[0].configuration(False, RATES)) == LEGACY_FINGERPRINT


@pytest.mark.anyio
@pytest.mark.parametrize("arm", ARMS[1:], ids=lambda a: a.id)
async def test_product_allowance_is_hashed_and_sent(budget, monkeypatch, arm):
    product = {}
    monkeypatch.setattr(decision_config, "DecisionClient", lambda *a, **kw: product.update(kw))
    decision_config.configured_decision(
        Settings(
            decision_model=MUSE_SPARK_OPEN.id,
            baseten_api_key="test",
        )
    )
    assert completion_limit() == product["max_output_tokens"] == 4096
    config = arm.configuration(False, RATES)
    assert config["max_output_tokens"] == arm.max_output_tokens == product["max_output_tokens"]
    defaults = ReviewCardLimits()
    assert (config["call_s"], config["total_s"], config["classifications"]) == (
        defaults.call_s,
        defaults.total_s,
        defaults.classifications,
    )
    # Factory receives the arm; the same per-arm value reaches the actual HTTP body.
    monkeypatch.setattr(
        "oversight.settings.read_env_file",
        lambda path: {
            "META_API_KEY": "test",
            "BASETEN_API_KEY": "test",
        },
    )
    api = FakeApi(ok(body(model=config["model"])))
    real_client = DetectionClient

    def simulated_client(*args, **kwargs):
        return real_client(
            *args,
            **kwargs,
            http_client=openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(api.handle)),
        )

    monkeypatch.setattr("tools.eval.detection.diagnostics.DetectionClient", simulated_client)
    sdk = cli.provider_factory(Path("/unused/.env"))(arm)
    try:
        adapter = Adapter(arm, budget, client=sdk)
        if arm.behavior:
            await adapter.classify_difference(
                INPUT, deadline=asyncio.get_running_loop().time() + 10
            )
        else:
            await adapter.task_diff(INPUT.task, "diff", INPUT.target)
        assert api.bodies[0]["max_completion_tokens"] == config["max_output_tokens"]
        diagnostic = adapter.evidence[0].diagnostics
        assert diagnostic is not None and diagnostic.validation_stage == "none"
    finally:
        await sdk.aclose()


@pytest.mark.anyio
async def test_task_diff_deadline_comes_from_the_arm(budget):
    arm = replace(ARMS[1], id="renamed-task-arm", limits=ReviewCardLimits(call_s=7, total_s=11))
    adapter = Adapter(arm, budget, fake_choice="asked_for")
    adapter._decide = AsyncMock(return_value=DecisionSelected(INPUT.target, "node", "asked_for"))
    start = asyncio.get_running_loop().time()
    await adapter.task_diff(INPUT.task, "diff", INPUT.target)
    deadline = adapter._decide.call_args.args[3]
    assert 7 <= deadline - start < 7.1


def test_pilot_mapping_and_inputs_follow_roles_not_ids(monkeypatch):
    renamed = tuple(replace(arm, id=f"unrelated-{i}") for i, arm in enumerate(ARMS))
    monkeypatch.setattr("tools.eval.detection.engine.ARMS", renamed)
    jobs = jobs_for(RATES, None, True, 1, None)
    assert [(a.role, p.id) for p, a, _, _ in jobs] == [
        ("tests-only", "d01"),
        ("task/diff", "d02"),
        ("behavior-Spark", "d01"),
        ("behavior-Spark-Open", "d02"),
    ]
    task_config = jobs[1][2]
    assert task_config["input_fields"] == ["target", "task", "diff", "behavior_evidence"]
    assert task_config["no_behavior"]
    assert not jobs[0][1].behavior and all(j[1].behavior for j in jobs[2:])
    assert [a.id for a in ARMS] == ["detect-1", "detect-2-v2", "detect-3-v2", "detect-4-v2"]


@pytest.mark.anyio
async def test_superseded_pilots_remain_charged_and_v2_is_immutable(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.eval.detection.engine.measure", simulated_measure)
    ledger = Ledger(tmp_path / "results.jsonl", 20, None, multi_tariff=True)
    try:
        await run_plan(
            ledger, RATES, ASSUMPTIONS, Path(sys.executable), pilot=True, fake_choice="asked_for"
        )
        # Reconstruct a ledger holding detect-1 plus the three historical model pilots.
        original_tests = next(r for r in ledger.state.records if r.arm_id == "detect-1")
        history = [original_tests]
        for r in ledger.state.records:
            if r.arm_id == "detect-1":
                continue
            identity = {
                "arm_id": r.arm_id.removesuffix("-v2"),
                "config_hash": "old-config",
                "attempt_id": "old-" + r.attempt_id,
            }
            assert r.result is not None
            history.append(
                r.model_copy(
                    update=identity
                    | {
                        "charged_usd": 0.1,
                        "result": r.result.model_copy(update=identity | {"cost_usd": 0.1}),
                    }
                )
            )
        ledger.state.records = history
        ledger.save()
        await run_plan(
            ledger, RATES, ASSUMPTIONS, Path(sys.executable), pilot=True, fake_choice="asked_for"
        )
        assert len(ledger.state.records) == 7
        assert next(r for r in ledger.state.records if r.arm_id == "detect-1") is original_tests
        assert original_tests.result is not None
        assert original_tests.result.detection is not None
        assert original_tests.result.detection.decisions == []
        assert ledger.spent == pytest.approx(0.3)
        jobs = jobs_for(RATES, "asked_for", False, 1, "pin")
        for _, _, config, _ in jobs:
            config["assumptions"] = ASSUMPTIONS.model_dump()
        estimate = pilot_projection(ledger, jobs)
        worst = {
            "detect-1": 0,
            "detect-2-v2": 0.073536,
            "detect-3-v2": 0.073536,
            "detect-4-v2": 0.155072,
        }
        assert estimate["worst_case_per_job_usd"] == pytest.approx(worst)
        assert estimate["pilot_per_job_usd"] == {a.id: 0 for a in ARMS}
        assert estimate["per_arm_usd"] == pytest.approx(worst)
        assert estimate["projected_total_usd"] == pytest.approx(0.3 + 20 * sum(worst.values()))
        report = summarize(ledger.state.records, {}, expected_repetitions=1)
        assert len(report["superseded_pilots"]) == 3
        assert sum(r["charged_usd"] for r in report["superseded_pilots"]) == pytest.approx(0.3)
        assert all(r["status"] == "superseded" for r in report["superseded_pilots"])
        assert set(report["arms"]) == {a.id for a in ARMS}
        # The cap sees historical charges and the greater bound, even with free fake pilots.
        ledger.state.cap_usd = 0.31
        with pytest.raises(BenchError, match="projection"):
            pilot_projection(ledger, jobs)
        ledger.state.cap_usd = 20
        jobs[1][2]["max_output_tokens"] += 1
        with pytest.raises(BenchError, match="configuration changed"):
            pilot_projection(ledger, jobs)
    finally:
        ledger.close()


@pytest.mark.anyio
@pytest.mark.parametrize("missing", ["call", "diagnostics"])
async def test_no_call_method_style_pilot_refuses_and_tests_only_passes(
    tmp_path, monkeypatch, missing
):
    monkeypatch.setattr("tools.eval.detection.engine.measure", simulated_measure)
    ledger = Ledger(tmp_path / "results.jsonl", 20, None, multi_tariff=True)
    try:
        await run_plan(
            ledger, RATES, ASSUMPTIONS, Path(sys.executable), pilot=True, fake_choice="asked_for"
        )
        jobs = jobs_for(RATES, "asked_for", False, 1, "pin")
        for _, _, config, _ in jobs:
            config["assumptions"] = ASSUMPTIONS.model_dump()
        assert pilot_projection(ledger, jobs)["remaining_attempts"] == 80
        model = next(r for r in ledger.state.records if r.arm_id == ARMS[2].id)
        assert model.result is not None and model.result.detection is not None
        if missing == "diagnostics":
            model.result.detection.decisions = []
        else:
            model.result.steps = 0
        with pytest.raises(BenchError, match="no model call"):
            pilot_projection(ledger, jobs)
    finally:
        ledger.close()


def test_dry_projection_prints_both_bounds_and_uses_larger():
    costs = {a.id: 0.0 for a in ARMS}
    p = projection(RATES, ASSUMPTIONS, 1, spent=0.3, cap=20, pilot_costs=costs)
    assert p["pilot_per_job_usd"] == costs
    assert p["per_attempt_usd"] == p["worst_case_per_job_usd"]
    costs[ARMS[1].id] = 0.5
    p = projection(RATES, ASSUMPTIONS, 1, spent=0.3, cap=20, pilot_costs=costs)
    assert p["per_attempt_usd"][ARMS[1].id] == 0.5


@pytest.mark.anyio
@pytest.mark.parametrize("attempts", [1, 5])
async def test_sdk_timeout_is_deadline(budget, attempts):
    request = httpx.Request("POST", "https://decision.test/v1/chat/completions")
    api = FakeApi(openai.APITimeoutError(request=request))
    sdk = DetectionClient(
        MUSE_SPARK_OPEN,
        base_url="https://decision.test/v1",
        api_key="test",
        max_output_tokens=completion_limit(),
        retry=RetryPolicy(max_attempts=attempts, base_s=0),
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(api.handle)),
    )
    try:
        adapter = Adapter(ARMS[3], budget, client=sdk)
        result = await adapter.classify_difference(
            INPUT, deadline=asyncio.get_running_loop().time() + 10
        )
        assert result.unavailable == "deadline"
        assert len(api.bodies) == budget.receipts == budget.sends == 1
        assert budget.record.unknown
        diagnostic = adapter.evidence[0].diagnostics
        assert diagnostic is not None and diagnostic.validation_stage == "deadline"
    finally:
        await sdk.aclose()
