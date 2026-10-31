"""Evidence-backed protected-path checks and exact live profile authority."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from typing import get_args

import pytest
from test_policy_profiles import policy_inputs, review

from oversight.agents import FAKE_MODEL, FAKE_MODEL_B
from oversight.changes import _protected_paths
from oversight.changes._assessment import PROFILE as ACTIVE_PROFILE
from oversight.changes._policy import PolicyInputs, assess, merges_alone
from oversight.changes._protected_paths import (
    TOUCHING_STATUSES,
    ChangedFileInventory,
    ProtectedPathMatch,
    ProtectedPathSnapshot,
    check_protected_paths,
)
from oversight.changes._rules import pending_policy
from oversight_contracts.common import FileStatus
from oversight_contracts.events import FileStat
from oversight_contracts.policy import POLICY_RULES, ChangeAssessment, EvidenceRef, RuleAssessment

PROFILE = "safe-merge-paths-v1"
TABLE = EvidenceRef(git_object="table-snapshot")
DIFF = EvidenceRef(event_seq=300)


def file(path: str, status: FileStatus = "modified") -> FileStat:
    return FileStat(path=path, status=status, lines_added=0, lines_removed=0)


@pytest.fixture
def ready() -> PolicyInputs:
    return replace(
        policy_inputs(),
        author=FAKE_MODEL,
        review=review(FAKE_MODEL, FAKE_MODEL_B),
        protected_paths=ProtectedPathSnapshot(evidence=(TABLE,)),
        changed_files=ChangedFileInventory(
            files=(file("shop.py"),), complete=True, evidence=(DIFF,)
        ),
    )


def result(inputs: PolicyInputs) -> RuleAssessment:
    return next(r for r in assess(inputs, PROFILE).rules if r.rule == "protected_paths")


def changed(ready: PolicyInputs, *files: FileStat) -> PolicyInputs:
    assert ready.changed_files is not None
    return replace(ready, changed_files=replace(ready.changed_files, files=files))


def test_evidenced_nonmatch_passes(ready: PolicyInputs) -> None:
    item = result(ready)
    assert item.result == "pass"
    assert item.observed == {"violations": 0, "complete": True}
    assert item.threshold == {"violations_max": 0, "complete": True}
    assert item.evidence == [TABLE, DIFF]


@pytest.mark.parametrize("status", get_args(FileStatus.__value__))
def test_protected_added_modified_deleted_fail(ready: PolicyInputs, status: FileStatus) -> None:
    item = result(changed(ready, file("pyproject.toml", status)))
    assert item.result == "fail"
    assert item.observed == {"violations": 1, "complete": True}
    assert TABLE in item.evidence and DIFF in item.evidence
    assert TABLE.model_copy(update={"path": "pyproject.toml"}) in item.evidence
    assert DIFF.model_copy(update={"path": "pyproject.toml"}) in item.evidence


@pytest.mark.parametrize(
    "path",
    [
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "requirements.txt",
        "requirements-dev.txt",
        "uv.lock",
        "poetry.lock",
        "Pipfile",
        "Pipfile.lock",
        "pytest.ini",
        "tox.ini",
        "noxfile.py",
        ".coveragerc",
        "conftest.py",
    ],
)
@pytest.mark.parametrize("prefix", ["", "packages/app/", "packages/app/tests/"])
def test_manifest_and_test_config_patterns_at_any_depth(
    ready: PolicyInputs, path: str, prefix: str
) -> None:
    assert result(changed(ready, file(prefix + path))).result == "fail"


@pytest.mark.parametrize(
    "path",
    [
        ".github",
        ".github/workflows/ci.yml",
        ".github/CODEOWNERS",
        ".github/actions/build/action.yml",
        ".gitlab-ci.yml",
        ".env",
        ".env.local",
        ".env.example",
        ".gitignore",
        ".pre-commit-config.yaml",
        ".gitleaks.toml",
    ],
)
def test_root_ci_and_security_patterns(ready: PolicyInputs, path: str) -> None:
    assert result(changed(ready, file(path))).result == "fail"


@pytest.mark.parametrize(
    "path",
    [
        "pyproject.toml.bak",
        "mysetup.py",
        "requirements-dev.txt.bak",
        "uv.locked",
        "conftest.py.bak",
        "pytest.ini.old",
        ".github-other/workflows/ci.yml",
        ".gitlab-ci.yml.old",
        ".envoy",
        ".gitignore.old",
        "nested/.gitignore",
        "nested/.github/workflows/ci.yml",
        "nested/.env",
        "PYPROJECT.TOML",
    ],
)
def test_builtin_names_do_not_match_neighbors(ready: PolicyInputs, path: str) -> None:
    assert result(changed(ready, file(path))).result == "pass"


def test_explicit_paths_are_exact_and_duplicates_do_not_inflate_counts(ready: PolicyInputs) -> None:
    ready = replace(
        ready,
        protected_paths=ProtectedPathSnapshot(
            paths=("private/a.py", "private/a.py", "literal*.py"), evidence=(TABLE, TABLE)
        ),
    )
    item = result(changed(ready, file("private/a.py"), file("private/a.py", "deleted")))
    assert item.result == "fail" and item.observed == {"violations": 1, "complete": True}
    assert len(item.evidence) == len({(r.event_seq, r.git_object, r.path) for r in item.evidence})
    for path in ("private/a.py.bak", "private/a.py/child", "a.py", "literal1.py", "pyproject.toml"):
        assert result(changed(ready, file(path))).result == "pass"
    assert result(changed(ready, file("literal*.py"))).result == "fail"


def test_counts_unique_files_not_sources_or_statuses(ready: PolicyInputs) -> None:
    item = result(
        changed(
            ready, file("uv.lock"), file("uv.lock", "added"), file("pytest.ini"), file("shop.py")
        )
    )
    assert item.observed == {"violations": 2, "complete": True}


def test_matches_record_category_and_pattern_without_inflating_overlaps(
    ready: PolicyInputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        _protected_paths,
        "BUILTIN_CATEGORIES",
        (*_protected_paths.BUILTIN_CATEGORIES, ("extra_manifest", ("requirements-dev.*",), True)),
    )
    ready = changed(ready, file("app/requirements-dev.txt"))
    captured = check_protected_paths(ready.protected_paths, ready.changed_files)
    assert captured is not None
    assert captured.matches == (
        ProtectedPathMatch(
            path="app/requirements-dev.txt",
            category="manifest_lockfile",
            pattern="requirements*.txt",
        ),
        ProtectedPathMatch(
            path="app/requirements-dev.txt", category="extra_manifest", pattern="requirements-dev.*"
        ),
    )
    assert result(ready).observed == {"violations": 1, "complete": True}


def test_status_table_is_exhaustive() -> None:
    assert set(TOUCHING_STATUSES) == set(get_args(FileStatus.__value__))
    assert all(TOUCHING_STATUSES.values())


@pytest.mark.parametrize("source", ["list", "inventory"])
@pytest.mark.parametrize("failure", ["absent", "unreadable", "incomplete", "no_receipt"])
@pytest.mark.parametrize("hit", [False, True])
def test_unavailable_inputs_wait_before_counting(
    ready: PolicyInputs, source: str, failure: str, hit: bool
) -> None:
    ready = changed(ready, file("uv.lock" if hit else "shop.py"))
    key = "protected_paths" if source == "list" else "changed_files"
    captured = getattr(ready, key)
    if failure in {"absent", "unreadable"}:
        captured = None  # An I/O producer maps collection errors to unavailable, never empty.
    elif failure == "incomplete":
        captured = replace(captured, complete=False)
    else:
        captured = replace(captured, evidence=())
    item = result(replace(ready, **{key: captured}))
    assert item.result == "unknown"
    assert item.observed is None


@pytest.mark.parametrize("paths", [(), ("private/a.py",)])
def test_captured_empty_inventory_is_known(ready: PolicyInputs, paths: tuple[str, ...]) -> None:
    ready = replace(ready, protected_paths=ProtectedPathSnapshot(paths=paths, evidence=(TABLE,)))
    item = result(changed(ready))
    assert item.result == "pass" and item.observed == {"violations": 0, "complete": True}
    assert item.evidence == [TABLE, DIFF]


def test_explicit_empty_list_does_not_use_builtin_table(ready: PolicyInputs) -> None:
    ready = replace(ready, protected_paths=ProtectedPathSnapshot(paths=(), evidence=(TABLE,)))
    assert result(changed(ready, file("uv.lock"))).result == "pass"
    assert result(replace(ready, protected_paths=None)).result == "unknown"


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/uv.lock",
        "../uv.lock",
        "a/../uv.lock",
        "./uv.lock",
        "a/./uv.lock",
        "a//uv.lock",
        "a/",
        "a\\uv.lock",
        "C:/uv.lock",
        ".git/config",
        "a/.git/config",
        "uv.lock\x00",
        "a/\nuv.lock",
    ],
)
@pytest.mark.parametrize("source", ["list", "inventory"])
def test_malformed_paths_make_even_mixed_hit_inventories_unknown(
    ready: PolicyInputs, path: str, source: str
) -> None:
    ready = changed(ready, file("uv.lock"))
    if source == "list":
        ready = replace(
            ready, protected_paths=ProtectedPathSnapshot(paths=("uv.lock", path), evidence=(TABLE,))
        )
    else:
        ready = changed(ready, file("uv.lock"), file(path))
    assert result(ready).result == "unknown"
    assert result(ready).observed is None


@pytest.mark.parametrize("status", ["renamed", "future", "", None, 0, [], {}])
@pytest.mark.parametrize("known_hit", [False, True])
def test_unvalidated_unknown_status_never_produces_pass_or_fail(
    ready: PolicyInputs, status: object, known_hit: bool
) -> None:
    unvalidated = FileStat.model_construct(
        path="other.py", status=status, lines_added=0, lines_removed=0
    )
    inventory = (file("uv.lock"), unvalidated) if known_hit else (unvalidated,)
    item = result(changed(ready, *inventory))
    assert item.result == "unknown" and item.observed is None


def test_default_inputs_use_builtin_but_require_capture_and_evidence(ready: PolicyInputs) -> None:
    defaults = PolicyInputs(
        change_id="change-1",
        agent_id="agent-1",
        revision=1,
        base_commit="base",
        head_commit="head",
        main_commit="main",
        mode="merge_when_safe",
        mode_revision=1,
    )
    assert defaults.protected_paths == ProtectedPathSnapshot()
    assert defaults.changed_files is None
    assert result(defaults).result == "unknown"
    assert result(replace(ready, protected_paths=defaults.protected_paths)).result == "unknown"


@pytest.mark.parametrize("source", ["protected_paths", "changed_files"])
def test_generic_rule_receipt_cannot_substitute_for_either_source(
    ready: PolicyInputs, source: str
) -> None:
    captured = replace(getattr(ready, source), evidence=())
    ready = replace(ready, evidence={"protected_paths": (TABLE, DIFF)}, **{source: captured})
    assert result(ready).result == "unknown"


def test_inputs_are_immutable_and_assessments_roundtrip_without_mutation(
    ready: PolicyInputs,
) -> None:
    ready = changed(ready, file("uv.lock"))
    before = deepcopy(ready)
    first = assess(ready, PROFILE)
    assert first == assess(ready, PROFILE)
    assert ChangeAssessment.model_validate_json(first.model_dump_json()) == first
    assert ready == before
    assert ready.protected_paths is not None and ready.changed_files is not None
    with pytest.raises(FrozenInstanceError):
        ready.protected_paths.paths = ()  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(FrozenInstanceError):
        ready.changed_files.complete = False  # pyright: ignore[reportAttributeAccessIssue]
    next(r for r in first.rules if r.rule == "protected_paths").evidence.clear()
    assert ready == before and assess(ready, PROFILE) != first


def test_paths_profile_exactly_nine_never_falls_back(ready: PolicyInputs) -> None:
    assessment = assess(ready, PROFILE)
    assert ACTIVE_PROFILE == PROFILE
    assert POLICY_RULES[PROFILE] == POLICY_RULES["safe-merge-probes-v1"] | {"protected_paths"}
    assert len(assessment.rules) == 9 and all(r.result == "pass" for r in assessment.rules)
    assert merges_alone(assessment) and not pending_policy(assessment)
    pending = assessment.model_copy(
        update={
            "rules": [
                r.model_copy(update={"observed": None, "result": "unknown"})
                if r.rule == "combined_tests"
                else r
                for r in assessment.rules
            ]
        }
    )
    assert pending_policy(pending)
    assert merges_alone(assess(ready, ACTIVE_PROFILE))
    for rules in (assessment.rules[:-1], [*assessment.rules[:-1], assessment.rules[0]]):
        assert not merges_alone(assessment.model_copy(update={"rules": rules}))
        assert not pending_policy(pending.model_copy(update={"rules": rules}))
    assert not merges_alone(assessment.model_copy(update={"policy_version": "unsupported"}))
    assert not pending_policy(pending.model_copy(update={"policy_version": "unsupported"}))
    for profile in POLICY_RULES.keys() - {PROFILE}:
        assert not merges_alone(assess(ready, profile))
        assert not pending_policy(assess(ready, profile))
        assert assess(ready, profile) == assess(
            replace(ready, protected_paths=None, changed_files=None), profile
        )
