import type { ChangeResolved, Plan, PlanSubtask, SuiteExecution, SuiteResult } from "@contracts";
import type { ChangeState, State } from "./types";

export const flowSteps = [
  "Agent works",
  "Proposal tests",
  "Approval",
  "Combined tests, if main moved",
  "Merged",
] as const;
export type FlowMark =
  "passed" | "merged" | "working" | "waiting" | "failed" | "not needed" | "not known";
export type FlowStep = { mark: FlowMark; detail: string };
export interface PlanFlowRow {
  index: number;
  wave: number;
  task: PlanSubtask;
  agentId: string | null;
  changeId: string | null;
  word: "Working" | "Waiting for you" | "Merged" | "Not merged" | "Waiting" | "Not known";
  condition: string;
  progress: string | null;
  steps: FlowStep[];
  main: "unchanged" | "moved" | "not known";
  note: string | null;
  merged: boolean;
}
const step = (mark: FlowMark, detail: string = mark): FlowStep => ({ mark, detail });
const numbers = (indices: number[]) => indices.map((i) => `#${String(i + 1)}`).join(" and ");

/** The plan is the winning revision selected by selectPlanEvidence at the displayed prefix. */
export function projectPlanFlow(state: State, plan: Plan): PlanFlowRow[] {
  const tasks = plan.subtasks ?? [];
  const depths = new Map<number, number>();
  const depth = (index: number): number => {
    const known = depths.get(index);
    if (known !== undefined) return known;
    // Validated plans are acyclic; this guard also bounds malformed legacy in-memory inputs.
    depths.set(index, 0);
    const parents = tasks[index]?.depends_on ?? [];
    const value = parents.reduce((max, parent) => Math.max(max, depth(parent) + 1), 0);
    depths.set(index, value);
    return value;
  };
  return tasks.map((task, index) => projectRow(state, task, index, depth(index)));
}

function rowChange(state: State, agentId: string | null): ChangeState | undefined {
  if (!agentId) return undefined;
  const current = state.agents[agentId];
  const assigned = current ? state.changes[current.change_id] : undefined;
  if (assigned?.agent_id === agentId) return assigned;
  return Object.values(state.changes)
    .filter((change) => change.agent_id === agentId)
    .sort((a, b) => (b.proposalEvent?.seq ?? 0) - (a.proposalEvent?.seq ?? 0))[0];
}

function execution(state: State, change: ChangeState | undefined, phase: SuiteExecution["phase"]) {
  if (!change?.proposal) return undefined;
  const proposal = change.proposal;
  return Object.entries(state.testExecutions)
    .filter(
      ([, test]) =>
        test.change_id === change.change_id &&
        test.agent_id === change.agent_id &&
        test.revision === proposal.revision &&
        test.phase === phase &&
        // A combined tree differs from the proposal; proposal executions name its exact head.
        (phase !== "proposal" ||
          test.tested_commit === null ||
          test.tested_commit === proposal.head_commit),
    )
    .sort(([a], [b]) => Number(b) - Number(a))[0]?.[1];
}

function suiteStep(suite?: SuiteResult): FlowStep {
  if (!suite) return step("not known", "No current-revision execution recorded");
  const counts = `${String(suite.passed)} passed, ${String(suite.failed)} failed`;
  return step(suite.outcome === "passed" ? "passed" : "failed", `${suite.outcome}: ${counts}`);
}

function mainMovement(state: State, change: ChangeState | undefined, base: string | null) {
  const resolution = change?.resolution;
  const terminalSeq =
    resolution && resolution.decision !== "changes_requested"
      ? change.resolutionEvent?.seq
      : undefined;
  const head = state.mainHeads.findLast((observation) =>
    terminalSeq === undefined
      ? true
      : observation.seq < terminalSeq && observation.commit !== resolution?.merge_commit,
  )?.commit;
  return !base || !head ? "not known" : base === head ? "unchanged" : "moved";
}

