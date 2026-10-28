import type { ServerEvent } from "@contracts";
import type { State } from "./types";
import { matchesNeedsYou, parseNeedsYou } from "./needsYou";

type Event = Extract<
  ServerEvent,
  {
    type:
      | "tests.executed"
      | "change.facts_recorded"
      | "change.assessed"
      | "merge_mode.changed"
      | "plan.updated"
      | "revert.resolved"
      | "queue.updated"
      | "lead.status"
      | "error";
  }
>;

export function applyEvidenceEvent(state: State, next: State, event: Event): State {
  switch (event.type) {
    case "tests.executed":
      return { ...next, testExecutions: { ...state.testExecutions, [event.seq]: event.payload } };
    case "change.facts_recorded":
      return {
        ...next,
        changeFacts: {
          ...state.changeFacts,
          [event.payload.change_id]: [
            ...(state.changeFacts[event.payload.change_id] ?? []),
            event.payload,
          ],
        },
      };
    case "change.assessed":
    case "merge_mode.changed":
    case "plan.updated":
    case "revert.resolved":
    case "queue.updated":
    case "lead.status":
      return next;
    case "error": {
      const report = event.payload;
      const note = parseNeedsYou(report.message);
      const change = report.change_id ? state.changes[report.change_id] : undefined;
      const duplicate =
        note &&
        state.errors.some((error) => {
          const previous = parseNeedsYou(error.message);
          return (
            error.change_id === report.change_id &&
            previous?.revision === note.revision &&
            previous.head === note.head &&
            previous.code === note.code
          );
        });
      return {
        ...next,
        errors: duplicate ? state.errors : [...state.errors, report],
        errorEvents: duplicate
          ? state.errorEvents
          : [...state.errorEvents, { seq: event.seq, ts: event.ts }],
        changes:
          change &&
          !change.personActed &&
          change.status === "ready_for_review" &&
          matchesNeedsYou(note, change.proposal)
            ? { ...state.changes, [change.change_id]: { ...change, needsYou: note } }
            : state.changes,
      };
    }
  }
}
