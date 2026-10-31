import type { ImpactCone } from "@contracts";
import type { Core } from "cytoscape";
import cytoscape from "cytoscape";
import { afterEach, describe, expect, it } from "vitest";

import { applyEvent } from "../state";

import { graphElements } from "./elements";
import { imageSource } from "./layerImages";
import { EDGE_CURVE_STYLE, graphStyles } from "./styles";
import { updateGraph } from "./updateGraph";

import { a, agents, b, event, initial, node, render, rgb, theme, updated } from "./graph.support";

let cy: Core | undefined;
afterEach(() => {
  cy?.destroy();
});

describe("graph mapping", () => {
  it("reads colors, type and alpha from the supplied theme", () => {
    const custom = {
      ...theme,
      card: theme.raised,
      "text-font": theme["code-font"],
      "node-name-size": theme["title-size"],
      "node-opacity": theme["module-opacity"],
      "agent-b-node": { color: theme.field, alpha: theme["dim-module"] },
    };
    cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(custom) });
    updateGraph(cy, graphElements(initial()));
    const target = cy.getElementById("billing:fee");
    expect(target.style("background-color")).toBe(rgb(custom.card));
    expect(target.style("font-family")).toBe(custom["text-font"]);
    expect(target.style("font-size")).toBe(custom["node-name-size"]);
    expect(Number(target.style("background-opacity"))).toBe(custom["node-opacity"]);
    target.classes("function focus-B-edit");
    expect(target.style("background-color")).toBe(rgb(custom["agent-b-node"].color));
    expect(Number(target.style("background-opacity"))).toBe(custom["agent-b-node"].alpha);
  });

  it("uses graph tokens for module surfaces, dimming and lit edges, preserving possible edges", () => {
    const graph = render(initial());
    const module = graph.getElementById("billing");
    expect(module.style("background-color")).toBe(rgb(theme.panel));
    expect(Number(module.style("background-opacity"))).toBe(theme["module-opacity"]);
    expect(module.style("color")).toBe(rgb(theme["module-label"]));
    expect(module.style("font-family")).toBe(theme["text-font"]);
    module.addClass("dimmed");
    // A frame dims through its own paint, never `opacity`: that would multiply into its children.
    expect(Number(module.style("opacity"))).toBe(1);
    for (const property of ["border-opacity", "text-opacity"])
      expect(Number(module.style(property))).toBe(theme["dim-module"]);
    expect(Number(module.style("background-opacity"))).toBeCloseTo(
      theme["module-opacity"] * theme["dim-module"],
    );
    const target = graph.getElementById("billing:fee").addClass("dimmed");
    expect(Number(target.style("opacity"))).toBe(theme["dim-node"]);
    const exact = graph.getElementById("edge-imports").addClass("dimmed");
    expect(Number(exact.style("opacity"))).toBe(theme["dim-node"]);
    exact.removeClass("dimmed").addClass("lit");
    expect(exact.style("line-color")).toBe(rgb(theme["lit-edge"]));
    expect(exact.style("target-arrow-color")).toBe(rgb(theme["lit-edge"]));
    expect(exact.style("width")).toBe(theme["lit-edge-width"]);
    const possible = graph.getElementById("edge-calls").addClass("pending-B lit");
    expect(possible.style("line-color")).toBe(rgb(theme["edge-possible"]));
    expect(possible.style("target-arrow-color")).toBe(rgb(theme["edge-possible"]));
    expect(possible.style("width")).toBe(theme["possible-edge-width"]);
    expect(possible.style("line-style")).toBe("solid");
    expect(possible.style("opacity")).toBe(exact.style("opacity"));
    expect(Number(possible.style("opacity"))).toBeGreaterThan(theme["dim-node"]);
  });

  it("dims to the token's opacity however deep a node sits in dimmed frames", () => {
    const graph = render(initial());
    const [module, frame, method, leaf] = [
      "billing",
      "billing:Bill",
      "billing:Bill.total",
      "billing:fee",
    ].map((id) => graph.getElementById(id));
    const effective = (...elements: (typeof module)[]) =>
      elements.map((element) => element?.effectiveOpacity());
    expect(effective(module, frame, method, leaf)).toEqual([1, 1, 1, 1]);
    // Everything dimmed, as review dims what the cone does not reach: each node is at the token.
    graph.nodes().addClass("dimmed");
    expect(effective(method, leaf)).toEqual([theme["dim-node"], theme["dim-node"]]);
    // A lit node in a dimmed frame keeps full opacity, and a dimmed one is not dimmed twice.
    method?.removeClass("dimmed");
    expect(effective(method)).toEqual([1]);
    expect(effective(leaf)).toEqual([theme["dim-node"]]);
    // A frame that is no longer dimmed does not lift the dimming of its children either.
    module?.removeClass("dimmed");
    frame?.removeClass("dimmed");
    expect(effective(module, frame)).toEqual([1, 1]);
    leaf?.addClass("dimmed");
    expect(effective(leaf)).toEqual([theme["dim-node"]]);
    // Edges are never inside a frame: their own opacity is the token.
    const edge = graph.getElementById("edge-imports").addClass("dimmed");
    expect(edge.effectiveOpacity()).toBe(theme["dim-node"]);
  });

  it("nests modules, classes, functions and methods only through parent_id", () => {
    const graph = render(initial());
    expect(graph.getElementById("billing:Bill").parent().first().id()).toBe("billing");
    expect(graph.getElementById("billing:Bill.total").parent().first().id()).toBe("billing:Bill");
    expect(graph.getElementById("billing:fee").parent().first().id()).toBe("billing");
    expect(graph.getElementById("billing").parent().empty()).toBe(true);
    expect(graph.nodes().map((n) => n.classes())).toEqual([
      ["module"],
      ["class"],
      ["method"],
      ["function"],
    ]);
    // In watch, no agent touches these, so each edge is quiet.
    expect(graph.edges().map((e) => e.classes())).toEqual([
      ["calls", "possible", "quiet"],
      ["imports", "exact", "quiet"],
      ["inherits", "exact", "quiet"],
    ]);
    expect(graph.getElementById("billing:fee").style("font-family")).toBe(theme["text-font"]);
  });

  it("draws possible edges thinner and grey, always solid, with a curve switch", () => {
    const graph = render(initial());
    const possible = graph.getElementById("edge-calls");
    const exact = graph.getElementById("edge-imports");
    expect(Number.parseFloat(String(possible.style("width")))).toBeLessThan(
      Number.parseFloat(String(exact.style("width"))),
    );
    const luminance = (color: string) => {
      const channels = (color.match(/\d+/g) ?? []).map((v) => {
        const c = Number(v) / 255;
        return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
      });
      const [red = 0, green = 0, blue = 0] = channels;
      return red * 0.2126 + green * 0.7152 + blue * 0.0722;
    };
    const background = luminance(rgb(theme.ground));
    const contrast = (color: string) => (luminance(color) + 0.05) / (background + 0.05);
    expect(contrast(String(exact.style("line-color")))).toBeGreaterThan(
      contrast(String(possible.style("line-color"))),
    );
    expect(contrast(String(possible.style("line-color")))).toBeGreaterThanOrEqual(3);
    expect(exact.style("line-color")).toBe(rgb(theme.edge));
    expect(possible.style("line-color")).toBe(rgb(theme["edge-possible"]));
    expect(possible.style("width")).toBe(theme["possible-edge-width"]);
    expect(exact.style("width")).toBe(theme["exact-edge-width"]);
    expect(possible.style("target-arrow-color")).toBe(possible.style("line-color"));
    for (const edge of graph.edges()) {
      expect(edge.style("line-style")).toBe("solid");
      // The default is taxi: updateGraph routes every call, and a routed edge is drawn in rounded
      // orthogonal segments. An import is not drawn at all, so it is not routed.
      expect(EDGE_CURVE_STYLE).toBe("taxi");
      expect(edge.style("curve-style")).toBe(
        edge.hasClass("possible")
          ? "straight"
          : edge.hasClass("imports")
            ? "bezier"
            : "round-segments",
      );
      expect(edge.style("display")).toBe(edge.is(".imports, .possible") ? "none" : "element");
    }
  });

  it("lights read and edit in each agent's color with letters, including simultaneous focus", () => {
    const state = [
      event(5, "agent.focus", {
        agent_id: a.agent_id,
        mode: "read",
        node_ids: ["billing:fee"],
        ranges: [],
      }),
      event(6, "agent.focus", {
        agent_id: b.agent_id,
        mode: "edit",
        node_ids: ["billing:fee"],
        ranges: [],
      }),
    ].reduce(applyEvent, initial());
    const graph = render(state);
    const lit = graph.getElementById("billing:fee");
    expect(lit.hasClass("focus-A-read")).toBe(true);
    expect(lit.hasClass("focus-B-edit")).toBe(true);
    expect(lit.data("label")).toBe("billing:fee");
    expect(lit.data("marks")).toEqual({
      word: "editing",
      slot: "B",
      badges: [
        { slot: "A", filled: false },
        { slot: "B", filled: true },
      ],
    });
    expect(lit.hasClass("focus-both")).toBe(true);
    expect(lit.style("background-fill")).toBe("solid");
    expect(lit.style("background-color")).toBe(rgb(theme["agent-b-node"].color));
    expect(lit.style("border-color")).toBe(rgb(theme["agent-b"]));
    expect(Number.parseFloat(String(lit.style("pie-1-background-size")))).toBe(0);
    expect(lit.style("border-style")).toBe("dashed");
    updateGraph(
      graph,
      graphElements(applyEvent(state, event(7, "agent.status", { ...a, state: "done" }))),
    );
    expect(lit.hasClass("focus-A-read")).toBe(false);
    expect(lit.hasClass("focus-both")).toBe(false);
    expect(lit.style("background-color")).toBe(rgb(theme["agent-b-node"].color));
    expect(lit.style("background-fill")).toBe("solid");
  });

  it("keeps read fills unchanged and uses the agent node fill and pending border for editing", () => {
    const graph = render(initial());
    const target = graph.getElementById("billing:fee");
    for (const slot of ["A", "B"] as const) {
      const agent = agents[slot];
      const fill = theme[`${agent}-node`];
      target.classes(`function focus-${slot}-read`);
      expect(target.style("background-color")).toBe(rgb(theme.card));
      expect(Number(target.style("background-opacity"))).toBe(theme["node-opacity"]);
      expect(target.style("border-color")).toBe(rgb(theme[agent]));
      expect(target.style("border-width")).toBe(theme["agent-border-width"]);
      expect(target.style("border-style")).toBe("solid");
      target.classes(`function focus-${slot}-edit`);
      expect(target.style("background-color")).toBe(rgb(fill.color));
      expect(Number(target.style("background-opacity"))).toBe(fill.alpha);
      expect(target.style("border-color")).toBe(rgb(theme[agent]));
      expect(target.style("border-width")).toBe(theme["agent-border-width"]);
      expect(target.style("border-style")).toBe("dashed");
      expect(target.style("border-dash-pattern")).toEqual(
        [theme["pending-dash-length"], theme["pending-dash-gap"]].map(Number.parseFloat).join(" "),
      );
      expect(Number.parseFloat(String(target.style("pie-1-background-size")))).toBe(0);
    }
  });

  it("maps semantic marks to classes and to the marks the node draws apart from its name", () => {
    const state = updated();
    state.focus = {
      [a.agent_id]: { agent_id: a.agent_id, mode: "edit", node_ids: ["billing:fee"], ranges: [] },
      [b.agent_id]: { agent_id: b.agent_id, mode: "read", node_ids: ["billing:fee"], ranges: [] },
    };
    const target = graphElements(state).find((e) => e.data.id === "billing:fee");
    if (!target) throw new Error("Missing fee");
    expect(Object.keys(target.data).sort()).toEqual([
      "h",
      "id",
      "kind",
      "label",
      "marks",
      "parent",
      "w",
    ]);
    // A edits and has the change pending (editing outranks changed); B only reads.
    expect(target.data.marks).toEqual({
      word: "editing",
      slot: "A",
      badges: [
        { slot: "A", filled: true },
        { slot: "B", filled: false },
      ],
    });
    expect(target.classes.split(" ")).toEqual(
      expect.arrayContaining([
        "pending",
        "pending-A",
        "focus-A-edit",
        "focus-B-read",
        "focus-both",
      ]),
    );
    expect(target.data.label).toBe("billing:fee");
    const unknown = { ...state, agents: {} };
    const graph = render(unknown);
    const added = graph.getElementById("billing:discount");
    expect(added.hasClass("pending")).toBe(true);
    expect(added.style("border-style")).toBe("dashed");
  });

  it("marks added, modified and removed pending nodes with agent fills and dashed borders", () => {
    const state = updated();
    const before = JSON.stringify(state);
    const graph = render(state);
    for (const id of ["billing:discount", "billing:fee", "billing:Bill.total"]) {
      const pending = graph.getElementById(id);
      expect(pending.hasClass("pending-A")).toBe(true);
      expect(pending.style("border-style")).toBe("dashed");
      expect(pending.style("border-color")).toBe(rgb(theme["agent-a"]));
      const fill = id === "billing:discount" ? theme["agent-a-node-new"] : theme["agent-a-node"];
      expect(pending.style("background-color")).toBe(rgb(fill.color));
      expect(Number(pending.style("background-opacity"))).toBe(fill.alpha);
      // Added is new; modified and removed are changed.
      expect(pending.data("marks")).toEqual({
        word: id === "billing:discount" ? "new" : "changed",
        slot: "A",
        badges: [{ slot: "A", filled: true }],
      });
    }
    expect(graph.getElementById("billing").style("border-style")).toBe("solid");
    expect(graph.getElementById("added-call").nonempty()).toBe(true);
    expect(graph.getElementById("added-call").style("line-style")).toBe("dashed");
    expect(graph.getElementById("added-call").style("line-color")).toBe(rgb(theme["agent-a"]));
    expect(graph.getElementById("edge-imports").nonempty()).toBe(true); // Main is unchanged.
    expect(JSON.stringify(state)).toBe(before);
    const second = {
      ...state,
      changes: Object.fromEntries(
        Object.entries(state.changes).map(([id, change]) => [
          id,
          { ...change, agent_id: b.agent_id },
        ]),
      ),
    };
    updateGraph(graph, graphElements(second));
    expect(graph.getElementById("billing:fee").style("border-color")).toBe(rgb(theme["agent-b"]));
    for (const status of ["approved", "rejected"] as const) {
      const closed = {
        ...state,
        changes: Object.fromEntries(
          Object.entries(state.changes).map(([id, change]) => [id, { ...change, status }]),
        ),
      };
      updateGraph(graph, graphElements(closed));
      expect(graph.nodes(".pending").empty()).toBe(true);
      expect(graph.getElementById("billing:discount").empty()).toBe(true);
    }
  });

  it("keeps pending-both in A's node fill with a dashed A-priority border and both agents named", () => {
    const state = updated();
    const change = state.changes[a.change_id];
    if (!change) throw new Error("Missing pending change");
    state.changes[b.change_id] = { ...change, change_id: b.change_id, agent_id: b.agent_id };
    const graph = render(state);
    const target = graph.getElementById("billing:fee");
    expect(target.hasClass("pending-both")).toBe(true);
    expect(target.hasClass("focus-both")).toBe(false);
    expect(target.style("background-fill")).toBe("solid");
    expect(target.style("background-color")).toBe(rgb(theme["agent-a-node"].color));
    expect(Number(target.style("background-opacity"))).toBe(theme["agent-a-node"].alpha);
    expect(target.style("border-style")).toBe("dashed");
    expect(target.style("border-color")).toBe(rgb(theme["agent-a"]));
    // Both agents changed it: one word, in A's color on a tie, and a filled badge for each.
    expect(target.data("marks")).toEqual({
      word: "changed",
      slot: "A",
      badges: [
        { slot: "A", filled: true },
        { slot: "B", filled: true },
      ],
    });
  });

  it("keeps the whole node name on one line, and the marks apart from it", () => {
    const state = initial();
    const original = state.graph?.nodes.find((n) => n.id === "billing:fee");
    if (!original || !state.graph) throw new Error("Missing fee");
    const source = { ...original, name: "compute_price_with_discounts_and_taxes" };
    state.graph = {
      ...state.graph,
      nodes: state.graph.nodes.map((n) => (n.id === source.id ? source : n)),
    };
    state.focus = {
      [a.agent_id]: { agent_id: a.agent_id, mode: "read", node_ids: [source.id], ranges: [] },
      [b.agent_id]: { agent_id: b.agent_id, mode: "edit", node_ids: [source.id], ranges: [] },
    };
    const target = render(state).getElementById(source.id);
    expect(target.data("label")).toBe(source.name);
    expect(target.data("marks")).toMatchObject({ word: "editing", slot: "B" });
    // The name is cut with an ellipsis where it would meet the marks, never wrapped under them.
    expect(target.style("text-wrap")).toBe("ellipsis");
    const room = Number.parseFloat(String(target.style("text-max-width")));
    const plain = Number.parseFloat(
      String(render(initial()).getElementById("billing:fee").style("text-max-width")),
    );
    expect(plain).toBe(282 - 18 - 12);
    // The word "editing" and two badges take the room the name gives up.
    expect(room).toBeGreaterThan(0);
    expect(room).toBeLessThan(plain - 2 * 26);
  });

  it("gives each frame its title and its count, and counts what a change adds as new", () => {
    const frames = Object.fromEntries(
      graphElements(updated())
        .filter((element) => element.data.title !== undefined)
        .map((element) => [element.data.id, element.data]),
    );
    // A module's title is its file's name; its count is the functions and methods below it.
    expect(frames["billing"]).toMatchObject({ title: "billing.py", count: "3 functions · 1 new" });
    // A class's title is its name (these fixtures name a node by its id).
    expect(frames["billing:Bill"]).toMatchObject({ title: "billing:Bill", count: "1 method" });
    // A leaf carries neither.
    expect(frames["billing:fee"]).toBeUndefined();
    // A module with only classes says so.
    const state = initial();
    if (!state.graph) throw new Error("Missing graph");
    state.graph = {
      ...state.graph,
      nodes: [node("m", "module"), node("m:A", "class", "m"), node("m:B", "class", "m")],
      edges: [],
    };
    expect(graphElements(state).find((element) => element.data.id === "m")?.data.count).toBe(
      "2 classes",
    );
  });

  it("draws a marked leaf's word and badge as one image under its name, and a frame's header as two", () => {
    const marked = render(updated());
    const fee = marked.getElementById("billing:fee");
    expect(fee.hasClass("marked")).toBe(true);
    // The word shares the name's line: the name is not lifted above it.
    expect(fee.numericStyle("text-margin-y")).toBe(0);
    const word = decodeURIComponent(imageSource(String(fee.style("background-image"))));
    expect(word).toContain(">changed</text>");
    expect(word).toContain(`fill="${theme["agent-a-text"]}"`);
    expect(word).toContain(`fill="${theme["agent-a"]}"`);
    const frame = marked.getElementById("billing");
    expect(frame.style("label")).toBe("");
    const header = String(frame.style("background-image")).split(" ");
    expect(header).toHaveLength(2);
    expect(decodeURIComponent(imageSource(header[0] ?? ""))).toContain(">billing.py</text>");
    expect(decodeURIComponent(imageSource(header[1] ?? ""))).toContain(
      ">3 functions · 1 new</text>",
    );
    expect(render(initial()).getElementById("billing:fee").style("background-image")).toBe("none");
  });

  it("gives a marked node in the cone both images, the marks and the distance badge", () => {
    const state = updated();
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
    cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(theme) });
    updateGraph(cy, graphElements(state, false, new Set(["billing:fee"]), cone));
    const fee = cy.getElementById("billing:fee");
    expect(fee.hasClass("cone") && fee.hasClass("marked")).toBe(true);
    const images = String(fee.style("background-image")).split(" ");
    expect(images).toHaveLength(2);
    expect(decodeURIComponent(imageSource(images[0] ?? ""))).toContain(">changed</text>");
    expect(decodeURIComponent(imageSource(images[1] ?? ""))).toContain(">1</text>");
  });
});
