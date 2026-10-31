import type { ImpactCone } from "@contracts";
import type { Core } from "cytoscape";
import cytoscape from "cytoscape";
import { afterEach, describe, expect, it } from "vitest";

import type { State } from "../state";
import { applyEvent } from "../state";

import { graphElements } from "./elements";
import { imageSource } from "./layerImages";
import { showSelection } from "./selection";
import { graphStyles } from "./styles";
import { updateGraph } from "./updateGraph";

import {
  a,
  agents,
  event,
  initial,
  node,
  nodes,
  render,
  rgb,
  theme,
  updated,
} from "./graph.support";

let cy: Core | undefined;
afterEach(() => {
  cy?.destroy();
});

describe("graph mapping", () => {
  describe("frames an agent touches or leaves empty", () => {
    const touch = (state: State, ids: string[], mode: "read" | "edit" = "read") =>
      applyEvent(
        state,
        event(state.lastSeq + 1, "agent.focus", {
          agent_id: a.agent_id,
          mode,
          node_ids: ids,
          ranges: [],
        }),
      );
    const images = (element: { style: (name: string) => unknown }) =>
      String(element.style("background-image"))
        .split(" ")
        .map((image) => decodeURIComponent(imageSource(image)));

    it("says the word and letter of an agent in a frame's header, in the count's place", () => {
      const frame = render(touch(initial(), ["billing"], "edit")).getElementById("billing");
      expect(frame.hasClass("marked")).toBe(true);
      const [title, cluster] = images(frame);
      expect(title).toContain(">billing.py</text>");
      expect(cluster).toContain(">editing</text>");
      expect(cluster).toContain(`fill="${theme["agent-a-text"]}"`);
      expect(cluster).toContain(">A</text>");
      expect(cluster).not.toContain("functions");
      // A class frame inside it says it too, and the untouched frames keep their counts.
      const bill = render(touch(initial(), ["billing:Bill"])).getElementById("billing:Bill");
      expect(images(bill)[1]).toContain(">read</text>");
      expect(images(render(initial()).getElementById("billing:Bill"))[1]).toContain(
        ">1 method</text>",
      );
    });

    it("sizes a touched frame's title room by the frame's outer width, padding included", () => {
      // One child, so the frame is as narrow as a frame gets: its padding is a large share of it.
      const state = initial();
      if (!state.graph) throw new Error("Missing graph");
      state.graph = {
        ...state.graph,
        nodes: [...nodes, node("pkg", "module"), node("pkg:f", "function", "pkg")],
        edges: [],
      };
      const frame = render(touch(state, ["pkg"], "edit")).getElementById("pkg");
      const [title, cluster] = String(frame.style("background-width"))
        .split(" ")
        .map((width) => Number.parseFloat(width));
      const pad = Number.parseFloat(theme["frame-title-inset"]);
      const gap = Number.parseFloat(theme["node-word-gap"]);
      // The padding is in outerWidth() but not in width(): the room is what the outer width leaves.
      expect(frame.outerWidth()).toBeGreaterThan(frame.width());
      expect(cluster).toBeGreaterThan(0);
      expect(title).toBeGreaterThan(0);
      expect(title).toBeLessThan(240);
      // Within a pixel or two: the mapper reads the width as the frame is first sized.
      const room = frame.outerWidth() - 2 * pad - (cluster ?? 0) - gap;
      expect(Math.abs((title ?? 0) - room)).toBeLessThan(3);
    });

    it("gives an empty module its file name and count, stacked, instead of a bare name", () => {
      const state = initial();
      if (!state.graph) throw new Error("Missing graph");
      state.graph = { ...state.graph, nodes: [...nodes, node("pkg", "module")], edges: [] };
      const empty = render(state).getElementById("pkg");
      expect(empty.style("label")).toBe("");
      const [title, count] = images(empty);
      expect(title).toContain(">billing.py</text>");
      expect(count).toContain(">0 functions</text>");
      // Touched, the state takes the count's row.
      const touched = render(touch(state, ["pkg"])).getElementById("pkg");
      const [, second] = images(touched);
      expect(second).toContain(">read</text>");
      expect(second).not.toContain("0 functions");
    });

    describe("when what a node says changes under it", () => {
      const cone: ImpactCone = {
        seeds: [],
        entries: [{ node_id: "billing:fee", distance: 1 }],
        edges_followed: "exact",
        max_depth: 3,
        max_nodes: 20,
        omitted_by_depth: 0,
        omitted_by_limit: 0,
        tests_reaching: 0,
      };
      const open = () => {
        cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(theme) });
        return cy;
      };

      it("goes back to the count and a bare box when the focus is gone", () => {
        const state = initial();
        const graph = open();
        updateGraph(graph, graphElements(touch(state, ["billing", "billing:fee"])));
        expect(images(graph.getElementById("billing"))[1]).toContain(">read</text>");
        expect(images(graph.getElementById("billing:fee"))[0]).toContain(">read</text>");
        updateGraph(graph, graphElements(state));
        const frame = graph.getElementById("billing");
        expect(frame.data("marks")).toBeUndefined();
        expect(images(frame)[1]).toContain(">2 functions</text>");
        expect(graph.getElementById("billing:fee").data("marks")).toBeUndefined();
        expect(graph.getElementById("billing:fee").style("background-image")).toBe("none");
      });

      it("leaves the read history off the cone in review, and drops its distance in watch", () => {
        const state = touch(initial(), ["billing", "billing:fee"]);
        const graph = open();
        updateGraph(graph, graphElements(state));
        expect(graph.getElementById("billing:fee").data("marks")).toBeDefined();
        updateGraph(graph, graphElements(state, false, new Set(["billing", "billing:fee"]), cone));
        const fee = graph.getElementById("billing:fee");
        expect(fee.hasClass("cone") && !fee.hasClass("marked")).toBe(true);
        expect(fee.data("marks")).toBeUndefined();
        expect(graph.getElementById("billing").data("marks")).toBeUndefined();
        // Only the distance badge is left on the node, with no word under its name.
        expect(images(fee)).toHaveLength(1);
        expect(images(fee)[0]).toContain(">1</text>");
        expect(images(fee)[0]).not.toContain(">read</text>");
        expect(images(graph.getElementById("billing"))[1]).toContain("functions</text>");
        // Back in watch, the node is no longer in the cone and says nothing of a distance.
        updateGraph(graph, graphElements(state));
        expect(fee.data("distance")).toBeUndefined();
        expect(fee.data("marks")).toBeDefined();
      });
    });

    it("keeps an empty module's header and adds the distance badge in the cone", () => {
      const state = initial();
      if (!state.graph) throw new Error("Missing graph");
      state.graph = { ...state.graph, nodes: [...nodes, node("pkg", "module")], edges: [] };
      cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(theme) });
      const cone: ImpactCone = {
        seeds: [],
        entries: [{ node_id: "pkg", distance: 2 }],
        edges_followed: "exact",
        max_depth: 3,
        max_nodes: 20,
        omitted_by_depth: 0,
        omitted_by_limit: 0,
        tests_reaching: 0,
      };
      updateGraph(cy, graphElements(state, false, new Set(["pkg"]), cone));
      const [title, count, badge] = images(cy.getElementById("pkg"));
      expect(title).toContain(">billing.py</text>");
      expect(count).toContain(">0 functions</text>");
      expect(badge).toContain(">2</text>");
    });
  });

  it.each(["A", "B"] as const)("prioritizes %s editing over the other agent reading", (slot) => {
    const other = slot === "A" ? "B" : "A";
    const target = render(initial()).getElementById("billing:fee");
    target.classes(`function focus-${slot}-edit focus-${other}-read focus-both`);
    expect(target.style("border-color")).toBe(rgb(theme[agents[slot]]));
    expect(Number(target.style("background-opacity"))).toBe(theme[`${agents[slot]}-node`].alpha);
    expect(target.style("background-color")).toBe(rgb(theme[`${agents[slot]}-node`].color));
  });

  it("hides test nodes and incident edges by default, with an explicit opt-in", () => {
    const state = initial();
    if (!state.graph) throw new Error("Missing test graph");
    state.graph = {
      ...state.graph,
      nodes: [
        ...nodes,
        { ...node("tests", "module"), is_test: true },
        { ...node("tests:fee", "function", "tests"), is_test: true },
      ],
      edges: [
        ...state.graph.edges,
        {
          id: "test-call",
          src: "tests:fee",
          dst: "billing:fee",
          kind: "calls",
          confidence: "exact",
          reason: null,
        },
      ],
    };
    const graph = render(state);
    expect(graph.getElementById("tests").empty()).toBe(true);
    expect(graph.getElementById("tests:fee").empty()).toBe(true);
    expect(graph.getElementById("test-call").empty()).toBe(true);
    updateGraph(graph, graphElements(state, true));
    expect(graph.getElementById("tests:fee").parent().first().id()).toBe("tests");
    expect(graph.getElementById("test-call").nonempty()).toBe(true);
  });
});

