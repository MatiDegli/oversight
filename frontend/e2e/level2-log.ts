import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import type { ServerEvent } from "@contracts";

import { before } from "./holds";

export const events = readFileSync(
  new URL("../../fixtures/events/scripted-level2.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);

export function eventOf<T extends ServerEvent["type"]>(
  type: T,
  matches: (event: Extract<ServerEvent, { type: T }>) => boolean = () => true,
) {
  const event = events.find(
    (event): event is Extract<ServerEvent, { type: T }> =>
      event.type === type && matches(event as Extract<ServerEvent, { type: T }>),
  );
  if (!event) throw new Error(`Missing ${type} in the backend recording`);
  return event;
}
export const a = eventOf("agent.status", (e) => e.payload.slot === "A").payload;
export const b = eventOf("agent.status", (e) => e.payload.slot === "B").payload;
export const proposal = eventOf(
  "change.proposed",
  (e) => e.payload.agent_id === a.agent_id,
).payload;
export const bProposal = eventOf(
  "change.proposed",
  (e) => e.payload.agent_id === b.agent_id,
).payload;
export const requestedA = eventOf(
  "change.resolved",
  (e) => e.payload.agent_id === a.agent_id && e.payload.decision === "changes_requested",
);
export const requestedB = eventOf(
  "change.resolved",
  (e) => e.payload.agent_id === b.agent_id && e.payload.decision === "changes_requested",
);
export const revising = eventOf(
  "agent.status",
  (e) => e.payload.agent_id === a.agent_id && e.payload.state === "revising",
);
export const revised = eventOf(
  "change.proposed",
  (e) => e.payload.agent_id === a.agent_id && e.payload.revision > proposal.revision,
);
export const reviewEdit = eventOf("change.updated", (e) => e.payload.author === "user");
export const stopped = eventOf(
  "agent.status",
  (e) => e.payload.agent_id === b.agent_id && e.payload.state === "stopped",
);
export const approved = eventOf(
  "change.resolved",
  (e) => e.payload.agent_id === a.agent_id && e.payload.merge === "merged",
);
export const approvedProposal = eventOf(
  "change.proposed",
  (e) => e.payload.change_id === a.change_id && e.payload.revision === approved.payload.revision,
).payload;
export const mainEdit = events.findLast(
  (e) => e.type === "graph.delta" && e.payload.cause === "user_edit",
);
if (mainEdit?.type !== "graph.delta") throw new Error("Missing main edit delta");
const addedNode = mainEdit.payload.nodes_added[0];
if (!addedNode) throw new Error("Main edit must add a function");
export const added = addedNode;
export const mainAppend = `\n\ndef ${added.name}() -> int:\n    return 42\n`;
export const firstUpdate = eventOf(
  "change.updated",
  (e) => e.payload.agent_id === a.agent_id,
).payload;
const revisionUpdate = eventOf(
  "change.updated",
  (e) => e.payload.agent_id === a.agent_id && e.seq > revising.seq && e.payload.author === "agent",
);
export const reviewAppend = (reviewEdit.payload.file.modified ?? "").slice(
  (revisionUpdate.payload.file.modified ?? "").length,
);
export const hash = (text: string) => createHash("sha256").update(text).digest("hex");

export const holds = {
  live: before(events, eventOf("change.proposed"), 1771),
  decisions: before(events, requestedA, 1773),
  revising: before(events, revised, 1775),
  edit: before(events, reviewEdit, 1777),
  saved: before(events, requestedB, 1779),
  stop: before(events, stopped, 1781),
  approve: before(events, approved, 1783),
  main: before(events, mainEdit, 1785),
};
