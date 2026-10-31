import { selectAgentCard as selectBaseCard } from "../state";
import type { State } from "../state";

/** The latest completed tool is authoritative; focus supplies a location before the first step.
 * Tools emit focus before step, so a later test/search result must not keep saying "Editing".
 */
export function selectAgentCard(state: State, agentId: string) {
  const data = selectBaseCard(state, agentId);
  if (!data) return undefined;
  const latest = data.steps[0];
  const focus = state.focus[agentId];
  const range = focus?.ranges[0];
  const node = state.graph?.nodes.find((node) => focus?.node_ids.includes(node.id));
  const wait =
    data.agent.state === "working" || data.agent.state === "revising"
      ? state.modelWaits[agentId]
      : undefined;
  const activity =
    latest?.summary ??
    (range
      ? `${focus.mode === "edit" ? "Editing" : "Reading"} ${node ? `${node.name} in ` : ""}${range.path}`
      : "Waiting for the first tool call.");
  return {
    ...data,
    activity: wait
      ? `Waiting for model (retry ${String(wait.attempt + 1)}/${String(wait.max_attempts)})`
      : activity,
    // run.started owns this limit; agent.status may carry a different revision allowance.
    maxSteps: state.run?.limits.max_steps ?? data.agent.max_steps,
    activityLabel: wait
      ? "Waiting"
      : latest?.result === "ok" && latest.tool === "edit_file"
        ? "Editing"
        : latest?.result === "ok" && latest.tool === "read_file"
          ? "Reading"
          : "Working",
  };
}

export type AgentCardDetail = NonNullable<ReturnType<typeof selectAgentCard>>;
