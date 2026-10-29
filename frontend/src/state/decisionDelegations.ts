import { matchesDecision } from "./decisionIdentity";
import type { DelegationAttempt } from "./decisionIdentity";
import type { PendingEntry } from "./decisionCandidates";
import type { State } from "./types";

interface Delegation {
  agentId: string;
  slot: DelegationAttempt["slot"];
  echoSeq: number;
  end: DelegationAttempt | null;
  succeeded: boolean;
}

/** One agent id identifies one task; repeated statuses retain its first echo and end. */
export function decisionDelegations(
  entry: PendingEntry,
  state: State,
): { closed: boolean; attempts: DelegationAttempt[] } {
  const delegations = new Map<string, Delegation>();
  for (const event of state.decisionEvents) {
    if (event.seq < entry.firstSeq) continue;
    if (event.type === "change.proposed") {
      const delegation = delegations.get(event.payload.agent_id);
      if (delegation) delegation.succeeded = true;
    }
    if (event.type !== "agent.status") continue;
    const status = event.payload;
    let delegation = delegations.get(status.agent_id);
    if (!delegation && status.answering && matchesDecision(status.answering, entry.identity)) {
      delegation = {
        agentId: status.agent_id,
        slot: status.slot,
        echoSeq: event.seq,
        end: null,
        succeeded: false,
      };
      delegations.set(status.agent_id, delegation);
    }
    if (!delegation) continue;
    if (status.state === "done") delegation.succeeded = true;
    if (delegation.end || event.seq <= delegation.echoSeq) continue;
    if (status.state === "halted" || status.state === "stopped" || status.state === "done")
      delegation.end = {
        agentId: delegation.agentId,
        slot: delegation.slot,
        endState: status.state,
        reason: status.detail,
        echoSeq: delegation.echoSeq,
        endSeq: event.seq,
      };
  }
  const tasks = [...delegations.values()];
  return {
    closed: tasks.some((task) => task.succeeded || !task.end),
    attempts: tasks.flatMap((task) => (task.end ? [task.end] : [])),
  };
}
