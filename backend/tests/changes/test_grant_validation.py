"""Pure authorization rejects incomplete, mismatched or broader grant evidence."""

from dataclasses import replace

import pytest
from test_policy_profiles import PROFILE, policy_inputs

from oversight.changes._evidence import WriteEvidence
from oversight.changes._grants import Measurement, measure
from oversight.changes._policy import assess, merges_alone
from oversight.changes._resolution_validation import (
    validate_resolution_evidence,
    validate_resolution_intent,
)
from oversight.changes._rules import pending_policy
from oversight.events.references import validate_references
from oversight.graph import change_cone, change_graph, parse_version
from oversight_contracts.events import ChangeResolved, ChangeResolvedEvent, GrantCreatedEvent
from oversight_contracts.grants import GrantAuthorization, GrantCreated, GrantScope, GrantUse

TS = "2026-10-29T14:00:00.000Z"
RUN = "run-20261029-140000"


def fixture():
    assessment = assess(replace(policy_inputs(), possible_callers=1), PROFILE)
    use = GrantUse(
        grant_id="grant-1",
        rule="small_cone",
        caller_ids=["shop:caller"],
        caller_count=1,
        authorization=GrantAuthorization(
            reason="possible_callers",
            scope=GrantScope(kind="module", value="shop", expires="run"),
            creation_seq=1,
            creation_ts=TS,
            run_id=RUN,
            checked_at=TS,
            active_at_effect=True,
            change_id=assessment.change_id,
            agent_id=assessment.agent_id,
            revision=assessment.revision,
            head_commit=assessment.head_commit,
        ),
    )
    return assessment, use


def pending(assessment):
    return assessment.model_copy(
        update={
            "rules": [
                r.model_copy(update={"result": "unknown", "observed": None})
                if r.rule == "combined_tests"
                else r
                for r in assessment.rules
            ]
        }
    )


def test_identical_pure_checks_and_event_round_trip():
    assessment, use = fixture()
    assert merges_alone(assessment, [use]) and pending_policy(pending(assessment), [use])
    evidence = WriteEvidence(
        assessment.change_id,
        assessment.agent_id,
        assessment.revision,
        assessment.base_commit,
        assessment.head_commit,
        "rules",
        "approved",
        None,
        assessment=assessment,
        grants=(use,),
        reasons=("Covered by a standing grant",),
    )
    payload = ChangeResolved(
        change_id=assessment.change_id,
        agent_id=assessment.agent_id,
        revision=assessment.revision,
        head_commit=assessment.head_commit,
        decided_by="rules",
        decision="approved",
        reason=None,
        comment=None,
        merge="merged",
        merge_commit="merged",
        conflict=None,
        assessment=assessment,
        grants=[use],
        reasons=list(evidence.reasons),
    )
    assert ChangeResolved.model_validate_json(payload.model_dump_json()) == payload
    validate_resolution_evidence(payload, evidence)
    with pytest.raises(ValueError, match=r"target|policy"):
        validate_resolution_intent(replace(evidence, head_commit="other"))
    with pytest.raises(ValueError, match="grants"):
        validate_resolution_evidence(
            payload, replace(evidence, grants=(use.model_copy(update={"grant_id": "other"}),))
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("reason", "protected_path"),
        ("active_at_effect", False),
        ("revision", 2),
        ("head_commit", "other"),
        ("agent_id", "agent-2"),
        ("change_id", "change-2"),
        ("checked_at", "2026-10-28T14:00:00.000Z"),
        ("scope", GrantScope(kind="run", value="run-20261029-140001", expires="run")),
        ("scope", GrantScope(kind="agent", value="agent-1", expires="run")),
        ("scope", GrantScope(kind="module", value="shop", expires="session")),
    ],
)
def test_capture_must_match_and_be_active(field, value):
    assessment, use = fixture()
    altered = use.model_copy(
        update={"authorization": use.authorization.model_copy(update={field: value})}
    )
    assert not merges_alone(assessment, [altered])
    assert not pending_policy(pending(assessment), [altered])


@pytest.mark.parametrize("rule", [r for r in policy_inputs().evidence if r != "combined_tests"])
@pytest.mark.parametrize("state", ["unknown", "fail"])
def test_grants_never_cover_other_rules_or_any_fail(rule, state):
    assessment, use = fixture()
    changed = assessment.model_copy(
        update={
            "rules": [
                r.model_copy(update={"result": state}) if r.rule == rule else r
                for r in assessment.rules
            ]
        }
    )
    expected = rule == "small_cone" and state == "unknown"
    assert merges_alone(changed, [use]) == expected
    assert pending_policy(pending(changed), [use]) == expected


