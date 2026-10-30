"""Committed temporal witnesses for assertion edits, test removals and skips."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import TypeAdapter

from oversight.changes._runrule import Edit, RunTrace, TestRun, Weakening, evaluate
from oversight.changes._shortcut_detector import ShortcutKind, detect_shortcuts
from oversight.changes._shortcuts_common import Source
from oversight.changes._shortcuts_skips import DECORATOR_SKIP, module_markers
from oversight.events import StoredEvent
from oversight_contracts.events import ChangeUpdated, ServerEvent, SuiteExecution
from oversight_contracts.policy import EvidenceRef
from oversight_contracts.review import ChangeTarget, IntegrityFact, RunRuleFinding

EVENT = TypeAdapter(ServerEvent)
type FindingKind = Literal["loosened", "removed", "skipped"]
KINDS: dict[str, FindingKind] = {
    "assertion_loosened": "loosened",
    "test_removed": "removed",
    "skip_added": "skipped",
}


@dataclass(frozen=True)
class EditReceipt:
    event: StoredEvent
    revision: int
    before: str


@dataclass(frozen=True)
class SuiteReceipt:
    event: StoredEvent
    revision: int
    sources: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class RunEvidence:
    edits: tuple[EditReceipt, ...] = ()
    suites: tuple[SuiteReceipt, ...] = ()


def receipt(value: object, payload: ChangeUpdated | SuiteExecution) -> StoredEvent | None:
    """Only a stored envelope proves event type, contents and sequence together."""
    if not isinstance(value, StoredEvent):
        return None
    event = EVENT.validate_json(value.envelope)
    if (event.seq, event.type, event.payload) != (value.seq, value.type, payload):
        raise ValueError("run rule receipt differs from publication")
    return value


def _owns(event: ServerEvent, target: ChangeTarget, agent_id: str) -> None:
    if event.type != "change.updated" and event.type != "tests.executed":
        raise ValueError("run rule receipt has wrong event type")
    if (event.payload.change_id, event.payload.agent_id) != (target.change_id, agent_id):
        raise ValueError("run rule receipt has foreign ownership")


def _event(stored: StoredEvent, target: ChangeTarget, agent_id: str) -> ServerEvent:
    event = EVENT.validate_json(stored.envelope)
    if (stored.seq, stored.type) != (event.seq, event.type):
        raise ValueError("run rule receipt envelope differs")
    _owns(event, target, agent_id)
    return event


def _supports(path: str, text: str, line: int, test_id: str, *, skip: bool = False) -> bool:
    """Resolve a detector line to exactly one definition, retaining class/parameter identity."""
    source = Source(text)
    enclosing = [
        qual
        for qual, node in source.tests.items()
        if min([node.lineno, *[d.lineno for d in node.decorator_list]])
        <= line
        <= (node.end_lineno or node.lineno)
    ]
    if skip and not enclosing:
        classes = [
            qual
            for qual, node in source.classes.items()
            if any(
                d.lineno == line and source.resolve(d) in DECORATOR_SKIP
                for d in node.decorator_list
            )
        ]
        if len(classes) == 1:
            enclosing = [qual for qual in source.tests if qual.startswith(classes[0] + ".")]
        elif any(marker_line == line for _, marker_line in module_markers(source)):
            enclosing = list(source.tests)
        # A shared marker can affect several independently resolved test ids.
        enclosing = [
            qual
            for qual in enclosing
            if "::".join([path, *qual.split(".")]) == test_id.split("[", 1)[0]
        ]
    return (
        len(enclosing) == 1
        and "#" not in enclosing[0]
        and ("::".join([path, *enclosing[0].split(".")]) == test_id.split("[", 1)[0])
    )


def _run(seq: int, execution: SuiteExecution, test_id: str, kind: FindingKind) -> TestRun:
    suite = execution.suite
    cases = {c.test_id: c.outcome for c in suite.cases or ()}
    # Only loosened assertions require an explicit per-case pass. Removals and
    # skips establish absence of failure from a complete later execution.
    failed = tuple(suite.failed_tests)
    observed_failure = cases.get(test_id) == "failed" and test_id in failed
    return TestRun(
        seq,
        seq,
        failed
        if kind != "loosened" or observed_failure
        else tuple(i for i in failed if i != test_id),
        suite.failed,
        suite.outcome in {"passed", "failed"}
        and suite.cases_complete is True
        and suite.failed == len(failed) == len(set(failed))
        and (kind != "loosened" or cases.get(test_id) == "passed"),
    )


type Suites = dict[int, tuple[SuiteExecution, dict[str, str]]]
type Edits = list[tuple[int, ChangeUpdated, str]]


def _observations(
    evidence: RunEvidence,
    executions: Mapping[int, SuiteExecution],
    target: ChangeTarget,
    agent_id: str,
) -> tuple[Suites, Edits]:
    suites: Suites = {}
    seen: set[int] = set()
    for item in evidence.suites:
        event = _event(item.event, target, agent_id)
        if event.type != "tests.executed" or event.seq in seen:
            raise ValueError("run rule duplicate or wrong suite receipt")
        seen.add(event.seq)
        if event.payload != executions.get(event.seq):
            raise ValueError("run rule execution differs from receipt")
        if item.revision == target.revision and (
            event.payload.phase == "agent"
            or (event.payload.phase == "proposal" and event.payload.revision == target.revision)
        ):
            suites[event.seq] = event.payload, dict(item.sources)
    edits: Edits = []
    for item in evidence.edits:
        event = _event(item.event, target, agent_id)
        if event.type != "change.updated" or event.seq in seen:
            raise ValueError("run rule duplicate or wrong edit receipt")
        seen.add(event.seq)
        if item.revision == target.revision and event.payload.author == "agent":
            edits.append((event.seq, event.payload, item.before))
    return suites, edits


def project_run_findings(
    evidence: RunEvidence | None,
    integrity: tuple[IntegrityFact, ...],
    executions: Mapping[int, SuiteExecution],
    target: ChangeTarget,
    agent_id: str,
) -> tuple[list[IntegrityFact], list[RunRuleFinding], tuple[str, ...]]:
    """Recompute the projection without mutable reads, Git or log scans."""
    facts = [fact.model_copy(deep=True) for fact in integrity]
    if evidence is None:
        return facts, [], ()  # Legacy/private captures have no temporal authority.
    suites, edits = _observations(evidence, executions, target, agent_id)
    findings: dict[str, RunRuleFinding] = {}
    receipts = {item.event.seq for item in evidence.suites}
    problems = {
        f"run rule unknown: suite {seq} receipt unavailable"
        for seq, execution in executions.items()
        if seq not in receipts
        and (
            execution.phase == "agent"
            or (execution.phase == "proposal" and execution.revision == target.revision)
        )
    }
    for index, fact in enumerate(facts):
        kind = KINDS.get(fact.kind)
        if kind is None:
            continue
        if fact.test_id is None:
            identity = "assertion" if kind == "loosened" else "test"
            problems.add(f"run rule unknown: unresolved {identity} identity")
            continue
        witnessed = False
        for seq, edit, before in sorted(edits):
            after = edit.file.modified
            if edit.file.path != fact.path or after is None or before == after:
                continue
            detected = detect_shortcuts([(fact.path, before, after)], grouped=False)
            if detected.status == "unknown":
                problems.add(f"run rule unknown: edit {seq} source analysis incomplete")
                continue
            text = before if kind == "removed" else after
            if not any(
                f.kind == ShortcutKind(fact.kind)
                and _supports(f.path, text, f.line, fact.test_id, skip=kind == "skipped")
                for f in detected
            ):
                continue
            entries: list[TestRun] = []
            for run_seq, (execution, sources) in sorted(suites.items()):
                # Exact successive file versions also fail closed on collection or
                # parametrization edits. Other source versions cannot witness this edit.
                expected = before if run_seq < seq else after
                if sources.get(fact.path) == expected:
                    entries.append(_run(run_seq, execution, fact.test_id, kind))
            result = evaluate(
                RunTrace(agent_id, (*entries, Edit(seq, fact.path))),
                [Weakening(kind=kind, test_id=fact.test_id)],
            )
            if result.status == "unknown":
                problems.add(f"run rule unknown: incomplete comparison for {fact.test_id}")
            for found in result.findings:
                witnessed = True
                findings.setdefault(
                    fact.test_id,
                    RunRuleFinding(
                        kind=kind,
                        test_id=fact.test_id,
                        fact_index=index,
                        failed_seq=found.failing_run.seq,
                        edited_seq=seq,
                        passed_seq=found.later_run.seq,
                    ),
                )
        if not witnessed:
            witness = "passing assertion" if kind == "loosened" else "absence-of-failure"
            problems.add(f"run rule unknown: no {witness} witness for {fact.test_id}")
    for finding in findings.values():
        fact = facts[finding.fact_index]
        fact.evidence.append(EvidenceRef(event_seq=finding.edited_seq, path=fact.path))
    return facts, list(findings.values()), tuple(sorted(problems))