describe("edges in watch: draw order and the quiet of edges nobody is at", () => {
  /** The fixture plus an edge between two functions that no agent touches. */
  function withSpareEdge(state: State): State {
    if (!state.graph) throw new Error("Missing graph");
    return {
      ...state,
      graph: {
        ...state.graph,
        nodes: [
          ...state.graph.nodes,
          node("billing:x", "function", "billing"),
          node("billing:y", "function", "billing"),
        ],
        edges: [
          ...state.graph.edges,
          {
            id: "xy",
            kind: "calls",
            src: "billing:x",
            dst: "billing:y",
            confidence: "exact",
            reason: null,
          },
        ],
      },
    };
  }
  const read = (state: State, ids: string[]) =>
    applyEvent(
      state,
      event(state.lastSeq + 1, "agent.focus", {
        agent_id: a.agent_id,
        mode: "read",
        node_ids: ids,
        ranges: [],
      }),
    );
  const quiet = (graph: Core) =>
    Object.fromEntries(graph.edges().map((edge) => [edge.id(), edge.hasClass("quiet")]));

  it("draws frames, then edges, then leaf nodes, so a line never crosses a node's name", () => {
    const graph = render(initial());
    const z = (id: string) => Number(graph.getElementById(id).style("z-index"));
    const frames = [z("billing"), z("billing:Bill")];
    const edges = graph
      .edges()
      .not(".possible")
      .map((edge) => Number(edge.style("z-index")));
    const leaves = [z("billing:fee"), z("billing:Bill.total")];
    expect(Math.max(...frames)).toBeLessThan(Math.min(...edges));
    expect(Math.max(...edges)).toBeLessThan(Math.min(...leaves));
    for (const element of [...graph.nodes(), ...graph.edges()])
      expect(element.style("z-index-compare")).toBe("manual");
  });

  it("keeps every edge quiet while no agent is anywhere near it, at the token's opacity", () => {
    const graph = render(withSpareEdge(initial()));
    expect(Object.values(quiet(graph)).every(Boolean)).toBe(true);
    for (const edge of graph.edges())
      expect(edge.numericStyle("opacity")).toBe(theme["quiet-edge"]);
  });

  it("brings back the edges at a node an agent reads, and leaves the rest quiet", () => {
    const graph = render(withSpareEdge(read(initial(), ["billing:fee"])));
    expect(quiet(graph)).toEqual({
      "edge-calls": false,
      "edge-imports": false,
      "edge-inherits": false,
      xy: true,
    });
    expect(graph.getElementById("edge-calls").numericStyle("opacity")).toBe(1);
    expect(graph.getElementById("xy").numericStyle("opacity")).toBe(theme["quiet-edge"]);
  });

  it("never quiets an edge a change adds, and a changed node's edges stay clear", () => {
    const graph = render(withSpareEdge(updated()));
    expect(quiet(graph)["added-call"]).toBe(false);
    expect(quiet(graph).xy).toBe(true);
    expect(quiet(graph)["edge-calls"]).toBe(false);
  });

  it("quiets nothing in review, where lit and dimmed already say which edges matter", () => {
    cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(theme) });
    updateGraph(cy, graphElements(withSpareEdge(initial()), false, new Set(["billing:fee"])));
    expect(Object.values(quiet(cy)).some(Boolean)).toBe(false);
  });

  it("brings back the edges at the node the person selects, and quiets them again after", () => {
    const graph = render(withSpareEdge(initial()));
    showSelection(graph, "billing:x");
    expect(graph.getElementById("xy").hasClass("touching")).toBe(true);
    expect(graph.getElementById("xy").numericStyle("opacity")).toBe(1);
    expect(graph.getElementById("edge-calls").numericStyle("opacity")).toBe(theme["quiet-edge"]);
    showSelection(graph, "billing:fee");
    expect(graph.getElementById("xy").numericStyle("opacity")).toBe(theme["quiet-edge"]);
    expect(graph.getElementById("edge-calls").numericStyle("opacity")).toBe(1);
    showSelection(graph, null);
    expect(graph.edges().filter(".touching")).toHaveLength(0);
  });
});