@pytest.mark.parametrize(
    "observed",
    [
        None,
        {},
        {"nodes": 0},
        {
            "nodes": 6,
            "omitted_by_depth": 0,
            "omitted_by_limit": 0,
            "parse_errors": 0,
            "unresolved_removed": 0,
        },
    ],
)
def test_missing_or_exceeded_measurements_cannot_be_waived(observed):
    assessment, use = fixture()
    changed = assessment.model_copy(
        update={
            "rules": [
                r.model_copy(update={"observed": observed}) if r.rule == "small_cone" else r
                for r in assessment.rules
            ]
        }
    )
    assert not merges_alone(changed, [use]) and not pending_policy(pending(changed), [use])


@pytest.mark.parametrize(
    "updates",
    [{"caller_count": 0}, {"caller_ids": []}, {"caller_count": 2}, {"rule": "combined_tests"}],
)
def test_invalid_caller_evidence_or_rule_mapping_cannot_authorize(updates):
    assessment, use = fixture()
    changed = use.model_copy(update=updates)
    assert not merges_alone(assessment, [changed]) and not pending_policy(
        pending(assessment), [changed]
    )
    assert not merges_alone(assessment, [use, use])


def test_module_scope_uses_all_changed_files_and_nodes_and_run_is_exact():
    measurement = Measurement(
        ("caller",), 1, ("shop/mod.py",), ("shop.mod",), (("shop.mod", "shop/mod.py"),), True
    )
    scope = GrantScope(kind="module", value="shop.mod", expires="run")
    assert measurement.covers(scope, RUN)
    for changed in (
        replace(measurement, files=("shop/mod.py", "other.py")),
        replace(measurement, modules=("shop.mod", "other")),
        replace(measurement, complete=False),
        replace(measurement, files=()),
    ):
        assert not changed.covers(scope, RUN)
    assert measurement.covers(GrantScope(kind="run", value=RUN, expires="run"), RUN)
    assert not measurement.covers(GrantScope(kind="run", value=RUN, expires="run"), "other")


def test_test_files_do_not_break_module_match_and_caller_list_is_bounded():
    source = "class Utility:\n    def helper(self):\n        return 1\n\n" + "\n".join(
        f"def caller_{i}(obj):\n    return obj.helper()\n" for i in range(25)
    )
    base = parse_version({"shop/mod.py": source.encode()}, "base", None)
    head = parse_version(
        {
            "shop/mod.py": source.replace("return 1", "return 2").encode(),
            "tests/test_extra.py": b"def test_extra():\n    assert True\n",
        },
        "head",
        None,
    )
    delta = change_graph(base, head)
    cone = change_cone(delta, head.graph, max_depth=4, max_nodes=40)
    measured = measure(base.graph, head.graph, delta, ["shop/mod.py", "tests/test_extra.py"], cone)
    assert measured.covers(GrantScope(kind="module", value="shop.mod", expires="run"), RUN)
    assert measured.caller_count == 25 and len(measured.caller_ids) == 20
    assert measured.caller_ids == tuple(sorted(measured.caller_ids))


@pytest.mark.parametrize(
    "field,value",
    [
        ("grant_id", "other"),
        ("run_id", "run-20261029-140001"),
        ("scope", GrantScope(kind="module", value="other", expires="run")),
        ("creation_ts", "2026-10-28T14:00:00.000Z"),
    ],
)
def test_use_provenance_must_match_the_creation_receipt(field, value):
    assessment, use = fixture()
    auth = use.authorization
    created = GrantCreated(
        grant_id=use.grant_id,
        reason=auth.reason,
        scope=auth.scope,
        change_id="change-1",
        revision=1,
        head_commit="head",
        run_id=auth.run_id,
        granted_by="person",
    )
    original = GrantCreatedEvent(seq=1, ts=TS, type="grant.created", payload=created)
    resolution = ChangeResolved(
        change_id=assessment.change_id,
        agent_id=assessment.agent_id,
        revision=assessment.revision,
        head_commit=assessment.head_commit,
        decided_by="rules",
        decision="approved",
        reason=None,
        comment=None,
        merge="merged",
        merge_commit="merged",
        conflict=None,
        assessment=assessment,
        grants=[use],
        reasons=["Standing grant"],
    )
    event = ChangeResolvedEvent(seq=2, ts=TS, type="change.resolved", payload=resolution)
    # The hand-built assessment references other events: isolate the grant reference check.
    clean = resolution.model_copy(
        update={
            "assessment": assessment.model_copy(
                update={"rules": [r.model_copy(update={"evidence": []}) for r in assessment.rules]}
            )
        }
    )
    event = event.model_copy(update={"payload": clean})
    validate_references(event, {1: original})
    if field == "grant_id":
        changed = use.model_copy(update={field: value})
    else:
        changed = use.model_copy(update={"authorization": auth.model_copy(update={field: value})})
    forged = event.model_copy(update={"payload": clean.model_copy(update={"grants": [changed]})})
    with pytest.raises(ValueError, match="grant authorization"):
        validate_references(forged, {1: original})
