import { readFileSync } from "node:fs";

import type { GraphSnapshot, ServerEvent } from "@contracts";
import { describe, expect, it } from "vitest";

import { applyEvent, createInitialState } from "../state";

import { graphElements } from "./elements";
import { foldElements } from "./foldElements";
import { initialFolds, observeEdits, reduceFolds } from "./folds";

const root = new URL("../../../", import.meta.url);
const graph = JSON.parse(
  readFileSync(new URL("fixtures/graph/demo-repo.json", root), "utf8"),
) as GraphSnapshot;
graph.positions = JSON.parse(
  readFileSync(new URL("fixtures/layouts/demo-repo.json", root), "utf8"),
) as GraphSnapshot["positions"];
const state = { ...createInitialState(), graph };
const full = graphElements(state);
const cart = "shop.cart:Cart";
const add = `${cart}.add`;
const allOpen = new Set(graph.nodes.filter((n) => n.kind === "class").map((n) => n.id));

describe("fold state", () => {
  it("starts folded, toggles on click, opens for an edit and can fold again", () => {
    let folds = initialFolds();
    expect(folds.expanded.size).toBe(0);
    folds = reduceFolds(folds, { type: "toggle", id: cart });
    expect(folds.expanded.has(cart)).toBe(true);
    folds = reduceFolds(folds, { type: "toggle", id: cart });
    const edit = { type: "change", revision: "edit-1", classes: [cart] } as const;
    folds = reduceFolds(folds, edit);
    expect(folds.expanded.has(cart)).toBe(true);
    folds = reduceFolds(folds, { type: "toggle", id: cart });
    expect(reduceFolds(folds, edit)).toBe(folds);
    expect(folds.expanded.has(cart)).toBe(false);
    expect(reduceFolds(folds, { ...edit, revision: "edit-2" }).expanded.has(cart)).toBe(true);
  });

  it("observes added and modified methods, but not a read or unrelated events", () => {
    const recorded = readFileSync(new URL("fixtures/events/handwritten-level1.jsonl", root), "utf8")
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as ServerEvent)
      .filter((event) => event.seq <= 14)
      .reduce(applyEvent, createInitialState());
    const change = recorded.changes["change-1"];
    if (!change) throw new Error("Missing fixture change");
    const changed = {
      ...recorded,
      changes: {
        ...recorded.changes,
        "change-1": { ...change, graph: { ...change.graph, nodes_modified: [add] } },
      },
    };
    let folds = observeEdits(initialFolds(), changed);
    expect(folds.expanded.has(cart)).toBe(true);
    folds = reduceFolds(folds, { type: "toggle", id: cart });
    expect(observeEdits(folds, { ...changed, lastSeq: 999 }).expanded.has(cart)).toBe(false);
    const method = graph.nodes.find((n) => n.id === add);
    if (!method) throw new Error("Missing method");
    const added = {
      ...changed,
      changes: {
        "change-1": {
          ...change,
          graph: {
            ...change.graph,
            nodes_modified: [],
            nodes_added: [{ ...method, id: `${cart}.new` }],
          },
        },
      },
    };
    expect(observeEdits(folds, added).expanded.has(cart)).toBe(true);
    expect(observeEdits(initialFolds(), state).expanded.size).toBe(0);
  });
});

describe("fold projection", () => {
  it("hides methods and represents the class as one row with its method count", () => {
    const folded = foldElements(full, new Set());
    expect(folded.visible.get(add)).toBe(cart);
    expect(folded.elements.some((e) => e.data.id === add)).toBe(false);
    expect(folded.elements.find((e) => e.data.id === cart)?.data.count).toBe("8 methods");
    const endpoints = new Set(
      folded.elements.filter((e) => e.group === "nodes").map((e) => e.data.id),
    );
    for (const edge of folded.elements.filter((e) => e.group === "edges")) {
      expect(endpoints.has(edge.data.source ?? "")).toBe(true);
      expect(endpoints.has(edge.data.target ?? "")).toBe(true);
    }
  });

  it("is a pure deterministic function of the preset and fold state", () => {
    const original = structuredClone(full);
    const folded = foldElements(full, new Set());
    foldElements(full, allOpen);
    expect(foldElements(full, new Set())).toEqual(folded);
    expect(full).toEqual(original);
    const open = foldElements(full, allOpen);
    for (const node of full.filter((e) => e.group === "nodes"))
      expect(open.elements.find((e) => e.data.id === node.data.id)?.position).toEqual(
        node.position,
      );
  });

  it("only shifts later siblings and modules in the same column", () => {
    const before = foldElements(full, new Set());
    const after = foldElements(full, new Set([cart]));
    const positions = (projection: typeof before) =>
      new Map(
        projection.elements.filter((e) => e.group === "nodes").map((e) => [e.data.id, e.position]),
      );
    const a = positions(before);
    const b = positions(after);
    // Inventory follows Cart in the preset's column. Pricing belongs to another column.
    expect(
      (b.get("shop.inventory:_check_quantity")?.y ?? 0) -
        (a.get("shop.inventory:_check_quantity")?.y ?? 0),
    ).toBeGreaterThan(300);
    expect(b.get("shop.pricing:compute_price")).toEqual(a.get("shop.pricing:compute_price"));
    for (const [id, point] of a) expect(b.get(id)?.x, id).toBe(point?.x);
  });

  it("aggregates read badges and nearest cone distance without unfolding", () => {
    const marked = full.map((e) =>
      e.data.id === add
        ? {
            ...e,
            data: {
              ...e.data,
              distance: 2,
              marks: {
                word: "read" as const,
                slot: "B" as const,
                badges: [{ slot: "B" as const, filled: false }],
              },
            },
            classes: `${e.classes} cone focus-B-read marked`,
          }
        : e,
    );
    const second = `${cart}.total`;
    const elements = marked.map((e) =>
      e.data.id === second
        ? { ...e, data: { ...e.data, distance: 1 }, classes: `${e.classes} cone` }
        : e,
    );
    const projected = foldElements(elements, new Set());
    const row = projected.elements.find((e) => e.data.id === cart);
    expect(row?.data.distance).toBe(1);
    expect(row?.data.marks?.badges).toEqual([{ slot: "B", filled: false }]);
    expect(row?.classes).toContain("folded");
    expect(row?.classes).toContain("focus-B-read");
  });

  it("merges edges to methods of one folded class into one row edge", () => {
    const extra = ["add", "remove"].map((name): (typeof full)[number] => ({
      group: "edges",
      data: {
        id: name,
        kind: "calls",
        source: "shop.pricing:compute_price",
        target: `${cart}.${name}`,
      },
      classes: "calls exact",
    }));
    const projected = foldElements([...full, ...extra], new Set());
    expect(
      projected.elements.filter(
        (e) =>
          e.group === "edges" &&
          e.data.source === "shop.pricing:compute_price" &&
          e.data.target === cart,
      ),
    ).toHaveLength(1);
  });
});
