"""Committed event logs and parsed files exercise the repeat guard without I/O in it."""

from typing import Any

import pytest
from pydantic import TypeAdapter

from oversight.changes import RepeatVerdict, repeat_verdict
from oversight.events import absolute_paths, read_jsonl
from oversight.graph import parse_version
from oversight.settings import REPO_ROOT
from oversight_contracts.events import ServerEvent
from oversight_contracts.review import ChangeTarget

ADAPTER = TypeAdapter(ServerEvent)
RUN = read_jsonl(REPO_ROOT / "fixtures/events/handwritten-level1.jsonl")[0]
TEST = "tests/test_app.py::test_target"
SOURCE = "def target(): return 1\ndef helper(): return target()\ndef isolated(): return 2\n"
TEST_SOURCE = "from app import helper\ndef test_target(): assert helper() == 2\n"
FILES = {"app.py": SOURCE, "tests/test_app.py": TEST_SOURCE}
TARGET = ChangeTarget(
    change_id="change-1",
    revision=1,
    base_commit="base",
    head_commit="head",
    main_commit="base",
    candidate_tree="S",
)


def event(seq: int, kind: str, **payload: Any) -> Any:
    return ADAPTER.validate_python(dict(seq=seq, ts=RUN.ts, type=kind, payload=payload))


def execution(
    seq=3,
    *,
    phase="agent",
    tree="L",
    outcome="failed",
    detail: str | None = "assert 1 == 2",
    test_id=TEST,
    complete=True,
    cases=True,
    change="change-1",
    revision=1,
):
    return event(
        seq,
        "tests.executed",
        change_id=change,
        agent_id="agent-1",
        revision=revision,
        phase=phase,
        tested_tree=tree,
        tested_commit=None,
        suite=dict(
            outcome=outcome,
            passed=int(outcome == "passed"),
            failed=int(outcome != "passed"),
            skipped=0,
            duration_ms=1,
            failed_tests=[test_id] if outcome != "passed" else [],
            cases=[dict(test_id=test_id, outcome="failed", detail=detail)] if cases else None,
            cases_complete=complete,
        ),
    )


def edit(seq=4, *, path="app.py", before=SOURCE, after=None):
    if after is None:
        after = SOURCE.replace("return 2", "return 3")
    return event(
        seq,
        "change.updated",
        change_id="change-1",
        agent_id="agent-1",
        author="agent",
        file=dict(
            path=path,
            status="modified",
            original=before,
            modified=after,
            lines_added=1,
            lines_removed=1,
        ),
        node_ids=[],
        graph=dict(
            nodes_added=[], nodes_modified=[], nodes_removed=[], edges_added=[], edges_removed=[]
        ),
        files_changed=1,
        lines_added=1,
        lines_removed=1,
    )


def with_suite(e, **changes):
    return e.model_copy(
        update={
            "payload": e.payload.model_copy(
                update={"suite": e.payload.suite.model_copy(update=changes)}
            )
        }
    )


def status(seq=2, *, base="base"):
    return event(
        seq,
        "agent.status",
        agent_id="agent-1",
        change_id="change-1",
        slot="A",
        task="t",
        model="fake",
        state="working",
        step=0,
        max_steps=30,
        detail=None,
        base_commit=base,
    )


def log(*edits, last=None, current=None):
    return [
        RUN,
        status(),
        last or execution(),
        *edits,
        current or execution(6, phase="proposal", tree="L"),
    ]


def verdict(es, files=None) -> RepeatVerdict:
    return repeat_verdict(
        es,
        TARGET,
        parse_version(
            {p: text.encode() for p, text in (FILES if files is None else files).items()}, "S"
        ).graph,
    )


def test_no_earlier_run_clears():
    assert verdict([RUN, execution(3, phase="proposal")]).outcome == "clear"


@pytest.mark.parametrize("side", ["last", "current"])
def test_passing_run_on_either_side_clears(side):
    kwargs = {
        side: execution(
            3 if side == "last" else 6,
            phase="agent" if side == "last" else "proposal",
            outcome="passed",
        )
    }
    assert verdict(log(**kwargs)).outcome == "clear"


def test_different_outcome_clears():
    assert verdict(log(last=execution(outcome="error"))).outcome == "clear"


def test_different_failing_ids_clear():
    assert verdict(log(last=execution(test_id="tests/test_app.py::test_other"))).outcome == "clear"


def test_different_detail_clears():
    assert verdict(log(last=execution(detail="assert 3 == 2"))).outcome == "clear"


def test_same_tree_same_failure_is_unchanged():
    result = verdict(log(edit()))  # Equal trees are authoritative even with intermediate edits.
    assert result.outcome == "unchanged" and result.test_ids == (TEST,)


