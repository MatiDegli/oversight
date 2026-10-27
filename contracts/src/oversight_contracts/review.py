"""Candidate identities, advisory reviews and neutral observed facts."""

from typing import Literal, Self

from pydantic import NonNegativeInt, PositiveInt, model_validator

from oversight_contracts.common import (
    ChangeId,
    ContractModel,
    NodeId,
    NonEmptyText,
    ReviewSeverity,
    WorkspacePath,
)
from oversight_contracts.policy import EvidenceRef


class ChangeTarget(ContractModel):
    """The exact candidate identified by the server."""

    change_id: ChangeId
    """The change identifier."""
    revision: NonNegativeInt
    """Zero before any proposal; otherwise the exact proposed revision."""
    base_commit: NonEmptyText
    """The change's base commit."""
    head_commit: NonEmptyText
    """The proposed head, or the agent base at revision zero."""
    main_commit: NonEmptyText
    """Main used to prepare the candidate."""
    candidate_tree: NonEmptyText
    """The exact candidate Git tree."""

    @model_validator(mode="after")
    def _zero_head(self) -> Self:
        if self.revision == 0 and self.head_commit != self.base_commit:
            raise ValueError("revision zero head must equal base")
        return self


class ReviewFinding(ContractModel):
    """One advisory model observation."""

    severity: ReviewSeverity | None
    """Null means an unclassified finding."""
    text: NonEmptyText
    """The model-authored finding."""
    path: WorkspacePath | None = None
    """The affected workspace file."""
    line: PositiveInt | None = None
    """The affected one-based line."""
    node_id: NodeId | None = None
    """The affected graph node."""


class CrossReview(ChangeTarget):
    """An advisory review of an exact candidate."""

    revision: PositiveInt
    """The exact proposed revision under review."""
    author_model: NonEmptyText
    """The exact author model."""
    reviewer_model: NonEmptyText
    """The exact reviewing model."""
    reviewer_provider: Literal["meta", "baseten", "fake"]
    """The reviewing inference provider."""
    reasoning: str | None
    """The explicit reviewing reasoning setting."""
    verdict: Literal["clean", "changes_requested", "unknown"]
    """An advisory review result."""
    findings: list[ReviewFinding]
    """Findings retained from this review."""
    complete: bool
    """Whether review material and output were complete."""
    problems: list[NonEmptyText]
    """Review failures and incompleteness diagnostics."""

    @model_validator(mode="after")
    def _findings_limit(self) -> Self:
        if len(self.findings) > 20:
            raise ValueError("at most 20 review findings are retained")
        return self

    @model_validator(mode="after")
    def _verdict_matches_findings(self) -> Self:
        if self.complete and self.problems:
            raise ValueError("complete reviews have no problems")
        if self.verdict == "unknown":
            if self.complete or not self.problems:
                raise ValueError("unknown reviews require incompleteness diagnostics")
        else:
            if not self.complete or self.author_model == self.reviewer_model:
                raise ValueError("known reviews require complete material and distinct models")
            severities = {finding.severity for finding in self.findings}
            if None in severities:
                raise ValueError("unclassified findings require unknown")
            if self.verdict == "clean" and severities - {"NIT"}:
                raise ValueError("clean reviews allow only NIT findings")
            if self.verdict == "changes_requested" and not severities.intersection(
                {"BLOCKING", "SHOULD-FIX"}
            ):
                raise ValueError("changes_requested requires an adverse finding")
        return self


class FinishDeclaration(ContractModel):
    """Claims made by the agent at finish, never server evidence."""

    files: list[WorkspacePath] | None = None
    """Files declared by the agent."""
    tests_run: bool | None = None
    """Whether the agent declares running tests."""
    tests: list[NonEmptyText] | None = None
    """Pytest ids declared by the agent."""


class IntegrityFact(ContractModel):
    """A neutral source observation retained per affected test."""

    kind: Literal[
        "test_removed",
        "assertion_loosened",
        "skip_added",
        "expected_value_changed",
        "literal_branch",
        "stub_left",
        "parse_error",
    ]
    """The detector's exact neutral fact kind."""
    path: WorkspacePath
    """The observed file."""
    line: PositiveInt
    """The observed one-based line."""
    text: NonEmptyText
    """The detector's neutral observation."""
    evidence: list[EvidenceRef]
    """Source events or Git objects."""
    test_id: NonEmptyText | None = None
    """The individual affected pytest id, when resolved."""
    node_id: NodeId | None = None
    """The resolved graph node, when known."""

    @model_validator(mode="after")
    def _evidence_present(self) -> Self:
        if not self.evidence:
            raise ValueError("integrity facts require at least one evidence reference")
        return self


