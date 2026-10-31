import type { ServerEvent } from "@contracts";
import log from "../../../fixtures/events/recorded-demo.jsonl?raw";
import { applyEvent } from "./applyEvent";
import { createInitialState } from "./types";

export const recordedEvents = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
export const baseEvents = recordedEvents.filter((event) => event.seq <= 52);
const pricing = recordedEvents.find((event) => event.seq === 33 && event.type === "change.updated");
const inventory = recordedEvents.find(
  (event) => event.seq === 29 && event.type === "change.updated",
);
if (pricing?.type !== "change.updated" || inventory?.type !== "change.updated")
  throw new Error("Missing recorded updates");
export const priceId = "shop.pricing:compute_price";
const read: ServerEvent = {
  seq: 53,
  ts: baseEvents.at(-1)?.ts ?? "",
  type: "agent.focus",
  payload: {
    agent_id: "agent-2",
    mode: "read",
    ranges: [{ path: "shop/pricing.py", start_line: null, end_line: null }],
    node_ids: ["shop.pricing", priceId, "shop.pricing:price_breaks"],
  },
};
export const readEvents: ServerEvent[] = [read];
export const sameNodeEvents: ServerEvent[] = [
  read,
  {
    seq: 54,
    ts: read.ts,
    type: "change.updated",
    payload: {
      ...pricing.payload,
      agent_id: "agent-2",
      change_id: "change-2",
      graph: {
        ...inventory.payload.graph,
        nodes_modified: [...inventory.payload.graph.nodes_modified, priceId],
      },
    },
  },
  {
    seq: 55,
    ts: read.ts,
    type: "agent.focus",
    payload: { ...read.payload, mode: "edit", node_ids: [priceId] },
  },
];
export const baseMoment = () => baseEvents.reduce(applyEvent, createInitialState());
export const readMoment = () => readEvents.reduce(applyEvent, baseMoment());
export const sameNodeMoment = () => sameNodeEvents.reduce(applyEvent, baseMoment());
export function required<T>(value: T | null | undefined): T {
  if (value == null) throw new Error("Missing overlap fixture value");
  return value;
}
