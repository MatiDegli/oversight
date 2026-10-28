"""Approved 1.5 additions preserve old payloads and constrain new evidence only."""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from oversight.workspace import run_process
from oversight_contracts.commands import (
    ApproveChange,
    ChangeView,
    DictateTask,
    RejectChange,
    RequestChanges,
    StartAgent,
    TaskDictated,
)
from oversight_contracts.events import ChangeResolved, ServerEvent
from oversight_contracts.examples import ExamplePin, NodeExample, TaskExample
from oversight_contracts.plans import Plan
from oversight_contracts.policy import ChangeAssessment, RuleAssessment
from oversight_contracts.review import (
    ChangeFacts,
    ChangeUsage,
    MeasuredCaller,
    NodeBehaviorDiff,
    ReviewCard,
    RoleUsage,
    ShapeFact,
)

ROOT = Path(__file__).resolve().parents[2]
EVENTS = TypeAdapter(ServerEvent)
TARGET = dict(
    change_id="change-1",
    revision=1,
    base_commit="base",
    head_commit="head",
    main_commit="main",
    candidate_tree="tree",
)
FACTS = dict(
    **TARGET,
    declared=None,
    edited_paths=[],
    test_runs=[],
    integrity=[],
    added_tests=[],
    run_findings=[],
    zone=None,
    reach=None,
    complete=True,
    problems=[],
)
CARD = dict(node_id="a:f", question="Keep the new behavior?", evidence_seq=1)
EXAMPLE = dict(example_id="empty", inputs="value=0", expected_output="")


@pytest.mark.parametrize(
    "summary", [[], ["a"] * 4, ["x" * 241], ["a\nb"], ["a\rb"], ["a\n"], ["a\r"], [""]]
)
def test_summary_rejects_out_of_bounds_or_multiline_text(summary: list[str]) -> None:
    with pytest.raises(ValidationError):
        ChangeFacts.model_validate({**FACTS, "summary": summary})


@pytest.mark.parametrize("summary", [["task"], ["x" * 240] * 3])
def test_summary_accepts_one_to_three_single_lines(summary: list[str]) -> None:
    assert ChangeFacts.model_validate({**FACTS, "summary": summary}).summary == summary


def test_differing_inputs_cannot_exceed_comparisons() -> None:
    NodeBehaviorDiff(node_id="a:f", compared_count=1, differing_count=1)
    with pytest.raises(ValidationError, match="cannot exceed"):
        NodeBehaviorDiff(node_id="a:f", compared_count=1, differing_count=2)


@pytest.mark.parametrize(
    "updates",
    [
        dict(compared_count=1, differing_count=1, outcome="changed"),
        dict(compared_count=1, complete=True, outcome="unchanged"),
        dict(compared_count=1, not_compared=["partial"]),
        dict(not_compared=["unsupported"]),
    ],
)
def test_measured_outcome_follows_comparisons(updates: dict[str, Any]) -> None:
    caller = MeasuredCaller.model_validate(dict(node_id="a:f", **updates))
    for outcome in {"changed", "unchanged", "not_compared"} - {caller.outcome}:
        with pytest.raises(ValidationError, match="outcome must follow"):
            MeasuredCaller.model_validate({**caller.model_dump(), "outcome": outcome})


def test_not_compared_requires_a_reason() -> None:
    with pytest.raises(ValidationError, match="requires a reason"):
        MeasuredCaller(node_id="a:f")


@pytest.mark.parametrize("answer", ["yes", "no", "unsure"])
def test_answer_and_principal_are_paired(answer: str) -> None:
    ReviewCard.model_validate(CARD)
    ReviewCard.model_validate({**CARD, "answer": answer, "answered_by": "person"})
    for updates in ({"answer": answer}, {"answered_by": "person"}):
        with pytest.raises(ValidationError, match="present together"):
            ReviewCard.model_validate({**CARD, **updates})


