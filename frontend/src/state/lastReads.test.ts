import { readFileSync } from "node:fs";

import type { AgentFocus, ServerEvent } from "@contracts";
import { expect, it } from "vitest";

import { applyEvent, createInitialState, createStateStore } from "./index";

const events = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const start = events[0];
if (!start || start.type !== "run.started") throw new Error("Missing run start");

function focus(
  seq: number,
  agent_id: string,
  mode: AgentFocus["mode"],
  node_ids: string[],
): ServerEvent {
  return {
    seq,
    ts: "2026-10-27T14:03:12.345Z",
    type: "agent.focus",
    payload: { agent_id, mode, node_ids, ranges: [] },
  };
}

it("retains one last read per agent, replaces it on each read, and keeps edits independent", () => {
  const empty = applyEvent(createInitialState(), start);
  const first = applyEvent(empty, focus(2, "a", "read", ["first", "extra"]));
  const second = applyEvent(first, focus(3, "b", "read", ["other"]));
  const replacement = applyEvent(second, focus(4, "a", "read", ["latest", "extra"]));
  const editing = applyEvent(replacement, focus(5, "a", "edit", ["current"]));
  expect(empty.lastReads).toEqual({});
  expect(first.lastReads).toEqual({ a: "first" });
  expect(second.lastReads).toEqual({ a: "first", b: "other" });
  expect(replacement.lastReads).toEqual({ a: "latest", b: "other" });
  expect(editing.lastReads).toEqual(replacement.lastReads);
  expect(editing.focus.a?.node_ids).toEqual(["current"]);
  expect(applyEvent(editing, focus(6, "a", "read", [])).lastReads).toEqual({ b: "other" });
  expect(applyEvent(editing, focus(7, "a", "read", ["gap"])).lastReads).toEqual(editing.lastReads);
});

it("resets both agents' last reads at a new run, including after a gap", () => {
  const dirty = [start, focus(2, "a", "read", ["old-a"]), focus(3, "b", "read", ["old-b"])].reduce(
    applyEvent,
    createInitialState(),
  );
  expect(dirty.lastReads).toEqual({ a: "old-a", b: "old-b" });
  const reset = applyEvent(
    { ...dirty, gap: true },
    {
      ...start,
      payload: { ...start.payload, run_id: "next-run" },
    },
  );
  expect(reset.lastReads).toEqual({});
  expect(reset.focus).toEqual({});
  expect(reset.gap).toBe(false);
});

it("rebuilds the same reads from serialized replay as from live batches at every cut", () => {
  const log = [
    start,
    focus(2, "a", "read", ["one"]),
    focus(3, "b", "read", ["two"]),
    focus(4, "a", "edit", ["current"]),
    focus(5, "a", "read", ["replacement"]),
  ];
  for (const source of [log, events]) {
    const recorded = source.map((entry) => JSON.parse(JSON.stringify(entry)) as ServerEvent);
    for (let cut = 1; cut <= source.length; cut++) {
      const live = createStateStore();
      live.getState().applyEvents(source.slice(0, cut - 1));
      live.getState().applyEvents(source.slice(cut - 1, cut));
      const replay = recorded.slice(0, cut).reduce(applyEvent, createInitialState());
      expect(live.getState().state).toEqual(replay);
    }
  }
  expect(log.reduce(applyEvent, createInitialState()).lastReads).toEqual({
    a: "replacement",
    b: "two",
  });
});
