import { ageWords } from "../age";
import { checkReason, recordChangeEntries } from "./decisionCandidates";
import type { Entries } from "./decisionCandidates";
import { decisionDelegations } from "./decisionDelegations";
import type { DecisionInboxEntry } from "./decisionIdentity";
import { isClosed, isCurrent, recordOtherEntries, recordPlanEntries } from "./decisionLifecycle";
import type { State } from "./types";

/** Pure projection: one pending candidate per change, standalone decisions keep their identity. */
export function selectDecisionInbox(state: State): DecisionInboxEntry[] {
  const entries: Entries = new Map();
  for (const event of state.decisionEvents) recordChangeEntries(entries, state, event);
  recordOtherEntries(entries, state);
  recordPlanEntries(entries, state);
  const groups = new Map<string, DecisionInboxEntry>();
  const ordered = [...entries.values()].sort(
    (a, b) => a.firstSeq - b.firstSeq || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
  );
  for (const entry of ordered) {
    if (!isCurrent(entry, state) || isClosed(entry, state)) continue;
    const id = entry.changeId ? JSON.stringify(["change", entry.changeId]) : entry.id;
    const group = groups.get(id) ?? {
      id,
      changeId: entry.changeId,
      firstSeq: entry.firstSeq,
      ts: entry.ts,
      age: ageWords(entry.ts, state.clock),
      decisions: [],
      attempts: [],
    };
    const attempts = decisionDelegations(entry, state);
    group.decisions.push({
      ...entry,
      reason: checkReason(entry, state),
      age: ageWords(entry.ts, state.clock),
      attempts,
    });
    group.attempts = [
      ...new Map(
        [...group.attempts, ...attempts].map((attempt) => [
          JSON.stringify([attempt.agentId, attempt.echoSeq]),
          attempt,
        ]),
      ).values(),
    ].sort((a, b) => a.echoSeq - b.echoSeq);
    groups.set(id, group);
  }
  return [...groups.values()];
}

/** Questions, checks and delegations within a change share one pending final decision. */
export const selectDecisionInboxCount = (state: State): number => selectDecisionInbox(state).length;
