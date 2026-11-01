import { readFileSync } from "node:fs";
import type { ServerEvent } from "@contracts";
import { expect, it, vi } from "vitest";
import { applyEvent, createInitialState } from "../state";
import { graphElements } from "../graph";
import { ArrivalClock, mergeArrivals, mergingChanges } from "./useMergeArrival";
const events = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const state = events.filter((event) => event.seq <= 21).reduce(applyEvent, createInitialState());
const change = state.changes["change-1"];
const agent = state.agents["agent-1"];
const timestamp = state.clock;
if (!change || !agent || !timestamp) throw new Error("Missing arrival fixture state");
it("holds changed and dashed pending marks until the shared 250 + 1450 ms arrival", () => {
  const starts = mergeArrivals(state, new Map(), 1000);
  const before = mergingChanges(starts, 2699, false);
  expect(before).toEqual(["change-1"]);
  const row = graphElements(state, false, null, null, false, null, before).find(
    (node) => node.data.id === "shop.pricing:compute_price",
  );
  expect(row?.data.marks?.word).toBe("changed");
  expect(row?.classes).toContain("pending-A");
  const after = mergingChanges(starts, 2700, false);
  expect(after).toEqual([]);
  expect(
    graphElements(state, false, null, null, false, null, after).find(
      (node) => node.data.id === row?.data.id,
    )?.classes,
  ).not.toContain("pending-A");
  expect(mergingChanges(starts, 1000, true)).toEqual([]);
  expect(mergingChanges(mergeArrivals(state, new Map(), 1000, true), 1000, false)).toEqual([]);
  expect(mergeArrivals(createInitialState(), starts, 3000).size).toBe(0);
});

it("preserves starts and snapshot identity for unrelated events", () => {
  const clock = new ArrivalClock(state);
  const before = clock.getSnapshot();
  const listener = vi.fn();
  const unsubscribe = clock.subscribe(listener);
  clock.update({ ...state, lastSeq: state.lastSeq + 1 });
  expect(clock.getSnapshot().starts).toBe(before.starts);
  expect(clock.getSnapshot()).toBe(before);
  expect(listener).not.toHaveBeenCalled();
  unsubscribe();
  clock.dispose();
});
it("shows an evicted merge as arrived instead of Merging without travel", () => {
  const clock = new ArrivalClock(createInitialState());
  const sources = ["A", "B", "C", "D", "E"] as const;
  const changes = Object.fromEntries(
    sources.map((slot, i) => [
      slot,
      {
        ...change,
        change_id: slot,
        agent_id: slot,
        resolution: i === 0 ? change.resolution : null,
        resolutionEvent: { seq: 30 + i, ts: timestamp },
      },
    ]),
  );
  const agents = Object.fromEntries(
    sources.map((slot) => [
      slot,
      {
        ...agent,
        agent_id: slot,
        slot,
        change_id: slot,
      },
    ]),
  );
  const decisionEvents = sources
    .slice(1)
    .map<Extract<ServerEvent, { type: "error" }>>((slot, i) => ({
      seq: 40 + i,
      ts: timestamp,
      type: "error",
      payload: {
        source: "git",
        agent_id: slot,
        change_id: slot,
        fatal: false,
        message: "Needs you — revision=1; head=abc; code=main_moved; reason=Main moved",
      },
    }));
  clock.update({ ...state, changes, agents, decisionEvents });
  const snapshot = clock.getSnapshot();
  expect(snapshot.labels.get("A")).toBe("Merged");
  expect(snapshot.merging).toEqual([]);
  expect(snapshot.starts.get("A:30")).toBe(-Infinity);
  clock.dispose();
});

it("keeps an added node new throughout Merging", () => {
  const node = state.graph?.nodes.find((node) => node.id === "shop.pricing:compute_price");
  if (!node) throw new Error("Missing arrival fixture node");
  const changed = {
    ...state,
    changes: {
      ...state.changes,
      "change-1": {
        ...change,
        graph: { ...change.graph, nodes_added: [node], nodes_modified: [] },
      },
    },
  };
  const row = graphElements(changed, false, null, null, false, null, ["change-1"]).find(
    (entry) => entry.data.id === node.id,
  );
  expect(row?.data.marks?.word).toBe("new");
  expect(row?.classes).toContain("pending-A");
  const arrived = graphElements(changed).find((entry) => entry.data.id === node.id);
  expect(arrived?.data.marks?.word).not.toBe("new");
  expect(arrived?.classes).not.toContain("pending-A");
});
