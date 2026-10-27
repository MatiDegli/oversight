"""Model execution evidence comes only from contract events and their bound receipt."""

import hashlib
import json
from pathlib import Path

import pytest

import meta_manifest as manifest
from oversight_contracts.events import (
    AgentStatus,
    AgentStatusEvent,
    Budget,
    ChangeResolved,
    ChangeResolvedEvent,
    ErrorReport,
    ErrorReportEvent,
    Limits,
    ModelProfile,
    RunStarted,
    RunStartedEvent,
    ServerEvent,
    SlotInfo,
    UsageTick,
    UsageTickEvent,
)
from recording import Provenance, provenance_path

META = ModelProfile(
    id="muse-spark",
    label="Muse Spark",
    provider="meta",
    model="muse-spark",
    reasoning="contributor",
)
OPEN = ModelProfile(
    id="muse-spark-open",
    label="Muse Spark Open",
    provider="baseten",
    model="meta-models/muse-spark-open-1026",
    reasoning=None,
)
FAKE = ModelProfile(
    id="fake", label="Scripted fake model", provider="fake", model="fake-scripted", reasoning=None
)
TS = "2026-10-27T14:00:00.000Z"


def started(*profiles: ModelProfile) -> RunStartedEvent:
    return RunStartedEvent(
        seq=1,
        ts=TS,
        type="run.started",
        payload=RunStarted(
            run_id="run-20261027-140000",
            contract_version="1.4.0",
            prompt_version="agent-v1:abc123",
            workspace="demo-repo",
            base_commit="abc123",
            models=list(profiles),
            slots=[
                SlotInfo(slot="A", default_model=profiles[0].id),
                SlotInfo(slot="B", default_model=profiles[-1].id),
            ],
            limits=Limits(
                max_steps=30,
                max_output_tokens=2048,
                max_wall_clock_s=180,
                test_timeout_s=30,
                cone_max_depth=4,
                cone_max_nodes=40,
            ),
            budget=Budget(budget_usd=100, spent_usd=0, warn_at=[0.5, 0.8], block_at=0.9),
        ),
    )


def status(number: int, profile: ModelProfile, state: str = "working") -> AgentStatusEvent:
    return AgentStatusEvent(
        seq=1,
        ts=TS,
        type="agent.status",
        payload=AgentStatus.model_validate(
            dict(
                agent_id=f"agent-{number}",
                slot="A" if number == 1 else "B",
                change_id=f"change-{number}",
                task="Fix pricing",
                model=profile.id,
                state=state,
                step=0,
                max_steps=30,
                detail=None,
            )
        ),
    )


def usage(number: int, model: str | None, **updates: object) -> UsageTickEvent:
    values: dict[str, object] = dict(
        agent_id=f"agent-{number}",
        model=model,
        input_tokens=100,
        output_tokens=20,
        cached_tokens=40,
        cost_usd=0.01,
        spent_usd=90,
        budget_usd=100,
        brake="ok",
    )
    return UsageTickEvent(
        seq=1, ts=TS, type="usage.tick", payload=UsageTick.model_validate(values | updates)
    )


def resolved(
    number: int, decision: str = "approved", merge: str | None = "merged"
) -> ChangeResolvedEvent:
    return ChangeResolvedEvent(
        seq=1,
        ts=TS,
        type="change.resolved",
        payload=ChangeResolved.model_validate(
            dict(
                agent_id=f"agent-{number}",
                change_id=f"change-{number}",
                revision=1,
                decision=decision,
                reason=None,
                comment=None,
                merge=merge,
                merge_commit="def456" if merge == "merged" else None,
                conflict=None,
            )
        ),
    )


def write_log(tmp_path: Path, events: list[ServerEvent], *, fake: bool = False) -> Path:
    log = tmp_path / "run.jsonl"
    log.write_text(
        "".join(
            event.model_copy(
                update={"seq": seq, "ts": f"2026-10-27T14:00:{seq:02}.000Z"}
            ).model_dump_json()
            + "\n"
            for seq, event in enumerate(events, 1)
        )
    )
    first = events[0]
    assert first.type == "run.started"
    provenance_path(log).write_text(
        Provenance(
            exporter="oversight.fake_recording.v1" if fake else "oversight.record_run.v1",
            sha256=hashlib.sha256(log.read_bytes()).hexdigest(),
            run_id=first.payload.run_id,
            event_count=len(events),
        ).model_dump_json()
    )
    return log


