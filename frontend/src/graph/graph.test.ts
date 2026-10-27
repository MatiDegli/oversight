import type { AgentStatus, GraphNode, ImpactCone, ServerEvent } from "@contracts";
import cytoscape from "cytoscape";
import type { Core } from "cytoscape";
import fcose from "cytoscape-fcose";
import { afterEach, describe, expect, it, vi } from "vitest";

import { applyEvent, createInitialState, stateStore } from "../state";
import type { State } from "../state";
import { themes } from "../theme";

import { loadGraphEvents } from "./devLog";
import { graphElements } from "./elements";
import { EDGE_CURVE_STYLE, graphStyles } from "./styles";
import { updateGraph } from "./updateGraph";

cytoscape.use(fcose);

const theme = themes.dark;
const agents = { A: "agent-a", B: "agent-b" } as const;

const rgb = (hex: string) =>
  `rgb(${[1, 3, 5].map((offset) => Number.parseInt(hex.slice(offset, offset + 2), 16)).join(",")})`;

const node = (id: string, kind: GraphNode["kind"], parent_id: string | null = null): GraphNode => ({
  id,
  kind,
  parent_id,
  name: id,
  qualname: id,
  path: "billing.py",
  start_line: 1,
  end_line: 4,
  is_test: false,
});
const nodes = [
  node("billing", "module"),
  node("billing:Bill", "class", "billing"),
  node("billing:Bill.total", "method", "billing:Bill"),
  node("billing:fee", "function", "billing"),
];
const agent = (slot: "A" | "B"): AgentStatus => ({
  agent_id: `agent-${slot}`,
  change_id: `change-${slot}`,
  slot,
  task: "Update billing",
  model: "fake",
  state: "working",
  step: 1,
  max_steps: 10,
  detail: null,
});
const a = agent("A");
const b = agent("B");
function event<K extends ServerEvent["type"]>(
  seq: number,
  type: K,
  payload: Extract<ServerEvent, { type: K }>["payload"],
): ServerEvent {
  return { seq, ts: "2026-10-26T15:00:00.000Z", type, payload } as ServerEvent;
}
const log: ServerEvent[] = [
  event(1, "run.started", {
    run_id: "run-graph-test",
    contract_version: "1.0.0",
    prompt_version: "test",
    workspace: "demo-repo",
    base_commit: "base",
    models: [],
    slots: [],
    limits: {
      max_steps: 10,
      max_output_tokens: 100,
      max_wall_clock_s: 60,
      test_timeout_s: 10,
      cone_max_depth: 3,
      cone_max_nodes: 20,
    },
    budget: { budget_usd: 10, spent_usd: 0, warn_at: [0.5, 0.8], block_at: 0.9 },
  }),
  event(2, "graph.snapshot", {
    version: "v1",
    commit: "base",
    nodes,
    edges: ["calls", "imports", "inherits"].map((kind, i) => ({
      id: `edge-${kind}`,
      kind: kind as "calls" | "imports" | "inherits",
      src: "billing:Bill.total",
      dst: "billing:fee",
      confidence: i === 0 ? "possible" : "exact",
      reason: i === 0 ? "unknown_receiver" : null,
    })),
    positions: { "billing:Bill.total": { x: 100, y: 100 }, "billing:fee": { x: 400, y: 100 } },
    parse_errors: [],
  }),
  event(3, "agent.status", a),
  event(4, "agent.status", b),
];
const initial = () => log.reduce(applyEvent, createInitialState());
function updated(state = initial()): State {
  return applyEvent(
    state,
    event(state.lastSeq + 1, "change.updated", {
      change_id: a.change_id,
      agent_id: a.agent_id,
      author: "agent",
      file: {
        path: "billing.py",
        status: "modified",
        original: "before",
        modified: "after",
        lines_added: 1,
        lines_removed: 1,
      },
      node_ids: ["billing:fee"],
      files_changed: 1,
      lines_added: 1,
      lines_removed: 1,
      graph: {
        nodes_added: [node("billing:discount", "function", "billing")],
        nodes_modified: ["billing:fee"],
        nodes_removed: ["billing:Bill.total"],
        edges_added: [
          {
            id: "added-call",
            kind: "calls",
            src: "billing:discount",
            dst: "billing:fee",
            confidence: "exact",
            reason: null,
          },
        ],
        edges_removed: ["edge-imports"],
      },
    }),
  );
}
let cy: Core | undefined;
function render(state: State) {
  cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(theme) });
  updateGraph(cy, graphElements(state));
  return cy;
}
afterEach(() => {
  cy?.destroy();
  stateStore.setState({ state: createInitialState() });
});

