"""Removal and skip witnesses establish absence of failure, never execution success."""

from dataclasses import dataclass, replace
from typing import Literal

import pytest
from changes_helpers import TESTS, Harness

from oversight.changes._run_findings import (
    EditReceipt,
    RunEvidence,
    SuiteReceipt,
    project_run_findings,
)
from oversight.events import StoredEvent
from oversight_contracts.events import (
    CaseResult,
    ChangeUpdated,
    ChangeUpdatedEvent,
    FileDiff,
    SuiteExecution,
    SuiteResult,
)
from oversight_contracts.events import TestsExecutedEvent as ExecutionEvent
from oversight_contracts.graph import ChangeGraph
from oversight_contracts.policy import EvidenceRef
from oversight_contracts.review import ChangeFacts, ChangeTarget, IntegrityFact

ID = f"{TESTS}::test_value"
OTHER = f"{TESTS}::test_other"
TS = "2026-10-30T15:00:00.000Z"
BODY = "def test_value():\n    assert False\n"
BEFORE = "import pytest\n\n" + BODY + "\ndef test_other():\n    assert True\n"
type Kind = Literal["removed", "skipped"]


def stored(seq: int, payload: ChangeUpdated | SuiteExecution) -> StoredEvent:
    if isinstance(payload, ChangeUpdated):
        event = ChangeUpdatedEvent(seq=seq, ts=TS, type="change.updated", payload=payload)
    else:
        event = ExecutionEvent(seq=seq, ts=TS, type="tests.executed", payload=payload)
    return StoredEvent(seq=seq, ts=TS, type=event.type, envelope=event.model_dump_json())


@dataclass(frozen=True)
class Sample:
    kind: Kind
    evidence: RunEvidence
    integrity: tuple[IntegrityFact, ...]
    executions: dict[int, SuiteExecution]
    target: ChangeTarget

    def project(self):
        return project_run_findings(
            self.evidence, self.integrity, self.executions, self.target, "agent-1"
        )

    def later(self, **fields) -> "Sample":
        execution = self.executions[3]
        execution = execution.model_copy(
            update={"suite": execution.suite.model_copy(update=fields)}
        )
        suites = (
            *self.evidence.suites[:1],
            replace(self.evidence.suites[1], event=stored(3, execution)),
        )
        return replace(
            self,
            evidence=replace(self.evidence, suites=suites),
            executions={1: self.executions[1], 3: execution},
        )


@pytest.fixture(params=["removed", "skipped"])
def sample(request: pytest.FixtureRequest) -> Sample:
    kind: Kind = request.param
    after = (
        BEFORE.replace(BODY, "")
        if kind == "removed"
        else BEFORE.replace(BODY, '@pytest.mark.skip(reason="review")\n' + BODY)
    )
    update = ChangeUpdated(
        change_id="change-1",
        agent_id="agent-1",
        author="agent",
        file=FileDiff(
            path=TESTS,
            status="modified",
            original=BEFORE,
            modified=after,
            lines_added=1,
            lines_removed=1,
        ),
        node_ids=[],
        graph=ChangeGraph(
            nodes_added=[], nodes_modified=[], nodes_removed=[], edges_added=[], edges_removed=[]
        ),
        files_changed=1,
        lines_added=1,
        lines_removed=1,
    )
    executions = {}
    for seq, cases in (
        (1, [CaseResult(test_id=ID, outcome="failed")]),
        (3, [] if kind == "removed" else [CaseResult(test_id=ID, outcome="skipped")]),
    ):
        executions[seq] = SuiteExecution(
            change_id="change-1",
            agent_id="agent-1",
            revision=1,
            phase="agent",
            tested_commit=None,
            tested_tree=f"tree-{seq}",
            suite=SuiteResult(
                outcome="failed" if seq == 1 else "passed",
                passed=0,
                failed=1 if seq == 1 else 0,
                skipped=1 if seq == 3 and kind == "skipped" else 0,
                duration_ms=1,
                failed_tests=[ID] if seq == 1 else [],
                cases=cases,
                cases_complete=True,
            ),
        )
    return Sample(
        kind,
        RunEvidence(
            edits=(EditReceipt(stored(2, update), 1, BEFORE),),
            suites=tuple(
                SuiteReceipt(stored(seq, execution), 1, ((TESTS, text),))
                for seq, execution, text in ((1, executions[1], BEFORE), (3, executions[3], after))
            ),
        ),
        (
            IntegrityFact(
                kind="test_removed" if kind == "removed" else "skip_added",
                path=TESTS,
                line=3,
                text="Source observation",
                test_id=ID,
                evidence=[EvidenceRef(git_object="candidate-tree", path=TESTS)],
            ),
        ),
        executions,
        ChangeTarget(
            change_id="change-1",
            revision=1,
            base_commit="base",
            head_commit="head",
            main_commit="main",
            candidate_tree="candidate-tree",
        ),
    )