def test_examples_require_an_expectation_and_preserve_provenance() -> None:
    for cls, extra in (
        (TaskExample, {}),
        (NodeExample, dict(source="test_literal", source_id="test_a", evidence=dict(event_seq=1))),
    ):
        for expectation in ({"expected_output": ""}, {"expected_behavior": "does nothing"}):
            example = cls.model_validate(
                {**EXAMPLE, "expected_output": None, **extra, **expectation}
            )
            assert example.pin is None
        with pytest.raises(ValidationError, match="requires expected"):
            cls.model_validate({**EXAMPLE, **extra, "expected_output": None})
    pin = ExamplePin(source_task="Keep empty output")
    assert pin.confirmed_by == "person"
    assert TaskExample.model_validate({**EXAMPLE, "pin": pin}).pin == pin
    for missing in ("source", "source_id", "evidence"):
        payload = {
            **EXAMPLE,
            "source": "pinned_example",
            "source_id": "task/empty",
            "evidence": {"event_seq": 1},
        }
        del payload[missing]
        with pytest.raises(ValidationError):
            NodeExample.model_validate(payload)


def plan(
    dependencies: list[list[int]], agents: Sequence[str | None], state: str = "running"
) -> Any:
    return dict(
        plan_id="plan-1",
        revision=1,
        task="Split",
        graph_version="g",
        state=state,
        subtasks=[
            dict(task="work", zone=dict(node_ids=[], paths=[]), depends_on=deps, agent_id=agent)
            for deps, agent in zip(dependencies, agents, strict=True)
        ],
    )


@pytest.mark.parametrize("deps", [[[1, 1], []], [[0], []], [[], [1]], [[1], [0]]])
def test_dependencies_are_unique_nonself_and_acyclic(deps: list[list[int]]) -> None:
    with pytest.raises(ValidationError):
        Plan.model_validate(plan(deps, [None, None], "proposed"))


@pytest.mark.parametrize("agents", [[None, None], ["agent-1", None], ["agent-1", "agent-1"]])
def test_independent_running_subtasks_need_distinct_agents(agents: list[str | None]) -> None:
    with pytest.raises(ValidationError):
        Plan.model_validate(plan([[], []], agents))


def test_dependent_subtask_may_wait_but_some_agent_must_run() -> None:
    Plan.model_validate(plan([[], [0]], ["agent-1", None]))
    Plan.model_validate(plan([[1], []], [None, "agent-2"]))
    Plan.model_validate(plan([[], []], ["agent-1", "agent-2"]))
    for agents in ([None, None], [None, "agent-2"], ["agent-1", "agent-1"]):
        with pytest.raises(ValidationError):
            Plan.model_validate(plan([[], [0]], agents))


@pytest.mark.parametrize(
    "model,base,field,item,limit",
    [
        (ChangeFacts, FACTS, "summary", "task", 3),
        (ChangeFacts, FACTS, "review_cards", CARD, 8),
        (ApproveChange, dict(revision=1, head_commit="h"), "answers", CARD, 8),
        (RequestChanges, dict(revision=1, head_commit="h", comment="fix"), "answers", CARD, 8),
        (RejectChange, dict(revision=0, head_commit="b"), "answers", CARD, 8),
        (
            ChangeResolved,
            dict(
                change_id="change-1",
                agent_id="agent-1",
                revision=1,
                decision="rejected",
                reason=None,
                comment=None,
                merge=None,
                merge_commit=None,
                conflict=None,
            ),
            "answers",
            CARD,
            8,
        ),
        (StartAgent, dict(task="work"), "examples", EXAMPLE, 5),
        (
            NodeBehaviorDiff,
            dict(node_id="a:f"),
            "rows",
            dict(inputs="x=1", before="int:1", after="int:2"),
            8,
        ),
    ],
)
def test_list_bounds(model: Any, base: Any, field: str, item: Any, limit: int) -> None:
    model.model_validate({**base, field: [item] * limit})
    with pytest.raises(ValidationError):
        model.model_validate({**base, field: [item] * (limit + 1)})


