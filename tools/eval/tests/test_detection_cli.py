"""Default paths have no credentials or SDK objects; paid gates precede every send."""

import json
import sys

import pytest
from tools.eval.behavior.catalog import sha256
from tools.eval.detection import __main__ as cli
from tools.eval.detection.accounting import task_for
from tools.eval.ledger import Ledger
from tools.eval.tasks import BenchError

ESTIMATE = [
    "--meta-input-usd",
    "1",
    "--meta-output-usd",
    "2",
    "--baseten-input-usd",
    "3",
    "--baseten-output-usd",
    "4",
    "--input-tokens",
    "1000",
    "--output-tokens",
    "512",
    "--calls",
    "8",
]


@pytest.fixture
def no_providers(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("default/dry-run/fake/refusal must not load credentials or construct a client")

    monkeypatch.setattr(cli, "provider_factory", forbidden)
    monkeypatch.setattr("oversight.agents.DecisionClient.__init__", forbidden)
    monkeypatch.setattr("oversight.agents.OpenAIClient.__init__", forbidden)
    monkeypatch.setattr("oversight.settings.Settings.load", forbidden)
    monkeypatch.setattr("oversight.settings.read_env_file", forbidden)


def test_default_and_projection_are_provider_free(tmp_path, monkeypatch, capsys, no_providers):
    out = tmp_path / "paid/results.jsonl"
    monkeypatch.setattr(cli, "CANONICAL_OUTPUT", out)
    assert cli.main([]) == 1
    assert cli.main(ESTIMATE) == 0
    projected = json.loads(capsys.readouterr().out)
    assert len(projected["configurations"]) == 4
    assert projected["spent_usd"] == 0
    assert not out.parent.exists()


@pytest.mark.parametrize(
    "extras",
    [
        [],
        ["--paid", "--cap-usd", "21"],
        ["--paid", "--cap-usd", "nan"],
        ["--paid", "--cap-usd", "0"],
        ["--paid", "--meta-input-usd", "0"],
        ["--paid", "--baseten-input-usd", "-1"],
    ],
)
def test_paid_flags_refuse_before_writes_or_clients(tmp_path, extras, no_providers):
    out = tmp_path / "other/results.jsonl"
    assert cli.main(["pilot", *ESTIMATE, "--out", str(out), *extras]) == 1
    assert not out.parent.exists()


def test_label_pin_precedes_every_other_precondition(tmp_path, monkeypatch, capsys, no_providers):
    labels = tmp_path / "labels.json"
    labels.write_text("not json")

    def forbidden(*args, **kwargs):
        pytest.fail("hash mismatch must precede all other work")

    monkeypatch.setattr(cli, "configured_rates", forbidden)
    monkeypatch.setattr(cli, "history", forbidden)
    assert cli.main(["run", "--labels", str(labels), "--labels-sha256", "0" * 64]) == 1
    assert "SHA-256 mismatch" in capsys.readouterr().err
    assert labels.read_text() == "not json"


def test_frozen_drift_refuses_without_creating_output(tmp_path, monkeypatch, no_providers):
    def drift():
        raise BenchError("frozen baseline drift")

    monkeypatch.setattr(cli, "check_baseline", drift)
    out = tmp_path / "fake/results.jsonl"
    assert (
        cli.main(
            [
                "pilot",
                "--fake",
                "--fake-choice",
                "asked_for",
                *ESTIMATE,
                "--out",
                str(out),
                "--demo-python",
                sys.executable,
            ]
        )
        == 1
    )
    assert not out.parent.exists()


def test_canonical_historical_charges_reduce_projection(
    tmp_path, monkeypatch, capsys, no_providers
):
    from tools.eval.behavior.catalog import load_patches

    out = tmp_path / "paid/results.jsonl"
    monkeypatch.setattr(cli, "CANONICAL_OUTPUT", out)
    ledger = Ledger(out, 20, None, multi_tariff=True)
    try:
        rec = ledger.reserve(
            task_for(next(p for p in load_patches() if p.id == "d01")),
            "other-task-front",
            "history",
            1,
        )
        rec.status = "finished"
        rec.charged_usd = 19.9
        ledger.save()
    finally:
        ledger.close()
    assert cli.main(ESTIMATE) == 1
    assert "projection" in capsys.readouterr().err


def test_missing_labels_refuse_scored_run(no_providers, capsys):
    assert cli.main(["run", "--fake", "--fake-choice", "asked_for"]) == 1
    assert "--labels and --labels-sha256" in capsys.readouterr().err


def test_runner_never_reads_construction_key(tmp_path, monkeypatch, no_providers):
    from pathlib import Path

    from tools.eval.detection.labels import load_labels
    from tools.eval.tests.test_detection_labels import synthetic_labels

    path, _ = synthetic_labels(tmp_path)
    raw = path.read_bytes()
    original = Path.read_bytes

    def guarded(path):
        assert path.name != "construction-key.json"
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", guarded)
    assert len(load_labels(path, sha256(raw)).answers) == 20
    assert cli.main([*ESTIMATE, "--out", str(tmp_path / "alternate.jsonl")]) == 1


def test_report_preserves_unknowns_and_reservations(tmp_path):
    from tools.eval.behavior.catalog import load_patches

    out = tmp_path / "results.jsonl"
    ledger = Ledger(out, 20, None, multi_tariff=True)
    try:
        rec = ledger.reserve(
            task_for(next(p for p in load_patches() if p.id == "d01")), "other-front", "unknown", 1
        )
        rec.unknown = True
        ledger.save()
        state = cli.history(out, 20, ready=False)
        assert state is not None and state.records[0].unknown
        with pytest.raises(BenchError, match="unknown"):
            cli.history(out, 20)
    finally:
        ledger.close()


def test_dry_run_cannot_ignore_unconsolidated_history(tmp_path, monkeypatch, no_providers):
    out = tmp_path / "paid/results.jsonl"
    monkeypatch.setattr(cli, "CANONICAL_OUTPUT", out)
    older = tmp_path / "legacy/ledger.json"
    older.parent.mkdir()
    older.write_text("{}")
    assert cli.main(ESTIMATE) == 1


def test_paid_requires_existing_shared_history(tmp_path, monkeypatch, no_providers):
    out = tmp_path / "paid/results.jsonl"
    monkeypatch.setattr(cli, "CANONICAL_OUTPUT", out)
    args = cli.parser_for().parse_args(["pilot", "--paid", *ESTIMATE])
    with pytest.raises(BenchError, match="existing consolidated"):
        cli.configured_rates(args)
