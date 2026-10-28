"""Caps fail before starts or setup; durable accounting survives crashes and restarts."""

import json

import pytest
from live_helpers import Scripts, live_server, live_settings  # pyright: ignore[reportMissingImports]
from tools.eval.grade import Tariff, Usage
from tools.eval.ledger import Ledger
from tools.eval.run import execute, main
from tools.eval.session import allowance, profile
from tools.eval.tasks import HERE, BenchError, load_tasks, read_template
from tools.eval.transport import API, Events

TASK = load_tasks()[0]
FREE = Tariff(input=0, output=0)


@pytest.mark.parametrize("cap", [None, "0", "-1", "nan", "inf", "-inf"])
def test_cli_cap_refusal_before_network_or_files(cap, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid cap must not contact a server")

    monkeypatch.setattr(API, "__init__", forbidden)
    args = ["--server", "http://127.0.0.1:1", "--model", "fake", "--out", str(tmp_path / "x")]
    if cap is not None:
        args += [f"--cap-usd={cap}"]
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
    assert not list(tmp_path.iterdir())


@pytest.mark.anyio
async def test_insufficient_allowance_refuses_before_setup_or_start(tmp_path):
    factory = Scripts()
    settings = live_settings(tmp_path)
    async with live_server(settings, factory) as server:
        initial = server.events()
        with pytest.raises(BenchError, match="cannot reserve"):
            await execute(
                str(server.client.base_url),
                "fake",
                0.001,
                tmp_path / "results" / "r.jsonl",
                [TASK],
                FREE,
            )
        assert factory.calls == 0
        for path, text in read_template().items():
            assert (settings.runtime_dir / "workspace" / path).read_text() == text
        assert server.events() == initial


def test_durable_reservation_is_locked_and_never_reset(tmp_path):
    out = tmp_path / "results.jsonl"
    ledger = Ledger(out, 1, None)
    try:
        ledger.reserve(TASK, "muse-spark", "run-1", 0.1)
        with pytest.raises(BlockingIOError):
            Ledger(out, 1, None)
    finally:
        ledger.close()
    ledger = Ledger(out, 1, None)
    try:
        with pytest.raises(BenchError, match="unsettled reservation"):
            ledger.ready()
        assert len(ledger.state.records) == 1
    finally:
        ledger.close()
    with pytest.raises(BenchError, match="cap, output filename or tariff"):
        Ledger(out, 2, None)


@pytest.mark.anyio
async def test_preflight_rejects_occupied_workspace(tmp_path):
    from live_helpers import GatedModel  # pyright: ignore[reportMissingImports]

    model = GatedModel()
    async with live_server(live_settings(tmp_path), lambda p, s: model) as server:
        await server.client.post("/agents", json={"task": "occupied", "model": "fake"})
        with pytest.raises(BenchError, match="occupied"):
            await execute(
                str(server.client.base_url),
                "fake",
                1,
                tmp_path / "results" / "r.jsonl",
                [TASK],
                FREE,
            )
        assert not (tmp_path / "results" / "r.jsonl").read_text()


@pytest.mark.anyio
async def test_preflight_pins_advertised_profile_and_limits(tmp_path):
    async with live_server(live_settings(tmp_path)) as server:
        run = server.events()[0].payload
        assert profile(run, "fake").model == "fake-scripted"
        with pytest.raises(BenchError, match="limits"):
            profile(
                run.model_copy(
                    update={"limits": run.limits.model_copy(update={"max_output_tokens": 4097})}
                ),
                "fake",
            )
        bad = run.models[0].model_copy(update={"reasoning": "high"})
        with pytest.raises(BenchError, match="profile"):
            profile(run.model_copy(update={"models": [bad]}), "fake")
        with pytest.raises(BenchError, match="baseline"):
            profile(run.model_copy(update={"base_commit": "wrong"}), "fake")
        api = API(str(server.client.base_url))
        try:
            with pytest.raises(BenchError, match="restarted"):
                await api.health("wrong-run")
        finally:
            await api.client.aclose()


def test_rates_are_explicit_and_estimate_uses_36_turns():
    assert allowance("muse-spark", None) == pytest.approx(0.0870912)
    assert allowance("muse-spark-open", Tariff(input=1, output=2)) == pytest.approx(0.870912)
    assert Usage().cost == "unknown"
    assert Usage().tokens.input == Usage().tokens.output == "unknown"


def test_result_schema_does_not_drift():
    from tools.eval.grade import Result
    from tools.eval.tasks import HERE

    assert json.loads((HERE / "results.schema.json").read_text()) == Result.model_json_schema()


@pytest.mark.anyio
async def test_events_reject_tampered_id_invalid_schema_and_gaps(tmp_path):
    lines = (HERE.parents[1] / "fixtures/events/handwritten-level1.jsonl").read_text().splitlines()
    event = json.loads(lines[0])
    api = API("http://127.0.0.1:1")
    events = Events(api, event["payload"]["run_id"], tmp_path / "events.jsonl")
    try:
        with pytest.raises(BenchError, match="SSE id"):
            events.accept(lines[0], "2")
        assert events.accept(lines[0], "1") is not None
        assert events.accept(lines[0], "1") is None
        with pytest.raises(BenchError, match="SSE gap"):
            events.accept(lines[2], "3")
        event["payload"]["prompt_version"] = "tampered"
        with pytest.raises(BenchError, match="conflicting duplicate"):
            events.accept(json.dumps(event), "1")
    finally:
        await events.close()
        await api.client.aclose()
