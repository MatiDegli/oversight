import type { AgentStatus, GraphNode, ServerEvent } from "@contracts";
import cytoscape from "cytoscape";
import type { Core } from "cytoscape";
import fcose from "cytoscape-fcose";
import { afterEach, describe, expect, it, vi } from "vitest";

import { applyEvent, createInitialState, stateStore } from "../state";
import type { State } from "../state";
import { agentColors, colors, fontFamilies } from "../theme";

import { loadGraphEvents } from "./devLog";
import { graphElements } from "./elements";
import { EDGE_CURVE_STYLE, graphStyles } from "./styles";
import { updateGraph } from "./updateGraph";

cytoscape.use(fcose);

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
  cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles });
  updateGraph(cy, graphElements(state));
  return cy;
}
afterEach(() => {
  cy?.destroy();
  stateStore.setState({ state: createInitialState() });
});

describe("graph mapping", () => {
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
    expect(graph.getElementById("billing:fee").style("font-family")).toBe(fontFamilies.sans);
  });

  it("draws possible edges thinner and grey, always solid, with a curve switch", () => {
    const graph = render(initial());
    const possible = graph.getElementById("edge-calls");
    const exact = graph.getElementById("edge-imports");
    expect(Number.parseFloat(String(possible.style("width")))).toBeLessThan(
      Number.parseFloat(String(exact.style("width"))),
    );
    expect(possible.style("line-color")).toBe(rgb(colors.muted));
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
    expect(lit.data("label")).toContain("A read\nB edit");
    expect(lit.style("pie-1-background-color")).toBe(rgb(agentColors.A));
    expect(lit.style("pie-2-background-color")).toBe(rgb(agentColors.B));
    expect(lit.data("aSize")).toBe(50);
    expect(lit.data("bSize")).toBe(50);
    expect(Number(lit.style("pie-1-background-opacity"))).toBeLessThan(
      Number(lit.style("pie-2-background-opacity")),
    );
    expect(lit.style("border-style")).toBe("solid");
    updateGraph(
      graph,
      graphElements(applyEvent(state, event(7, "agent.status", { ...a, state: "done" }))),
    );
    expect(lit.hasClass("focus-A-read")).toBe(false);
    expect(lit.data("bSize")).toBe(100);
  });

  it("marks added, modified and removed pending nodes with only an agent-colored dashed border", () => {
    const state = updated();
    const before = JSON.stringify(state);
    const graph = render(state);
    for (const id of ["billing:discount", "billing:fee", "billing:Bill.total"]) {
      const pending = graph.getElementById(id);
      expect(pending.hasClass("pending-A")).toBe(true);
      expect(pending.data("pendingColor")).toBe(agentColors.A);
      expect(pending.style("border-style")).toBe("dashed");
      expect(pending.style("border-color")).toBe(rgb(agentColors.A));
      expect(pending.data("label")).toContain("A pending");
    }
    expect(graph.getElementById("billing").style("border-style")).toBe("solid");
    expect(graph.getElementById("added-call").nonempty()).toBe(true);
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
    expect(graph.getElementById("billing:fee").data("pendingColor")).toBe(agentColors.B);
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

  it("runs fcose once when there is no preset and retains those positions on updates", () => {
    const state = initial();
    if (!state.graph) throw new Error("Missing test graph");
    state.graph = { ...state.graph, positions: {} };
    cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyles });
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