@pytest.mark.parametrize("unrelated_failure", [False, True])
def test_complete_execution_without_target_failure_is_a_witness(
    sample: Sample, unrelated_failure: bool
) -> None:
    if unrelated_failure:
        cases = [
            *(sample.executions[3].suite.cases or []),
            CaseResult(test_id=OTHER, outcome="failed"),
        ]
        sample = sample.later(outcome="failed", failed=1, failed_tests=[OTHER], cases=cases)
    facts, [finding], problems = sample.project()
    assert finding.kind == sample.kind
    assert finding.test_id == ID
    assert (finding.failed_seq, finding.edited_seq, finding.passed_seq) == (1, 2, 3)
    assert finding.fact_index == 0
    assert EvidenceRef(event_seq=2, path=TESTS) in facts[0].evidence
    assert not problems


def test_target_still_listed_as_failed_has_no_witness(sample: Sample) -> None:
    # Even a contradictory per-case skip must not erase a listed failure.
    sample = sample.later(outcome="failed", failed=1, failed_tests=[ID])
    _, findings, problems = sample.project()
    assert not findings
    assert problems and all(p.startswith("run rule unknown:") for p in problems)
    assert not any("pass" in p.lower() for p in problems)


@pytest.mark.parametrize("mode", ["truncated", "cases", "timeout", "error", "no_later", "receipt"])
def test_incomplete_later_evidence_stays_unknown(sample: Sample, mode: str) -> None:
    if mode == "truncated":
        sample = sample.later(outcome="failed", failed=21, failed_tests=[OTHER])
    elif mode == "cases":
        sample = sample.later(cases_complete=False)
    elif mode in {"timeout", "error"}:
        sample = sample.later(outcome=mode)
    else:
        sample = replace(
            sample, evidence=replace(sample.evidence, suites=sample.evidence.suites[:1])
        )
        if mode == "no_later":
            sample = replace(sample, executions={1: sample.executions[1]})
    _, findings, problems = sample.project()
    assert not findings
    assert problems and all(p.startswith("run rule unknown:") for p in problems)
    assert not any("pass" in p.lower() for p in problems)


def test_first_complete_later_execution_resolves_truncation(sample: Sample) -> None:
    complete = sample.executions[3]
    sample = sample.later(cases_complete=False)
    suites = (
        *sample.evidence.suites,
        replace(sample.evidence.suites[1], event=stored(4, complete)),
    )
    sample = replace(
        sample,
        evidence=replace(sample.evidence, suites=suites),
        executions={**sample.executions, 4: complete},
    )
    _, [finding], problems = sample.project()
    assert finding.passed_seq == 4 and not problems


@pytest.mark.parametrize(
    "mode",
    [
        "human",
        "duplicate",
        "revision",
        "before_version",
        "after_version",
        "identity",
        "other_test",
        "analysis",
        "deleted",
        "missing_edit",
    ],
)
def test_unproven_edit_stays_unknown(sample: Sample, mode: str) -> None:
    receipt = sample.evidence.edits[0]
    from oversight.changes._run_findings import EVENT

    event = EVENT.validate_json(receipt.event.envelope)
    assert event.type == "change.updated"
    update = event.payload
    if mode == "human":
        receipt = replace(receipt, event=stored(2, update.model_copy(update={"author": "user"})))
    elif mode == "duplicate":
        # The cumulative diff still contains the weakening; this publication changed nothing.
        receipt = replace(receipt, before=update.file.modified or "")
    elif mode == "revision":
        receipt = replace(receipt, revision=2)
    elif mode.endswith("_version"):
        index = 0 if mode == "before_version" else 1
        suites = list(sample.evidence.suites)
        suites[index] = replace(suites[index], sources=((TESTS, "# another version\n"),))
        sample = replace(sample, evidence=replace(sample.evidence, suites=tuple(suites)))
    elif mode == "identity":
        sample = replace(
            sample, integrity=(sample.integrity[0].model_copy(update={"test_id": None}),)
        )
    elif mode == "other_test":
        sample = replace(
            sample, integrity=(sample.integrity[0].model_copy(update={"test_id": OTHER}),)
        )
    elif mode == "deleted":
        file = update.file.model_copy(update={"status": "deleted", "modified": None})
        receipt = replace(receipt, event=stored(2, update.model_copy(update={"file": file})))
    elif mode == "analysis":
        receipt = replace(receipt, before="def broken(:\n")
    sample = replace(
        sample,
        evidence=replace(sample.evidence, edits=() if mode == "missing_edit" else (receipt,)),
    )
    _, findings, problems = sample.project()
    assert not findings
    assert problems and all(p.startswith("run rule unknown:") for p in problems)
    assert not any("pass" in p.lower() for p in problems)