def test_two_models_totals_outcome_and_outputs(tmp_path: Path) -> None:
    log = write_log(
        tmp_path,
        [
            started(META, OPEN),
            status(1, META),
            status(2, OPEN),
            usage(1, META.model),
            usage(2, OPEN.model),
            usage(1, META.model),
            resolved(1),
            status(1, META, "done"),
            resolved(2, "rejected", None),
            status(2, OPEN, "stopped"),
        ],
    )
    result = manifest.build_manifest(log, expect_b=OPEN.model, real=True)
    assert not result.checks
    a, b = result.agents
    assert (a.requested_model, a.provider, a.reported_models, a.mode) == (
        META.model,
        "meta",
        {META.model: 2},
        "contributor",
    )
    assert (b.requested_model, b.provider, b.reported_models) == (
        OPEN.model,
        "baseten",
        {OPEN.model: 1},
    )
    assert (a.calls, a.input_tokens, a.output_tokens, a.cached_tokens, a.cost_usd) == (
        2,
        200,
        40,
        80,
        0.02,
    )
    assert a.cached_input_share == 0.4
    assert a.prompt_version == "agent-v1:abc123"
    assert a.limits["max_steps"] == 30
    assert (a.final_status, a.decision, a.merge_result) == ("done", "approved", "merged")
    assert (b.final_status, b.decision, b.merge_result) == ("stopped", "rejected", None)
    assert a.first_event_time == "2026-10-27T14:00:02.000Z"
    assert a.last_event_time == "2026-10-27T14:00:08.000Z"
    assert manifest.main([str(log)]) == 0
    data = json.loads(Path(str(log) + ".manifest.json").read_text())
    assert data["agents"][0]["calls"] == 2
    table = Path(str(log) + ".manifest.md").read_text()
    assert "40.0%" in table and "Limits: max_steps=30" in table


