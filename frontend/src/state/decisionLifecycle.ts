import type { DecisionRef, ReviewCard, TaskExample } from "@contracts";

import { decisionIdentityKey, matchesDecision, noRecordedExample } from "./decisionIdentity";
import type { InboxIdentity } from "./decisionIdentity";
import { recordEntry, recordExamples } from "./decisionCandidates";
import type { Candidate, Entries, PendingEntry } from "./decisionCandidates";
import type { State } from "./types";

export function sameCandidate(target: Candidate, identity: InboxIdentity): boolean {
  return (
    "head_commit" in identity &&
    target.change_id === identity.change_id &&
    target.revision === identity.revision &&
    target.head_commit === identity.head_commit
  );
}

/** Proposals are authoritative; otherwise retain the latest recorded candidate evidence. */
export function currentCandidate(state: State, changeId: string): Candidate | null {
  const proposal = state.changes[changeId]?.proposal;
  if (proposal) return proposal;
  const fact = state.changeFacts[changeId]?.at(-1);
  if (fact) return fact;
  const assessment = state.decisionEvents.findLast(
    (event) => event.type === "change.assessed" && event.payload.change_id === changeId,
  );
  if (assessment?.type === "change.assessed") return assessment.payload;
  const agent = Object.values(state.agents).find((agent) => agent.change_id === changeId);
  return agent?.base_commit
    ? { change_id: changeId, revision: 0, head_commit: agent.base_commit }
    : null;
}

function matchesCard(card: ReviewCard, identity: DecisionRef): boolean {
  return (
    identity.kind === "review_card" &&
    card.node_id === identity.node_id &&
    card.question === identity.question &&
    card.evidence_seq === identity.evidence_seq
  );
}

function answered(card: ReviewCard, identity: DecisionRef): boolean {
  return (
    matchesCard(card, identity) &&
    card.answered_by === "person" &&
    (card.answer === "yes" || card.answer === "no")
  );
}

export function isClosed(entry: PendingEntry, state: State): boolean {
  const identity = entry.identity;
  return state.decisionEvents.some((event) => {
    if (event.seq < entry.firstSeq) return false;
    if (event.type === "agent.status" && event.payload.answering) {
      return matchesDecision(event.payload.answering, identity);
    }
    if (identity.kind === "lead_plan" || identity.kind === "revert_needs_you") return false;
    if (event.type === "change.facts_recorded" && sameCandidate(event.payload, identity)) {
      return event.payload.review_cards?.some((card) => answered(card, identity)) ?? false;
    }
    if (
      event.type !== "change.resolved" ||
      !event.payload.head_commit ||
      !sameCandidate({ ...event.payload, head_commit: event.payload.head_commit }, identity)
    )
      return false;
    const resolution = event.payload;
    if (resolution.answers?.some((card) => answered(card, identity))) return true;
    if (resolution.answers?.some((card) => matchesCard(card, identity) && card.answer === "unsure"))
      return false;
    if (resolution.merge === "conflict") return false;
    return true;
  });
}

/** Plan revision and lead revision are different counters; never substitute one for the other. */
export function recordPlanEntries(entries: Entries, state: State): void {
  const leads = new Map<
    string,
    Extract<State["decisionEvents"][number], { type: "lead.status" }>
  >();
  const plans = new Map<
    string,
    Extract<State["decisionEvents"][number], { type: "plan.updated" }>
  >();
  for (const event of state.decisionEvents) {
    if (event.type === "lead.status") {
      const old = leads.get(event.payload.agent_id);
      if (!old || old.payload.revision < event.payload.revision)
        leads.set(event.payload.agent_id, event);
    }
    if (event.type === "plan.updated") {
      const old = plans.get(event.payload.plan_id);
      if (!old || old.payload.revision <= event.payload.revision)
        plans.set(event.payload.plan_id, event);
    }
    for (const plan of plans.values()) {
      const leadId = plan.payload.started_by;
      const lead = leadId ? leads.get(leadId) : null;
      const siblings = [...plans.values()].filter(
        (p) => p.payload.started_by === leadId && p.payload.state === "proposed",
      );
      if (!lead || plan.payload.state !== "proposed" || siblings.length !== 1) continue;
      recordEntry(
        entries,
        state,
        event,
        {
          kind: "lead_plan",
          agent_id: lead.payload.agent_id,
          revision: lead.payload.revision,
          evidence_seq: event.seq,
        },
        plan.payload.detail ?? "Lead plan awaits approval",
        noRecordedExample,
        lead.payload.agent_id,
        plan.payload.plan_id,
      );
    }
  }
  for (const [id, entry] of entries) {
    if (entry.identity.kind !== "lead_plan") continue;
    const plan = entry.planId ? plans.get(entry.planId) : null;
    const lead = leads.get(entry.identity.agent_id);
    const siblings = [...plans.values()].filter(
      (p) => p.payload.started_by === entry.agentId && p.payload.state === "proposed",
    );
    if (
      plan?.payload.state !== "proposed" ||
      lead?.payload.revision !== entry.identity.revision ||
      siblings.length !== 1
    )
      entries.delete(id);
  }
}

export function recordOtherEntries(entries: Entries, state: State): void {
  for (const event of state.decisionEvents) {
    if (event.type === "agent.status" && event.payload.base_commit) {
      const agent = event.payload;
      if (agent.state === "ready_for_review" || agent.state === "halted")
        recordExamples(
          entries,
          state,
          event,
          { change_id: agent.change_id, revision: 0, head_commit: event.payload.base_commit },
          agent.examples,
        );
    }
    if (event.type === "revert.resolved") {
      const p = event.payload;
      const identity = {
        kind: "revert_needs_you" as const,
        change_id: p.change_id,
        revision: p.revision,
        resolution_seq: p.resolution_seq,
        merge_commit: p.merge_commit,
      };
      if (p.result === "needs_you")
        recordEntry(entries, state, event, identity, p.reason ?? "Revert needs intervention");
      else entries.delete(decisionIdentityKey(identity));
    }
  }
}

function examplesConfirmed(entry: PendingEntry, state: State): boolean {
  let examples: TaskExample[] | null = null;
  for (const event of state.decisionEvents) {
    if (event.type === "change.proposed" && sameCandidate(event.payload, entry.identity)) {
      examples = event.payload.examples ?? examples;
    }
    if (
      event.type === "agent.status" &&
      event.payload.base_commit &&
      sameCandidate(
        {
          change_id: event.payload.change_id,
          revision: 0,
          head_commit: event.payload.base_commit,
        },
        entry.identity,
      )
    )
      examples = event.payload.examples ?? examples;
  }
  return examples !== null && examples.every((example) => example.pin);
}

export function isCurrent(entry: PendingEntry, state: State): boolean {
  if (entry.identity.kind === "lead_plan" || entry.identity.kind === "revert_needs_you")
    return true;
  if (entry.kind === "examples_before_code" && examplesConfirmed(entry, state)) return false;
  const current = currentCandidate(state, entry.identity.change_id);
  return current !== null && sameCandidate(current, entry.identity);
}