@pytest.mark.parametrize("mode", ["foreign_agent", "foreign_change", "duplicate_receipt"])
def test_invalid_receipts_are_rejected(sample: Sample, mode: str) -> None:
    if mode == "duplicate_receipt":
        sample = replace(sample, evidence=replace(sample.evidence, edits=sample.evidence.edits * 2))
    else:
        execution = sample.executions[3].model_copy(
            update={"agent_id": "agent-2"} if mode == "foreign_agent" else {"change_id": "change-2"}
        )
        suites = (
            *sample.evidence.suites[:1],
            replace(sample.evidence.suites[1], event=stored(3, execution)),
        )
        sample = replace(sample, evidence=replace(sample.evidence, suites=suites))
    with pytest.raises(ValueError, match="run rule"):
        sample.project()


@pytest.mark.parametrize("phase", ["base_probe", "head_probe", "combined", "old_proposal"])
def test_unrelated_execution_phase_has_no_authority(sample: Sample, phase: str) -> None:
    execution = sample.executions[3].model_copy(
        update={"phase": "proposal", "revision": 2} if phase == "old_proposal" else {"phase": phase}
    )
    suites = (
        sample.evidence.suites[0],
        replace(sample.evidence.suites[1], event=stored(3, execution)),
    )
    sample = replace(
        sample,
        evidence=replace(sample.evidence, suites=suites),
        executions={1: sample.executions[1], 3: execution},
    )
    _, findings, problems = sample.project()
    assert not findings and any(p.startswith("run rule unknown:") for p in problems)


@pytest.mark.parametrize(
    ("text", "line", "test_id"),
    [
        (
            "import pytest\n@pytest.mark.skip\nclass TestValue:\n"
            "    def test_value(self):\n        assert False\n"
            "    def test_other(self):\n        assert True\n",
            2,
            f"{TESTS}::TestValue::test_value[param]",
        ),
        ("import pytest\npytestmark = pytest.mark.skip\n" + BODY, 2, ID),
    ],
)
def test_shared_skip_markers_support_only_resolved_test_identities(
    text: str, line: int, test_id: str
) -> None:
    from oversight.changes._run_findings import _supports

    assert _supports(TESTS, text, line, test_id, skip=True)
    assert not _supports(TESTS, text, line, f"{TESTS}::unrelated", skip=True)


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["removed", "skipped"])
async def test_published_facts_validate_with_absence_witnesses(h: Harness, kind: Kind) -> None:
    tree = await h.open(1)
    assert await h.edit(1, TESTS, "from shop.mod", "import pytest\nfrom shop.mod")
    assert await h.edit(1, TESTS, " > 0\n", " > 0\n\n\ndef test_other():\n    assert True\n")
    assert await h.edit(1, "shop/mod.py", "return 1", "return 0")
    initial = await h.workspace.run_tests(tree, timeout_s=60)
    assert initial.result.failed_tests == [ID]
    await h.manager.tests_executed("agent-1", initial)
    body = "def test_value() -> None:\n    assert value() > 0\n"
    replacement = "" if kind == "removed" else '@pytest.mark.skip(reason="review")\n' + body
    assert await h.edit(1, TESTS, body, replacement)
    assert await h.finish(1)
    [facts] = h.payloads(ChangeFacts)
    assert ChangeFacts.model_validate_json(facts.model_dump_json()) == facts
    [finding] = facts.run_findings
    assert finding.kind == kind and finding.test_id == ID
    events = {e.seq: e for e in h.events()}
    assert [
        events[s].type for s in (finding.failed_seq, finding.edited_seq, finding.passed_seq)
    ] == ["tests.executed", "change.updated", "tests.executed"]
    later = events[finding.passed_seq].payload.suite
    assert later.cases_complete
    assert [c.outcome for c in later.cases if c.test_id == ID] == (
        [] if kind == "removed" else ["skipped"]
    )
    assert not any("run rule unknown:" in p for p in facts.problems)
    assert not any("pass" in p.lower() for p in facts.problems)