def test_inherited_b_and_actual_agent_override(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = write_log(tmp_path, [started(META), status(2, META), usage(2, META.model)])
    assert manifest.main([str(log), "--expect-b", OPEN.model]) == 1
    assert len(capsys.readouterr().err.splitlines()) == 1
    assert manifest.build_manifest(log).agents[0].requested_model == META.model
    # Offered slot defaults are not proof of the model an individual agent selected.
    log = write_log(tmp_path, [started(META, OPEN), status(2, META), usage(2, META.model)])
    assert manifest.build_manifest(log, expect_b=OPEN.model).checks


@pytest.mark.parametrize("metric", manifest.METRICS)
def test_unknown_usage_poisons_only_its_total(tmp_path: Path, metric: str) -> None:
    log = write_log(
        tmp_path,
        [
            started(META),
            status(1, META),
            usage(1, META.model),
            usage(1, META.model, **{metric: None}),
        ],
    )
    result = manifest.build_manifest(log)
    a = result.agents[0]
    assert getattr(a, metric) is None
    assert a.calls == 2 and a.unknown_usage_calls == 1
    assert a.unknown_calls[metric] == 1
    if metric in {"input_tokens", "cached_tokens"}:
        assert a.cached_input_share is None
    assert "unknown" in manifest.markdown(result)
    assert "2 (1 / 0)" in manifest.markdown(result)


@pytest.mark.parametrize("unknown", [True, False])
def test_zero_is_observed_and_no_calls_is_unknown(tmp_path: Path, unknown: bool) -> None:
    events: list[ServerEvent] = [started(META), status(1, META)]
    if not unknown:
        events.append(
            usage(1, META.model, input_tokens=0, output_tokens=0, cached_tokens=0, cost_usd=0)
        )
    a = manifest.build_manifest(write_log(tmp_path, events)).agents[0]
    assert a.input_tokens == (None if unknown else 0)
    assert a.cost_usd == (None if unknown else 0)
    assert a.cached_input_share is None


def test_every_reported_model_is_counted_and_mismatch_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = write_log(
        tmp_path,
        [
            started(META),
            status(1, META),
            usage(1, META.model),
            usage(1, "other"),
            usage(1, "other"),
        ],
    )
    assert manifest.main([str(log)]) == 1
    assert len(capsys.readouterr().err.splitlines()) == 1
    result = manifest.build_manifest(log)
    assert result.agents[0].reported_models == {META.model: 1, "other": 2}
    assert "differs from requested" in result.checks[0]


@pytest.mark.parametrize("reported", [None, META.model])
def test_expect_b_checks_reported_model(tmp_path: Path, reported: str | None) -> None:
    log = write_log(tmp_path, [started(META, OPEN), status(2, OPEN), usage(2, reported)])
    assert manifest.build_manifest(log, expect_b=OPEN.model).checks


def test_expect_b_requires_an_observed_agent(tmp_path: Path) -> None:
    log = write_log(tmp_path, [started(META, OPEN), status(1, META)])
    assert manifest.build_manifest(log, expect_b=OPEN.model).checks == [
        "--expect-b: no recorded agent in slot B"
    ]


@pytest.mark.parametrize("profile", [FAKE, META])
def test_fake_receipt_cannot_claim_real_execution(
    tmp_path: Path, profile: ModelProfile, capsys: pytest.CaptureFixture[str]
) -> None:
    log = write_log(
        tmp_path, [started(profile), status(1, profile), usage(1, profile.model)], fake=True
    )
    assert manifest.main([str(log), "--real"]) == 1
    assert len(capsys.readouterr().err.splitlines()) == 1
    result = manifest.build_manifest(log)
    assert result.recording_kind == "scripted fake model"
    assert result.agents[0].requested_model == "scripted fake model"
    assert result.agents[0].provider == "fake"
    assert manifest.main([str(log)]) == 0


def test_fake_profile_with_real_receipt_fails(tmp_path: Path) -> None:
    log = write_log(tmp_path, [started(FAKE), status(1, FAKE)])
    result = manifest.build_manifest(log)
    assert result.recording_kind == "scripted fake model"
    assert result.checks


def test_scripted_level2_fixture_is_fake_and_unchanged(tmp_path: Path) -> None:
    log = Path(__file__).resolve().parents[2] / "fixtures/events/scripted-level2.jsonl"
    before = log.read_bytes(), provenance_path(log).read_bytes()
    out = tmp_path / "fixture.json"
    assert manifest.main([str(log), "--out", str(out)]) == 0
    result = manifest.build_manifest(log)
    assert result.recording_kind == "scripted fake model"
    assert len(result.agents) == 2
    agents_by_slot = {agent.slot: agent for agent in result.agents}
    assert set(agents_by_slot) == {"A", "B"}
    a, b = agents_by_slot["A"], agents_by_slot["B"]
    assert a.provider == b.provider == "fake"
    assert a.requested_model == b.requested_model == "scripted fake model"
    assert (a.calls, b.calls) == (10, 7)
    assert (a.final_status, a.decision, a.merge_result) == ("done", "approved", "merged")
    assert (b.final_status, b.decision) == ("stopped", "rejected")
    assert (log.read_bytes(), provenance_path(log).read_bytes()) == before
    assert out.exists() and out.with_suffix(".md").exists()


@pytest.mark.parametrize(
    "case", ["invalid-event", "gap", "second-run", "receipt", "missing-receipt"]
)
def test_invalid_input_fails_one_line(
    tmp_path: Path, case: str, capsys: pytest.CaptureFixture[str]
) -> None:
    log = write_log(tmp_path, [started(META), status(1, META)])
    if case == "missing-receipt":
        provenance_path(log).unlink()
    elif case == "receipt":
        log.write_text(log.read_text() + "\n")
    elif case == "invalid-event":
        log.write_text(log.read_text() + '{"seq":3,"type":"usage.tick","payload":{}}\n')
    elif case == "gap":
        log.write_text(log.read_text().replace('"seq":2', '"seq":3'))
    else:
        log = write_log(tmp_path, [started(META), started(META)])
    assert manifest.main([str(log)]) == 1
    assert len(capsys.readouterr().err.splitlines()) == 1
    assert not Path(str(log) + ".manifest.json").exists()


def test_fatal_errors_are_counted_without_inventing_status(tmp_path: Path) -> None:
    error = ErrorReportEvent(
        seq=1,
        ts=TS,
        type="error",
        payload=ErrorReport(
            source="model_api",
            message="Failed",
            agent_id="agent-1",
            change_id="change-1",
            fatal=True,
        ),
    )
    log = write_log(tmp_path, [started(META), status(1, META), error])
    a = manifest.build_manifest(log).agents[0]
    assert (a.errors, a.fatal_errors, a.final_status) == (1, 1, "working")


def test_output_cannot_replace_input(tmp_path: Path) -> None:
    log = write_log(tmp_path, [started(META), status(1, META)])
    out = tmp_path / "out.json"
    out.symlink_to(log)
    before = log.read_bytes()
    assert manifest.main([str(log), "--out", str(out)]) == 1
    assert log.read_bytes() == before
