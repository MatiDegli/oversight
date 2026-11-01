"""Proof 2 measures use frozen definitions and independent expected values."""

import json
import sys
from hashlib import sha256
from pathlib import Path

import pytest
from tools.eval.proof2.__main__ import main
from tools.eval.proof2.report import write_report
from tools.eval.proof2.report.output import counted
from tools.eval.tests.report_events import event, files, receipts, renumber, scenario


def report(tmp_path, capsys, events=None, results=None):
    write_report(*files(tmp_path, events, results))
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_meta(tmp_path, capsys):
    log, results = files(tmp_path)
    write_report(log, results)
    meta = json.loads(capsys.readouterr().out.splitlines()[0])
    assert meta == dict(
        record="meta",
        schema=1,
        run_id=None,
        contract_version=None,
        prompt_version=None,
        through_seq=26,
        changes=3,
        revisions=3,
        with_hidden=3,
        log_sha256=sha256(log.read_bytes()).hexdigest(),
        results_sha256=sha256(results.read_bytes()).hexdigest(),
        price_source="https://dev.meta.ai/docs/pricing-rate-limits",
        price_retrieved="2026-10-30",
    )
    results.write_text("")
    write_report(Path("fixtures/events/recorded-demo.jsonl"), results)
    demo = json.loads(capsys.readouterr().out.splitlines()[0])
    assert (demo["through_seq"], demo["revisions"], demo["with_hidden"]) == (69, 2, 0)
    assert demo["run_id"] is not None


@pytest.mark.parametrize(
    "n, expected", [(9, {"count": 3, "n": 9}), (10, {"count": 3, "n": 10, "pct": 30.0})]
)
def test_counted(n, expected):
    assert counted(3, n) == expected


@pytest.mark.parametrize(
    "edit",
    [
        {"kind": "other"},
        {"task": "p99"},
        {"result": []},
        {"result": {}},
        {"result": {"exit_code": True}},
        {"result": {"exit_code": 0.0}},
        {"change_id": "change-0"},
        {"revision": 0},
        {"revision": True},
        {"revision": 3},
        {"head_commit": "different"},
    ],
)
def test_invalid_results(tmp_path, monkeypatch, capsys, edit):
    rows = receipts()
    rows[1].update(edit)
    log, results = files(tmp_path, results=rows)
    out = tmp_path / "out"
    monkeypatch.setattr(
        sys,
        "argv",
        ["proof2", "report", "--log", str(log), "--results", str(results), "--out", str(out)],
    )
    assert main() == 2
    assert "line 2" in json.loads(capsys.readouterr().out.splitlines()[0])["error"]
    assert not out.exists()


@pytest.mark.parametrize(
    "rows",
    [
        lambda: [receipts()[0], receipts()[0]],
        lambda: [receipts()[1], receipts()[1]],
        lambda: [receipts()[1], {**receipts()[2], "task": "p05"}],
    ],
)
def test_duplicate_and_conflicting_results(tmp_path, rows):
    with pytest.raises(ValueError, match="line 2"):
        write_report(*files(tmp_path, results=rows()))


def test_bad_json_and_bad_log(tmp_path):
    log, results = files(tmp_path)
    results.write_text("\n{bad\n")
    with pytest.raises(ValueError, match="line 2"):
        write_report(log, results)
    for seq in (1, 28):
        events = scenario()
        events[-1] = events[-1].model_copy(update={"seq": seq})
        with pytest.raises(ValueError, match="seq"):
            write_report(*files(tmp_path, events))


def test_cli_output(tmp_path, monkeypatch, capsys):
    log, results = files(tmp_path)
    argv = ["proof2", "report", "--log", str(log), "--results", str(results)]
    monkeypatch.setattr(sys, "argv", argv)
    assert main() == 0
    expected = capsys.readouterr().out.encode()
    assert expected.endswith(b"\n")
    assert main() == 0
    assert capsys.readouterr().out.encode() == expected
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", [*argv, "--out", str(out)])
    assert main() == 0
    assert capsys.readouterr().out == ""
    assert out.read_bytes() == expected


@pytest.mark.parametrize("command", ["check", "validate"])
def test_lazy_import(monkeypatch, command):
    import builtins

    from tools.eval.proof2 import __main__ as cli

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert "report" not in name
        return original(name, *args, **kwargs)

    async def fake(*args, **kwargs):
        return {"exit_code": 0} if command == "check" else []

    monkeypatch.setattr(builtins, "__import__", guarded)
    monkeypatch.setattr(cli, command, fake)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "proof2",
            command,
            *(["--task", "p04", "--tree", "."] if command == "check" else ["--source", "."]),
        ],
    )
    assert cli.main() == 0


