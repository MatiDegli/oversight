// Hand-written agent events over an offline graph; no model calls.
import type { GraphSnapshot, ImpactCone, ServerEvent } from "@contracts";
import { createRoot, type Root } from "react-dom/client";
import { flushSync } from "react-dom";
import { useLayoutEffect, useState, useSyncExternalStore } from "react";
import { applyEvent, createInitialState, stateStore } from "../state";
import { Frame } from "./Frame";
import { graphElements } from "../graph";
import { getActiveTheme } from "../theme";
// eslint-disable-next-line no-restricted-imports -- Browser regression for the map's retained layout.
import { MapLayer } from "../graph/MapLayer";
// eslint-disable-next-line no-restricted-imports -- Fixed model isolates cone styling from review framing.
import { mapModel } from "../graph/mapModel";
// eslint-disable-next-line no-restricted-imports -- Apply actual cone evidence to the fixed model.
import { mapEvidence } from "../graph/mapEvidence";
import { initialView, type View } from "./view";
import { syntheticMapGraph } from "./map.browser.fixture";
import log from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import demo from "../../../fixtures/graph/demo-repo.json";
import demoPositions from "../../../fixtures/layouts/demo-repo.json";
const recorded = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const agentDefaults = {
  detail: null,
  plan_id: null,
  zone: null,
  started_by: null,
  queued_task_id: null,
  base_commit: null,
  examples: null,
  answering: null,
};

function evidenceEvents(graph: GraphSnapshot, name: string): ServerEvent[] {
  const run = recorded.find((e) => e.type === "run.started");
  if (run?.type !== "run.started") throw new Error("Missing run");
  const seeds = ["boltons.iterutils:is_iterable", "boltons.strutils:slugify"];
  const fallback = graph.nodes.filter((n) => n.kind === "function").map((n) => n.id);
  const events: ServerEvent[] = [
    { ...run, payload: { ...run.payload, workspace: name } },
    { seq: 2, ts: run.ts, type: "graph.snapshot", payload: graph },
  ];
  for (const [index, slot] of (["A", "B"] as const).entries()) {
    const node =
      graph.nodes.find((n) => n.id === seeds[index]) ??
      graph.nodes.find((n) => n.id === fallback[index]);
    if (!node) continue;
    const agent_id = `agent-${slot}`,
      change_id = `change-${slot}`;
    const payload = {
      ...agentDefaults,
      agent_id,
      slot,
      change_id,
      model: index ? "muse-spark-open" : "muse-spark",
      task: index ? "Keep digits at the start of a slug" : "Treat strings and bytes as scalars",
      state: "working" as const,
      step: 1,
      max_steps: 30,
    };
    events.push(
      { seq: events.length + 1, ts: run.ts, type: "agent.status", payload },
      {
        seq: events.length + 2,
        ts: run.ts,
        type: "change.updated",
        payload: {
          change_id,
          agent_id,
          author: "agent",
          node_ids: [node.id],
          graph: {
            nodes_added: [],
            nodes_modified: [node.id],
            nodes_removed: [],
            edges_added: [],
            edges_removed: [],
          },
          files_changed: 1,
          lines_added: 1,
          lines_removed: 1,
          file: {
            path: node.path,
            status: "modified",
            original: "def run():\n    pass\n",
            modified: "def run():\n    return True\n",
            lines_added: 1,
            lines_removed: 1,
          },
        },
      },
    );
    if (index)
      events.push({
        seq: events.length + 1,
        ts: run.ts,
        type: "agent.focus",
        payload: {
          agent_id,
          mode: "edit",
          node_ids: [node.id],
          ranges: [{ path: node.path, start_line: node.start_line, end_line: node.end_line }],
        },
      });
  }
  return events;
}