describe("graph mapping", () => {
  it("reads colors, type and alpha from the supplied theme", () => {
    const custom = {
      ...theme,
      card: theme.raised,
      "text-font": theme["code-font"],
      "body-size": theme["title-size"],
      "node-opacity": theme["module-opacity"],
      "agent-b-node": { color: theme.field, alpha: theme["dim-module"] },
    };
    cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(custom) });
    updateGraph(cy, graphElements(initial()));
    const target = cy.getElementById("billing:fee");
    expect(target.style("background-color")).toBe(rgb(custom.card));
    expect(target.style("font-family")).toBe(custom["text-font"]);
    expect(target.style("font-size")).toBe(custom["body-size"]);
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
    expect(graph.edges().map((e) => e.classes())).toEqual([
      ["calls", "possible"],
      ["imports", "exact"],
      ["inherits", "exact"],
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
      expect(edge.style("curve-style")).toBe(EDGE_CURVE_STYLE);
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
    expect(Object.keys(target.data).sort()).toEqual(["id", "kind", "label", "marks", "parent"]);
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
    expect(target.style("text-wrap")).toBe("wrap");
    expect(Number.parseFloat(String(target.style("text-max-width")))).toBeGreaterThan(1000);
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
    expect(fee.numericStyle("text-margin-y")).toBeLessThan(0);
    const word = decodeURIComponent(String(fee.style("background-image")));
    expect(word).toContain(">changed</text>");
    expect(word).toContain(`fill="${theme["agent-a-text"]}"`);
    expect(word).toContain(`fill="${theme["agent-a"]}"`);
    const frame = marked.getElementById("billing");
    expect(frame.style("label")).toBe("");
    const header = String(frame.style("background-image")).split(" ");
    expect(header).toHaveLength(2);
    expect(decodeURIComponent(header[0] ?? "")).toContain(">billing.py</text>");
    expect(decodeURIComponent(header[1] ?? "")).toContain(">3 functions · 1 new</text>");
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
    expect(decodeURIComponent(images[0] ?? "")).toContain(">changed</text>");
    expect(decodeURIComponent(images[1] ?? "")).toContain(">1</text>");
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

describe("graph updates and log entry point", () => {
  it("starts empty and feeds inline events through the shared store without a transport", () => {
    expect(graphElements(stateStore.getState().state)).toEqual([]);
    loadGraphEvents(log);
    expect(stateStore.getState().state).toEqual(initial());
    expect(graphElements(stateStore.getState().state)).toHaveLength(7);
    loadGraphEvents([
      event(5, "agent.focus", {
        agent_id: a.agent_id,
        mode: "edit",
        node_ids: ["billing:fee"],
        ranges: [],
      }),
    ]);
    expect(
      graphElements(stateStore.getState().state).find((e) => e.data.id === "billing:fee")?.classes,
    ).toContain("focus-A-edit");
  });

  it("uses preset positions, then adds a graph.delta node without moving existing leaves or viewport", () => {
    loadGraphEvents(log);
    const graph = render(stateStore.getState().state);
    expect(graph.getElementById("billing:fee").position()).toEqual({ x: 400, y: 100 });
    graph.getElementById("billing:fee").position({ x: 600, y: 120 }); // Human drag beats stale preset.
    expect(stateStore.getState().state.graph?.positions["billing:fee"]).toEqual({ x: 400, y: 100 });
    const positions = graph
      .nodes(":childless")
      .map((n) => ({ id: n.id(), position: { ...n.position() } }));
    graph.zoom(1.7);
    graph.pan({ x: 20, y: 40 });
    const layout = vi.spyOn(graph, "layout");
    const batch = vi.spyOn(graph, "batch");
    loadGraphEvents([
      event(5, "graph.delta", {
        from_version: "v1",
        to_version: "v2",
        commit: "next",
        cause: "rescan",
        nodes_added: [node("billing:tax", "function", "billing")],
        nodes_changed: [],
        nodes_removed: [],
        edges_added: [],
        edges_changed: [],
        edges_removed: [],
        parse_errors: [],
      }),
    ]);
    updateGraph(graph, graphElements(stateStore.getState().state));
    expect(batch).toHaveBeenCalledTimes(1);
    expect(layout).not.toHaveBeenCalled();
    for (const { id, position } of positions)
      expect(graph.getElementById(id).position()).toEqual(position);
    const added = graph.getElementById("billing:tax");
    expect(added.parent().first().id()).toBe("billing");
    expect(Number.isFinite(added.position().x)).toBe(true);
    expect(graph.zoom()).toBe(1.7);
    expect(graph.pan()).toEqual({ x: 20, y: 40 });
  });

  it.each([0, 1200])(
    "places additions at b's box without growing b across another module at %s",
    (aX) => {
      const state = initial();
      if (!state.graph) throw new Error("Missing test graph");
      state.graph = {
        ...state.graph,
        nodes: [
          node("a", "module"),
          node("a:f", "function", "a"),
          node("b", "module"),
          node("b:f", "function", "b"),
        ],
        edges: [],
        positions: { "a:f": { x: aX, y: 300 }, "b:f": { x: 900, y: 300 } },
      };
      const graph = render(state);
      const bounds = (id: string) => graph.getElementById(id).boundingBox({ includeLabels: false });
      const aBox = bounds("a");
      const bBox = bounds("b");
      const before = graph.getElementById("b:f").position();
      state.graph.nodes.push(node("b:new", "function", "b"));
      updateGraph(graph, graphElements(state));
      const added = graph.getElementById("b:new");
      const point = added.position();
      const dx = Math.max(bBox.x1 - point.x, 0, point.x - bBox.x2);
      const dy = Math.max(bBox.y1 - point.y, 0, point.y - bBox.y2);
      expect(Math.hypot(dx, dy)).toBeLessThanOrEqual(140);
      const grown = bounds("b");
      expect(
        grown.x1 >= aBox.x2 || grown.x2 <= aBox.x1 || grown.y1 >= aBox.y2 || grown.y2 <= aBox.y1,
      ).toBe(true);
      expect(graph.getElementById("b:f").position()).toEqual(before);
      expect(Math.abs(point.x - before.x) >= 150 || Math.abs(point.y - before.y) >= 60).toBe(true);
      expect(bounds("a")).toEqual(aBox);
    },
  );

  it("avoids sibling compound boxes including labels when placing a new node", () => {
    const state = initial();
    if (!state.graph) throw new Error("Missing graph");
    state.graph = {
      ...state.graph,
      nodes: [
        node("m", "module"),
        node("c", "class", "m"),
        node("left", "method", "c"),
        node("right", "method", "c"),
      ],
      edges: [],
      positions: { left: { x: 0, y: 0 }, right: { x: 500, y: 0 } },
    };
    const graph = render(state);
    const sibling = graph.getElementById("c");
    const measure = vi.spyOn(sibling, "boundingBox");
    const before = sibling.boundingBox({ includeLabels: true, includeOverlays: false });
    measure.mockClear();
    state.graph.nodes.push(node("new", "function", "m"));
    updateGraph(graph, graphElements(state));
    const added = graph
      .getElementById("new")
      .boundingBox({ includeLabels: true, includeOverlays: false });
    expect(
      added.x1 >= before.x2 ||
        added.x2 <= before.x1 ||
        added.y1 >= before.y2 ||
        added.y2 <= before.y1,
    ).toBe(true);
    expect(measure).toHaveBeenCalledWith({ includeLabels: true, includeOverlays: false });
    expect(sibling.boundingBox({ includeLabels: true, includeOverlays: false })).toEqual(before);
  });

  it("runs fcose once when there is no preset and retains those positions on updates", () => {
    const state = initial();
    if (!state.graph) throw new Error("Missing test graph");
    state.graph = { ...state.graph, positions: {} };
    cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles(theme) });
    const layout = vi.spyOn(cy, "layout");
    updateGraph(cy, graphElements(state));
    expect(layout).toHaveBeenCalledWith(expect.objectContaining({ name: "fcose", animate: false }));
    const positions = cy
      .nodes(":childless")
      .map((n) => ({ id: n.id(), position: { ...n.position() } }));
    updateGraph(cy, graphElements(updated(state)));
    expect(layout).toHaveBeenCalledTimes(1);
    for (const { id, position } of positions)
      expect(cy.getElementById(id).position()).toEqual(position);
  });

  it("reparents survivors before deleting their old compound parent and updates edge endpoints", () => {
    const state = initial();
    const graph = render(state);
    graph.getElementById("billing:Bill.total").position({ x: 225, y: 200 });
    if (!state.graph) throw new Error("Missing test graph");
    state.graph = {
      ...state.graph,
      nodes: nodes
        .filter((n) => n.kind !== "class")
        .map((n) => (n.kind === "method" ? { ...n, parent_id: "billing" } : n)),
      edges: state.graph.edges.map((e) => ({ ...e, dst: "billing" })),
    };
    updateGraph(graph, graphElements(state));
    expect(graph.getElementById("billing:Bill").empty()).toBe(true);
    expect(graph.getElementById("billing:Bill.total").parent().first().id()).toBe("billing");
    expect(graph.getElementById("billing:Bill.total").position()).toEqual({ x: 225, y: 200 });
    expect(graph.getElementById("edge-calls").target().id()).toBe("billing");
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
