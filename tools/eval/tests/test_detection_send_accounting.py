"""Transport accounting regressions through the job path, with simulated HTTP only."""

import asyncio

import httpx2 as httpx
import openai
import pytest
from decision_client_helpers import INPUT, FakeApi, body, ok  # pyright: ignore[reportMissingImports]
from tools.eval.behavior.catalog import load_patches
from tools.eval.detection.accounting import Budget
from tools.eval.detection.config import ARMS
from tools.eval.detection.diagnostics import DetectionClient
from tools.eval.detection.engine import run_attempt
from tools.eval.detection_types import DetectionEvidence
from tools.eval.ledger import Ledger
from tools.eval.tests.test_detection_plan import ASSUMPTIONS, RATES, simulated_measure

from oversight.agents import MUSE_SPARK_OPEN, DecisionUnavailable


def client(api, **kwargs):
    return DetectionClient(
        MUSE_SPARK_OPEN,
        base_url="https://decision.test/v1",
        api_key="test",
        max_output_tokens=512,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(api.handle)),
        **kwargs,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("cancel", [False, True], ids=["slow_persist", "cancel_before_send"])
async def test_presend_job_stays_known(tmp_path, monkeypatch, cancel):
    loop = asyncio.get_running_loop()
    clock = loop.time
    deadline = clock() + 3600
    budgets = []
    persist = Budget.persist

    def slow_persist(budget):
        persist(budget)
        if not budgets:
            budgets.append(budget)
            if cancel:
                task = asyncio.current_task()
                assert task is not None
                task.cancel()
            else:
                monkeypatch.setattr(loop, "time", lambda: deadline + 1)

    async def measure(patch, arm, config, adapter, *args):
        try:
            outcome = await adapter.classify_difference(INPUT, deadline=deadline)
            assert outcome.unavailable == "deadline"
        finally:
            monkeypatch.setattr(loop, "time", clock)
        return DetectionEvidence(
            patch_id=patch.id,
            patch_sha256=patch.sha256,
            configuration=config,
            labels_sha256=None,
            prompt_version=config["prompt_version"],
            prompt_sha256=config["prompt_sha256"],
            decisions=adapter.evidence,
            tests_passed=True,
            detected=None,
            let_through=False,
            question=True,
        )

    monkeypatch.setattr(Budget, "persist", slow_persist)
    monkeypatch.setattr("tools.eval.detection.engine.measure", measure)
    api = FakeApi(ok(body()))
    arm = ARMS[3]
    patch = next(p for p in load_patches() if p.id == "d01")
    config = arm.configuration(False, RATES)
    config["assumptions"] = ASSUMPTIONS.model_dump()
    ledger = Ledger(tmp_path / "results.jsonl", 20, None, multi_tariff=True)
    kwargs: dict = dict(
        fake_choice=None, pilot=False, labels_hash=None, factory=lambda _: client(api)
    )
    try:
        task = asyncio.create_task(
            run_attempt(ledger, (patch, arm, config, 1), ASSUMPTIONS, tmp_path / "python", **kwargs)
        )
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await task
        budget = budgets[0]
        assert not api.bodies
        assert budget.sends == budget.receipts == 0
        assert not budget.record.unknown and budget.record.charged_usd == 0
        assert ledger.state.plans["detection-receipts"][budget.record.attempt_id] == {
            "admitted": 1,
            "sends": 0,
            "receipts": 0,
        }
        if not cancel:
            assert budget.record.result is not None
            assert budget.record.result.steps == budget.record.result.turns == 0
            assert budget.record.result.cost_usd == 0
            monkeypatch.setattr("tools.eval.detection.engine.measure", simulated_measure)
            await run_attempt(
                ledger, (patch, arm, config, 2), ASSUMPTIONS, tmp_path / "python", **kwargs
            )
            assert len(api.bodies) == 1
    finally:
        monkeypatch.undo()
        ledger.close()


@pytest.mark.anyio
async def test_unbound_client_refuses_before_transport():
    api = FakeApi(ok(body()))
    sdk = client(api)
    try:
        with pytest.raises(DecisionUnavailable, match="budget_unconfigured"):
            await sdk._completion({})
        assert not api.bodies
    finally:
        await sdk.aclose()


@pytest.mark.anyio
async def test_send_is_durable_before_transport(tmp_path, monkeypatch):
    import json

    ledger = Ledger(tmp_path / "results.jsonl", 20, None, multi_tariff=True)
    api = FakeApi()

    def handle(request):
        stored = json.loads(ledger.path.read_text())
        assert stored["plans"]["detection-receipts"][ledger.state.records[0].attempt_id] == {
            "admitted": 1,
            "sends": 1,
            "receipts": 0,
        }
        return ok(body())

    monkeypatch.setattr(api, "handle", handle)
    monkeypatch.setattr("tools.eval.detection.engine.measure", simulated_measure)
    arm = ARMS[3]
    patch = next(p for p in load_patches() if p.id == "d01")
    config = arm.configuration(False, RATES) | {"assumptions": ASSUMPTIONS.model_dump()}
    try:
        await run_attempt(
            ledger,
            (patch, arm, config, 1),
            ASSUMPTIONS,
            tmp_path / "python",
            fake_choice=None,
            pilot=False,
            labels_hash=None,
            factory=lambda _: client(api),
        )
    finally:
        ledger.close()


@pytest.mark.anyio
@pytest.mark.parametrize("arm", [ARMS[0], ARMS[3]], ids=lambda a: a.id)
async def test_fake_job_keeps_send_counts(tmp_path, monkeypatch, arm):
    monkeypatch.setattr("tools.eval.detection.engine.measure", simulated_measure)
    patch = next(p for p in load_patches() if p.id == "d01")
    ledger = Ledger(tmp_path / "results.jsonl", 20, None, multi_tariff=True)
    config = arm.configuration(True, RATES)
    config["assumptions"] = ASSUMPTIONS.model_dump()
    try:
        await run_attempt(
            ledger,
            (patch, arm, config, 1),
            ASSUMPTIONS,
            tmp_path / "python",
            fake_choice="asked_for",
            pilot=True,
            labels_hash=None,
            factory=None,
        )
        record = ledger.state.records[0]
        assert record.result is not None
        sends = int(arm.profile != "none")
        assert record.result.steps == record.result.turns == sends
        assert record.result.cost_usd == record.charged_usd == 0
        if sends:
            assert ledger.state.plans["detection-receipts"][record.attempt_id] == {
                "admitted": 1,
                "sends": 1,
                "receipts": 1,
            }
        else:
            assert record.result.tokens.input == record.result.tokens.output == 0
    finally:
        ledger.close()
