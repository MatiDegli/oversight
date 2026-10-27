import { readFileSync } from "node:fs";

import type { ImpactCone, ServerEvent } from "@contracts";
import cytoscape from "cytoscape";
import { afterEach, describe, expect, it, vi } from "vitest";

import { applyEvent, createInitialState } from "../state";
import { themes } from "../theme";
import { frameCamera } from "./camera";
import { changeZoom } from "./changeFrame";
import { graphElements } from "./elements";
import { graphStyles } from "./styles";
import { updateGraph } from "./updateGraph";

const theme = themes.dark;
const state = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent)
  .filter((event) => event.seq <= 20)
  .reduce(applyEvent, createInitialState());
const change = state.changes["change-1"];
if (!change?.proposal) throw new Error("Missing recorded proposal");
const cone = change.proposal.impact;
const changed = "shop.pricing:compute_price";
const lit = new Set([changed, ...cone.entries.map((entry) => entry.node_id)]);
const caller = "shop.cart:Cart.subtotals";
const unrelated = "shop.cart:Cart.add";
const graphs: cytoscape.Core[] = [];
afterEach(() => {
  for (const cy of graphs.splice(0)) cy.destroy();
});

function render(impact: ImpactCone | null = cone, withPossible = false) {
  const cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(theme) });
  graphs.push(cy);
  const graph = state.graph;
  const edge = graph?.edges.find((edge) => lit.has(edge.src) && lit.has(edge.dst));
  if (withPossible && (!graph || !edge)) throw new Error("Missing lit edge");
  const rendered =
    withPossible && graph && edge
      ? {
          ...state,
          graph: {
            ...graph,
            edges: [
              ...graph.edges,
              {
                ...edge,
                id: "inside-possible",
                confidence: "possible" as const,
                reason: "unknown_receiver" as const,
              },
            ],
          },
        }
      : state;
  updateGraph(cy, graphElements(rendered, false, lit, impact));
  return cy;
}

