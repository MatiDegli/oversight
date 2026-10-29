"""Real decision parsing and accounting through simulated HTTP only."""

import asyncio
import json

import httpx2 as httpx
import openai
import pytest
from decision_client_helpers import (  # pyright: ignore[reportMissingImports]
    INPUT,
    FakeApi,
    body,
    client,
    ok,
    status,
)
from tools.eval.behavior.catalog import load_patches
from tools.eval.detection.accounting import Budget, task_for
from tools.eval.detection.adapters import Adapter, messages
from tools.eval.detection.config import ARMS, NO_BEHAVIOR, system_prompt
from tools.eval.grade import Tariff
from tools.eval.ledger import Ledger
from tools.eval.tasks import BenchError

from oversight.agents import MUSE_SPARK, DecisionClient, RetryPolicy, classify_difference


@pytest.fixture
def budget(tmp_path):
    ledger = Ledger(tmp_path / "results.jsonl", 20, None, multi_tariff=True)
    patch = next(p for p in load_patches() if p.id == "d01")
    record = ledger.reserve(task_for(patch), "baseten", "test", 1, arm_id="detect-4")
    budget = Budget(ledger, record, Tariff(input=1, output=2), 0.001, fake=False)
    yield budget
    ledger.close()


def test_diff_prompt_has_no_empty_behavior_or_label_fields():
    prompt = messages("task", diff="diff", target=INPUT.target)
    payload = json.loads(prompt[1]["content"])
    assert payload["behavior_evidence"] == NO_BEHAVIOR
    assert "difference" not in payload
    assert set(payload) == {"task", "diff", "target", "behavior_evidence"}
    assert "SYNTHETIC GRADING SECRET" not in json.dumps(prompt)


@pytest.mark.anyio
async def test_shared_prompt_matches_actual_product_request(budget):
    api = FakeApi(ok(body()))
    sdk = client(api)
    try:
        await classify_difference(
            sdk,
            INPUT,
            deadline=asyncio.get_running_loop().time() + 10,
            admission=budget.admission,
            accounting=budget.accounting,
        )
        assert api.bodies[0]["messages"] == messages(INPUT.task, input=INPUT)
        assert api.bodies[0]["messages"][0]["content"] == system_prompt()
        assert api.bodies[0]["response_format"]["json_schema"]["strict"] is True
        assert api.bodies[0]["reasoning_effort"] == "low"
    finally:
        await sdk.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "text,identity,expected",
    [
        ('{"choice":"cannot_tell"}', None, "cannot_tell"),
        ('{"choice":"asked_for"}', "wrong-model", "invalid_response"),
        ('{"choice":"approve"}', None, "invalid_response"),
        ('{"choice":"asked_for","extra":1}', None, "invalid_response"),
        ('{"choice":"asked_for","choice":"not_asked_for"}', None, "invalid_response"),
        ("not json", None, "invalid_response"),
    ],
)
async def test_open_actual_schema_parsing_and_charges(budget, text, identity, expected):
    api = FakeApi(ok(body(text, **({"model": identity} if identity else {}))))
    sdk = client(api)
    adapter = Adapter(ARMS[3], budget, client=sdk)
    try:
        result = await adapter.classify_difference(
            INPUT, deadline=asyncio.get_running_loop().time() + 10
        )
        assert result.choice == expected or result.unavailable == expected
        assert budget.record.charged_usd == pytest.approx(0.00008)
        assert budget.receipts == 1
        assert len(api.bodies) == 1
    finally:
        await sdk.aclose()


@pytest.mark.anyio
async def test_unknown_http_cost_stops_retry_admission(budget):
    api = FakeApi(status(429), ok(body()))
    sdk = client(api, retry=RetryPolicy(base_s=0))
    try:
        result = await Adapter(ARMS[3], budget, client=sdk).classify_difference(
            INPUT, deadline=asyncio.get_running_loop().time() + 10
        )
        assert result.unavailable == "ledger_unknown"
        assert len(api.bodies) == 1
        assert budget.record.unknown
    finally:
        await sdk.aclose()


