import type { ChangeFacts, ChangeProposed } from "@contracts";
import type { State } from "../state";

export function factsForRevision(state: State, proposal: ChangeProposed): ChangeFacts | undefined {
  return state.changeFacts[proposal.change_id]?.findLast(
    (facts) =>
      facts.change_id === proposal.change_id &&
      facts.revision === proposal.revision &&
      facts.head_commit === proposal.head_commit,
  );
}
