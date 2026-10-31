import type { Core, CytoscapeOptions } from "cytoscape";
import { afterEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  effects: [] as (() => undefined | (() => void))[],
  instances: [] as Core[],
  refs: [] as { current: unknown }[],
  refIndex: 0,
}));
vi.mock("react", () => ({
  // The container ref (null) stands in as a host; other refs keep their initial value.
  useRef: (...initial: unknown[]) => {
    const index = mocks.refIndex++;
    return (mocks.refs[index] ??= {
      current:
        initial.length > 0 && initial[0] !== null
          ? initial[0]
          : Object.assign(new EventTarget(), {
              getBoundingClientRect: () => ({ left: 0, top: 0 }),
            }),
    });
  },
  useEffect: (effect: () => undefined | (() => void)) => mocks.effects.push(effect),
}));
vi.mock("cytoscape", async (importOriginal) => {
  const { default: cytoscape } = await importOriginal<{ default: typeof import("cytoscape") }>();
  return {
    default: Object.assign(
      (options: CytoscapeOptions) => {
        // Exercise real graph updates and disposal without a browser renderer.
        const cy = cytoscape({
          ...options,
          container: undefined,
          headless: true,
          styleEnabled: true,
        });
        mocks.instances.push(cy);
        return cy;
      },
      { use: cytoscape.use },
    ),
  };
});

import { createInitialState, stateStore } from "../state";

import type { GraphApi } from "./GraphView";
import * as camera from "./camera";

import { frames, paint, setLabel, setupGraphView } from "./GraphView.support";

