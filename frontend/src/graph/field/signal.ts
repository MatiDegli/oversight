import type { AgentState } from "@contracts";

import { agentLook } from "../../theme";
import type { State } from "../../state";
import { getActiveTheme } from "../../theme";
import type { FieldLight } from "./lights";

/** One last read per agent, retained by the event reducer, shared by live and replay. */
export type LastReads = Readonly<Record<string, string | undefined>>;

/** An agent acts while it works or revises; a proposal, a pause, a halt or a stop ends it. */
export const isActing = (state: AgentState) => state === "working" || state === "revising";

/**
 * R4 sources are independent of navigation, pending edits and the impact cone. Colored light means
 * an agent is acting now (DESIGN.md, Agent light): an idle agent has none. What a merge leaves on the
 * graph is the fading light of `MergeLife`, not a source chosen here.
 */
export function selectSignalLights(
  state: State,
  lastReads: LastReads = {},
  theme: {
    "watch-editing-weight": number;
    "live-diff-read-weight": number;
    "light-source-limit": number;
  } = getActiveTheme(),
): FieldLight[] {
  const lights: FieldLight[] = [];
  for (const agent of Object.values(state.agents).sort(
    (a, b) => a.slot.localeCompare(b.slot) || a.agent_id.localeCompare(b.agent_id),
  )) {
    if (!isActing(agent.state)) continue;
    const change = state.changes[agent.change_id];
    if (change?.status === "approved" || change?.status === "rejected") continue;
    const id = state.focus[agent.agent_id]?.node_ids[0];
    if (!id) continue;
    const source = agentLook[agent.slot].family;
    lights.push({
      id,
      source,
      weight: theme["watch-editing-weight"],
      pulses: true,
      marker: agent.agent_id,
      steady: true,
    });
    const read = lastReads[agent.agent_id];
    if (read && read !== id)
      lights.push({
        id: read,
        source,
        weight: theme["live-diff-read-weight"],
        pulses: false,
        steady: true,
      });
  }
  // Current foci precede last reads; stable slot/id order breaks ties. Keep the field budget.
  return lights
    .sort((a, b) => Number(Boolean(b.marker)) - Number(Boolean(a.marker)))
    .slice(0, theme["light-source-limit"]);
}
