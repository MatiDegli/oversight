"""Candidate identities, advisory reviews and neutral observed facts."""

from typing import Annotated, Literal, Self

from pydantic import (
    Field,
    FiniteFloat,
    NonNegativeInt,
    PositiveInt,
    StringConstraints,
    model_validator,
)

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

    kind: Literal["loosened", "removed", "skipped", "deleted"] = "loosened"
    """The weakening witnessed between failure and later execution; non-loosened
    kinds establish absence of failure, not proof that the case executed or passed."""

    test_id: NonEmptyText
    """The same pytest case throughout."""
    failed_seq: PositiveInt
    """The execution where it failed."""
    edited_seq: PositiveInt
    """The edit weakening its assertion."""
    passed_seq: PositiveInt
    """The execution where it passed; for non-loosened kinds, a later complete execution
    showing absence of failure, not proof that the case executed or passed."""
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


type FactsSummaryLine = Annotated[
    NonEmptyText, StringConstraints(max_length=240, pattern=r"^[^\r\n]+$")
]
"""One rendered line, at most 240 characters, without line breaks."""


class BehaviorDiffRow(ContractModel):
    """One bounded display sample; rendered text is never executable input."""

    inputs: NonEmptyText
    """Rendered arguments, including argument names where applicable."""
    before: NonEmptyText
    """Rendered type-tagged base value or exception."""
    after: NonEmptyText
    """Rendered type-tagged proposed value or exception."""
    unstable: bool = False
    """Repeated fresh-process executions disagreed; never stable change evidence."""


class NodeBehaviorDiff(ContractModel):
    """Server-observed comparisons for one changed node."""

    node_id: NodeId
    """The stable graph identity, including removed or unsupported nodes."""
    compared_count: NonNegativeInt = 0
    """Inputs compared on both sides with equal typed inputs."""
    differing_count: NonNegativeInt = 0
    """Compared inputs with reproducible differing results; excludes unstable rows."""
    rows: list[BehaviorDiffRow] = Field(default_factory=list[BehaviorDiffRow], max_length=8)
    """At most eight samples of differing or unstable inputs; equal results count only in
    compared_count. Counts cover all admitted comparisons."""
    not_probed: NonEmptyText | None = None
    """Why all or part of probing was unavailable, unsupported or budget-limited."""

    @model_validator(mode="after")
    def _counts_agree(self) -> Self:
        if self.differing_count > self.compared_count:
            raise ValueError("differing inputs cannot exceed compared inputs")
        return self


class MeasuredCaller(NodeBehaviorDiff):
    """Measured caller observations, never a replacement for the static cone."""

    outcome: Literal["changed", "unchanged", "not_compared"] = "not_compared"
    """Changed requires stable differences; unchanged requires complete comparison."""
    unmatched_before: NonNegativeInt = 0
    """Base invocations without an admitted matching proposed invocation."""
    unmatched_after: NonNegativeInt = 0
    """Proposed invocations without an admitted matching base invocation."""
    not_compared: list[NonEmptyText] = Field(default_factory=list)
    """Reasons for unmatched or excluded calls, including unequal inputs."""
    complete: bool = False
    """All observed invocations of this caller were admitted and compared."""
    problems: list[NonEmptyText] = Field(default_factory=list)
    """Recording, matching, instability and retention-limit diagnostics."""

    @model_validator(mode="after")
    def _outcome_agrees(self) -> Self:
        expected = (
            "changed"
            if self.differing_count
            else "unchanged"
            if self.compared_count and self.complete
            else "not_compared"
        )
        if self.outcome != expected:
            raise ValueError("measured outcome must follow comparisons")
        if self.outcome == "not_compared" and not self.not_compared:
            raise ValueError("not compared requires a reason")
        return self


class ReviewCard(ContractModel):
    """One server-authored question about an exact change target."""

    node_id: NodeId
    """The node affected by a choice the task did not ask for."""
    question: NonEmptyText
    """The single question shown for this candidate."""
    evidence_seq: PositiveInt
    """The supporting event's id in this run; the server verifies target and existence."""
    example: BehaviorDiffRow | None = None
    """The smallest before/after example supporting this question, when available."""
    answer: Literal["yes", "no", "unsure"] | None = None
    """The person's answer; null means unanswered."""
    answered_by: Literal["person"] | None = None
    """The existing single-user human principal, never inferred from the model."""

    @model_validator(mode="after")
    def _answer_has_principal(self) -> Self:
        if (self.answer is None) != (self.answered_by is None):
            raise ValueError("answer and answered_by must be present together")
        return self


class RoleUsage(ContractModel):
    """One disjoint role/model/round bucket of attributed model calls."""

    role: Literal["agent", "review", "plan", "lead"] | None = None
    """Existing tick roles: author, reviewer, planner or lead; null means unattributed."""
    model: str | None = None
    """The exact reported model, or unknown."""
    round: NonNegativeInt | None = None
    """Zero for initial work; subsequent confirmed revision requests increment it."""
    cost_usd: FiniteFloat | None = Field(default=None, ge=0)
    """Total US dollars; null if any call cost or required attribution is unknown."""
    unknown_calls: NonNegativeInt | None = None
    """Calls with unknown dollar cost; null when the call inventory is incomplete."""


class ChangeUsage(ContractModel):
    """All work attributed to a change, including failed, stopped and rejected work."""

    cost_usd: FiniteFloat | None = Field(default=None, ge=0)
    """Total US dollars across all buckets; null unless the accounting is complete."""
    unknown_calls: NonNegativeInt | None = None
    """Unknown-cost calls across buckets; null for an incomplete inventory."""
    by_role: list[RoleUsage] | None = None
    """Disjoint role/model/round buckets; null when attribution was not reconstructed."""


class ShapeFact(ContractModel):
    """One neutral structural observation, never an automatic size gate."""

    kind: Literal["duplicate_logic", "import_cycle", "import_added", "function_length"]
    """The analyzer's structural finding kind."""
    path: WorkspacePath
    """The affected workspace file."""
    node_id: NodeId | None = None
    """The affected node, or null for a file-level observation."""
    message: NonEmptyText
    """The neutral before/after observation."""
    evidence: list[EvidenceRef]
    """Captured source or graph evidence supporting the observation."""


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

    summary: list[FactsSummaryLine] | None = Field(default=None, min_length=1, max_length=3)
    """Server-written deterministic summary: task; changed files/nodes and tests;
    observed behavior and cost. At most three lines, at most 720 characters total.
    Unknown measurements remain explicit. Never copied from the author's report."""
    behavior_diff: list[NodeBehaviorDiff] | None = None
    """Per-node comparisons; null means no behavior-probe evidence was recorded."""
    measured_cone: list[MeasuredCaller] | None = None
    """Per-caller measurements; inherited counts count invocations, not cone nodes."""
    review_cards: list[ReviewCard] | None = Field(default=None, max_length=8)
    """The current candidate's questions and supporting evidence, one per unrequested choice."""
    shape: list[ShapeFact] | None = None
    """Structural findings; null means no shape analysis was recorded."""
    shape_complete: bool | None = None
    """Whether the bounded structural checks completed; null means unavailable."""

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
                fact.kind
                != {
                    "loosened": "assertion_loosened",
                    "removed": "test_removed",
                    "skipped": "skip_added",
                    "deleted": "test_removed",
                }[finding.kind]
                or fact.test_id != finding.test_id
                or not any(ref.event_seq == finding.edited_seq for ref in fact.evidence)
            ):
                raise ValueError("temporal finding requires the same test's weakening edit")
        return self
