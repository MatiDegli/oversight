import type { ServerEvent } from "@contracts";
import type { State } from "./types";
import { createChange } from "./types";

export function applyUpdate(
  state: State,
  next: State,
  event: Extract<ServerEvent, { type: "change.updated" }>,
): State {
  const update = event.payload;
  const change = state.changes[update.change_id] ?? createChange(update.change_id, update.agent_id);
  return {
    ...next,
    changes: {
      ...state.changes,
      [update.change_id]: {
        ...change,
        ...(update.author === "user"
          ? { personActed: true, crossReview: null, needsYou: null }
          : {}),
        files: {
          ...change.files,
          [update.file.path]: {
            file: update.file,
            author: update.author,
            node_ids: update.node_ids,
          },
        },
        fileUpdateSeq: { ...change.fileUpdateSeq, [update.file.path]: event.seq },
        graph: update.graph,
        files_changed: update.files_changed,
        lines_added: update.lines_added,
        lines_removed: update.lines_removed,
      },
    },
  };
}

export function applyProposal(
  state: State,
  next: State,
  event: Extract<ServerEvent, { type: "change.proposed" }>,
): State {
  const proposal = event.payload;
  const change =
    state.changes[proposal.change_id] ?? createChange(proposal.change_id, proposal.agent_id);
  return {
    ...next,
    changes: {
      ...state.changes,
      [proposal.change_id]: {
        ...change,
        ...(!change.proposal ||
        proposal.revision !== change.proposal.revision ||
        proposal.head_commit !== change.proposal.head_commit
          ? { crossReview: null, needsYou: null }
          : {}),
        status: "ready_for_review",
        proposal,
        proposalEvent: { seq: event.seq, ts: event.ts },
        resolution: null,
        resolutionEvent: null,
        graph: proposal.graph,
        files_changed: proposal.files.length,
        lines_added: proposal.lines_added,
        lines_removed: proposal.lines_removed,
      },
    },
  };
}

export function applyResolution(
  state: State,
  next: State,
  event: Extract<ServerEvent, { type: "change.resolved" }>,
): State {
  const resolution = event.payload;
  const change =
    state.changes[resolution.change_id] ?? createChange(resolution.change_id, resolution.agent_id);
  const status =
    resolution.merge === "conflict"
      ? "conflict"
      : resolution.decision === "changes_requested"
        ? "revising"
        : resolution.decision;
  const changeReads = { ...state.changeReads };
  if (status === "approved" || status === "rejected")
    Reflect.deleteProperty(changeReads, resolution.change_id);
  return {
    ...next,
    changeReads,
    changes: {
      ...state.changes,
      [resolution.change_id]: {
        ...change,
        resolution,
        resolutionSeq: event.seq,
        resolutionEvent: { seq: event.seq, ts: event.ts },
        crossReviewRound:
          resolution.decided_by === "cross_review" ? resolution : change.crossReviewRound,
        ...((resolution.decided_by ?? "person") === "person"
          ? { personActed: true, crossReview: null, needsYou: null }
          : {}),
        status,
      },
    },
  };
}

export function applyReview(
  state: State,
  next: State,
  event: Extract<ServerEvent, { type: "change.reviewed" }>,
): State {
  const review = event.payload;
  const change = state.changes[review.change_id];
  if (!change) return next;
  const current =
    !change.personActed &&
    change.status === "ready_for_review" &&
    change.proposal?.revision === review.revision &&
    change.proposal.head_commit === review.head_commit &&
    change.proposal.base_commit === review.base_commit;
  return {
    ...next,
    changes: {
      ...state.changes,
      [review.change_id]: {
        ...change,
        reviews: [...change.reviews, { seq: event.seq, review }],
        crossReview: current ? review : change.crossReview,
      },
    },
  };
}