@pytest.mark.parametrize(
    "kind,fact_kind",
    [
        ("loosened", "assertion_loosened"),
        ("removed", "test_removed"),
        ("skipped", "skip_added"),
        ("deleted", "test_removed"),
    ],
)
def test_temporal_kind_preserves_all_local_evidence_checks(kind: str, fact_kind: str) -> None:
    fact = dict(
        kind=fact_kind,
        path="test_a.py",
        line=1,
        text="weakened",
        test_id="test_a",
        evidence=[dict(event_seq=2)],
    )
    finding = dict(
        kind=kind, test_id="test_a", failed_seq=1, edited_seq=2, passed_seq=3, fact_index=0
    )
    payload = {**FACTS, "integrity": [fact], "run_findings": [finding]}
    ChangeFacts.model_validate(payload)
    if kind == "loosened":
        legacy = {key: value for key, value in finding.items() if key != "kind"}
        assert (
            ChangeFacts.model_validate({**payload, "run_findings": [legacy]}).run_findings[0].kind
            == kind
        )
    for updates in (
        dict(failed_seq=2),
        dict(edited_seq=3),
        dict(fact_index=1),
        dict(test_id="other"),
    ):
        with pytest.raises(ValidationError):
            ChangeFacts.model_validate({**payload, "run_findings": [{**finding, **updates}]})
    for updates in (dict(kind="stub_left"), dict(evidence=[dict(event_seq=4)])):
        with pytest.raises(ValidationError):
            ChangeFacts.model_validate({**payload, "integrity": [{**fact, **updates}]})


def test_protected_paths_profile_is_additive_with_strict_inputs() -> None:
    from oversight_contracts.policy import POLICY_RULES

    baseline = json.loads((ROOT / "contracts/baselines/1.4.0.schema.json").read_text())
    assert "protected_paths" not in baseline["$defs"]["RuleId"]["enum"]
    probes = POLICY_RULES["safe-merge-probes-v1"]
    assert POLICY_RULES["safe-merge-paths-v1"] == probes | {"protected_paths"}
    assert all(
        "protected_paths" not in rules
        for name, rules in POLICY_RULES.items()
        if name != "safe-merge-paths-v1"
    )
    payload = dict(
        rule="protected_paths",
        observed=dict(violations=0, complete=True),
        threshold=dict(violations_max=0, complete=True),
        result="pass",
        evidence=[dict(git_object="tree", path="pyproject.toml")],
    )
    RuleAssessment.model_validate(payload)
    for observed in (
        dict(violations=True, complete=True),
        dict(violations="0", complete=True),
        dict(violations=0, complete=1),
        dict(violations=0, complete=True, other=0),
    ):
        with pytest.raises(ValidationError):
            RuleAssessment.model_validate({**payload, "observed": observed})
    for updates in (dict(threshold=dict(violations_max=1, complete=True)), dict(result="fail")):
        with pytest.raises(ValidationError):
            RuleAssessment.model_validate({**payload, **updates})
    historical = json.loads((ROOT / "fixtures/contracts/reserved-1.3.json").read_text())
    assessment = next(row["payload"] for row in historical if row["type"] == "change.assessed")
    # Select the original eight rules from v2; the new profile requires exactly one more.
    old_rules = [rule for rule in assessment["rules"] if rule["rule"] in probes]
    old_rules += [{**payload, "rule": rule} for rule in probes - {r["rule"] for r in old_rules}]
    candidate = {
        **assessment,
        "policy_version": "safe-merge-paths-v1",
        "rules": [*old_rules, payload],
    }
    ChangeAssessment.model_validate(candidate)
    with pytest.raises(ValidationError, match="exact rules"):
        ChangeAssessment.model_validate({**candidate, "rules": old_rules})