function coneFor(graph: GraphSnapshot, seed: string): ImpactCone {
  const entries = new Map<string, number>();
  let frontier = [seed];
  for (let depth = 1; depth <= 2; depth++) {
    const next: string[] = [];
    for (const edge of graph.edges)
      if (
        edge.kind === "calls" &&
        edge.confidence === "exact" &&
        frontier.includes(edge.dst) &&
        edge.src !== seed &&
        !entries.has(edge.src) &&
        !graph.nodes.find((n) => n.id === edge.src)?.is_test
      ) {
        entries.set(edge.src, depth);
        next.push(edge.src);
      }
    frontier = next;
  }
  return {
    seeds: [seed],
    entries: [...entries].map(([node_id, distance]) => ({ node_id, distance })),
    max_depth: 2,
    max_nodes: 40,
    edges_followed: "exact",
    omitted_by_depth: 0,
    omitted_by_limit: 0,
    tests_reaching: 0,
  };
}
export function evidenceGraph() {
  const graph = syntheticMapGraph(24);
  // Keep the two agent modules inside the group camera at 1280, as on the real snapshot.
  // Swap complete cells so the synthetic grid retains its size, spacing and function positions.
  for (const [agent, middle] of [
    ["00", "04"],
    ["01", "05"],
  ] as const)
    for (const suffix of ["", ":run"]) {
      const a = `library.module_${agent}${suffix}`;
      const b = `library.module_${middle}${suffix}`;
      const first = graph.positions[a],
        second = graph.positions[b];
      if (!first || !second) throw new Error("Missing synthetic cell position");
      graph.positions[a] = second;
      graph.positions[b] = first;
    }
  graph.edges.push(
    ...[0, 1, 2].map((i) => ({
      id: `evidence-${String(i)}`,
      src: `tools.module_${String(12 + i)}:run`,
      dst: "library.module_00:run",
      kind: "calls" as const,
      confidence: "exact" as const,
      reason: null,
    })),
  );
  const repeated = graph.edges[1];
  if (!repeated) throw new Error("Missing repeated call");
  graph.edges.push({ ...repeated, id: "repeated" });
  const testNode = graph.nodes[1];
  if (!testNode) throw new Error("Missing test template");
  graph.nodes.push({
    ...testNode,
    id: "tests:test_run",
    name: "test_run",
    path: "tests/test_map.py",
    parent_id: null,
    is_test: true,
  });
  for (let i = 0; i < 2; i++)
    graph.edges.push({ ...repeated, id: `test-call-${String(i)}`, src: "tests:test_run" });
  return graph;
}
let root: Root | undefined;
let updateView: (view: View) => void;
let taps = 0;
function Harness() {
  const state = useSyncExternalStore(stateStore.subscribe, () => stateStore.getState().state);
  const [view, setView] = useState<View>(initialView);
  useLayoutEffect(() => {
    updateView = setView;
  }, []);
  return (
    <Frame
      state={state}
      view={view}
      onNodeTap={() => {
        taps++;
      }}
      canOpenChange={() => true}
      onOpenChange={() => undefined}
      onClosePanel={() => undefined}
      replayLabel="Hand-written map fixture"
      replayOf="hand-written fixture"
    />
  );
}
export function mountEvidence(graph = evidenceGraph(), name = "map fixture") {
  root?.unmount();
  taps = 0;
  const events = evidenceEvents(graph, name);
  const proposal = recorded.find((e) => e.type === "change.proposed");
  const updated = events.find((e) => e.type === "change.updated");
  if (proposal?.type !== "change.proposed" || updated?.type !== "change.updated")
    throw new Error("Missing change");
  const seed = updated.payload.graph.nodes_modified[0];
  if (!seed) throw new Error("Missing change seed");
  events.push({
    ...proposal,
    seq: events.length + 1,
    payload: {
      ...proposal.payload,
      change_id: "change-A",
      agent_id: "agent-A",
      task: "Treat strings and bytes as scalars",
      summary: "Treat strings and bytes as scalars",
      graph: updated.payload.graph,
      files: [
        { path: updated.payload.file.path, status: "modified", lines_added: 1, lines_removed: 1 },
      ],
      impact: coneFor(graph, seed),
    },
  });
  const status = events.find((e) => e.type === "agent.status");
  if (status?.type === "agent.status")
    events.push({
      ...status,
      seq: events.length + 1,
      payload: { ...status.payload, state: "ready_for_review" },
    });
  stateStore.setState({ state: events.reduce(applyEvent, createInitialState()) });
  const host = document.createElement("div");
  host.style.height = "100vh";
  document.body.replaceChildren(host);
  root = createRoot(host);
  flushSync(() => root?.render(<Harness />));
}
let stableModel: ReturnType<typeof mapModel>;
export function stationaryEvidence(cone = false) {
  const state = stateStore.getState().state;
  const elements = graphElements(state, false);
  if (!cone) {
    stableModel = mapModel(elements, getActiveTheme());
    root?.unmount();
    const host = document.createElement("div");
    document.body.replaceChildren(host);
    root = createRoot(host);
  }
  mapEvidence(
    stableModel,
    elements,
    state,
    cone ? state.changes["change-A"]?.proposal?.impact : null,
  );
  flushSync(() =>
    root?.render(
      <MapLayer frame={{ model: stableModel, level: "map", zoom: 0.3, pan: { x: 40, y: 40 } }} />,
    ),
  );
}
export function reviewEvidence() {
  flushSync(() => {
    updateView({ ...initialView, review: "change-A" });
  });
}
export function demoEvidence() {
  mountEvidence({ ...demo, positions: demoPositions } as unknown as GraphSnapshot, "demo-repo");
}
export function evidenceTaps() {
  return taps;
}
declare global {
  interface Window {
    mapLinesTest: {
      mount: typeof mountEvidence;
      review: typeof reviewEvidence;
      stationary: typeof stationaryEvidence;
      demo: typeof demoEvidence;
      taps: typeof evidenceTaps;
    };
  }
}