def test_unrelated_edit_is_unchanged():
    result = verdict(
        log(edit(), current=execution(6, phase="proposal", tree="S")),
        FILES | {"app.py": SOURCE.replace("return 2", "return 3")},
    )
    assert result.outcome == "unchanged"


def test_edit_reaching_the_test_clears():
    after = SOURCE.replace("return 1", "return 4")
    es = log(edit(after=after), current=execution(6, phase="proposal", tree="S"))
    assert verdict(es, FILES | {"app.py": after}).outcome == "clear"
    # Missing assertion text and non-Python changes do not hide a proven reach.
    es = log(
        edit(after=after),
        edit(5, path="README.md", before="a", after="b"),
        last=execution(detail=None),
        current=execution(6, phase="proposal", tree="S"),
    )
    assert verdict(es, FILES | {"app.py": after}).outcome == "clear"


def test_edit_to_the_test_itself_clears():
    after = TEST_SOURCE.replace("== 2", "== 3")
    es = log(
        edit(path="tests/test_app.py", before=TEST_SOURCE, after=after),
        current=execution(6, phase="proposal", tree="S"),
    )
    assert verdict(es, FILES | {"tests/test_app.py": after}).outcome == "clear"


def test_missing_detail_without_reach_is_unknown():
    assert verdict(log(last=execution(detail=None))).outcome == "unknown"


@pytest.mark.parametrize(("cases", "complete"), [(True, False), (False, None)])
def test_incomplete_cases_are_unknown(cases, complete):
    assert verdict(log(last=execution(cases=cases, complete=complete))).outcome == "unknown"


def test_non_python_change_is_unknown():
    es = log(
        edit(path="settings.txt", before="old", after="new"),
        current=execution(6, phase="proposal", tree="S"),
    )
    assert verdict(es).outcome == "unknown"


def test_removed_function_is_unknown():
    after = SOURCE.replace("def isolated(): return 2\n", "")
    es = log(edit(after=after), current=execution(6, phase="proposal", tree="S"))
    assert verdict(es, FILES | {"app.py": after}).outcome == "unknown"


def test_parse_error_is_unknown():
    es = log(edit(after="def broken("), current=execution(6, phase="proposal", tree="S"))
    assert verdict(es).outcome == "unknown"
    assert verdict(log(), FILES | {"bad.py": "def broken("}).outcome == "unknown"
    es = log(
        edit(before="def broken(", after="def isolated(): return 2"),
        current=execution(6, phase="proposal", tree="S"),
    )
    assert verdict(es).outcome == "unknown"


def test_unmapped_test_id_is_unknown():
    es = log(
        last=execution(test_id="unknown"), current=execution(6, phase="proposal", test_id="unknown")
    )
    assert verdict(es).outcome == "unknown"


def test_moved_base_is_unknown():
    es = log()
    es[1] = status(base="old-base")
    assert verdict(es).outcome == "unknown"


def test_used_round_defers_to_the_existing_check():
    resolved = event(
        7,
        "change.resolved",
        change_id="change-1",
        agent_id="agent-1",
        revision=1,
        reason=None,
        merge_commit=None,
        conflict=None,
        decision="changes_requested",
        comment="Fix",
        merge=None,
        decided_by="cross_review",
        review_seq=5,
        head_commit="head",
    )
    assert verdict([resolved]).outcome == "clear"


def test_reason_is_bounded_and_has_no_absolute_path():
    cases = [
        dict(test_id=f"tests/test_app.py::test_x[{i}]", outcome="failed", detail="same")
        for i in range(6)
    ]
    before, after = execution(), execution(6, phase="proposal")
    from oversight_contracts.events import CaseResult

    before, after = [
        with_suite(e, cases=[CaseResult.model_validate(c) for c in cases]) for e in (before, after)
    ]
    result = verdict(log(last=before, current=after))
    assert len(result.reason) <= 400 and "+3 more" in result.reason
    assert absolute_paths(result.reason) == []
    for name in ["/private/test.py::test_x", "tests/test_app.py::test_x[" + "x" * 800 + "]"]:
        result = verdict(
            log(last=execution(test_id=name), current=execution(6, phase="proposal", test_id=name))
        )
        assert len(result.reason) <= 400 and absolute_paths(result.reason) == []


def test_verdict_never_raises_on_odd_logs():
    before, after = execution(), execution(6, phase="proposal")
    before, after = [with_suite(e, cases=[]) for e in (before, after)]
    unknown_phase = execution().model_copy(deep=True)
    unknown_phase = unknown_phase.model_copy(
        update={"payload": unknown_phase.payload.model_copy(update={"phase": "future_phase"})}
    )
    for es in [
        [],
        [execution()],
        [RUN],
        [RUN, unknown_phase],
        log(last=before, current=after),
        [None],
        [RUN, execution(6, phase="proposal", revision=2)],
    ]:
        assert verdict(es).outcome == "unknown"