def test_correctness_and_selections(tmp_path, capsys):
    rows = report(tmp_path, capsys)
    revisions = [r for r in rows if r["record"] == "revision"]
    assert [
        (
            r["change_id"],
            r["revision"],
            r["tests"],
            r["hidden"],
            r["correct"],
            r["first_green"],
            r["merged"],
            r["verdicts"],
            r["request"],
            r["previous"],
        )
        for r in revisions
    ] == [
        (
            "change-1",
            1,
            "passed",
            "fail",
            False,
            True,
            False,
            ["changes_requested"],
            "cross_review",
            None,
        ),
        ("change-1", 2, "passed", "pass", True, False, True, ["clean"], None, 1),
        ("change-2", 1, "passed", "pass", True, True, True, ["clean"], None, None),
    ]
    assert [(r["regress_vs_base"], r["regress_vs_previous"]) for r in revisions] == [
        ({"status": "known", "ids": ["t_old"]}, {"status": "not_applicable", "ids": []}),
        ({"status": "known", "ids": []}, {"status": "known", "ids": []}),
        ({"status": "not_applicable", "ids": []}, {"status": "not_applicable", "ids": []}),
    ]
    changes = [r for r in rows if r["record"] == "change"]
    assert [
        (
            r["change_id"],
            r["agent_id"],
            r["task"],
            r["proposals"],
            r["rounds"],
            r["end"],
            r["first_green"],
            r["merged_revision"],
            r["merged_by"],
        )
        for r in changes
    ] == [
        ("change-1", "agent-1", "p04", 2, 1, "merged", 1, 2, "person"),
        ("change-2", "agent-2", "p10", 1, 0, "merged", 1, 1, "person"),
        ("change-3", "agent-3", None, 0, 0, "stopped", None, None, None),
    ]
    arms = [r for r in rows if r["record"] == "arm"]
    assert [
        (
            a["arm"],
            a["correct"],
            a["incorrect"],
            a["unknown"],
            a["not_selected"],
            a["regressions_let_through"],
        )
        for a in arms
    ] == [
        (
            "without_oversight",
            {"count": 1, "n": 3},
            1,
            0,
            1,
            {"changes": 1, "checks": 1, "unknown_changes": 0},
        ),
        (
            "with_oversight",
            {"count": 2, "n": 3},
            0,
            0,
            1,
            {"changes": 0, "checks": 0, "unknown_changes": 0},
        ),
    ]


def test_missing_revision_result(tmp_path, capsys):
    rows = report(tmp_path, capsys, results=[r for r in receipts() if r.get("revision") != 2])
    revision = next(r for r in rows if r.get("revision") == 2)
    assert (revision["hidden"], revision["correct"]) == ("unknown", None)
    assert revision["regress_vs_base"] == {"status": "unknown", "ids": []}
    assert revision["regress_vs_previous"] == {"status": "unknown", "ids": []}
    arm = next(r for r in rows if r.get("arm") == "with_oversight")
    assert (arm["correct"], arm["unknown"]) == ({"count": 1, "n": 3}, 1)


@pytest.mark.parametrize("task, expected", [("p04", False), ("p10", True)])
def test_failed_suite_first_green_and_d3(tmp_path, capsys, task, expected):
    failed = dict(
        outcome="failed", passed=0, failed=1, skipped=0, duration_ms=1, failed_tests=["t"]
    )
    events = renumber(
        [
            event("change.proposed", tests=failed),
            event("change.proposed", revision=2, head_commit="h1b"),
            event("change.resolved"),
        ]
    )
    rows = report(
        tmp_path, capsys, events, [{**receipts()[1], "task": task, "result": {"exit_code": 0}}]
    )
    revisions = [r for r in rows if r["record"] == "revision"]
    assert (revisions[0]["correct"], revisions[0]["merged"], revisions[1]["first_green"]) == (
        expected,
        True,
        True,
    )
    assert next(r for r in rows if r["record"] == "change")["first_green"] == 2


def test_unattributable_regressions_and_missing_base(tmp_path, capsys):
    results = receipts()[1:]
    results[0]["result"] = dict(exit_code=1, failed=0, errors=1, failing_test_ids=[])
    rows = report(tmp_path, capsys, results=results)
    revisions = [r for r in rows if r["record"] == "revision"]
    assert [r["regress_vs_base"]["status"] for r in revisions] == [
        "unknown",
        "unknown",
        "not_applicable",
    ]
    assert revisions[1]["regress_vs_previous"] == {"status": "unknown", "ids": []}


