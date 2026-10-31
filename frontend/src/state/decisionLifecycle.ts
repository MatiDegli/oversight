import { decisionIdentityKey, noRecordedExample } from "./decisionIdentity";
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

/** Answers and delegations are evidence; only a completed final action closes a change. */
export function isClosed(entry: PendingEntry, state: State): boolean {
  const identity = entry.identity;
  if (identity.kind === "lead_plan" || identity.kind === "revert_needs_you") return false;
  return state.decisionEvents.some(
    (event) =>
      event.type === "change.resolved" &&
      event.payload.change_id === identity.change_id &&
      event.payload.revision === identity.revision &&
      (!event.payload.head_commit || event.payload.head_commit === identity.head_commit) &&
      event.payload.merge !== "conflict",
  );
}

/** Plan revision and lead revision are different counters; never substitute one for the other. */
export function recordPlanEntries(entries: Entries, state: State): void {
  type LeadEvent = Extract<State["decisionEvents"][number], { type: "lead.status" }>;
  const leads = new Map<string, LeadEvent>();
  const latest = new Map<string, number>();
  const linked = new Set(
    state.decisionEvents.flatMap((event) =>
      event.type === "lead.status" && event.payload.requested_plan_id
        ? [event.payload.agent_id]
        : [],
    ),
  );
  const key = (agentId: string, revision: number) => JSON.stringify([agentId, revision]);
  const plans = new Map<
    string,
    Extract<State["decisionEvents"][number], { type: "plan.updated" }>
  >();
  const planFor = (lead: LeadEvent) => {
    const { agent_id, revision, requested_plan_id } = lead.payload;
    let plan = requested_plan_id ? plans.get(requested_plan_id) : undefined;
    if (!linked.has(agent_id) && latest.get(agent_id) === revision) {
      const siblings = [...plans.values()].filter(
        (p) => p.payload.started_by === agent_id && p.payload.state === "proposed",
      );
      if (siblings.length === 1) plan = siblings[0];
    }
    return plan?.payload.state === "proposed" && plan.payload.started_by === agent_id
      ? plan
      : undefined;
  };
  for (const event of state.decisionEvents) {
    if (event.type === "lead.status") {
      const { agent_id, revision } = event.payload;
      const id = key(agent_id, revision);
      if (!leads.has(id)) leads.set(id, event);
      latest.set(agent_id, Math.max(latest.get(agent_id) ?? 0, revision));
    }
    if (event.type === "plan.updated") {
      const old = plans.get(event.payload.plan_id);
      if (!old || old.payload.revision <= event.payload.revision)
        plans.set(event.payload.plan_id, event);
    }
    for (const lead of leads.values()) {
      const plan = planFor(lead);
      if (!plan) continue;
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
    const lead = leads.get(key(entry.identity.agent_id, entry.identity.revision));
    if (!lead || planFor(lead)?.payload.plan_id !== entry.planId) entries.delete(id);
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

export function isCurrent(entry: PendingEntry, state: State): boolean {
  if (entry.identity.kind === "lead_plan" || entry.identity.kind === "revert_needs_you")
    return true;
  const current = currentCandidate(state, entry.identity.change_id);
  return current !== null && sameCandidate(current, entry.identity);
}