let cleanup: undefined | (() => void);
afterEach(() => {
  cleanup?.();
  cleanup = undefined;
  frames.clear();
  for (const cy of mocks.instances) cy.destroy();
  mocks.instances.length = 0;
  mocks.effects.length = 0;
  mocks.refs.length = 0;
  mocks.refIndex = 0;
  stateStore.setState({ state: createInitialState() });
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const { mount, rerender } = setupGraphView(mocks, (value) => {
  cleanup = value;
});

it("reframes a second review while the panel remains open", async () => {
  setLabel("Caller");
  const { settle } = mount();
  await settle("resolved");
  const cy = mocks.instances[0];
  if (!cy) throw new Error("Missing graph");
  cy.zoom(0.9);
  const frame = vi.spyOn(camera, "frameCamera").mockReturnValue(null);
  rerender({ panelOpen: true, frameKey: "review:change-1", frameIds: ["first"] });
  cy.zoom(0.4);
  frame.mockClear();
  rerender({ panelOpen: true, frameKey: "review:change-2", frameIds: ["second"] });
  expect(frame).toHaveBeenCalledExactlyOnceWith(cy, ["second"], false, {
    coneIds: undefined,

    restoreCamera: undefined,
  });
});

it("expands watch only for added nodes and yields to active pan, wheel zoom and open panels", async () => {
  setLabel("Caller");
  const { settle } = mount();
  await settle("resolved");
  const cy = mocks.instances[0];
  if (!cy) throw new Error("Missing graph");
  const expand = vi.spyOn(camera, "includeNewNodes").mockImplementation(() => undefined);
  const add = (id: string) => {
    const state = stateStore.getState().state;
    const graph = state.graph;
    const template = graph?.nodes[0];
    if (!graph || !template) throw new Error("Missing graph data");
    stateStore.setState({
      state: {
        ...state,
        graph: { ...graph, nodes: [...graph.nodes, { ...template, id, name: id }] },
      },
    });
  };
  add("new");
  paint();
  expect(expand).toHaveBeenCalledExactlyOnceWith(cy, ["new"], false, 0);
  const animating = vi.spyOn(cy, "animated").mockReturnValue(true);
  expand.mockClear();
  add("before-ease-ends");
  paint();
  expect(expand).toHaveBeenCalledExactlyOnceWith(cy, ["new", "before-ease-ends"], false, 0);
  animating.mockReturnValue(false);
  expand.mockClear();
  stateStore.setState({ state: { ...stateStore.getState().state } });
  expect(expand).not.toHaveBeenCalled();
  expand.mockClear();
  const stop = vi.spyOn(cy, "stop");
  cy.emit("tapstart");
  add("during-pan");
  paint();
  expect(expand).not.toHaveBeenCalled();
  expect(stop).toHaveBeenCalledOnce();
  cy.emit("tapend");
  cy.emit("scrollzoom");
  add("during-wheel");
  paint();
  expect(expand).not.toHaveBeenCalled();
  const navigationEnded = performance.now();
  const now = vi.spyOn(performance, "now").mockReturnValue(navigationEnded + 1000);
  add("one-second-after-wheel");
  paint();
  expect(expand.mock.calls.length).toBe(0);
  now.mockReturnValue(navigationEnded + 3100);
  vi.spyOn(camera, "frameCamera").mockReturnValue(null);
  rerender({ panelOpen: true, frameKey: "review:1", frameIds: ["new"] });
  add("during-review");
  paint();
  expect(expand).not.toHaveBeenCalled();
  rerender({ panelOpen: false, frameKey: null });
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  add("after-pan");
  paint();
  expect(expand).toHaveBeenCalledExactlyOnceWith(cy, ["after-pan"], true, 0);
});

it.each([0, 400])("passes the styled panel occlusion to growth (%s px)", async (covered) => {
  setLabel("Caller");
  const overlays = {} as HTMLElement;
  const startAreaRef = {
    current: {
      parentElement: overlays,
      getBoundingClientRect: () => ({ left: 24, top: 80, right: 876, bottom: 810 }),
    } as HTMLDivElement,
  };
  const property = vi.fn(() => `${String(covered)}px`);
  const computed = vi.fn(() => ({ getPropertyValue: property }));
  vi.stubGlobal("getComputedStyle", computed);
  const { settle } = mount({ startAreaRef });
  await settle("resolved");
  const cy = mocks.instances[0];
  if (!cy) throw new Error("Missing graph");
  const expand = vi.spyOn(camera, "includeNewNodes").mockImplementation(() => undefined);
  const state = stateStore.getState().state;
  const template = state.graph?.nodes[0];
  if (!state.graph || !template) throw new Error("Missing graph data");
  stateStore.setState({
    state: {
      ...state,
      graph: { ...state.graph, nodes: state.graph.nodes.concat({ ...template, id: "new" }) },
    },
  });
  expect(expand).toHaveBeenCalledExactlyOnceWith(cy, ["new"], false, covered);
  expect(computed).toHaveBeenCalledWith(overlays);
  expect(property).toHaveBeenCalledWith("--graph-overlay-occlusion");
});

it("measures the card's real height and refreshes marks when that height changes", async () => {
  setLabel("Caller");
  const { settle, resize } = mount();
  await settle("resolved");
  const cy = mocks.instances[0];
  if (!cy) throw new Error("Graph was not created");
  vi.spyOn(cy, "width").mockReturnValue(928);
  vi.spyOn(cy, "height").mockReturnValue(1028);
  cy.getElementById("billing:fee").position({ x: 200, y: 400 });
  cy.viewport({ zoom: 1, pan: { x: 0, y: 0 } });
  vi.spyOn(camera, "frameCamera").mockReturnValue({
    zoom: 1,
    pan: { x: 0, y: 0 },
    framed: new Set(),
    outOfView: new Set(),
  });
  let bottom = 300;
  const coneCardRef = {
    current: {
      getBoundingClientRect: () => ({ left: 24, right: 304, top: 24, bottom }),
    } as HTMLDivElement,
  };
  const onFramed = vi.fn();
  rerender({
    panelOpen: true,
    frameKey: "review:change-1",
    frameIds: ["billing:fee"],
    coneCardRef,
    onFramed,
    cone: {
      seeds: ["billing:changed"],
      entries: [{ node_id: "billing:fee", distance: 1 }],
      edges_followed: "exact",
      max_depth: 3,
      max_nodes: 40,
      omitted_by_depth: 0,
      omitted_by_limit: 0,
      tests_reaching: 0,
    },
  });
  paint();
  expect(onFramed).toHaveBeenLastCalledWith(expect.objectContaining({ outOfView: new Set() }));
  bottom = 500;
  resize();
  paint();
  expect(onFramed).toHaveBeenLastCalledWith(
    expect.objectContaining({ outOfView: new Set(["billing:fee"]) }),
  );
  bottom = 300;
  resize();
  paint();
  expect(onFramed).toHaveBeenLastCalledWith(expect.objectContaining({ outOfView: new Set() }));
});

it("pans to a cone row's node only when asked, and tells the cone card what that left in view", async () => {
  setLabel("Caller");
  const apiRef = { current: null as GraphApi | null };
  const onFramed = vi.fn();
  const coneCardRef = {
    current: {
      getBoundingClientRect: () => ({ left: 24, right: 304, top: 24, bottom: 300 }),
    } as HTMLDivElement,
  };
  const cone = {
    seeds: ["billing:changed"],
    entries: [{ node_id: "billing:fee", distance: 1 }],
    edges_followed: "exact" as const,
    max_depth: 3,
    max_nodes: 40,
    omitted_by_depth: 0,
    omitted_by_limit: 0,
    tests_reaching: 0,
  };
  const { settle } = mount({ apiRef, onFramed, coneCardRef });
  await settle("resolved");
  const cy = mocks.instances[0];
  if (!cy) throw new Error("Graph was not created");
  vi.spyOn(cy, "width").mockReturnValue(928);
  vi.spyOn(cy, "height").mockReturnValue(1028);
  cy.getElementById("billing:fee").position({ x: 5000, y: 400 });
  cy.viewport({ zoom: 1, pan: { x: 0, y: 0 } });
  const reveal = vi.spyOn(camera, "revealNode");
  const frame = vi.spyOn(camera, "frameCamera").mockReturnValue({
    zoom: 1,
    pan: { x: 0, y: 0 },
    framed: new Set(),
    outOfView: new Set(),
  });
  rerender({
    apiRef,
    onFramed,
    coneCardRef,
    cone,
    panelOpen: true,
    frameKey: "review:change-1",
    frameIds: [],
  });
  // Entering review framed the camera and listed the row as out of view; it moved nothing else.
  expect(frame).toHaveBeenCalledOnce();
  paint();
  expect(onFramed).toHaveBeenLastCalledWith(
    expect.objectContaining({ outOfView: new Set(["billing:fee"]) }),
  );
  expect(reveal).not.toHaveBeenCalled();
  expect(cy.pan()).toEqual({ x: 0, y: 0 });
  onFramed.mockClear();
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  apiRef.current?.reveal("billing:fee");
  expect(reveal).toHaveBeenCalledExactlyOnceWith(cy, "billing:fee", true);
  expect(cy.zoom()).toBe(1);
  expect(cy.getElementById("billing:fee").renderedPosition().x).toBeCloseTo((304 + 928) / 2);
  paint();
  expect(onFramed).toHaveBeenLastCalledWith(expect.objectContaining({ outOfView: new Set() }));
  // Several viewport events in one frame report the final camera only, without reframing.
  onFramed.mockClear();
  cy.pan({ x: -4500, y: 0 });
  cy.zoom(1.1);
  cy.pan({ x: -3000, y: 0 });
  const manualCamera = { zoom: cy.zoom(), pan: { ...cy.pan() } };
  expect(onFramed).not.toHaveBeenCalled();
  expect(frames.size).toBe(1);
  paint();
  expect(onFramed).toHaveBeenCalledExactlyOnceWith(
    expect.objectContaining({ ...manualCamera, outOfView: new Set(["billing:fee"]) }),
  );
  expect({ zoom: cy.zoom(), pan: cy.pan() }).toEqual(manualCamera);
  expect(frame).toHaveBeenCalledOnce();
  // A node that is not on the graph is no move and no report.
  onFramed.mockClear();
  apiRef.current?.reveal("missing");
  paint();
  expect(onFramed).not.toHaveBeenCalled();
  cy.pan({ x: 0, y: 0 });
  expect(frames.size).toBe(1);
  cleanup?.();
  cleanup = undefined;
  expect(frames.size).toBe(0);
  paint();
  expect(onFramed).not.toHaveBeenCalled();
});

it("limits the zoom to the buttons' range, and routes a call again when a node it joins is moved", async () => {
  const fn = (id: string, parent: string | null, line: number) => ({
    id,
    kind: "function" as const,
    parent_id: parent,
    name: id,
    qualname: id,
    path: `${parent ?? id}.py`,
    start_line: line,
    end_line: line + 1,
    is_test: false,
    examples: null,
  });
  stateStore.setState({
    state: {
      ...createInitialState(),
      graph: {
        version: "v1",
        commit: "base",
        nodes: [
          { ...fn("a", null, 1), kind: "module" as const },
          { ...fn("b", null, 1), kind: "module" as const },
          fn("a:f", "a", 2),
          fn("b:g", "b", 2),
        ],
        edges: [
          {
            id: "call",
            kind: "calls",
            src: "a:f",
            dst: "b:g",
            confidence: "exact",
            reason: null,
          },
        ],
        positions: {
          a: { x: 0, y: 0 },
          b: { x: 700, y: 0 },
          "a:f": { x: 0, y: 0 },
          "b:g": { x: 700, y: 300 },
        },
        parse_errors: [],
      },
    },
  });
  const { settle } = mount();
  await settle("resolved");
  const cy = mocks.instances[0];
  if (!cy) throw new Error("Graph was not created");
  expect([cy.minZoom(), cy.maxZoom()]).toEqual([camera.ZOOM_RANGE.min, camera.ZOOM_RANGE.max]);
  const route = () => JSON.stringify(cy.edges(".calls").first().data("route"));
  const before = route();
  expect(before).not.toBeUndefined();
  // Drag the callee to the same height as its caller, and let go.
  cy.getElementById("b").position({ x: 700, y: 0 });
  expect(route()).toBe(before);
  cy.getElementById("b").emit("free");
  paint();
  expect(route()).not.toBe(before);
  // Exercise the per-frame drag path as well as the drop-only reroute.
  const scratch = vi.spyOn(cy, "scratch");
  const module = cy.getElementById("b");
  module.emit("grab");
  module.position({ x: 710, y: 20 });
  module.emit("drag");
  paint();
  expect(scratch.mock.calls.filter((call: unknown[]) => call.length === 2)).toEqual([]);
  expect(cy.scratch("dragCosts")).toEqual([expect.any(Number)]);
});

it("rebuilds fallback header layers once after fonts recover, without moving the graph", async () => {
  setLabel("Fallback");
  const { settle } = mount();
  const check = vi.fn(() => false);
  const listen = vi.fn();
  const remove = vi.fn();
  Object.assign(document.fonts, { check, addEventListener: listen, removeEventListener: remove });
  await settle("rejected");
  const cy = mocks.instances[0];
  if (!cy) throw new Error("Missing graph");
  const cameraBefore = { zoom: cy.zoom(), pan: { ...cy.pan() } };
  const positions = cy.nodes().map((n) => ({ ...n.position() }));
  const oldStyle = cy.style();
  const refresh = listen.mock.calls[0]?.[1] as () => void;
  refresh();
  expect(cy.style()).toBe(oldStyle);
  check.mockReturnValue(true);
  refresh();
  expect(cy.style()).not.toBe(oldStyle);
  expect(remove).toHaveBeenCalledWith("loadingdone", refresh);
  expect({ zoom: cy.zoom(), pan: cy.pan() }).toEqual(cameraBefore);
  expect(cy.nodes().map((n) => ({ ...n.position() }))).toEqual(positions);
  expect(mocks.instances).toHaveLength(1);
});