class AddedTestProbe(ContractModel):
    """Observed before and after behavior of one added test."""

    test_id: NonEmptyText
    """The added pytest case."""
    before_seq: PositiveInt
    """The base-probe execution event."""
    after_seq: PositiveInt
    """The head-probe execution event."""
    result: Literal[
        "fails_before_passes_after",
        "passes_before_and_after",
        "fails_after",
        "not_collected_after",
        "skipped",
        "unknown",
    ]
    """The core's exact evidence-backed fact kind."""

    @model_validator(mode="after")
    def _ordered_evidence(self) -> Self:
        if self.before_seq >= self.after_seq:
            raise ValueError("probe executions must be chronological")
        return self


class RunRuleFinding(ContractModel):
    """One temporal finding supported by an integrity fact."""

    test_id: NonEmptyText
    """The same pytest case throughout."""
    failed_seq: PositiveInt
    """The execution where it failed."""
    edited_seq: PositiveInt
    """The edit weakening its assertion."""
    passed_seq: PositiveInt
    """The execution where it passed."""
    fact_index: NonNegativeInt
    """The supporting integrity fact in this payload."""


class ZoneFacts(ContractModel):
    """Observed zone classification."""

    outside: list[EvidenceRef]
    """Observed edits outside the assigned zone."""
    complete: bool
    """Whether every edit was classified."""


class ReachFacts(ContractModel):
    """Evidence of test reachability."""

    basis: Literal["call_graph", "coverage"]
    """The basis of test reachability."""
    edges_followed: Literal["exact"] | None
    """Exact call edges, or null for coverage."""
    unreached_nodes: list[NodeId]
    """Changed functions without demonstrated test reach."""
    complete: bool
    """Whether all changed functions were classified."""
    evidence: list[EvidenceRef]
    """The graph or coverage evidence."""

    @model_validator(mode="after")
    def _basis_matches_edges(self) -> Self:
        if self.edges_followed != ("exact" if self.basis == "call_graph" else None):
            raise ValueError("reach basis must agree with edges")
        if self.complete and not self.evidence:
            raise ValueError("complete reach requires evidence")
        return self


class ChangeFacts(ChangeTarget):
    """Neutral facts about an exact candidate."""

    declared: FinishDeclaration | None
    """The finish declaration, when available."""
    edited_paths: list[WorkspacePath]
    """Every observed edited path, including restored edits."""
    test_runs: list[PositiveInt]
    """Execution event seqs in chronological order."""
    integrity: list[IntegrityFact]
    """Neutral detector observations, kept separate per affected test."""
    added_tests: list[AddedTestProbe]
    """Added-test probe results."""
    run_findings: list[RunRuleFinding]
    """Evidence-backed temporal findings."""
    zone: ZoneFacts | None
    """Zone evidence, or unknown."""
    reach: ReachFacts | None
    """Test-reach evidence, or unknown."""
    complete: bool
    """Whether the required observations are complete."""
    problems: list[NonEmptyText]
    """Missing evidence and analysis incompleteness diagnostics."""

    @model_validator(mode="after")
    def _local_evidence(self) -> Self:
        if self.test_runs != sorted(set(self.test_runs)):
            raise ValueError("test runs must be ordered and unique")
        if self.complete == bool(self.problems):
            raise ValueError("incomplete facts require problems; complete facts forbid them")
        for finding in self.run_findings:
            if not finding.failed_seq < finding.edited_seq < finding.passed_seq:
                raise ValueError("temporal finding events must be chronological")
            if finding.fact_index >= len(self.integrity):
                raise ValueError("temporal finding refers to a missing fact")
            fact = self.integrity[finding.fact_index]
            if (
                fact.kind != "assertion_loosened"
                or fact.test_id != finding.test_id
                or not any(ref.event_seq == finding.edited_seq for ref in fact.evidence)
            ):
                raise ValueError("temporal finding requires the same test's weakening edit")
        return self
