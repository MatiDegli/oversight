import { agentLook } from "../../theme";
import type { State } from "../../state";
import { getActiveTheme } from "../../theme";
import type { FieldLight } from "./lights";

/** One last read per agent, retained by the event reducer, shared by live and replay. */
export type LastReads = Readonly<Record<string, string | undefined>>;

/** R4 sources are independent of navigation, pending edits and the impact cone. */
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
    const change = state.changes[agent.change_id];
    if (change?.status === "approved" || change?.status === "rejected") continue;
    const id = state.focus[agent.agent_id]?.node_ids[0];
    if (!id) continue;
    const source = agentLook[agent.slot].family;
    lights.push({
      id,
      source,
      weight: theme["watch-editing-weight"],
      pulses: agent.state === "working" || agent.state === "revising",
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