describe("review's impact graph", () => {
  it("frames the real demo cone, with marks from final boxes", () => {
    const cy = render();
    vi.spyOn(cy, "width").mockReturnValue(928);
    vi.spyOn(cy, "height").mockReturnValue(1028);
    const before = Object.fromEntries(cy.nodes().map((node) => [node.id(), node.position()]));
    const camera = frameCamera(cy, [changed], true, {
      coneIds: cone.entries.map((entry) => entry.node_id),
    });
    expect(camera?.zoom).toBeGreaterThanOrEqual(changeZoom().min);
    expect(camera?.zoom).toBeLessThanOrEqual(1);
    expect(camera?.framed.has(changed)).toBe(true);
    expect(camera?.framed.has("shop.pricing:price_breaks")).toBe(true);
    expect([...(camera?.framed ?? [])].every((id) => lit.has(id))).toBe(true);
    for (const entry of cone.entries) {
      const node = cy.getElementById(entry.node_id);
      const box = node.renderedBoundingBox({ includeLabels: true, includeOverlays: false });
      const whollyVisible =
        box.x1 >= -1e-6 && box.x2 <= 928 + 1e-6 && box.y1 >= -1e-6 && box.y2 <= 1028 + 1e-6;
      expect(camera?.outOfView.has(entry.node_id), entry.node_id).toBe(!whollyVisible);
    }
    expect(Object.fromEntries(cy.nodes().map((node) => [node.id(), node.position()]))).toEqual(
      before,
    );
  });

  it("keeps the handwritten cone lit with badges only on its entries, preserving the change", () => {
    const cy = render();
    expect(cy.nodes(".cone").map((node) => node.id())).toEqual(
      expect.arrayContaining(cone.entries.map((entry) => entry.node_id)),
    );
    expect(cy.nodes(".cone")).toHaveLength(cone.entries.length);
    for (const entry of cone.entries) {
      const node = cy.getElementById(entry.node_id);
      expect(node.hasClass("dimmed")).toBe(false);
      expect(node.effectiveOpacity()).toBe(1);
      expect(node.data("distance")).toBe(entry.distance);
      expect(node.numericStyle("background-width")).toEqual([
        Number.parseFloat(theme["distance-badge-size"]),
      ]);
      const svg = decodeURIComponent(String(node.style("background-image")));
      expect(svg).toContain(`fill="${theme.text}"`);
      expect(svg).toContain(`fill="${theme.page}"`);
      expect(svg).toContain(`>${String(entry.distance)}</text>`);
      expect(svg).not.toContain(theme["agent-a"]);
    }
    const seed = cy.getElementById(changed);
    expect(seed.hasClass("pending-A")).toBe(true);
    expect(seed.hasClass("cone")).toBe(false);
    expect(seed.data("distance")).toBeUndefined();
    expect(seed.style("border-style")).toBe("dashed");
    expect(seed.effectiveOpacity()).toBe(1);
    expect(cy.getElementById(unrelated).hasClass("cone")).toBe(false);
    expect(render(null).nodes(".cone")).toHaveLength(0);
  });

  it("dims functions, methods and class frames to 16%, module frames to 60%, without multiplying child opacity", () => {
    const cy = render();
    for (const node of cy.nodes()) {
      if (lit.has(node.id())) continue;
      expect(node.hasClass("dimmed"), node.id()).toBe(true);
      const dim = node.hasClass("module") ? theme["dim-module"] : theme["dim-node"];
      if (node.isParent()) {
        expect(node.numericStyle("opacity"), node.id()).toBe(1);
        expect(node.numericStyle("border-opacity"), node.id()).toBe(dim);
        expect(node.numericStyle("text-opacity"), node.id()).toBe(dim);
        expect(node.numericStyle("background-opacity"), node.id()).toBeCloseTo(
          dim * theme["module-opacity"],
        );
      } else {
        expect(node.effectiveOpacity(), node.id()).toBe(dim);
      }
    }
    const classNode = cy.getElementById("shop.cart:Cart");
    expect(classNode.hasClass("dimmed")).toBe(true);
    expect(cy.getElementById(caller).effectiveOpacity()).toBe(1);
    expect(cy.getElementById(unrelated).effectiveOpacity()).toBe(theme["dim-node"]);
  });

  it("keeps inside-cone possible edges thin, neutral and solid while lighting exact edges", () => {
    const cy = render(cone, true);
    let litEdges = 0;
    let possibleEdges = 0;
    for (const edge of cy.edges()) {
      const inside = lit.has(edge.source().id()) && lit.has(edge.target().id());
      if (inside && edge.hasClass("exact")) {
        litEdges++;
        expect(edge.hasClass("lit")).toBe(true);
        expect(edge.style("width")).toBe(theme["lit-edge-width"]);
        expect(edge.style("line-color")).toBe(edge.style("target-arrow-color"));
        expect(edge.numericStyle("opacity")).toBe(1);
      } else if (inside && edge.hasClass("possible")) {
        possibleEdges++;
        expect(edge.hasClass("lit")).toBe(false);
        expect(edge.hasClass("dimmed")).toBe(false);
        expect(edge.numericStyle("opacity")).toBe(1);
        expect(edge.style("width")).toBe(theme["possible-edge-width"]);
        expect(edge.style("line-style")).toBe("solid");
        const [r, g, b] = [1, 3, 5].map((offset) =>
          parseInt(theme["edge-possible"].slice(offset, offset + 2), 16),
        );
        expect(edge.style("line-color")).toBe(`rgb(${String(r)},${String(g)},${String(b)})`);
        expect(edge.style("target-arrow-color")).toBe(edge.style("line-color"));
      } else if (!inside) {
        expect(edge.hasClass("dimmed")).toBe(true);
        expect(edge.numericStyle("opacity")).toBe(theme["dim-node"]);
      }
    }
    expect(litEdges).toBeGreaterThan(0);
    expect(possibleEdges).toBeGreaterThan(0);
    const exact = cy.edges(".lit").first();
    const [r, g, b] = [1, 3, 5].map((offset) =>
      parseInt(theme["lit-edge"].slice(offset, offset + 2), 16),
    );
    expect(exact.style("line-color")).toBe(`rgb(${String(r)},${String(g)},${String(b)})`);
  });

  it("drops read marks in review, keeping the cone neutral and reads outside it dimmed", () => {
    const agent = state.agents["agent-1"];
    if (!agent) throw new Error("Missing agent");
    const focused = {
      ...state,
      agents: { ...state.agents, "agent-1": { ...agent, state: "working" as const } },
      focus: {
        "agent-1": {
          agent_id: "agent-1",
          mode: "read" as const,
          node_ids: [caller, unrelated],
          ranges: [],
        },
      },
    };
    const watch = graphElements(focused);
    expect(watch.find((node) => node.data.id === caller)?.classes).toContain("focus-A-read");
    const review = graphElements(focused, false, lit, cone);
    for (const id of [caller, unrelated]) {
      const node = review.find((node) => node.data.id === id);
      expect(node?.classes).not.toContain("focus-A-read");
      expect(node?.data.label).not.toContain("A read");
      expect(node?.classes.includes("dimmed")).toBe(id === unrelated);
    }
  });

  it("removes badge styling when review closes, without altering positions", () => {
    const cy = render();
    const before = cy.nodes().map((node) => node.position());
    updateGraph(cy, graphElements(state));
    expect(cy.nodes(".cone")).toHaveLength(0);
    expect(cy.nodes(".dimmed")).toHaveLength(0);
    expect(cy.getElementById(caller).style("background-image")).toBe("none");
    expect(cy.nodes().map((node) => node.position())).toEqual(before);
  });
});
