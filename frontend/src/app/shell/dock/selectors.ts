import type { SuiteResult } from "@contracts";
import type { State } from "../../../state";

export const LOG_LIMIT = 300;

export function agentLetter(state: State, agentId: string | null): string {
  return agentId ? (state.agents[agentId]?.slot ?? "?") : "—";
}

export interface TestRun {
  id: string;
  agentId: string;
  label: string;
  suite: SuiteResult | null;
}

/** tests.executed has sequence ordering; older recordings fall back to retained summaries. */
export function selectTestRuns(state: State): TestRun[] {
  const runs = new Map<string, TestRun>();
  for (const execution of Object.values(state.testExecutions)) {
    runs.set(execution.change_id, {
      id: execution.change_id,
      agentId: execution.agent_id,
      label: `${execution.change_id} · ${execution.phase.replaceAll("_", " ")}`,
      suite: execution.suite,
    });
  }
  for (const change of Object.values(state.changes)) {
    if (!runs.has(change.change_id) && change.proposal) {
      runs.set(change.change_id, {
        id: change.change_id,
        agentId: change.agent_id,
        label: change.change_id,
        suite: change.proposal.tests,
      });
    }
  }
  const represented = new Set([...runs.values()].map((run) => run.agentId));
  for (const [agentId, steps] of Object.entries(state.steps)) {
    if (represented.has(agentId)) continue;
    const step = steps.findLast((step) => step.tests !== null);
    if (step?.tests) {
      runs.set(agentId, {
        id: agentId,
        agentId,
        label: `Step ${String(step.step)}`,
        suite: step.tests,
      });
    }
  }
  return [...runs.values()];
}

export interface ActivityEntry {
  id: string;
  agentId: string | null;
  seq: number;
  time: string | null;
  title: string;
  summary: string;
  failed: boolean;
  duration: number | null;
}

/** No event buffer: bounded projections of the same state used by live and static replay. */
export function selectActivity(state: State) {
  const entries: ActivityEntry[] = [];
  let total = state.errors.length;
  for (const [agentId, steps] of Object.entries(state.steps)) {
    total += steps.length;
    for (const step of steps.slice(-LOG_LIMIT)) {
      entries.push({
        id: `step:${agentId}:${String(step.step)}`,
        agentId,
        seq: state.stepSeqs[agentId]?.[step.step] ?? 0,
        time: state.stepTimes[agentId]?.[step.step] ?? null,
        title: `${step.tool} · ${step.result}`,
        summary: [step.summary, step.error].filter(Boolean).join(" · "),
        failed: step.result === "error",
        duration: step.duration_ms,
      });
    }
  }
  for (const change of Object.values(state.changes)) {
    if (change.proposal) {
      total++;
      entries.push({
        id: `proposal:${change.change_id}`,
        agentId: change.agent_id,
        seq: change.proposalEvent?.seq ?? 0,
        time: change.proposalEvent?.ts ?? null,
        title: `Proposed · ${change.change_id}`,
        summary: change.proposal.summary ?? change.proposal.task,
        failed: false,
        duration: null,
      });
    }
    if (change.resolution) {
      total++;
      const resolution = change.resolution;
      const decision = {
        approved: "Approved",
        changes_requested: "Changes requested",
        rejected: "Rejected",
      }[resolution.decision];
      entries.push({
        id: `resolution:${change.change_id}`,
        agentId: change.agent_id,
        seq: change.resolutionEvent?.seq ?? change.resolutionSeq ?? 0,
        time: change.resolutionEvent?.ts ?? null,
        title: `${decision}${resolution.merge ? ` · ${resolution.merge}` : ""} · ${change.change_id}`,
        summary: resolution.comment ?? "",
        failed: resolution.merge === "conflict",
        duration: null,
      });
    }
  }
  state.errors.slice(-LOG_LIMIT).forEach((error, index) => {
    const position = state.errorEvents[Math.max(0, state.errors.length - LOG_LIMIT) + index];
    entries.push({
      id: `error:${String(Math.max(0, state.errors.length - LOG_LIMIT) + index)}`,
      agentId: error.agent_id,
      seq: position?.seq ?? 0,
      time: position?.ts ?? null,
      title: `Error · ${error.source}`,
      summary: error.message,
      failed: true,
      duration: null,
    });
  });
  entries.sort((a, b) => a.seq - b.seq);
  return { entries: entries.slice(-LOG_LIMIT), omitted: Math.max(0, total - LOG_LIMIT) };
}

export function duration(ms: number): string {
  return `${(ms / 1000).toFixed(2)} s`;
}
