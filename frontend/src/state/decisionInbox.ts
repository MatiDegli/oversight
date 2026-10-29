import { ageWords } from "../age";
import { checkReason, recordChangeEntries } from "./decisionCandidates";
import type { Entries } from "./decisionCandidates";
import { decisionDelegations } from "./decisionDelegations";
import type { DecisionInboxEntry } from "./decisionIdentity";
import { isClosed, isCurrent, recordOtherEntries, recordPlanEntries } from "./decisionLifecycle";
import type { State } from "./types";

/** Pending decisions only. Events never open a panel, change selection or call a command. */
export function selectDecisionInbox(state: State): DecisionInboxEntry[] {
  const entries: Entries = new Map();
  for (const event of state.decisionEvents) recordChangeEntries(entries, state, event);
  recordOtherEntries(entries, state);
  recordPlanEntries(entries, state);
  return [...entries.values()]
    .flatMap((entry) => {
      if (!isCurrent(entry, state) || isClosed(entry, state)) return [];
      const delegation = decisionDelegations(entry, state);
      if (delegation.closed) return [];
      return [
        {
          ...entry,
          reason: checkReason(entry, state),
          age: ageWords(entry.ts, state.clock),
          attempts: delegation.attempts,
        },
      ];
    })
    .sort((a, b) => a.firstSeq - b.firstSeq || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

/** A candidate with three questions and unavailable checks contributes four decisions. */
export const selectDecisionInboxCount = (state: State): number => selectDecisionInbox(state).length;
