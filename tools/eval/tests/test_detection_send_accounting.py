"""Transport accounting regressions through the job path, with simulated HTTP only."""

import asyncio
import json

import httpx2 as httpx
import openai
import pytest
from decision_client_helpers import INPUT, FakeApi, body, ok  # pyright: ignore[reportMissingImports]
from tools.eval.behavior.catalog import load_patches
from tools.eval.detection.accounting import Budget, task_for
from tools.eval.detection.config import ARMS
from tools.eval.detection.diagnostics import DetectionClient
from tools.eval.detection.engine import run_attempt
from tools.eval.detection.transport import TransportClient
from tools.eval.detection_types import DetectionEvidence
from tools.eval.ledger import Ledger
from tools.eval.tasks import BenchError
from tools.eval.tests.test_detection_plan import ASSUMPTIONS, RATES, simulated_measure

from oversight.agents import MUSE_SPARK_OPEN, DecisionUnavailable, classify_difference
from oversight.agents.client import CallScope


def client(api, **kwargs):
    return TransportClient(
        MUSE_SPARK_OPEN,
        base_url="https://decision.test/v1",
        api_key="test",
        max_output_tokens=512,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(api.handle)),
        **kwargs,
    )


@pytest.mark.anyio
async def test_transport_counter_preserves_request_bytes_and_headers(tmp_path):
    requests: list[tuple[bytes, dict[str, str]]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append((request.content, dict(request.headers)))
        return ok(body())

    transport = httpx.MockTransport(handle)
    ledger = Ledger(tmp_path / "results.jsonl", 20, None, multi_tariff=True)
    patch = next(p for p in load_patches() if p.id == "d01")
    record = ledger.reserve(task_for(patch), "baseten", "test", 1)
    deadline = asyncio.get_running_loop().time() + 10
    try:
        for client_type in (TransportClient, DetectionClient):
            budget = Budget(ledger, record, RATES["baseten"], 0.001, fake=False)
            kwargs: dict = {"budget": budget} if client_type is TransportClient else {}
            sdk = client_type(
                MUSE_SPARK_OPEN,
                base_url="https://decision.test/v1",
                api_key="test",
                max_output_tokens=512,
                http_client=openai.DefaultAsyncHttpxClient(transport=transport),
                **kwargs,
            )
            try:
                result = await classify_difference(
                    sdk,
                    INPUT,
                    deadline=deadline,
                    admission=budget.admission,
                    accounting=budget.accounting,
                )
                assert result.choice == "asked_for"
            finally:
                await sdk.aclose()
        assert len(requests) == 2
        assert requests[0] == requests[1]
    finally:
        ledger.close()


@pytest.mark.anyio
@pytest.mark.parametrize("cancel", [False, True], ids=["slow_persist", "cancel_before_send"])
@pytest.mark.parametrize("persist_number", [1, 2], ids=["admission_write", "send_write"])
async def test_presend_job_stays_known(tmp_path, monkeypatch, cancel, persist_number):
    loop = asyncio.get_running_loop()
    clock = loop.time
    deadline = clock() + 3600
    budgets = []
    persist = Budget.persist
    persists = 0

    def slow_persist(budget):
        nonlocal persists
        persist(budget)
        if not budgets:
            budgets.append(budget)
        if budget is budgets[0]:
            persists += 1
        if budget is budgets[0] and persists == persist_number:
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
        persisted = json.loads(ledger.path.read_text())["plans"]
        assert persisted["detection-receipts"] == ledger.state.plans["detection-receipts"]
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


@pytest.mark.anyio
@pytest.mark.parametrize("entry", ["completion", "decision_attempt"])
async def test_unbound_client_refuses_before_admission(tmp_path, entry):
    ledger = Ledger(tmp_path / "results.jsonl", 20, None, multi_tariff=True)
    patch = next(p for p in load_patches() if p.id == "d01")
    record = ledger.reserve(task_for(patch), "baseten", "test", 1)
    budget = Budget(ledger, record, RATES["baseten"], 0.001, fake=False)
    api = FakeApi(ok(body()))
    sdk = client(api)
    scope = CallScope(None, budget.accounting, "review", budget.admission, True)
    try:
        with pytest.raises(DecisionUnavailable, match="budget_unconfigured"):
            if entry == "completion":
                await sdk._completion({})
            else:
                await sdk._decision_attempt({}, scope)
        assert budget.admitted == budget.sends == budget.receipts == 0
        assert not api.bodies and not record.unknown
        assert "detection-receipts" not in ledger.state.plans
    finally:
        await sdk.aclose()
        ledger.close()


@pytest.mark.anyio
async def test_job_refuses_client_without_send_counter(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.eval.detection.engine.measure", simulated_measure)
    api = FakeApi(ok(body()))
    sdk = DetectionClient(
        MUSE_SPARK_OPEN,
        base_url="https://decision.test/v1",
        api_key="test",
        max_output_tokens=512,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(api.handle)),
    )
    arm = ARMS[3]
    patch = next(p for p in load_patches() if p.id == "d01")
    config = arm.configuration(False, RATES) | {"assumptions": ASSUMPTIONS.model_dump()}
    ledger = Ledger(tmp_path / "results.jsonl", 20, None, multi_tariff=True)
    try:
        with pytest.raises(BenchError, match="transport-counted client"):
            await run_attempt(
                ledger,
                (patch, arm, config, 1),
                ASSUMPTIONS,
                tmp_path / "python",
                fake_choice=None,
                pilot=False,
                labels_hash=None,
                factory=lambda _: sdk,  # pyright: ignore[reportArgumentType]
            )
        assert not api.bodies
        assert not ledger.state.records[0].unknown
    finally:
        await sdk.aclose()
        ledger.close()


@pytest.mark.anyio
@pytest.mark.parametrize("at_construction", [False, True])
async def test_transport_refuses_fake_budget(tmp_path, at_construction):
    ledger = Ledger(tmp_path / "results.jsonl", 20, None, multi_tariff=True)
    patch = next(p for p in load_patches() if p.id == "d01")
    record = ledger.reserve(task_for(patch), "baseten", "test", 1)
    budget = Budget(ledger, record, RATES["baseten"], 0.001, fake=True)
    api = FakeApi(ok(body()))
    sdk = client(api)
    try:
        with pytest.raises(BenchError, match="nonfake budget"):
            if at_construction:
                sdk = client(api, budget=budget)
            else:
                sdk.budget = budget
        assert not api.bodies
        assert budget.sends == budget.receipts == budget.admitted == 0
    finally:
        await sdk.aclose()
        ledger.close()
