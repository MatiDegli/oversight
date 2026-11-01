"""Proof 2 measures use frozen definitions and independent expected values."""

import asyncio
import io
import json
import sys
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest
from tools.eval.proof2.__main__ import main
from tools.eval.proof2.report import write_report
from tools.eval.proof2.report.arms import build_arms
from tools.eval.proof2.report.inputs import load_inputs
from tools.eval.proof2.report.output import counted, write_lines
from tools.eval.proof2.report.spend import add_spend
from tools.eval.proof2.report.timing import add_timing
from tools.eval.tests.report_events import event, files, receipts, renumber, scenario


def report(tmp_path, capsys, events=None, results=None):
    write_report(*files(tmp_path, events, results))
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_meta(tmp_path, capsys, monkeypatch):
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
    monkeypatch.chdir(tmp_path)
    demo_log = Path(__file__).resolve().parents[3] / "fixtures/events/recorded-demo.jsonl"
    write_report(demo_log, results)
    demo = json.loads(capsys.readouterr().out.splitlines()[0])
    assert (demo["through_seq"], demo["revisions"], demo["with_hidden"]) == (69, 2, 0)
    assert (demo["run_id"], demo["contract_version"], demo["prompt_version"]) == (
        "run-20261027-185214",
        "1.3.0",
        "agent-v1-3739943df858",
    )


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


def test_tests_only_base_is_refused(tmp_path):
    rows = [{"kind": "base", "task": "p10", "result": {"exit_code": 0}}]
    with pytest.raises(ValueError, match=r"^results line 1: base requires a bug or feature task$"):
        write_report(*files(tmp_path, results=rows))


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
    import importlib

    from tools.eval.proof2 import __main__ as cli

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert "report" not in name
        return original(name, *args, **kwargs)

    async def fake(*args, **kwargs):
        return {"exit_code": 0} if command == "check" else []

    monkeypatch.setattr(builtins, "__import__", guarded)
    importlib.reload(cli)
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


def test_inputs_uses_relative_runner_import(monkeypatch):
    import builtins
    import importlib

    from tools.eval.proof2.report import inputs

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert name != "tools.eval.proof2.runner"
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    importlib.reload(inputs)


def test_correctness_and_selections(tmp_path, capsys):
    rows = report(tmp_path, capsys)
    revisions = [r for r in rows if r["record"] == "revision"]
    assert [
        (
            r["change_id"],
            r["revision"],
            r["seq"],
            r["head_commit"],
            r["task"],
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
            13,
            "h1a",
            "p04",
            "passed",
            "fail",
            False,
            True,
            False,
            ["changes_requested"],
            "cross_review",
            None,
        ),
        ("change-1", 2, 21, "h1b", "p04", "passed", "pass", True, False, True, ["clean"], None, 1),
        ("change-2", 1, 9, "h2", "p10", "passed", "pass", True, True, True, ["clean"], None, None),
    ]
    assert [(r["task"], r["seq"], r["head_commit"]) for r in revisions] == [
        ("p04", 13, "h1a"),
        ("p04", 21, "h1b"),
        ("p10", 9, "h2"),
    ]
    assert [(r["regress_vs_base"], r["regress_vs_previous"]) for r in revisions] == [
        ({"status": "known", "ids": ["t_old"]}, {"status": "not_applicable", "ids": []}),
        ({"status": "known", "ids": []}, {"status": "known", "ids": []}),
        ({"status": "not_applicable", "ids": []}, {"status": "not_applicable", "ids": []}),
    ]


