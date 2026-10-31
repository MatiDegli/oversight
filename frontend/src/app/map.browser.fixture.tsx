// Hand-written map evidence: no backend, inference, or reference-sketch code.
import type { GraphNode, GraphSnapshot, ServerEvent } from "@contracts";
import { createRoot, type Root } from "react-dom/client";
import { flushSync } from "react-dom";
import { useSyncExternalStore } from "react";
import { applyEvent, createInitialState, stateStore } from "../state";
import { Frame } from "./Frame";
import { initialView } from "./view";
import log from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import demo from "../../../fixtures/graph/demo-repo.json";
import demoPositions from "../../../fixtures/layouts/demo-repo.json";

const recorded = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
export function syntheticMapGraph(): GraphSnapshot {
  const nodes: GraphNode[] = [],
    positions: GraphSnapshot["positions"] = {};
  for (let i = 0; i < 20; i += 1) {
    const folder = i < 10 ? "library" : "tools",
      name = `module_${String(i).padStart(2, "0")}`;
    const id = `${folder}.${name}`,
      path = `${folder}/${name}.py`;
    nodes.push(
      {
        id,
        path,
        kind: "module",
        name,
        qualname: id,
        parent_id: null,
        examples: null,
        is_test: false,
        start_line: 1,
        end_line: 8,
      },
      {
        id: `${id}:run`,
        path,
        kind: "function",
        name: "run",
        qualname: "run",
        parent_id: id,
        examples: null,
        is_test: false,
        start_line: 1,
        end_line: 8,
      },
    );
    positions[id] = { x: Math.floor(i / 9) * 446 + 163, y: (i % 9) * 220 + 35 };
    positions[`${id}:run`] = { x: Math.floor(i / 9) * 446 + 163, y: (i % 9) * 220 + 93 };
  }
  return {
    parse_errors: [],
    version: "map-synthetic",
    commit: "map-synthetic",
    nodes,
    positions,
    edges: [
      {
        id: "map-call",
        kind: "calls",
        src: "library.module_00:run",
        dst: "tools.module_10:run",
        confidence: "exact",
        reason: null,
      },
    ],
  };
}

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

function mapEvents(graph: GraphSnapshot, name: string): ServerEvent[] {
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
let root: Root | undefined;
function Harness() {
  const state = useSyncExternalStore(stateStore.subscribe, () => stateStore.getState().state);
  return (
    <Frame
      state={state}
      view={initialView}
      onNodeTap={() => undefined}
      canOpenChange={() => false}
      onOpenChange={() => undefined}
      onClosePanel={() => undefined}
      replayLabel="Hand-written map fixture"
      replayOf="hand-written fixture"
    />
  );
}
export function mountMap(graph = syntheticMapGraph(), name = "map fixture") {
  root?.unmount();
  stateStore.setState({ state: mapEvents(graph, name).reduce(applyEvent, createInitialState()) });
  const host = document.createElement("div");
  host.style.height = "100vh";
  document.body.replaceChildren(host);
  root = createRoot(host);
  flushSync(() => root?.render(<Harness />));
}
export function mountMapDemo() {
  mountMap({ ...demo, positions: demoPositions } as unknown as GraphSnapshot, "demo-repo");
}
export function mapTick() {
  const state = stateStore.getState().state;
  flushSync(() => {
    stateStore.setState({ state: { ...state, clock: "2026-10-31T21:52:00Z" } });
  });
}
declare global {
  interface Window {
    mapTest: { mount: typeof mountMap; demo: typeof mountMapDemo; tick: typeof mapTick };
  }
}