@pytest.mark.anyio
async def test_cancellation_drains_unknown_accounting(budget):
    entered = asyncio.Event()

    async def handle(request: httpx.Request) -> httpx.Response:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("cancelled transport resumed")

    sdk = DecisionClient(
        client_profile(),
        base_url="https://decision.test/v1",
        api_key="test",
        max_output_tokens=512,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(handle)),
    )
    task = asyncio.create_task(
        Adapter(ARMS[3], budget, client=sdk).classify_difference(
            INPUT, deadline=asyncio.get_running_loop().time() + 30
        )
    )
    try:
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert budget.record.unknown and budget.receipts == 1
        assert json.loads(budget.ledger.path.read_text())["records"][0]["unknown"]
    finally:
        await sdk.aclose()


def client_profile():
    from oversight.agents import MUSE_SPARK_OPEN

    return MUSE_SPARK_OPEN


@pytest.mark.anyio
async def test_spark_uses_strict_inherited_schema_and_actual_identity(budget):
    api = FakeApi(ok(body(model=MUSE_SPARK.model)))
    sdk = DecisionClient(
        MUSE_SPARK,
        base_url="https://meta.test/v1",
        api_key="test",
        max_output_tokens=512,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(api.handle)),
    )
    try:
        adapter = Adapter(ARMS[2], budget, client=sdk)
        result = await adapter.classify_difference(
            INPUT, deadline=asyncio.get_running_loop().time() + 10
        )
        assert result.choice == "asked_for"
        assert api.bodies[0]["model"] == MUSE_SPARK.model
        assert api.bodies[0]["messages"] == messages(INPUT.task, input=INPUT)
        assert api.bodies[0]["response_format"]["json_schema"]["strict"]
    finally:
        await sdk.aclose()


def test_missing_spark_adapter_refuses_explicitly(budget):
    with pytest.raises(BenchError, match="unavailable model adapter"):
        Adapter(ARMS[1], budget)


@pytest.mark.anyio
async def test_deadline_sends_nothing(budget):
    api = FakeApi()
    sdk = client(api)
    try:
        result = await Adapter(ARMS[3], budget, client=sdk).classify_difference(
            INPUT, deadline=asyncio.get_running_loop().time() - 1
        )
        assert result.unavailable == "deadline"
        assert not api.bodies and budget.sends == 0
    finally:
        await sdk.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("arm", ARMS[1:], ids=lambda a: a.id)
@pytest.mark.parametrize("choice", ["asked_for", "not_asked_for", "cannot_tell", "unavailable"])
async def test_all_model_adapters_accept_explicit_fake_outcomes(budget, arm, choice):
    adapter = Adapter(arm, budget, fake_choice=choice)
    if arm.role == "task/diff":
        await adapter.task_diff(INPUT.task, "frozen diff", INPUT.target)
    else:
        await adapter.classify_difference(INPUT, deadline=asyncio.get_running_loop().time() + 10)
    assert len(adapter.evidence) == 1
    result = adapter.evidence[0]
    assert result.choice == choice or result.unavailable == "transport"
    assert budget.receipts == budget.sends == 1


@pytest.mark.anyio
async def test_retry_rechecks_admission_and_has_one_receipt_per_send(budget):
    budget.fake = True  # Explicit simulated zero cost, including the HTTP error receipt.
    api = FakeApi(status(500), ok(body()))
    sdk = client(api, retry=RetryPolicy(base_s=0))
    try:
        result = await Adapter(ARMS[3], budget, client=sdk).classify_difference(
            INPUT, deadline=asyncio.get_running_loop().time() + 10
        )
        assert result.choice == "asked_for"
        assert budget.sends == budget.receipts == len(api.bodies) == 2
        assert budget.usage.tokens.input == "unknown"
        assert budget.record.charged_usd == 0
    finally:
        await sdk.aclose()


@pytest.mark.anyio
async def test_inflight_timeout_retains_unknown_receipt(budget):
    async def handle(request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("timed out transport resumed")

    sdk = DecisionClient(
        client_profile(),
        base_url="https://decision.test/v1",
        api_key="test",
        max_output_tokens=512,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx.MockTransport(handle)),
    )
    try:
        result = await Adapter(ARMS[3], budget, client=sdk).classify_difference(
            INPUT, deadline=asyncio.get_running_loop().time() + 0.1
        )
        assert result.unavailable == "deadline"
        assert budget.receipts == 1 and budget.record.unknown
    finally:
        await sdk.aclose()