describe("review dimming", () => {
  it("dims context including compound frames while retaining the lit children's opacity", () => {
    const state = initial();
    const classes = (lit: ReadonlySet<string> | null) =>
      Object.fromEntries(
        graphElements(state, false, lit).map((element) => [element.data.id, element.classes]),
      );
    expect(Object.values(classes(null)).some((names) => names.includes("dimmed"))).toBe(false);
    const lit = classes(new Set(["billing:Bill.total"]));
    for (const id of ["billing", "billing:Bill"]) expect(lit[id]).toContain("dimmed");
    expect(lit["billing:Bill.total"]).not.toContain("dimmed");
    expect(lit["billing:fee"]).toContain("dimmed");
    for (const kind of ["calls", "imports", "inherits"])
      expect(lit[`edge-${kind}`]).toContain("dimmed");
    const both = classes(new Set(["billing:Bill.total", "billing:fee"]));
    expect(both["edge-calls"]).not.toContain("dimmed");

    cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(theme) });
    updateGraph(cy, graphElements(state, false, new Set(["billing:Bill.total"])));
    expect(cy.getElementById("billing:fee").numericStyle("opacity")).toBe(theme["dim-node"]);
    expect(cy.getElementById("billing:Bill.total").numericStyle("opacity")).toBe(1);
    expect(cy.getElementById("billing").numericStyle("opacity")).toBe(1);
  });
});
