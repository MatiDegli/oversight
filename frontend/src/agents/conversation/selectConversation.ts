import type { AgentStatus, AgentStep, ChangeResolved, CrossReview } from "@contracts";
import type { State } from "../../state";

export interface ConversationMessage {
  seq: number;
  ts: string;
  text: string | null;
}

export type ConversationEntry = { seq: number } & (
  | { kind: "task"; ts: string; text: string }
  | { kind: "step"; ts: string | undefined; step: AgentStep }
  | { kind: "proposed"; ts: string; summary: string | null }
  | {
      kind: "cross-review";
      review: CrossReview;
      label: string;
      findingCount: number;
      blockingCount: number;
      complete: boolean;
    }
  | { kind: "resolution"; ts: string; resolution: ChangeResolved; round: number }
  | ({ kind: "message" } & ConversationMessage)
);

export interface ConversationHeader {
  agentId: string;
  changeId: string;
  task: string;
  slot: AgentStatus["slot"];
  state: AgentStatus["state"];
  modelLabel: string;
  reasoning: string | null;
  step: number;
  maxSteps: number;
  cost: number | null | undefined;
  round: number;
  zonePaths: string[];
}

export interface Conversation {
  header: ConversationHeader;
  entries: ConversationEntry[];
}

/** Project retained evidence only; messages are supplied independently of the event contract. */
export function selectConversation(
  state: State,
  agentId: string,
  messages: readonly ConversationMessage[],
): Conversation | undefined {
  const agent = state.agents[agentId];
  if (!agent) return undefined;
  const entries: ConversationEntry[] = [];
  let round = 1;
  let taskSeen = false;
  for (const event of [...state.decisionEvents].sort((a, b) => a.seq - b.seq)) {
    const { seq, ts } = event;
    if (event.type === "agent.status" && event.payload.agent_id === agentId && !taskSeen) {
      entries.push({ kind: "task", seq, ts, text: event.payload.task });
      taskSeen = true;
    } else if (
      event.type === "change.proposed" &&
      event.payload.change_id === agent.change_id &&
      event.payload.agent_id === agentId
    ) {
      entries.push({ kind: "proposed", seq, ts, summary: event.payload.summary });
    } else if (
      event.type === "change.resolved" &&
      event.payload.change_id === agent.change_id &&
      event.payload.agent_id === agentId
    ) {
      if (event.payload.decision === "changes_requested") round++;
      entries.push({ kind: "resolution", seq, ts, resolution: event.payload, round });
    }
  }
  for (const step of state.steps[agentId] ?? []) {
    const seq = state.stepSeqs[agentId]?.[step.step];
    if (seq !== undefined)
      entries.push({ kind: "step", seq, ts: state.stepTimes[agentId]?.[step.step], step });
  }
  for (const { seq, review } of state.changes[agent.change_id]?.reviews ?? []) {
    const profile = state.run?.models.find(
      (model) => model.id === review.reviewer_model || model.model === review.reviewer_model,
    );
    entries.push({
      kind: "cross-review",
      seq,
      review,
      label: profile?.label ?? review.reviewer_model,
      findingCount: review.findings.length,
      blockingCount: review.findings.filter((finding) => finding.severity === "BLOCKING").length,
      complete: review.complete,
    });
  }
  for (const message of messages) entries.push({ ...message, kind: "message" });
  const profile = state.run?.models.find((model) => model.id === agent.model);
  return {
    header: {
      agentId,
      changeId: agent.change_id,
      task: agent.task,
      slot: agent.slot,
      state: agent.state,
      modelLabel: profile?.label ?? agent.model,
      reasoning: profile?.reasoning ?? null,
      step: agent.step,
      maxSteps: agent.max_steps,
      cost: state.agentCosts[agentId],
      round,
      zonePaths: agent.zone?.paths ?? [],
    },
    entries: entries.sort((a, b) => a.seq - b.seq),
  };
}
