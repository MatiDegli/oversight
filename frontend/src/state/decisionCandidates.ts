import type {
  ChangeFacts,
  ChangeProposed,
  DecisionRef,
  ReviewCard,
  RuleAssessment,
} from "@contracts";

import { decisionIdentityKey, noRecordedExample } from "./decisionIdentity";
import type { DecisionInboxDecision, InboxExample, InboxIdentity } from "./decisionIdentity";
import type { DecisionEvent } from "./decisionProjection";
import { parseNeedsYou } from "./needsYou";
import type { State } from "./types";

type ChangeRef = Exclude<DecisionRef, { kind: "lead_plan" }>;
export type Candidate = Pick<ChangeRef, "change_id" | "revision" | "head_commit">;
export type PendingEntry = Omit<DecisionInboxDecision, "age" | "attempts">;
export type Entries = Map<string, PendingEntry>;
const checkLabels: Record<RuleAssessment["rule"], string> = {
  combined_tests: "combined_tests",
  small_cone: "small_cone",
  small_change: "small_change",
  known_spend: "known_spend",
  existing_tests_untouched: "existing_tests_untouched",
  cross_review: "cross_review",
  test_integrity: "test_integrity",
  added_tests_fail_before: "added_tests_fail_before",
  run_test_integrity: "run_test_integrity",
  tests_reach_changes: "tests_reach_changes",
  within_zone: "within_zone",
  protected_paths: "protected_paths",
};

export function recordEntry(
  entries: Entries,
  state: State,
  event: DecisionEvent,
  identity: InboxIdentity,
  reason: string,
  example: InboxExample = noRecordedExample,
  agentId: string | null = null,
  planId: string | null = null,
): void {
  const id = decisionIdentityKey(identity);
  const previous = entries.get(id);
  const changeId = "change_id" in identity ? identity.change_id : null;
  agentId ??= changeId ? (state.changes[changeId]?.agent_id ?? null) : null;
  entries.set(id, {
    id,
    kind: identity.kind,
    identity: previous?.identity ?? identity,
    changeId,
    agentId,
    slot: agentId ? (state.agents[agentId]?.slot ?? null) : null,
    planId,
    reason,
    example,
    firstSeq: previous?.firstSeq ?? event.seq,
    ts: previous?.ts ?? event.ts,
  });
}

export function candidateRef(
  kind: Exclude<ChangeRef["kind"], "review_card">,
  target: Candidate,
  seq: number,
): ChangeRef {
  return {
    kind,
    change_id: target.change_id,
    revision: target.revision,
    head_commit: target.head_commit,
    evidence_seq: seq,
  };
}

function recordCard(
  entries: Entries,
  state: State,
  event: DecisionEvent,
  f: ChangeFacts,
  card: ReviewCard,
) {
  recordEntry(
    entries,
    state,
    event,
    {
      kind: "review_card",
      change_id: f.change_id,
      revision: f.revision,
      head_commit: f.head_commit,
      node_id: card.node_id,
      question: card.question,
      evidence_seq: card.evidence_seq,
    },
    card.question,
    card.example ? { source: "review_card", value: card.example } : noRecordedExample,
  );
}

export function recordExamples(
  entries: Entries,
  state: State,
  event: DecisionEvent,
  target: Candidate,
  examples: ChangeProposed["examples"],
): void {
  const unconfirmed = examples?.filter((example) => !example.pin) ?? [];
  if (!unconfirmed.length) return;
  // Pick the smallest recorded rendering, with an event-order tie-break.
  const size = (example: (typeof unconfirmed)[number]) =>
    example.inputs.length +
    (example.expected_output?.length ?? 0) +
    (example.expected_behavior?.length ?? 0);
  const example = unconfirmed.reduce((smallest, item) =>
    size(item) < size(smallest) ? item : smallest,
  );
  recordEntry(
    entries,
    state,
    event,
    candidateRef("examples_before_code", target, event.seq),
    "Proposed examples await confirmation before code",
    { source: "task", value: example },
  );
}

export function recordChangeEntries(entries: Entries, state: State, event: DecisionEvent): void {
  if (event.type === "change.facts_recorded") {
    for (const card of event.payload.review_cards ?? [])
      recordCard(entries, state, event, event.payload, card);
  }
  if (event.type === "change.assessed") {
    const risk = event.payload.rules.find(
      (rule) => rule.rule === "protected_paths" && rule.result === "fail",
    );
    if (risk)
      recordEntry(
        entries,
        state,
        event,
        candidateRef("risky_area", event.payload, event.seq),
        `Protected area changed: ${risk.evidence.flatMap((ref) => (ref.path ? [ref.path] : [])).join(", ") || "recorded protected paths"}`,
        noRecordedExample,
        event.payload.agent_id,
      );
  }
  if (event.type === "change.proposed") {
    const { change_id, revision, head_commit } = event.payload;
    recordEntry(
      entries,
      state,
      event,
      { kind: "change", change_id, revision, head_commit, evidence_seq: event.seq },
      "Change awaits Approve, Request changes or Reject",
      noRecordedExample,
      event.payload.agent_id,
    );
    recordExamples(entries, state, event, event.payload, event.payload.examples);
  }
  if (
    event.type === "change.resolved" &&
    event.payload.merge === "conflict" &&
    event.payload.head_commit
  ) {
    const resolution = event.payload;
    recordEntry(
      entries,
      state,
      event,
      candidateRef(
        "follow_main",
        { ...resolution, head_commit: event.payload.head_commit },
        event.seq,
      ),
      resolution.conflict?.kind === "tests"
        ? "Combined tests failed while following main"
        : `Merge conflict while following main: ${resolution.conflict?.paths.join(", ") ?? ""}`,
      noRecordedExample,
      resolution.agent_id,
    );
  }
  if (event.type === "error" && event.payload.change_id) {
    const note = parseNeedsYou(event.payload.message);
    if (!note) return;
    const kind = ["main_moved", "main_suspect", "git_failed", "suite_failed"].includes(note.code)
      ? "follow_main"
      : "checks_unavailable";
    recordEntry(
      entries,
      state,
      event,
      candidateRef(
        kind,
        {
          change_id: event.payload.change_id,
          revision: note.revision,
          head_commit: note.head,
        },
        event.seq,
      ),
      note.reason,
      noRecordedExample,
      event.payload.agent_id,
    );
  }
}

/** Add recorded check diagnostics only after a terminal, candidate-scoped Needs you signal. */
export function checkReason(entry: PendingEntry, state: State): string {
  if (entry.kind !== "checks_unavailable") return entry.reason;
  const diagnostics = state.decisionEvents.flatMap((event) => {
    const p = event.payload;
    if (
      !("head_commit" in p) ||
      !("change_id" in p) ||
      !("revision" in p) ||
      !("head_commit" in entry.identity) ||
      p.change_id !== entry.identity.change_id ||
      p.revision !== entry.identity.revision ||
      p.head_commit !== entry.identity.head_commit
    )
      return [];
    if (event.type === "change.facts_recorded") return event.payload.problems;
    if (event.type === "change.assessed")
      return event.payload.rules
        .filter((rule) => rule.result === "unknown")
        .map((rule) => `Unavailable check: ${checkLabels[rule.rule]}`);
    return [];
  });
  return [...new Set([entry.reason, ...diagnostics])].join("; ");
}