function projectRow(state: State, task: PlanSubtask, index: number, wave: number): PlanFlowRow {
  const agent = task.agent_id ? state.agents[task.agent_id] : undefined;
  const change = rowChange(state, task.agent_id);
  const proposal = change?.proposal;
  const resolution =
    change?.resolution && (!proposal || change.resolution.revision === proposal.revision)
      ? change.resolution
      : null;
  const merged = resolution?.merge === "merged" && !!resolution.merge_commit;
  const terminal =
    task.state === "rejected" ||
    task.state === "cancelled" ||
    resolution?.decision === "rejected" ||
    resolution?.merge === "conflict";
  const main = mainMovement(state, change, agent?.base_commit ?? null);
  const proposalTest = execution(state, change, "proposal");
  const combined = execution(state, change, "combined");
  const working = !!agent && ["starting", "working", "revising"].includes(agent.state);
  const waiting = !!proposal && !resolution;
  const workStep = proposal
    ? step("passed", "Agent submitted the change")
    : working
      ? step("working", agent.state)
      : step("not known", agent?.state ?? "No agent assigned");
  const approval = resolution
    ? step(
        resolution.decision === "approved" ? "passed" : "failed",
        `${resolution.decision.replaceAll("_", " ")} · ${resolution.decided_by ?? "principal not known"}`,
      )
    : waiting
      ? step("waiting", "Waiting for your decision")
      : step("not known", "No decision recorded");
  const combinedStep = combined
    ? suiteStep(combined.suite)
    : main === "unchanged"
      ? step("not needed", "Main is unchanged from the agent’s base")
      : step(
          "not known",
          main === "moved"
            ? "Main moved; no combined execution recorded"
            : "Main movement is not known",
        );
  const failed = combinedStep.mark === "failed" || terminal;
  const word = merged
    ? "Merged"
    : failed
      ? "Not merged"
      : waiting
        ? "Waiting for you"
        : working
          ? "Working"
          : task.state === "waiting"
            ? "Waiting"
            : "Not known";
  const started = !!agent || task.state === "merged";
  return {
    index,
    wave,
    task,
    agentId: task.agent_id,
    changeId: change?.change_id ?? null,
    word,
    condition: rowCondition(task, started),
    main,
    merged,
    progress: agent ? `Step ${String(agent.step)} of ${String(agent.max_steps)}` : null,
    steps: [
      workStep,
      suiteStep(proposalTest?.suite),
      approval,
      combinedStep,
      merged
        ? step("merged", `Merged into ${resolution.merge_commit ?? ""}`)
        : step(
            resolution?.conflict?.kind === "merge" ? "failed" : "not known",
            failed ? "Not merged" : "No merge recorded",
          ),
    ],
    note: rowNote(task, { resolution, combined, terminal, merged, waiting, main }),
  };
}

type RowEvidence = {
  resolution: ChangeResolved | null;
  combined: SuiteExecution | undefined;
  terminal: boolean;
  merged: boolean;
  waiting: boolean;
  main: PlanFlowRow["main"];
};
function rowNote(task: PlanSubtask, evidence: RowEvidence): string | null {
  const { resolution, combined, terminal, merged, waiting, main } = evidence;
  if (combined && combined.suite.outcome !== "passed")
    return `${resolution?.decision === "approved" ? "Approved, not merged" : "Not merged; approval not known"}: combined tests ${suiteStep(combined.suite).detail}.`;
  if (terminal) {
    if (task.state === "cancelled") return "Cancelled; this subtask did not merge.";
    return resolution?.merge === "conflict"
      ? "Approved, not merged: the merge conflicts with main."
      : "Rejected; this subtask did not merge.";
  }
  if (merged) return `Merged into ${resolution?.merge_commit ?? ""}`;
  if (!waiting) return null;
  if (main === "moved")
    return "Main moved since the agent started. Combined tests run before merge after approval.";
  return main === "not known" ? "Main movement is not known at this point in the run." : null;
}

function rowCondition(task: PlanSubtask, started: boolean) {
  if (!task.depends_on.length) return started ? "Started first" : "Starts first";
  return `${started ? "Started" : "Starts"} after ${numbers(task.depends_on)} ${started ? "merged" : task.depends_on.length === 1 ? "merges" : "merge"}`;
}
