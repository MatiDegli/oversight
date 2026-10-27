"""Contracts 1.4 guards, intrinsic authority, arrays and reserved runtime-tree shape."""

import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from oversight_contracts.commands import (
    ApproveChange,
    RejectChange,
    RequestChanges,
    SaveFile,
    TreeFile,
    TreeResponse,
)
from oversight_contracts.events import ChangeResolved, SuiteResult
from oversight_contracts.policy import (
    POLICY_RULES,
    SAFE_MERGE_V1_RULES,
    SAFE_MERGE_V2_RULES,
    RuleAssessment,
)
from oversight_contracts.review import ChangeTarget, CrossReview, IntegrityFact

ROOT = Path(__file__).resolve().parents[2]
TARGET = dict(
    change_id="change-1",
    revision=1,
    base_commit="base",
    head_commit="head",
    main_commit="main",
    candidate_tree="tree",
)
REVIEW = dict(
    **TARGET,
    author_model="author",
    reviewer_model="reviewer",
    reviewer_provider="fake",
    reasoning=None,
    verdict="clean",
    findings=[],
    complete=True,
    problems=[],
)
RESOLUTION = dict(
    change_id="change-1",
    agent_id="agent-1",
    revision=1,
    decision="rejected",
    reason=None,
    comment=None,
    merge=None,
    merge_commit=None,
    conflict=None,
)


