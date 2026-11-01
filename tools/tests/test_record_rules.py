"""Rules exports stay fake-only and confined to the completed harness-owned runtime."""

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from tools import record_rules
from tools.recording import RecordError, load_recording, validate_text
from tools.rules_scenario import settings

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "fixtures/events/scripted-rules.jsonl"


def test_fixture_is_fake_only_with_real_rules_and_person_revert():
    load_recording(LOG, fake_fixture=True)
    with pytest.raises(RecordError):
        load_recording(LOG)
    events = validate_text(LOG.read_text(), fake_fixture=True)
    merged = [e for e in events if e.type == "change.resolved"]
    assert len(merged) == 1 and merged[0].payload.decided_by == "rules"
    assert merged[0].payload.assessment is not None
    assert all(r.result == "pass" and r.evidence for r in merged[0].payload.assessment.rules)
    inverse = next(e.payload for e in events if e.type == "revert.resolved")
    assert inverse.result == "reverted" and inverse.decided_by == "person"
    assert inverse.resolution_seq == merged[0].seq
    assert any(e.type == "error" and "code=review_invalid" in e.payload.message for e in events)


def test_settings_never_load_environment_or_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr("oversight.settings.Settings.load", lambda: pytest.fail("read .env"))
    config = settings(tmp_path / ".runtime")
    assert config.agent_model == "fake" and config.review_model == "fake-b"
    assert config.meta_api_key is None and config.baseten_api_key is None
    assert config.budget_usd is not None and config.budget_usd > 0
    assert config.reviewer_fake_b_script != config.fake_script
    assert config.reviewer_fake_b_script is not None
    assert config.reviewer_fake_b_script.is_relative_to(tmp_path)


@pytest.mark.parametrize("failure", [None, "scenario", "reset"])
def test_three_resets_export_only_after_all_scenarios(tmp_path, monkeypatch, failure):
    calls = []
    exported = []

    def run(number, scratch, runtime):
        assert scratch == runtime.parent and runtime.name == ".runtime"
        calls.append((number, runtime))
        if number == 2 and failure == "scenario":
            raise RuntimeError("C unavailable")
        return "graph", "different" if number == 2 and failure == "reset" else "initial"

    def latest(runs):
        assert runs == calls[-1][1] / "runs"
        return runs / "run-20261101-172000.sqlite"

    def export(store, out, **options):
        assert len(calls) == 3 and len({runtime for _, runtime in calls}) == 1
        exported.append((store, out, options))
        return SimpleNamespace(count=59)

    monkeypatch.setattr(record_rules.demo, "adopting_orphans", nullcontext)
    monkeypatch.setattr(record_rules, "run_once", run)
    monkeypatch.setattr(record_rules, "latest_store", latest)
    monkeypatch.setattr(record_rules, "record_run", export)
    if failure:
        with pytest.raises((RuntimeError, AssertionError)):
            record_rules.run_checks(out=tmp_path / "rules.jsonl")
        assert not exported
    else:
        record_rules.run_checks(out=tmp_path / "rules.jsonl")
        assert exported[0][2] == {"fake_fixture": True}
