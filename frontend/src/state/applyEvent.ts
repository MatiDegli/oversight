import type { ServerEvent } from "@contracts";

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
        stepTimes: {
          ...state.stepTimes,
          [step.agent_id]: { ...state.stepTimes[step.agent_id], [step.step]: event.ts },
        },
      };
    }
    case "agent.focus":
      return { ...next, focus: { ...state.focus, [event.payload.agent_id]: event.payload } };
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
            files: {
              ...change.files,
              [update.file.path]: {
                file: update.file,
                author: update.author,
                node_ids: update.node_ids,
              },
            },
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
            status: "ready_for_review",
            proposal,
            resolution: null,
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
    // Reserved contracts: consume the sequence without enabling level 3 behavior.
    case "change.assessed":
    case "merge_mode.changed":
    case "plan.updated":
    case "change.reviewed":
    case "revert.resolved":
    case "queue.updated":
    case "lead.status":
      return next;
    case "error":
      return { ...next, errors: [...state.errors, event.payload] };
    default: {
      const exhaustive: never = event;
      return exhaustive;
    }
  }
}
