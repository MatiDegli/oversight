import type { AgentState } from "@contracts";

import { agentLook } from "../../theme";
import { selectDecisionInbox } from "../../state";
import type { State } from "../../state";
import { getActiveTheme } from "../../theme";
import type { FieldLight } from "./lights";
import { changeLightNode } from "./life";

/** One last read per agent, retained by the event reducer, shared by live and replay. */
export type LastReads = Readonly<Record<string, string | undefined>>;

/** An agent acts while it works or revises; a proposal, a pause, a halt or a stop ends it. */
export const isActing = (state: AgentState) => state === "working" || state === "revising";

/**
 * The light belongs to a change, in three states (DESIGN.md, Agent light). While its agent acts it
 * is the working light: a ring on the node the agent reads or edits, and a fainter light on its last
 * read. While the change has an open entry in the decisions inbox, read from `selectDecisionInbox`
 * and not from a rule of this file, it is one steady, dimmer light on the change's node, with no
 * motion; opening the review or clicking the node does not change it, and only closing the entries
 * does. Once the change is decided the light fades out (`MergeLife`), so it is not a source here.
 * An idle agent whose change waits on nothing has none.
 */
export function selectSignalLights(
  state: State,
  lastReads: LastReads = {},
  theme: {
    "watch-editing-weight": number;
    "live-diff-read-weight": number;
    "waiting-light-weight": number;
    "light-source-limit": number;
  } = getActiveTheme(),
): FieldLight[] {
  const lights: FieldLight[] = [];
  let waiting: ReadonlySet<string> | undefined;
  for (const agent of Object.values(state.agents).sort(
    (a, b) => a.slot.localeCompare(b.slot) || a.agent_id.localeCompare(b.agent_id),
  )) {
    const change = state.changes[agent.change_id];
    if (change?.status === "approved" || change?.status === "rejected") continue;
    const source = agentLook[agent.slot].family;
    if (!isActing(agent.state)) {
      // The inbox is read once, and only when an idle agent might have a change waiting.
      waiting ??= new Set(selectDecisionInbox(state).flatMap((entry) => entry.changeId ?? []));
      const id = change && waiting.has(change.change_id) ? changeLightNode(change) : undefined;
      if (id !== undefined)
        lights.push({
          id,
          source,
          weight: theme["waiting-light-weight"],
          pulses: false,
          steady: true,
        });
      continue;
    }
    const id = state.focus[agent.agent_id]?.node_ids[0];
    if (!id) continue;
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
