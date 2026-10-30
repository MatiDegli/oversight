"""Unknown enum values must not turn otherwise passing evidence into merge authority."""

from dataclasses import replace
from typing import cast, get_args
from unittest.mock import Mock

import pytest
from test_policy_profiles import PROFILE, policy_inputs

from oversight.changes import _assessment, _beforeafter, _policy
from oversight.changes._assessment import AssessmentWait, RulesScheduler
from oversight.changes._beforeafter import Outcome, affected_tests
from oversight.changes._beforeafter import TestRun as Run
from oversight.changes._facts import AddedTestsInventory
from oversight.changes._policy import assess, merges_alone
from oversight.graph import _impact, impact_cone, parse_version, possible_callers
from oversight_contracts.common import CaseOutcome, FileStatus
from oversight_contracts.events import ChangeProposed, FileStat
from oversight_contracts.graph import Confidence, EdgeKind
from oversight_contracts.review import AddedTestProbe, CrossReview


@pytest.mark.parametrize(
    "table,enum",
    [
        (_assessment._EXISTING_TEST_STATUS, FileStatus.__value__),
        (_beforeafter._AFTER_OUTCOMES, CaseOutcome.__value__),
        (_beforeafter._BEFORE_OUTCOMES, CaseOutcome.__value__),
        (_policy._REVIEW_VERDICTS, CrossReview.model_fields["verdict"].annotation),
        (_impact._CALL_KINDS, EdgeKind.__value__),
        (_impact._EXACT_CONFIDENCE, Confidence.__value__),
    ],
)
def test_enum_tables_cover_every_contract_value(table, enum):
    assert set(table) == set(get_args(enum))


def test_junit_outcomes_match_contract_cases():
    assert set(get_args(Outcome)) == set(get_args(CaseOutcome.__value__))


def assert_waits(inputs, name):
    assessment = assess(inputs, PROFILE)
    assert next(rule for rule in assessment.rules if rule.rule == name).result != "pass"
    assert not merges_alone(assessment)
    assert all(rule.result == "pass" for rule in assessment.rules if rule.rule != name)


def test_unknown_file_status_touches_existing_test():
    ready = policy_inputs()
    assert merges_alone(assess(ready, PROFILE))
    proposal = ChangeProposed.model_construct(
        base_commit=ready.base_commit,
        files=[FileStat.model_construct(path="tests/test_old.py", status="renamed")],
        impact=ready.cone,
        lines_added=1,
        lines_removed=0,
    )
    wait = Mock(
        spec=AssessmentWait,
        rec=Mock(change_id=ready.change_id, agent_id=ready.agent_id),
        proposal=proposal,
        proposal_seq=1,
        target=Mock(revision=ready.revision, head=ready.head_commit),
        main_commit=ready.main_commit,
        mode=Mock(mode=ready.mode, revision=ready.mode_revision),
        facts=None,
    )
    scheduler = RulesScheduler(Mock(), lambda: 0, Mock())
    inputs = scheduler.inputs(wait)
    assert_waits(
        replace(
            ready,
            modified_existing_test_paths=inputs.modified_existing_test_paths,
            deleted_existing_test_paths=inputs.deleted_existing_test_paths,
        ),
        "existing_tests_untouched",
    )


def test_unknown_after_outcome_cannot_prove_pass():
    ready = policy_inputs()
    assert merges_alone(assess(ready, PROFILE))
    test_id = "tests/test_new.py::test_new"
    [fact] = affected_tests(
        [test_id], Run({test_id: "failed"}), Run({test_id: cast(Outcome, "xpassed")})
    )
    probe = AddedTestProbe(test_id=test_id, before_seq=10, after_seq=20, result=fact.kind)
    assert_waits(
        replace(
            ready,
            added_tests=(probe,),
            added_tests_inventory=AddedTestsInventory(test_ids=(test_id,), complete=True),
        ),
        "added_tests_fail_before",
    )
    assert fact.kind == "unknown"


def test_unknown_cross_review_verdict_waits():
    ready = policy_inputs()
    assert merges_alone(assess(ready, PROFILE))
    assert ready.review is not None
    review = ready.review.model_copy(update={"verdict": "approved"})
    assert_waits(replace(ready, review=review), "cross_review")


@pytest.mark.parametrize(
    "updates",
    [
        {"confidence": "likely"},
        {"kind": "dispatches"},
        {"kind": "dispatches", "confidence": "possible", "reason": "dynamic_dispatch"},
        {"kind": "dispatches", "confidence": "likely"},
        {"kind": "imports", "confidence": "likely"},
        {"kind": "inherits", "confidence": "likely"},
    ],
)
def test_unknown_edge_enum_keeps_small_cone_unknown(updates):
    ready = policy_inputs()
    assert merges_alone(assess(ready, PROFILE))
    graph = parse_version({"shop.py": b"def seed(): ...\ndef caller(): seed()\n"}, "head").graph
    [edge] = [edge for edge in graph.edges if edge.kind == "calls"]
    graph = graph.model_copy(update={"edges": [edge.model_copy(update=updates)]})
    cone = impact_cone(graph, ["shop:seed"], max_depth=2, max_nodes=5)
    assert_waits(
        replace(ready, cone=cone, possible_callers=len(possible_callers(graph, cone))),
        "small_cone",
    )