@pytest.mark.parametrize("model", [ApproveChange, RejectChange, RequestChanges])
@pytest.mark.parametrize(
    "guard",
    [
        {},
        {"revision": 1},
        {"head_commit": "h"},
        {"revision": None, "head_commit": "h"},
        {"revision": 1, "head_commit": None},
    ],
)
def test_decisions_require_both_nonnull_guards(model: Any, guard: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({"comment": "Please revise", **guard})


def test_zero_is_only_for_reject_and_targets_the_agent_base() -> None:
    assert RejectChange(revision=0, head_commit="base").revision == 0
    for model in (ApproveChange, RequestChanges):
        with pytest.raises(ValidationError):
            model.model_validate(dict(revision=0, head_commit="base", comment="x"))
    with pytest.raises(ValidationError):
        ChangeTarget.model_validate({**TARGET, "revision": 0})
    ChangeTarget.model_validate({**TARGET, "revision": 0, "head_commit": "base"})
    for actor in (None, "person"):
        ChangeResolved.model_validate({**RESOLUTION, "revision": 0, "decided_by": actor})
    for decision in ("approved", "changes_requested"):
        with pytest.raises(ValidationError):
            ChangeResolved.model_validate({**RESOLUTION, "revision": 0, "decision": decision})


def test_live_provenance_is_required_but_readers_keep_historical_null() -> None:
    assert ChangeResolved.model_validate(RESOLUTION).decided_by is None
    with pytest.raises(ValidationError):
        ChangeResolved.model_validate(RESOLUTION, context={"live": True})
    ChangeResolved.model_validate(
        {**RESOLUTION, "decided_by": "person", "head_commit": "head"}, context={"live": True}
    )
    for actor in ("cross_review", "lead_agent"):
        with pytest.raises(ValidationError):
            ChangeResolved.model_validate({**RESOLUTION, "decided_by": actor})
    ChangeResolved.model_validate(
        {
            **RESOLUTION,
            "decided_by": "cross_review",
            "decision": "changes_requested",
            "comment": "Fix this",
            "head_commit": "head",
            "review_seq": 1,
        }
    )


def test_review_array_limits_are_intrinsic_and_known_verdicts_need_complete_evidence() -> None:
    finding = dict(severity="NIT", text="Consider a name")
    for count in (0, 20):
        CrossReview.model_validate({**REVIEW, "findings": [finding] * count})
    for updates in (
        {"findings": [finding] * 21},
        {"complete": False},
        {"problems": ["missing"]},
        {"author_model": "reviewer"},
        {"findings": [{"severity": None, "text": "unclear"}]},
    ):
        with pytest.raises(ValidationError):
            CrossReview.model_validate({**REVIEW, **updates})
    CrossReview.model_validate(
        {**REVIEW, "verdict": "unknown", "complete": False, "problems": ["missing"]}
    )
    fact = dict(kind="stub_left", path="a.py", line=1, text="Placeholder", evidence=[])
    with pytest.raises(ValidationError):
        IntegrityFact.model_validate(fact)
    IntegrityFact.model_validate({**fact, "evidence": [{"git_object": "head"}]})


def tree(directories: int, files: int, truncated: bool = False) -> dict[str, Any]:
    return dict(
        run_id="run-20261027-140312",
        agent_id=None,
        directories=[f"d{i:04}" for i in range(directories)],
        files=[dict(path=f"f{i:04}", hash=None) for i in range(files)],
        graph_version=None,
        truncated=truncated,
    )


@pytest.mark.parametrize("dirs,files", [(2000, 0), (0, 2000), (1000, 1000)])
def test_tree_joint_cap_and_truncation(dirs: int, files: int) -> None:
    TreeResponse.model_validate(tree(dirs, files))
    TreeResponse.model_validate(tree(dirs, files, True))
    with pytest.raises(ValidationError):
        TreeResponse.model_validate(tree(dirs, files + 1))
    with pytest.raises(ValidationError):
        TreeResponse.model_validate(
            tree(dirs, files - 1 if files else 0, True) if files else tree(dirs - 1, 0, True)
        )


def test_tree_required_nullable_hash_and_sorted_unique_paths() -> None:
    TreeFile(path="binary", hash=None)
    TreeFile(path="a.py", hash="a" * 64)
    for data in (
        {"path": "a.py"},
        {"path": "a.py", "hash": "A" * 64},
        {"path": "a.py", "hash": "ab"},
    ):
        with pytest.raises(ValidationError):
            TreeFile.model_validate(data)
    for updates in (
        {"directories": ["b", "a"]},
        {"directories": ["a", "a"]},
        {"directories": ["a"], "files": [{"path": "a", "hash": None}]},
    ):
        with pytest.raises(ValidationError):
            TreeResponse.model_validate({**tree(0, 0), **updates})
    for key in tree(0, 0):
        with pytest.raises(ValidationError):
            TreeResponse.model_validate({k: v for k, v in tree(0, 0).items() if k != key})


def test_save_file_stays_1_3_and_suites_do_not_compare_phase_counts() -> None:
    for body in ({}, {"base_hash": None}):
        with pytest.raises(ValidationError):
            SaveFile.model_validate(dict(path="a.py", content="x", **body))
    suite = dict(
        outcome="failed",
        passed=0,
        failed=2,
        skipped=0,
        duration_ms=1,
        failed_tests=["a"],
        cases=[dict(test_id="a", outcome="error")],
        cases_complete=True,
    )
    SuiteResult.model_validate(suite)
    with pytest.raises(ValidationError):
        SuiteResult.model_validate({**suite, "cases": suite["cases"] * 2})


def test_profiles_have_independent_exact_sets_and_strict_new_rule_inputs() -> None:
    assert len(SAFE_MERGE_V1_RULES) == 5 and len(SAFE_MERGE_V2_RULES) == 11
    assert [len(rules) for rules in POLICY_RULES.values()] == [5, 6, 7, 8, 9, 9, 9, 11]
    assert not {"run_test_integrity", "tests_reach_changes"}.intersection(
        POLICY_RULES["safe-merge-zone-v1"]
    )
    for violations, complete, result in (
        (0, True, "pass"),
        (1, False, "fail"),
        (0, False, "unknown"),
    ):
        RuleAssessment.model_validate(
            dict(
                rule="cross_review",
                observed=dict(violations=violations, complete=complete),
                threshold=dict(violations_max=0, complete=True),
                result=result,
                evidence=[dict(git_object="h")],
            )
        )
    with pytest.raises(ValidationError):
        RuleAssessment.model_validate(
            dict(
                rule="cross_review",
                observed=dict(violations=True, complete=True),
                threshold=dict(violations_max=0, complete=True),
                result="pass",
                evidence=[dict(git_object="h")],
            )
        )


def test_generated_arrays_and_required_guards_are_plain() -> None:
    definitions = json.loads((ROOT / "contracts/generated/oversight.schema.json").read_text())[
        "$defs"
    ]
    for name, field in (
        ("CrossReview", "findings"),
        ("IntegrityFact", "evidence"),
        ("TreeResponse", "directories"),
        ("TreeResponse", "files"),
    ):
        assert not {"minItems", "maxItems", "prefixItems"}.intersection(
            definitions[name]["properties"][field]
        )
    ts = (ROOT / "contracts/generated/contracts.ts").read_text()
    for declaration in (
        "findings: ReviewFinding[];",
        "evidence: EvidenceRef[];",
        "directories: WorkspacePath[];",
        "files: TreeFile[];",
    ):
        assert declaration in ts
    assert ts.count("export interface StartAgent ") == 1
    for name in ("ApproveChange", "RejectChange", "RequestChanges"):
        assert {"revision", "head_commit"} <= set(definitions[name]["required"])


def test_authority_validator_keeps_the_approved_literal() -> None:
    import hashlib
    import inspect
    import textwrap

    source = textwrap.dedent(inspect.getsource(cast(Any, ChangeResolved._authority)))
    assert len(source.encode()) == 1083
    assert (
        hashlib.sha256(source.encode()).hexdigest()
        == "75209af059b68c47d410248ae7995f1ca557f078b685c290bb298d23bc60daff"
    )
