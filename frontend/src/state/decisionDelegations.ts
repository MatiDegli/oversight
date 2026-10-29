import { matchesDecision } from "./decisionIdentity";
import type { DelegationAttempt } from "./decisionIdentity";
import type { PendingEntry } from "./decisionCandidates";
import type { State } from "./types";

/** Every matching task is history, including active tasks; none consumes the decision. */
export function decisionDelegations(entry: PendingEntry, state: State): DelegationAttempt[] {
  const attempts = new Map<string, DelegationAttempt>();
  for (const event of state.decisionEvents) {
    if (event.seq < entry.firstSeq || event.type !== "agent.status") continue;
    const status = event.payload;
    let attempt = attempts.get(status.agent_id);
    if (!attempt && status.answering && matchesDecision(status.answering, entry.identity)) {
      attempt = {
        agentId: status.agent_id,
        slot: status.slot,
        echoSeq: event.seq,
        endState: null,
        reason: null,
        endSeq: null,
      };
      attempts.set(status.agent_id, attempt);
    }
    if (!attempt || attempt.endSeq !== null || event.seq <= attempt.echoSeq) continue;
    if (status.state === "halted" || status.state === "stopped" || status.state === "done") {
      attempt.endState = status.state;
      attempt.reason = status.detail;
      attempt.endSeq = event.seq;
    }
  }
  return [...attempts.values()];
}