@pytest.mark.parametrize("outcome", ["error", "timeout"])
def test_repeated_error_or_timeout_is_unknown(outcome):
    assert (
        verdict(
            log(
                last=execution(outcome=outcome),
                current=execution(6, phase="proposal", outcome=outcome),
            )
        ).outcome
        == "unknown"
    )


def test_different_trees_without_edits_is_unknown():
    assert verdict(log(current=execution(6, phase="proposal", tree="S"))).outcome == "unknown"


def test_latest_modified_content_is_used_instead_of_base_original():
    before = SOURCE.replace("return 1", "return 4")
    after = before.replace("return 2", "return 3")
    es = [
        RUN,
        edit(2, before=SOURCE, after=before),
        execution(),
        edit(4, before=SOURCE, after=after),
        execution(6, phase="proposal", tree="S"),
    ]
    assert verdict(es, FILES | {"app.py": after}).outcome == "unchanged"


def test_reverted_edit_adds_no_changed_nodes():
    es = log(edit(after=SOURCE), current=execution(6, phase="proposal", tree="S"))
    assert verdict(es).outcome == "unchanged"


def test_deleted_file_is_unknown():
    removed = edit().model_copy(deep=True)
    removed = removed.model_copy(
        update={
            "payload": removed.payload.model_copy(
                update={
                    "file": removed.payload.file.model_copy(
                        update={"modified": None, "status": "deleted"}
                    )
                }
            )
        }
    )
    assert (
        verdict(log(removed, current=execution(6, phase="proposal", tree="S"))).outcome == "unknown"
    )


def test_possible_call_and_cut_reach_are_unknown():
    possible = FILES | {
        "tests/test_app.py": "from app import helper\nhelper = None\ndef test_target(): helper()"
    }
    assert verdict(log(), possible).outcome == "unknown"
    assert RUN.type == "run.started"
    limited = RUN.model_copy(
        update={
            "payload": RUN.payload.model_copy(
                update={"limits": RUN.payload.limits.model_copy(update={"cone_max_nodes": 1})}
            )
        }
    )
    es = log()
    es[0] = limited
    assert verdict(es).outcome == "unknown"


def test_parameterized_method_id_maps_to_its_single_node():
    files = {"tests/test_app.py": "class TestApp:\n def test_target(self): pass"}
    test_id = "tests/test_app.py::TestApp::test_target[case]"
    es = log(
        last=execution(test_id=test_id), current=execution(6, phase="proposal", test_id=test_id)
    )
    assert verdict(es, files).outcome == "unchanged"
    files["tests/test_app.py"] += "\n def test_target(self): pass"
    assert verdict(es, files).outcome == "unknown"


def test_last_run_limits_and_only_this_changes_runs_are_used():
    assert RUN.type == "run.started"
    negative = RUN.model_copy(
        update={
            "seq": 2,
            "payload": RUN.payload.model_copy(
                update={"limits": RUN.payload.limits.model_copy(update={"cone_max_depth": -1})}
            ),
        }
    )
    assert verdict([*log(), negative]).outcome == "unknown"
    assert (
        verdict([*log(), execution(7, phase="proposal", change="change-2")]).outcome == "unchanged"
    )
    assert (
        verdict([RUN, execution(2, phase="head_probe"), execution(6, phase="proposal")]).outcome
        == "clear"
    )


def test_latest_base_evidence_at_the_earlier_run_is_used():
    proposed = next(
        e
        for e in read_jsonl(REPO_ROOT / "fixtures/events/handwritten-level1.jsonl")
        if e.type == "change.proposed"
    )
    assert proposed.type == "change.proposed"
    proposed = proposed.model_copy(
        update={
            "seq": 2,
            "payload": proposed.payload.model_copy(
                update={"base_commit": "old", "change_id": TARGET.change_id}
            ),
        }
    )
    es = [RUN, proposed, execution(), execution(6, phase="proposal")]
    assert verdict(es).outcome == "unknown"
    es.insert(2, status(3))  # Later base evidence at L takes precedence.
    assert verdict(es).outcome == "unchanged"
    es[-1] = execution(6, phase="proposal")
    es.append(status(7, base="future-base"))
    assert verdict(es).outcome == "unchanged"


def test_python_file_outside_the_parser_is_unknown():
    es = log(
        edit(path="non-importable.py", before="value = 1", after="value = 2"),
        current=execution(6, phase="proposal", tree="S"),
    )
    assert verdict(es).outcome == "unknown"
