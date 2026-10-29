import { readFileSync } from "node:fs";

import type { GraphSnapshot, ServerEvent } from "@contracts";
import { describe, expect, it } from "vitest";

import { applyEvent, createInitialState } from "../../state";
import { selectMerges, selectModules } from "./life";

const events = readFileSync(
  new URL("../../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const through = (seq: number) =>
  events.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState());

describe("merges", () => {
  it("has none while the change waits, and one once it is merged, at its node and agent", () => {
    expect(selectMerges(through(13))).toEqual([]);
    expect(selectMerges(through(20))).toEqual([]);
    const [merge, ...others] = selectMerges(through(21));
    expect(others).toEqual([]);
    expect(merge).toMatchObject({
      key: "change-1:21",
      slot: "A",
      source: "agent-a",
      id: "shop.pricing:compute_price",
      age: 0,
    });
  });

  it("is as old as the events applied since the decision, from event times alone", () => {
    // The graph delta lands 160 ms after the decision, and the agent's last status 10 ms after.
    expect(selectMerges(through(22))[0]?.age).toBe(160);
    expect(selectMerges(through(23))[0]?.age).toBe(170);
    expect(selectMerges(through(23))[0]?.key).toBe("change-1:21");
  });

  it("tells a rejected change without a pulse, and leaves out one that conflicted", () => {
    const state = through(21);
    const change = state.changes["change-1"];
    if (!change?.resolution) throw new Error("Missing resolution");
    const rejected = {
      ...state,
      changes: {
        "change-1": {
          ...change,
          resolution: { ...change.resolution, decision: "rejected", merge: null },
        },
      },
    } as typeof state;
    // A rejection fades the change's light as a merge does, but no pulse runs to its node.
    expect(selectMerges(rejected)).toMatchObject([
      { key: "change-1:21", id: "shop.pricing:compute_price", pulse: false },
    ]);
    expect(selectMerges(through(21))[0]?.pulse).toBe(true);
    const conflict = {
      ...state,
      changes: {
        "change-1": { ...change, resolution: { ...change.resolution, merge: "conflict" } },
      },
    } as typeof state;
    expect(selectMerges(conflict)).toEqual([]);
  });

  it("starts at age 0 when the state has no clock or the decision has no time", () => {
    const state = { ...through(21), clock: null };
    expect(selectMerges(state)[0]?.age).toBe(0);
  });
});

describe("modules", () => {
  const node = (
    id: string,
    kind: "module" | "class" | "function" | "method",
    parent: string | null,
    test = false,
  ) => ({
    id,
    kind,
    name: id,
    qualname: id,
    path: `${id}.py`,
    start_line: 1,
    end_line: 2,
    parent_id: parent,
    is_test: test,
    examples: null,
  });
  const edge = (src: string, dst: string, kind: "calls" | "imports" = "calls") => ({
    id: `${src}>${dst}`,
    kind,
    src,
    dst,
    confidence: "exact" as const,
    reason: null,
  });
  const graph = (): GraphSnapshot =>
    ({
      version: "v",
      commit: "c",
      nodes: [
        node("a", "module", null),
        node("a:f", "function", "a"),
        node("b", "module", null),
        node("b:C", "class", "b"),
        node("b:C.m", "method", "b:C"),
        node("t", "module", null, true),
        node("t:test", "function", "t", true),
      ],
      edges: [
        edge("a:f", "b:C.m"),
        edge("a:f", "b:C.m"),
        edge("t:test", "a:f"),
        edge("b:C.m", "b:C"),
        edge("a", "b", "imports"),
      ],
      broken_files: [],
    }) as unknown as GraphSnapshot;

  it("lists the modules but not the test ones, counting calls that reach in from another module", () => {
    const modules = selectModules(graph());
    expect(modules).toEqual([
      { id: "a", callers: 1 },
      { id: "b", callers: 2 },
    ]);
  });

  it("counts neither calls inside a module nor imports", () => {
    expect(selectModules(graph()).find((module) => module.id === "b")?.callers).toBe(2);
  });

  it("keeps the same list for the same graph, and has none without a graph", () => {
    const g = graph();
    expect(selectModules(g)).toBe(selectModules(g));
    expect(selectModules(null)).toEqual([]);
  });
});