def test_spend(tmp_path, capsys):
    rows = report(tmp_path, capsys)
    changes = [r for r in rows if r["record"] == "change"]
    assert [c["review_cost"] for c in changes] == [
        {"calls": 2, "usd": 0.1, "known_usd": 0.1, "coverage": "complete"},
        {"calls": 1, "usd": 0.05, "known_usd": 0.05, "coverage": "complete"},
        {"calls": 0, "usd": 0.0, "known_usd": 0.0, "coverage": "complete"},
    ]
    arms = [r for r in rows if r["record"] == "arm"]
    assert [a["cost"] for a in arms] == [
        {"calls": 3, "usd": 0.3, "known_usd": 0.3, "coverage": "complete"},
        {"calls": 7, "usd": 0.65, "known_usd": 0.65, "coverage": "complete"},
    ]
    assert [a["usd_per_correct"] for a in arms] == [0.3, 0.325]
    assert [a["by_role"] for a in arms] == [
        {"agent": {"calls": 3, "usd": 0.3, "known_usd": 0.3, "coverage": "complete"}},
        {
            "agent": {"calls": 4, "usd": 0.5, "known_usd": 0.5, "coverage": "complete"},
            "review": {"calls": 3, "usd": 0.15, "known_usd": 0.15, "coverage": "complete"},
        },
    ]
    assert [a["problems"] for a in arms] == [[], []]


@pytest.mark.parametrize(
    "variation, usd, known, coverage, calls, agent_usd, agent_known",
    [
        (2, None, 0.45, "missing_metrics", 7, None, 0.3),
        (3, 0.75, 0.75, "complete", 8, 0.5, 0.5),
        (4, 0.75, 0.75, "complete", 8, 0.5, 0.5),
        (6, None, 0.55, "missing_metrics", 7, None, 0.4),
    ],
)
def test_spend_variations(
    tmp_path, capsys, variation, usd, known, coverage, calls, agent_usd, agent_known
):
    events = scenario()
    if variation in (2, 6):
        index = 19 if variation == 2 else 1
        update = (
            dict(input_tokens=None, cached_tokens=None, output_tokens=None)
            if variation == 2
            else dict(model="unpriced")
        )
        events[index] = events[index].model_copy(
            update={"payload": events[index].payload.model_copy(update=update)}
        )
    else:
        events.append(
            event(
                "usage.tick",
                142,
                role="plan" if variation == 3 else None,
                change_id=None if variation == 3 else "change-1",
            )
        )
    rows = report(tmp_path, capsys, renumber(events))
    without, with_ = [r for r in rows if r["record"] == "arm"]
    assert with_["cost"] == dict(calls=calls, usd=usd, known_usd=known, coverage=coverage)
    assert with_["by_role"]["agent"] == dict(
        calls=4,
        usd=agent_usd,
        known_usd=agent_known,
        coverage="complete" if agent_usd is not None else "missing_metrics",
    )
    assert with_["by_role"]["review"]["usd"] == 0.15
    assert with_["usd_per_correct"] == (None if variation in (2, 6) else 0.375)
    assert without["cost"] == dict(
        calls=3,
        usd=None if variation in (4, 6) else 0.3,
        known_usd=0.2 if variation == 6 else 0.3,
        coverage="missing_metrics" if variation in (4, 6) else "complete",
    )
    if variation == 3:
        assert with_["by_role"]["plan"] == dict(
            calls=1, usd=0.1, known_usd=0.1, coverage="complete"
        )
    if variation == 4:
        assert with_["by_role"]["unknown_role"]["usd"] == 0.1
        assert without["problems"] == ["1 ticks without a role"]


def test_zero_and_unattributed_cost(tmp_path, capsys):
    for tokens, expected in [(0, 0.0), (1000000, 0.1)]:
        rows = report(
            tmp_path, capsys, [event("usage.tick", change_id=None, input_tokens=tokens)], []
        )
        without = next(r for r in rows if r.get("arm") == "without_oversight")
        assert without["cost"] == dict(
            calls=1, usd=expected, known_usd=expected, coverage="complete"
        )
        assert without["usd_per_correct"] is None
        assert without["problems"] == ["1 agent ticks without a change"]