def test_all_historical_profiles_keep_their_exact_membership() -> None:
    from oversight_contracts.policy import POLICY_RULES

    base = {
        "combined_tests",
        "small_cone",
        "small_change",
        "known_spend",
        "existing_tests_untouched",
    }
    review = base | {"cross_review"}
    integrity = review | {"test_integrity"}
    probes = integrity | {"added_tests_fail_before"}
    assert {
        name: rules for name, rules in POLICY_RULES.items() if name != "safe-merge-paths-v1"
    } == {
        "safe-merge-v1": base,
        "safe-merge-review-v1": review,
        "safe-merge-integrity-v1": integrity,
        "safe-merge-probes-v1": probes,
        "safe-merge-run-v1": probes | {"run_test_integrity"},
        "safe-merge-reach-v1": probes | {"tests_reach_changes"},
        "safe-merge-zone-v1": probes | {"within_zone"},
        "safe-merge-v2": probes | {"run_test_integrity", "tests_reach_changes", "within_zone"},
    }


def test_new_defaults_do_not_assert_known_usage_or_enable_execution() -> None:
    old_facts = ChangeFacts.model_validate(FACTS)
    assert all(
        getattr(old_facts, field) is None
        for field in (
            "summary",
            "behavior_diff",
            "measured_cone",
            "review_cards",
            "shape",
            "shape_complete",
        )
    )
    assert ChangeUsage().model_dump() == dict(cost_usd=None, unknown_calls=None, by_role=None)
    assert (
        ChangeView.model_validate(
            dict(
                change_id="change-1",
                seq=0,
                proposal=None,
                files=[],
                facts=None,
                review=None,
                assessment=None,
                resolution=None,
            )
        ).usage
        is None
    )
    assert StartAgent(task="work").examples_only is None
    assert DictateTask().model_dump() == dict(audio_base64=None, media_type=None, language=None)
    assert TaskDictated().text is None
    for role in ("agent", "review", "plan", "lead", None):
        assert RoleUsage(role=role).role == role
    for cost in (-1, float("inf"), float("nan")):
        for cls in (RoleUsage, ChangeUsage):
            with pytest.raises(ValidationError):
                cls(cost_usd=cost)


def test_shape_facts_allow_file_observations_without_a_node() -> None:
    fact = dict(
        kind="import_added",
        path="a.py",
        message="Added an import",
        evidence=[dict(git_object="head", path="a.py")],
    )
    assert ShapeFact.model_validate(fact).node_id is None
    assert ChangeFacts.model_validate({**FACTS, "shape": [fact], "shape_complete": False}).complete
    with pytest.raises(ValidationError):
        ShapeFact.model_validate({**fact, "kind": "size_gate"})


@pytest.mark.anyio
async def test_every_recorded_event_validates_against_the_regenerated_schema(
    tmp_path: Path,
) -> None:
    # The project's schema describes serialized output: validate legacy input first and include
    # the new null defaults before checking that output against the actual generated schema.
    from oversight_contracts import CONTRACT_VERSION

    schema = json.loads((ROOT / "contracts/generated/oversight.schema.json").read_text())
    assert CONTRACT_VERSION == "1.5.0" and "1.5.0" in schema["$comment"]
    records = []
    for path in ROOT.rglob("*.jsonl"):
        if any(part in {"node_modules", ".git", ".runtime", ".venv"} for part in path.parts):
            continue
        for line in path.read_text().splitlines():
            event = EVENTS.validate_json(line)
            assert EVENTS.validate_json(EVENTS.dump_json(event)) == event, path
            records.append(
                dict(source=str(path.relative_to(ROOT)), event=event.model_dump(mode="json"))
            )
    assert len(records) > 0
    serialized = tmp_path / "serialized.json"
    serialized.write_text(json.dumps(records))
    result = await run_process(
        ["node", "contracts/tests/validate_serialized.cjs", str(serialized)], cwd=ROOT, timeout_s=60
    )
    assert result.returncode == 0, result.stdout + result.stderr