def test_change_records_and_arm_counts(tmp_path, capsys):
    rows = report(tmp_path, capsys)
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
    for arm in arms:
        assert arm["changes"] == 3
        assert arm["questions"] == {"status": "not_built"}
        assert arm["questions_not_needed"] == {"status": "not_built"}
    assert [
        (
            a["arm"],
            a["changes"],
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
            3,
            {"count": 1, "n": 3},
            1,
            0,
            1,
            {"changes": 1, "checks": 1, "unknown_changes": 0},
        ),
        (
            "with_oversight",
            3,
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


def test_e1_e2(tmp_path, capsys):
    rows = report(tmp_path, capsys)
    e1 = next(r for r in rows if r["record"] == "E1")
    assert (e1["n"], e1["duplicate_reviews"]) == (3, 0)
    assert [
        (c["verdict"], c["hidden_pass"], c["hidden_fail"], c["hidden_unknown"]) for c in e1["cells"]
    ] == [
        ("clean", {"count": 2, "n": 3}, {"count": 0, "n": 3}, {"count": 0, "n": 3}),
        ("changes_requested", {"count": 0, "n": 3}, {"count": 1, "n": 3}, {"count": 0, "n": 3}),
        ("unknown", {"count": 0, "n": 3}, {"count": 0, "n": 3}, {"count": 0, "n": 3}),
    ]
    assert e1["review_cost"] == dict(calls=3, usd=0.15, known_usd=0.15, coverage="complete")
    assert next(r for r in rows if r["record"] == "E2") == dict(
        record="E2",
        changes_with_requests=1,
        rounds=1,
        requests_by=dict(person=0, rules=0, cross_review=1, lead_agent=0, unrecorded=0),
        regress_vs_base=dict(checks=1, unknown_revisions=0),
        regress_vs_previous=dict(checks=0, unknown_revisions=0),
        hidden_by_round=[
            {"round": 0, "pass": 1, "fail": 1, "unknown": 0},
            {"round": 1, "pass": 1, "fail": 0, "unknown": 0},
        ],
        findings=dict(status="not_built"),
    )
    rows = report(tmp_path, capsys, results=[r for r in receipts() if r.get("revision") != 2])
    e1 = next(r for r in rows if r["record"] == "E1")
    assert e1["cells"][0]["hidden_pass"] == {"count": 1, "n": 3}
    assert e1["cells"][0]["hidden_unknown"] == {"count": 1, "n": 3}
    e2 = next(r for r in rows if r["record"] == "E2")
    assert e2["hidden_by_round"][1] == {"round": 1, "pass": 0, "fail": 0, "unknown": 1}
    assert (
        e2["regress_vs_base"]["unknown_revisions"],
        e2["regress_vs_previous"]["unknown_revisions"],
    ) == (1, 1)


def test_duplicate_reviews(tmp_path, capsys):
    events = [*scenario(), event("change.reviewed", 142)]
    rows = report(tmp_path, capsys, renumber(events))
    e1 = next(r for r in rows if r["record"] == "E1")
    assert (e1["n"], e1["duplicate_reviews"]) == (3, 1)
    assert e1["cells"][0]["hidden_fail"] == {"count": 0, "n": 3}
    assert e1["cells"][1]["hidden_fail"] == {"count": 1, "n": 3}
    revision = next(r for r in rows if r["record"] == "revision")
    assert revision["verdicts"] == ["changes_requested", "clean"]


def test_ten_reviewed_pairs(tmp_path, capsys):
    events = [
        e
        for i in range(1, 11)
        for e in (event("change.proposed", change=i), event("change.reviewed", change=i))
    ]
    results = [
        {**receipts()[1], "change_id": f"change-{i}", "result": {"exit_code": 0}}
        for i in range(1, 11)
    ]
    rows = report(tmp_path, capsys, renumber(events), results)
    e1 = next(r for r in rows if r["record"] == "E1")
    assert e1["n"] == 10
    assert e1["cells"][0]["hidden_pass"] == {"count": 10, "n": 10, "pct": 100.0}
    assert e1["cells"][2]["hidden_unknown"] == {"count": 0, "n": 10, "pct": 0.0}
    assert [r["change_id"] for r in rows if r["record"] == "change"][-2:] == [
        "change-9",
        "change-10",
    ]


@pytest.mark.parametrize("code, hidden", [(None, "unknown"), (0, "pass"), (1, "fail")])
def test_unknown_verdict_is_separate(tmp_path, capsys, code, hidden):
    events = renumber(
        [
            event("change.proposed"),
            event("change.reviewed", verdict="unknown", complete=False, problems=["unavailable"]),
        ]
    )
    results = [] if code is None else [{**receipts()[1], "result": {"exit_code": code}}]
    rows = report(tmp_path, capsys, events, results)
    e1 = next(r for r in rows if r["record"] == "E1")
    assert e1["n"] == 1
    assert e1["cells"][2][f"hidden_{hidden}"] == {"count": 1, "n": 1}
    assert e1["cells"][0][f"hidden_{hidden}"] == {"count": 0, "n": 1}
    assert e1["cells"][1][f"hidden_{hidden}"] == {"count": 0, "n": 1}


def test_last_merge_and_problem(tmp_path, capsys):
    events = renumber([*scenario(), event("change.resolved", 142)])
    rows = report(tmp_path, capsys, events)
    change = next(r for r in rows if r["record"] == "change")
    assert change["merged_revision"] == 1
    arm = next(r for r in rows if r.get("arm") == "with_oversight")
    assert arm["correct"] == {"count": 1, "n": 3}
    assert arm["problems"] == ["multiple merged resolutions for change-1"]


def test_timing(tmp_path, capsys):
    rows = report(tmp_path, capsys)
    assert [r["wall_ms"] for r in rows if r["record"] == "change"] == [
        {"without_oversight": 60000, "with_oversight": 140000},
        {"without_oversight": 40000, "with_oversight": 80000},
        {"without_oversight": None, "with_oversight": None},
    ]
    assert [r["wall_ms_per_correct"] for r in rows if r["record"] == "arm"] == [40000, 110000]
    assert next(r for r in rows if r["record"] == "run") == dict(
        record="run",
        agent_ms=188000,
        known_agent_ms=188000,
        agent_hours=0.052222,
        active_ms=128000,
        known_active_ms=128000,
        parallelism=1.46875,
        coverage="complete",
        problems=[],
        parallelism_over_time={"status": "not_built"},
    )


def test_plan_activity_unknown(tmp_path, capsys):
    events = renumber([*scenario(), event("usage.tick", 142, role="plan", change_id=None)])
    rows = report(tmp_path, capsys, events)
    assert next(r for r in rows if r["record"] == "run") == dict(
        record="run",
        agent_ms=None,
        known_agent_ms=188000,
        agent_hours=None,
        active_ms=None,
        known_active_ms=128000,
        parallelism=None,
        coverage="missing_metrics",
        problems=["plan calls have no activity interval"],
        parallelism_over_time={"status": "not_built"},
    )
    assert [r["wall_ms_per_correct"] for r in rows if r["record"] == "arm"] == [40000, 110000]


def test_negative_wall_and_correct_mean(tmp_path, capsys):
    events = renumber(
        [
            event("usage.tick", 100),
            event("change.proposed", 60),
            event("change.resolved", 80),
            event("change.proposed", 90, change=2, head_commit="h2"),
            event("change.resolved", 100, change=2),
        ]
    )
    results = [receipts()[3], {**receipts()[1], "result": {"exit_code": 0}}]
    rows = report(tmp_path, capsys, events, results)
    assert [r["wall_ms"] for r in rows if r["record"] == "change"] == [
        {"without_oversight": None, "with_oversight": None},
        {"without_oversight": 0, "with_oversight": 10000},
    ]
    assert [r["wall_ms_per_correct"] for r in rows if r["record"] == "arm"] == [0, 10000]


def test_unselected_and_no_correct_timing(tmp_path, capsys):
    rows = report(tmp_path, capsys, results=[])
    assert [r["wall_ms_per_correct"] for r in rows if r["record"] == "arm"] == [None, None]
    rows = report(tmp_path, capsys, [], [])
    assert [r["wall_ms_per_correct"] for r in rows if r["record"] == "arm"] == [None, None]


def test_wall_uses_selected_endpoints(tmp_path, capsys):
    events = renumber([*scenario(), event("change.resolved", 142)])
    rows = report(tmp_path, capsys, events)
    change = next(r for r in rows if r["record"] == "change")
    assert change["wall_ms"] == {"without_oversight": 60000, "with_oversight": 142000}
    events = scenario()
    proposal = events[12]
    assert proposal.type == "change.proposed"
    events[12] = proposal.model_copy(
        update={
            "payload": proposal.payload.model_copy(
                update={"tests": proposal.payload.tests.model_copy(update={"outcome": "failed"})}
            )
        }
    )
    rows = report(tmp_path, capsys, events)
    change = next(r for r in rows if r["record"] == "change")
    assert change["wall_ms"] == {"without_oversight": 120000, "with_oversight": 140000}


def test_wall_mean_floors_milliseconds(tmp_path, capsys):
    events = renumber(
        [
            event("agent.status", state="working"),
            event("agent.status", change=2, state="working"),
            event("change.proposed", 1),
            event("change.proposed", 2, change=2, head_commit="h2").model_copy(
                update={"ts": "2026-10-31T12:00:02.001Z"}
            ),
        ]
    )
    results = [{**receipts()[1], "result": {"exit_code": 0}}, receipts()[3]]
    rows = report(tmp_path, capsys, events, results)
    assert [r["wall_ms_per_correct"] for r in rows if r["record"] == "arm"] == [1500, None]


@pytest.mark.parametrize(
    "kind, keys",
    [
        (
            "meta",
            (
                "changes",
                "contract_version",
                "log_sha256",
                "price_retrieved",
                "price_source",
                "prompt_version",
                "record",
                "results_sha256",
                "revisions",
                "run_id",
                "schema",
                "through_seq",
                "with_hidden",
            ),
        ),
        (
            "revision",
            (
                "change_id",
                "correct",
                "first_green",
                "head_commit",
                "hidden",
                "merged",
                "previous",
                "record",
                "regress_vs_base",
                "regress_vs_previous",
                "request",
                "revision",
                "seq",
                "task",
                "tests",
                "verdicts",
            ),
        ),
        (
            "change",
            (
                "agent_id",
                "change_id",
                "end",
                "first_green",
                "merged_by",
                "merged_revision",
                "proposals",
                "record",
                "review_cost",
                "rounds",
                "task",
                "wall_ms",
            ),
        ),
        ("E1", ("cells", "duplicate_reviews", "n", "record", "review_cost")),
        (
            "E2",
            (
                "changes_with_requests",
                "findings",
                "hidden_by_round",
                "record",
                "regress_vs_base",
                "regress_vs_previous",
                "requests_by",
                "rounds",
            ),
        ),
        (
            "arm",
            (
                "arm",
                "by_role",
                "changes",
                "correct",
                "cost",
                "incorrect",
                "not_selected",
                "problems",
                "questions",
                "questions_not_needed",
                "record",
                "regressions_let_through",
                "unknown",
                "usd_per_correct",
                "wall_ms_per_correct",
            ),
        ),
        (
            "run",
            (
                "active_ms",
                "agent_hours",
                "agent_ms",
                "coverage",
                "known_active_ms",
                "known_agent_ms",
                "parallelism",
                "parallelism_over_time",
                "problems",
                "record",
            ),
        ),
        ("E3", ("record", "status")),
        ("E4", ("record", "status")),
        ("E6", ("record", "status")),
        ("E7", ("record", "status")),
    ],
)
def test_record_keys(tmp_path, capsys, kind, keys):
    records = [r for r in report(tmp_path, capsys) if r["record"] == kind]
    assert records
    assert all(tuple(sorted(r)) == keys for r in records)


def test_full_record_order_and_not_built(tmp_path, capsys):
    rows = report(tmp_path, capsys)
    assert tuple(r["record"] for r in rows) == (
        "meta",
        "revision",
        "revision",
        "revision",
        "change",
        "change",
        "change",
        "E1",
        "E2",
        "arm",
        "arm",
        "run",
        "E3",
        "E4",
        "E6",
        "E7",
    )
    assert [r["arm"] for r in rows if r["record"] == "arm"] == [
        "without_oversight",
        "with_oversight",
    ]
    assert rows[-4:] == [
        {"record": "E3", "status": "not_built"},
        {"record": "E4", "status": "not_built"},
        {"record": "E6", "status": "not_built"},
        {"record": "E7", "status": "not_built"},
    ]
    assert [r["questions"] for r in rows if r["record"] == "arm"] == [
        {"status": "not_built"},
        {"status": "not_built"},
    ]
    assert [r["questions_not_needed"] for r in rows if r["record"] == "arm"] == [
        {"status": "not_built"},
        {"status": "not_built"},
    ]


@pytest.mark.anyio
async def test_cli_deterministic_bytes(tmp_path):
    log, results = files(tmp_path)
    command = [
        sys.executable,
        "-m",
        "tools.eval.proof2",
        "report",
        "--log",
        str(log),
        "--results",
        str(results),
    ]

    async def invoke(*extra):
        process = await asyncio.create_subprocess_exec(
            *command,
            *extra,
            cwd=Path(__file__).resolve().parents[3],  # noqa: ASYNC240 - Static test location.
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        assert process.returncode == 0
        assert stderr == b""
        return stdout

    first = await invoke()
    assert first.endswith(b'\n{"record": "E7", "status": "not_built"}\n')
    assert b'"pct":' not in first
    assert await invoke() == first
    out = tmp_path / "report.jsonl"
    assert await invoke("--out", str(out)) == b""
    assert out.read_bytes() == first


def test_first_green_later_revision_counts_and_spend(tmp_path, capsys):
    events = scenario()
    events[12] = event(
        "change.proposed",
        60,
        tests=dict(
            outcome="failed", passed=5, failed=1, skipped=0, duration_ms=1, failed_tests=["t"]
        ),
    )
    rows = report(tmp_path, capsys, renumber(events))
    without = next(r for r in rows if r.get("arm") == "without_oversight")
    assert without["correct"] == {"count": 2, "n": 3}
    assert without["incorrect"] == 0
    assert without["cost"] == {"calls": 4, "usd": 0.5, "known_usd": 0.5, "coverage": "complete"}
    assert without["by_role"] == {
        "agent": {"calls": 4, "usd": 0.5, "known_usd": 0.5, "coverage": "complete"}
    }
    assert without["usd_per_correct"] == 0.25


@pytest.mark.parametrize(
    "task, status, unknown", [("p04", "unknown", 1), ("p10", "not_applicable", 0)]
)
def test_missing_first_revision_regression(tmp_path, capsys, task, status, unknown):
    results = [
        r for r in receipts() if not (r.get("change_id") == "change-1" and r.get("revision") == 1)
    ]
    results[1] = {**results[1], "task": task}
    rows = report(tmp_path, capsys, results=results)
    first, second = [
        r for r in rows if r.get("change_id") == "change-1" and r["record"] == "revision"
    ]
    assert first["previous"] is None
    assert first["regress_vs_previous"] == {"status": status, "ids": []}
    assert second["previous"] is None
    assert second["regress_vs_previous"] == {"status": "not_applicable", "ids": []}
    e2 = next(r for r in rows if r["record"] == "E2")
    assert e2["regress_vs_previous"] == {"checks": 0, "unknown_revisions": unknown}


def test_zero_cost_per_correct_is_unknown(tmp_path, capsys):
    events = [
        e.model_copy(
            update={
                "payload": e.payload.model_copy(
                    update={"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0}
                )
            }
        )
        if e.type == "usage.tick"
        else e
        for e in scenario()
    ]
    rows = report(tmp_path, capsys, events)
    without, with_ = [r for r in rows if r["record"] == "arm"]
    assert [without["correct"], with_["correct"]] == [{"count": 1, "n": 3}, {"count": 2, "n": 3}]
    assert [without["cost"], with_["cost"]] == [
        {"calls": 3, "usd": 0.0, "known_usd": 0.0, "coverage": "complete"},
        {"calls": 7, "usd": 0.0, "known_usd": 0.0, "coverage": "complete"},
    ]
    assert without["usd_per_correct"] is None
    assert with_["usd_per_correct"] is None


def test_spend_returns_new_records(tmp_path):
    inputs = load_inputs(*files(tmp_path))
    _, changes, arms = build_arms(inputs)
    original_changes, original_arms = deepcopy(changes), deepcopy(arms)
    original_seqs = [event.seq for event in inputs.events]
    enriched_changes, enriched_arms = add_spend(inputs, changes, arms)
    assert [event.seq for event in inputs.events] == original_seqs
    assert changes == original_changes
    assert arms == original_arms
    assert all(new is not old for new, old in zip(enriched_changes, changes, strict=True))
    assert all(new is not old for new, old in zip(enriched_arms, arms, strict=True))
    assert enriched_changes[0].get("review_cost") == {
        "calls": 2,
        "usd": 0.1,
        "known_usd": 0.1,
        "coverage": "complete",
    }
    assert enriched_arms[0].get("cost") == {
        "calls": 3,
        "usd": pytest.approx(0.3),
        "known_usd": pytest.approx(0.3),
        "coverage": "complete",
    }


def test_timing_returns_new_records(tmp_path):
    inputs = load_inputs(*files(tmp_path))
    revisions, changes, arms = build_arms(inputs)
    changes, arms = add_spend(inputs, changes, arms)
    for change in changes:
        change["wall_ms"] = {"without_oversight": -1, "with_oversight": -1}
    original_inputs, original_revisions, original_changes, original_arms = deepcopy(
        (inputs, revisions, changes, arms)
    )
    enriched_changes, enriched_arms, run = add_timing(inputs, revisions, changes, arms)
    assert inputs == original_inputs
    assert revisions == original_revisions
    assert changes == original_changes
    assert arms == original_arms
    assert enriched_changes is not changes
    assert enriched_arms is not arms
    assert all(new is not old for new, old in zip(enriched_changes, changes, strict=True))
    assert all(new is not old for new, old in zip(enriched_arms, arms, strict=True))
    assert [change.get("wall_ms") for change in enriched_changes] == [
        {"without_oversight": 60000, "with_oversight": 140000},
        {"without_oversight": 40000, "with_oversight": 80000},
        {"without_oversight": None, "with_oversight": None},
    ]
    assert [arm.get("wall_ms_per_correct") for arm in enriched_arms] == [40000, 110000]
    assert run == dict(
        record="run",
        agent_ms=188000,
        known_agent_ms=188000,
        agent_hours=pytest.approx(0.052222222222),
        active_ms=128000,
        known_active_ms=128000,
        parallelism=1.46875,
        coverage="complete",
        problems=[],
        parallelism_over_time={"status": "not_built"},
    )


def test_stdout_and_file_are_utf8_bytes(tmp_path, monkeypatch):
    buffer = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(buffer, encoding="ascii"))
    records = [{"text": "revisión"}]
    write_lines(records, None)
    out = tmp_path / "out"
    write_lines(records, out)
    assert buffer.getvalue() == out.read_bytes() == b'{"text": "revisi\xc3\xb3n"}\n'
