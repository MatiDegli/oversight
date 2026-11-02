import type { LeadStatusEvent } from "@contracts";
import type { State } from "../../state";
import { selectPlanEvidence } from "../../review";
import { teamIdentities } from "../chatAgentSelectors";
import type { LeadConversation, LeadEntry } from "./selectConversation";

/** Status revisions belong to the lead; envelope order places evidence within each turn. */
export function selectLeadConversation(state: State, leadId: string): LeadConversation | undefined {
  const statuses = state.decisionEvents.filter(
    (event): event is LeadStatusEvent =>
      event.type === "lead.status" && event.payload.agent_id === leadId,
  );
  const entries: LeadEntry[] = [];
  const attached = new Set<string>();
  const { plans } = selectPlanEvidence(state);
  let current: (typeof statuses)[number] | undefined;
  const messages = state.messages[leadId] ?? [];
  const replies = state.leadReplies[leadId] ?? [];
  for (const event of statuses) {
    if (event.payload.revision <= (current?.payload.revision ?? 0)) continue;
    if (!current)
      entries.push({ kind: "task", seq: event.seq, ts: event.ts, text: event.payload.task });
    current = event;
    entries.push({ kind: "lead-status", seq: event.seq, ts: event.ts, status: event.payload });
    const planId = event.payload.requested_plan_id;
    if (planId && !attached.has(planId) && plans.get(planId)?.payload.started_by === leadId) {
      attached.add(planId);
      const message = messages.findLast((item) => item.seq < event.seq);
      const nextMessage = messages.find((item) => item.seq > event.seq);
      const reply = replies.find(
        (item) => item.seq > (message?.seq ?? 0) && item.seq < (nextMessage?.seq ?? Infinity),
      );
      entries.push({
        kind: "lead-plan",
        seq: event.seq,
        planId,
        answersSeq: reply ? reply.payload.answers_seq : (message?.seq ?? null),
      });
    }
  }
  const identity = teamIdentities(state).get(leadId);
  if (!current || !identity) return undefined;
  entries.push(...messages.map((message) => ({ ...message, kind: "message" as const })));
  entries.push(
    ...replies.map((event) => ({
      kind: "lead-reply" as const,
      seq: event.seq,
      event,
    })),
  );
  return {
    kind: "lead",
    header: {
      ...current.payload,
      hue: identity.hue,
      letter: identity.letter,
      kind: "lead",
      agentId: leadId,
      modelLabel:
        state.run?.models.find((model) => model.id === current.payload.model)?.label ??
        current.payload.model,
    },
    entries: entries.sort((a, b) => a.seq - b.seq),
  };
}
