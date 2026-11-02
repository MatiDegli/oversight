import type { AgentStatus, ChangeResolved, CrossReview } from "@contracts";
import { selectRounds, type Round, type RoundStep } from "./rounds";
import type { State, BudgetMark, ModelWait } from "../../state";

export interface ConversationMessage {
  seq: number;
  ts: string;
  text: string | null;
}

export type ConversationEntry = { seq: number } & (
  | { kind: "task"; ts: string; text: string }
  | RoundStep
  | { kind: "activity" | "round"; round: Round; children: ConversationEntry[] }
  | { kind: "budget"; mark: BudgetMark }
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
  slot: AgentStatus["slot"];
  state: AgentStatus["state"];
  modelLabel: string;
  reasoning: string | null;
  step: number;
  maxSteps: number;
  waiting: boolean;
  cost: number | null | undefined;
  round: number;
  zonePaths: string[];
}

export interface Conversation {
  header: ConversationHeader;
  entries: ConversationEntry[];
  progress: {
    wait: ModelWait | undefined;
    openRound: Round | undefined;
    limits: number;
    budget: NonNullable<State["run"]>["budget"] | undefined;
    spendUnknown: boolean;
  };
}

function groupRounds(
  entries: ConversationEntry[],
  rounds: Round[],
  messages: readonly ConversationMessage[],
) {
  for (const [index, item] of rounds.entries()) {
    // Messages split a round into stable segments; accepted person text never folds away.
    const boundaries = messages
      .filter((m) => m.seq >= item.seq && m.seq < (rounds[index + 1]?.seq ?? Infinity))
      .map((m) => m.seq)
      .sort((a, b) => a - b);
    const folded =
      item.end && rounds[index + 1]
        ? entries.filter(
            (entry) =>
              (entry.kind === "proposed" || entry.kind === "cross-review") &&
              entry.seq >= item.seq &&
              entry.seq < (rounds[index + 1]?.seq ?? Infinity),
          )
        : [];
    for (const entry of folded) entries.splice(entries.indexOf(entry), 1);
    const groups = new Map<number, ConversationEntry[]>();
    for (const entry of [...item.steps, ...folded].sort((a, b) => a.seq - b.seq)) {
      const segment = boundaries.filter((seq) => seq < entry.seq).length;
      const group = groups.get(segment) ?? [];
      group.push(entry);
      groups.set(segment, group);
    }
    for (const children of groups.values()) {
      const steps = children.filter((entry): entry is RoundStep => entry.kind === "step");
      if (!steps[0]) {
        entries.push(...children);
        continue;
      }
      entries.push({
        kind: item.end ? "round" : "activity",
        seq: steps[0].seq,
        round: { ...item, steps, toolMs: steps.reduce((sum, e) => sum + e.step.duration_ms, 0) },
        children,
      });
    }
  }
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
  const rounds = selectRounds(state, agentId);
  const firstStatus = entries.find((entry) => entry.kind === "task")?.seq ?? Infinity;
  for (const mark of state.budgetMarks) {
    if (mark.seq > firstStatus) entries.push({ kind: "budget", seq: mark.seq, mark });
  }
  groupRounds(entries, rounds, messages);
  const profile = state.run?.models.find((model) => model.id === agent.model);
  return {
    header: {
      agentId,
      changeId: agent.change_id,
      slot: agent.slot,
      state: agent.state,
      modelLabel: profile?.label ?? agent.model,
      reasoning: profile?.reasoning ?? null,
      step: Math.max(agent.step, ...(state.steps[agentId] ?? []).map((step) => step.step)),
      maxSteps: state.run?.limits.max_steps ?? agent.max_steps,
      waiting: agent.state === "working" && !!state.modelWaits[agentId],
      cost: state.agentCosts[agentId],
      round,
      zonePaths: agent.zone?.paths ?? [],
    },
    progress: {
      wait: state.modelWaits[agentId],
      openRound: rounds.find((r) => !r.end),
      limits: state.run?.limits.max_wall_clock_s ?? 0,
      budget: state.run?.budget,
      spendUnknown: state.spendUnknown,
    },
    entries: entries.sort((a, b) => a.seq - b.seq),
  };
}
