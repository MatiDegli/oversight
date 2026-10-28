import { readFileSync } from "node:fs";

import type { GraphSnapshot, ServerEvent } from "@contracts";
import cytoscape from "cytoscape";
import { afterEach, describe, expect, it, vi } from "vitest";

import { applyEvent, createInitialState } from "../state";
import { themes } from "../theme";

import { graphElements } from "./elements";
import { foldController } from "./foldController";
import { cacheLayers } from "./layerCache";
import * as layers from "./layers";
import { setScratch } from "./scratch";
import { showSelection } from "./selection";
import { graphStyles } from "./styles";
import { writeData } from "./writes";

const root = new URL("../../../", import.meta.url);
const graph = JSON.parse(
  readFileSync(new URL("fixtures/graph/demo-repo.json", root), "utf8"),
) as GraphSnapshot;
graph.positions = JSON.parse(
  readFileSync(new URL("fixtures/layouts/demo-repo.json", root), "utf8"),
) as GraphSnapshot["positions"];
const started = JSON.parse(
  readFileSync(new URL("fixtures/events/handwritten-level1.jsonl", root), "utf8").split("\n")[0] ??
    "",
) as ServerEvent;
const state = { ...applyEvent(createInitialState(), started), graph };
const tick: ServerEvent = {
  seq: state.lastSeq + 1,
  ts: "2026-10-28T12:00:00.000Z",
  type: "usage.tick",
  payload: {
    role: null,
    change_id: null,
    plan_id: null,
    agent_id: "agent-1",
    model: "fake",
    input_tokens: 100,
    output_tokens: 10,
    cached_tokens: null,
    cost_usd: 0.1,
    spent_usd: 0.1,
    budget_usd: 10,
    brake: "ok",
  },
};
const graphs: cytoscape.Core[] = [];
function create() {
  const cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(themes.dark) });
  graphs.push(cy);
  return cy;
}
afterEach(() => {
  graphs.splice(0).forEach((cy) => {
    cy.destroy();
  });
  vi.restoreAllMocks();
});

describe("incremental graph presentation", () => {
  for (const detail of ["Modules", "Functions", "Details"] as const) {
    it.each([false, true])(
      `restyles nothing for a usage tick at ${detail}, held selection: %s`,
      (held) => {
        const cy = create();
        const view = foldController(cy);
        view.render(state, graphElements(state));
        view.detail(detail);
        view.render(state, graphElements(state));
        const selected = held ? "shop.cart:Cart.add" : null;
        showSelection(cy, selected);
        const before = cy.elements(":selected").map((node) => node.id());
        const touching = cy.edges(".touching").map((edge) => edge.id());
        const headers = vi.spyOn(layers, "headerLayers");
        const marks = vi.spyOn(layers, "marksLayer");
        const restyled: string[] = [];
        cy.on("style", () => {
          restyled.push("element");
        });
        const next = applyEvent(state, tick);
        expect(next.usage).toEqual(tick.payload);
        view.render(next, graphElements(next));
        showSelection(cy, selected);
        expect(headers).not.toHaveBeenCalled();
        expect(marks).not.toHaveBeenCalled();
        expect(restyled).toEqual([]);
        expect(cy.elements(":selected").map((node) => node.id())).toEqual(before);
        expect(cy.edges(".touching").map((edge) => edge.id())).toEqual(touching);
      },
    );
  }

  it("sets only changed and cleared keys in one data write", () => {
    const cy = create();
    const node = cy.add({ data: { id: "row", w: 50, marks: { word: "read" }, title: "old" } });
    const data = vi.spyOn(node, "data");
    writeData(node, { id: "row", w: 50, title: "new" }, ["marks"]);
    expect(data).toHaveBeenCalledTimes(2);
    expect(data).toHaveBeenNthCalledWith(2, { title: "new", marks: undefined });
    expect(node.data("marks")).toBeUndefined();
  });

  it("shares layer work and invalidates on data, classes and measured frame size", () => {
    const cy = create();
    const node = cy.add({ data: { id: "frame" } });
    const width = vi.spyOn(node, "outerWidth").mockReturnValue(100);
    const compute = vi.fn(() => []);
    const cached = cacheLayers(compute, true);
    for (let property = 0; property < 9; property++) cached(node);
    expect(compute).toHaveBeenCalledTimes(1);
    node.data("title", "Changed");
    cached(node);
    node.addClass("folded");
    cached(node);
    width.mockReturnValue(200);
    cached(node);
    cached(node);
    expect(compute).toHaveBeenCalledTimes(4);
  });

  it("writes readable view scratch without restyling", () => {
    const cy = create();
    cy.add({ data: { id: "row" } });
    const style = vi.fn();
    cy.on("style", style);
    setScratch(cy, "fullHub", "row");
    expect(cy.scratch("fullHub")).toBe("row");
    expect(style).not.toHaveBeenCalled();
  });
});
