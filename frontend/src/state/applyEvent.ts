import type { ServerEvent } from "@contracts";

import { matchesNeedsYou, parseNeedsYou } from "./needsYou";
import { createChange, createInitialState } from "./types";
import type { State } from "./types";

function patchItems<T extends { id: string }>(
  current: T[],
  added: T[],
  changed: T[],
  removed: string[],
): T[] {
  const removedIds = new Set(removed);
  const items = Object.fromEntries(
    current.filter((item) => !removedIds.has(item.id)).map((item) => [item.id, item]),
  );
  for (const item of [...added, ...changed]) items[item.id] = item;
  return Object.values(items);
}

/** Apply validated events, identically for live streams and parsed replay logs. */
export function applyEvent(state: State, event: ServerEvent): State {
  // Only run.started identifies a run on the wire. A new run restarts its sequence at 1.
  const newRun = event.type === "run.started" && event.payload.run_id !== state.run?.run_id;
  if (event.seq !== (newRun ? 1 : state.lastSeq + 1)) return { ...state, gap: true };
  if (!state.run && event.type !== "run.started") return { ...state, gap: true };

  const next = { ...state, lastSeq: event.seq, gap: false, clock: event.ts };
  switch (event.type) {
    case "run.started":
      return { ...createInitialState(), run: event.payload, lastSeq: event.seq, clock: event.ts };
    case "graph.snapshot":
      return { ...next, graph: event.payload };
    case "graph.delta": {
      const delta = event.payload;
      if (!state.graph || state.graph.version !== delta.from_version) {
        return { ...state, gap: true };
      }
      const nodes = patchItems(
        state.graph.nodes,
        delta.nodes_added,
        delta.nodes_changed,
        delta.nodes_removed,
      );
      const nodeIds = new Set(nodes.map((node) => node.id));
      return {
        ...next,
        graph: {
          version: delta.to_version,
          commit: delta.commit,
          nodes,
          edges: patchItems(
            state.graph.edges,
            delta.edges_added,
            delta.edges_changed,
            delta.edges_removed,
          ).filter((edge) => nodeIds.has(edge.src) && nodeIds.has(edge.dst)),
          parse_errors: delta.parse_errors,
          positions: state.graph.positions,
        },
      };
    }
    case "agent.status": {
      const status = event.payload;
      return {
        ...next,
        agents: { ...state.agents, [status.agent_id]: status },
        changes: {
          ...state.changes,
          [status.change_id]:
            state.changes[status.change_id] ?? createChange(status.change_id, status.agent_id),
        },
      };
    }
    case "agent.step": {
      const step = event.payload;
      return {
        ...next,
        steps: {
          ...state.steps,
          [step.agent_id]: [...(state.steps[step.agent_id] ?? []), step],
        },
        stepSeqs: {
          ...state.stepSeqs,
          [step.agent_id]: { ...state.stepSeqs[step.agent_id], [step.step]: event.seq },
        },
        stepTimes: {
          ...state.stepTimes,
          [step.agent_id]: { ...state.stepTimes[step.agent_id], [step.step]: event.ts },
        },
      };
    }
    case "agent.focus": {
      const focus = event.payload;
      const lastReads = { ...state.lastReads };
      if (focus.mode === "read") {
        const id = focus.node_ids[0];
        if (id) lastReads[focus.agent_id] = id;
        else Reflect.deleteProperty(lastReads, focus.agent_id);
      }
      return { ...next, focus: { ...state.focus, [focus.agent_id]: focus }, lastReads };
    }
    case "change.updated": {
      const update = event.payload;
      const change =
        state.changes[update.change_id] ?? createChange(update.change_id, update.agent_id);
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
    case "change.proposed": {
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
    case "change.resolved": {
      const resolution = event.payload;
      const change =
        state.changes[resolution.change_id] ??
        createChange(resolution.change_id, resolution.agent_id);
      return {
        ...next,
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
            status:
              resolution.merge === "conflict"
                ? "conflict"
                : resolution.decision === "changes_requested"
                  ? "revising"
                  : resolution.decision,
          },
        },
      };
    }
    case "usage.tick": {
      const tick = event.payload;
      const before = state.agentCosts[tick.agent_id];
      // Unknown stays unknown: a later tick with a cost does not make the total known again.
      const cost = tick.cost_usd === null || before === null ? null : (before ?? 0) + tick.cost_usd;
      // The status names a profile; the tick's model is the provider's reported API id.
      const modelId = state.agents[tick.agent_id]?.model;
      const profile = state.run?.models.find((model) => model.id === modelId);
      return {
        ...next,
        usage: tick,
        agentCosts: { ...state.agentCosts, [tick.agent_id]: cost },
        spendUnknown:
          !profile || profile.provider === "meta" ? tick.cost_usd === null : state.spendUnknown,
      };
    }
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
    case "change.reviewed": {
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
    // Reserved contracts: consume the sequence without enabling unrelated level 3 behavior.
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
    default: {
      const exhaustive: never = event;
      return exhaustive;
    }
  }
}
