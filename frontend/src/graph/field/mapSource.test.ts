import { readFileSync } from "node:fs";
import cytoscape from "cytoscape";
import type { AgentStatus, ServerEvent } from "@contracts";
import { expect, it, vi } from "vitest";
import { applyEvent, createInitialState } from "../../state";
import { themes } from "../../theme";
import type { GraphElement } from "../elements";
import { moduleOwners } from "../mapEvidence";
import { mapModel, moduleLayout } from "../mapModel";
import type { MapFrame } from "../MapLayer";
import { createBoxSource } from "./boxSource";
import { createMapSource } from "./mapSource";
import { MarkerMotion } from "./marker";
import { placeLights } from "./model";
import { selectSignalLights } from "./signal";

const node = (
  id: string,
  kind: GraphElement["data"]["kind"],
  parent?: string,
  classes = "",
): GraphElement => ({
  group: "nodes",
  data: { id, kind, ...(parent ? { parent } : {}) },
  classes,
  position: { x: id.startsWith("work") ? 800 : 100, y: 200 },
});
const full = [
  node("pending", "module"),
  node("pending:change", "function", "pending", "pending-A"),
  node("pending:read", "function", "pending"),
  node("work", "module"),
  node("work:Outer", "class", "work"),
  node("work:Inner", "class", "work:Outer"),
  node("work:method", "method", "work:Inner", "pending-B focus-B-edit"),
  node("undrawn", "module"),
  node("undrawn:method", "method", "undrawn"),
  node("orphan", "function", "missing"),
];
function fixture() {
  const cy = cytoscape({
    headless: true,
    styleEnabled: true,
    layout: { name: "preset" },
    elements: [{ data: { id: "pending" } }, { data: { id: "work" } }],
  });
  const cards = createBoxSource();
  const model = mapModel(full, themes.dark);
  model.groups = model.groups.filter((g) => g.id !== "undrawn");
  let frame: MapFrame = { model, level: "map", zoom: 0.21, pan: { x: 30, y: 40 } };
  let owners = moduleOwners(full);
  const listeners = new Set<() => void>();
  const control = {
    getFrame: () => frame,
    level: () => frame.level,
    moduleOwners: () => owners,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
  const source = createMapSource(cards, control);
  source.attach(cy);
  return {
    cy,
    cards,
    source,
    listeners,
    publish(next: Partial<MapFrame>, elements?: readonly GraphElement[]) {
      if (elements) owners = moduleOwners(elements);
      frame = { ...frame, ...next };
      for (const listener of listeners) listener();
    },
    cell(id: string) {
      const module = frame.model.groups.flatMap((g) => g.modules).find((m) => m.id === id);
      if (!module) throw new Error("Missing module");
      const { x1, y1, x2, y2 } = moduleLayout(module, frame.model, frame.zoom, frame.pan);
      return { x1, y1, x2, y2 };
    },
    dispose() {
      source.detach();
      cy.destroy();
    },
  };
}
function signals() {
  const events = readFileSync(
    new URL("../../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
    "utf8",
  )
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as ServerEvent);
  let state = events.filter((e) => e.seq <= 20).reduce(applyEvent, createInitialState());
  const a = state.agents["agent-1"],
    change = state.changes["change-1"];
  if (!a || !change) throw new Error("Missing waiting fixture");
  change.graph.nodes_modified = ["pending:change"];
  const working: AgentStatus = {
    ...a,
    agent_id: "agent-B",
    slot: "B",
    state: "working",
    change_id: "change-B",
  };
  state = applyEvent(state, {
    type: "agent.status",
    seq: state.lastSeq + 1,
    ts: state.runStartedAt ?? "",
    payload: working,
  });
  state.changes["change-B"] = { ...change, change_id: "change-B", agent_id: "agent-B" };
  state.focus["agent-B"] = {
    agent_id: "agent-B",
    mode: "edit",
    node_ids: ["work:method"],
    ranges: [],
  };
  return selectSignalLights(state, { "agent-B": "pending:read" });
}
it("projects current focus, waiting change, last read and fading light without rendered child rows", () => {
  const f = fixture();
  try {
    const lights = signals();
    expect(lights.map((l) => [l.id, l.weight])).toEqual([
      ["work:method", 1],
      ["pending:change", 0.5],
      ["pending:read", 0.4],
    ]);
    const fading = {
      id: "pending:change",
      agentId: "agent-1",
      source: "agent-a",
      weight: 0.2,
      pulses: false,
    } as const;
    const all = [...lights, fading];
    const before = structuredClone(all);
    const boxes = f.source.boxes(all.map((l) => l.id));
    expect(f.cy.getElementById("work:method").empty()).toBe(true);
    expect(boxes.get("work:method")).toEqual(f.cell("work"));
    expect(boxes.get("pending:change")).toEqual(f.cell("pending"));
    expect(boxes.get("pending:read")).toEqual(f.cell("pending"));
    expect([...boxes.keys()]).toEqual(["work:method", "pending:change", "pending:read"]);
    const markers = new MarkerMotion();
    markers.set(all, f.source, 0, "240ms", "linear", true);
    const placed = placeLights(themes.dark, all, markers.boxes(all, f.source, 0));
    expect(placed.map((l) => l.weight)).toEqual([1, 0.5, 0.4]);
    expect(all).toEqual(before);
    // Fold aliases cannot override the original nested parent chain on the map.
    f.cy.scratch("foldAliases", new Map([["work:method", "pending"]]));
    expect(f.source.boxes(["work:method"]).get("work:method")).toEqual(f.cell("work"));
    expect(f.source.boxes(["missing", "orphan", "undrawn:method"])).toEqual(new Map());
  } finally {
    f.dispose();
  }
});
it("follows published pan, zoom, layout and original parent updates, then restores card geometry", () => {
  const f = fixture();
  try {
    const changed = vi.fn();
    const stop = f.source.subscribe(changed);
    for (const zoom of [0.21, 0.3, 0.6]) {
      f.publish({ zoom, pan: { x: zoom * 100, y: -20 } });
      expect(f.source.boxes(["work:method"]).get("work:method")).toEqual(f.cell("work"));
    }
    const moved = full.map((n) =>
      n.data.id === "work" ? { ...n, position: { x: 1400, y: 700 } } : n,
    );
    f.publish({ model: mapModel(moved, themes.dark) }, moved);
    expect(f.source.boxes(["work:method"]).get("work:method")).toEqual(f.cell("work"));
    const reparented = moved.map((n) =>
      n.data.id === "work:method" ? { ...n, data: { ...n.data, parent: "pending" } } : n,
    );
    f.publish({}, reparented);
    expect(f.source.boxes(["work:method"]).get("work:method")).toEqual(f.cell("pending"));
    f.cy.scratch("foldAliases", new Map([["work:method", "work"]]));
    f.publish({ level: "cards" });
    expect(f.source.boxes(["work:method"])).toEqual(f.cards.boxes(["work:method"]));
    expect(f.source.frames?.(["work"])).toEqual(f.cards.frames?.(["work"]));
    expect(f.source.headers?.()).toEqual(f.cards.headers?.());
    expect(f.source.zoom?.()).toBe(f.cards.zoom?.());
    expect(f.source.level?.()).toBe("cards");
    f.cy.emit("resize");
    expect(changed).toHaveBeenCalledTimes(7);
    stop();
    f.publish({ level: "map" });
    expect(changed).toHaveBeenCalledTimes(7);
    f.source.detach();
    expect(f.listeners.size).toBe(0);
    expect(f.source.boxes(["work:method"]).size).toBe(0);
    f.source.attach(f.cy);
    expect(f.listeners.size).toBe(1);
    expect(f.source.boxes(["work:method"]).get("work:method")).toEqual(f.cell("pending"));
  } finally {
    f.dispose();
  }
});
