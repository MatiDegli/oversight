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
import { themes } from "../theme";

const theme = themes.dark;

import { GraphView } from "./GraphView";
import type { GraphApi, GraphViewProps } from "./GraphView";
import * as camera from "./camera";

const frames = new Map<number, FrameRequestCallback>();
function paint() {
  const pending = [...frames.values()];
  frames.clear();
  for (const callback of pending) callback(performance.now());
}

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

function setLabel(name: string) {
  stateStore.setState({
    state: {
      ...createInitialState(),
      graph: {
        version: "v1",
        commit: "base",
        nodes: [
          {
            id: "billing:fee",
            kind: "function",
            parent_id: null,
            name,
            qualname: "billing:fee",
            path: "billing.py",
            start_line: 1,
            end_line: 4,
            is_test: false,
            examples: null,
          },
        ],
        edges: [],
        positions: { "billing:fee": { x: 100, y: 100 } },
        parse_errors: [],
      },
    },
  });
}

function mount(props?: GraphViewProps) {
  let frameId = 0;
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frames.set(++frameId, callback);
    return frameId;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  let resolve!: (fonts: FontFace[]) => void;
  let reject!: (error: Error) => void;
  const pending = new Promise<FontFace[]>((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  const load = vi.fn(() => pending);
  const observer = { observe: vi.fn(), disconnect: vi.fn() };
  let onResize: (() => void) | undefined;
  vi.stubGlobal("document", {
    fonts: { load, check: () => false, addEventListener: vi.fn(), removeEventListener: vi.fn() },
  });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback: () => void) {
        onResize = callback;
      }
      observe = observer.observe;
      disconnect = observer.disconnect;
    },
  );
  GraphView(props);
  // The props effect runs first, as React runs effects in order; the last effect creates the graph.
  if (props) mocks.effects.slice(0, -1).forEach((effect) => effect());
  cleanup = mocks.effects.at(-1)?.();
  const settle = async (result: "resolved" | "rejected") => {
    if (result === "resolved") resolve([]);
    else reject(new Error("Font unavailable"));
    await pending.catch(() => undefined);
    await Promise.resolve();
    paint();
  };
  return { load, observer, settle, resize: () => onResize?.() };
}

function rerender(props: GraphViewProps) {
  mocks.refIndex = 0;
  mocks.effects.length = 0;
  GraphView(props);
  // Run the props and camera effects; the creation effect's [] dependencies have not changed.
  mocks.effects.slice(0, -1).forEach((effect) => effect());
}

it("synchronizes each store update immediately, including transport-only updates", async () => {
  setLabel("Initial");
  const { settle } = mount();
  await settle("resolved");
  const cy = mocks.instances[0];
  if (!cy) throw new Error("Graph was not created");
  setLabel("First");
  expect(cy.getElementById("billing:fee").data("label")).toBe("First");
  setLabel("Second");
  expect(cy.getElementById("billing:fee").data("label")).toBe("Second");
  const batch = vi.spyOn(cy, "batch");
  stateStore.setState(({ state }) => ({ state: { ...state, lastSeq: state.lastSeq + 1 } }));
  expect(batch).toHaveBeenCalled();
});

it.each(["resolved", "rejected"] as const)(
  "creates exactly one graph after the font load is %s, syncs current state and disposes synchronously",
  async (result) => {
    setLabel("Before loading");
    const { load, observer, settle } = mount();
    expect(load.mock.calls).toEqual([
      [`${theme["body-size"]} ${theme["text-font"]}`],
      [`${String(theme["frame-title-weight"])} ${theme["frame-title-size"]} ${theme["text-font"]}`],
      [`${String(theme["caption-weight"])} ${theme["frame-count-size"]} ${theme["text-font"]}`],
    ]);
    await Promise.resolve();
    expect(mocks.instances).toHaveLength(0);
    expect(observer.observe).not.toHaveBeenCalled();
    setLabel("During loading");

    await settle(result);
    expect(mocks.instances).toHaveLength(1);
    const cy = mocks.instances[0];
    if (!cy) throw new Error("Graph was not created");
    expect(cy.destroyed()).toBe(false);
    expect(cy.nodes()).toHaveLength(1);
    expect(cy.getElementById("billing:fee").data("label")).toBe("During loading");
    expect(cy.getElementById("billing:fee").style("font-family")).toBe(theme["text-font"]);
    expect(observer.observe).toHaveBeenCalledTimes(1);

    // Cytoscape's own bounding boxes read the stylesheet through cy.style(); only a call that
    // passes one sets it again.
    const style = vi.spyOn(cy, "style");
    setLabel("After loading");
    paint();
    expect(cy.getElementById("billing:fee").data("label")).toBe("After loading");
    await Promise.resolve();
    expect(mocks.instances).toHaveLength(1);
    expect(style.mock.calls.filter((call) => call.length > 0)).toEqual([]);

    cleanup?.();
    cleanup = undefined;
    expect(cy.destroyed()).toBe(true);
    expect(observer.disconnect).toHaveBeenCalledTimes(1);
    const batch = vi.spyOn(cy, "batch");
    setLabel("After unmount");
    expect(batch).not.toHaveBeenCalled();
  },
);

it.each(["resolved", "rejected"] as const)(
  "never creates a graph when the font load is %s after unmount",
  async (result) => {
    const { observer, settle } = mount();
    cleanup?.();
    cleanup = undefined;
    await settle(result);
    setLabel("After unmount");
    expect(mocks.instances).toHaveLength(0);
    expect(observer.observe).not.toHaveBeenCalled();
    expect(observer.disconnect).not.toHaveBeenCalled();
  },
);

it("reports node taps and keeps the app's selected ring through state updates", async () => {
  setLabel("Selected");
  const onNodeTap = vi.fn();
  const { settle } = mount({ selectedId: "billing:fee", onNodeTap });
  await settle("resolved");
  const cy = mocks.instances[0];
  if (!cy) throw new Error("Graph was not created");
  const node = cy.getElementById("billing:fee");
  expect(node.selected()).toBe(true);
  setLabel("After an event");
  expect(cy.getElementById("billing:fee").selected()).toBe(true);
  node.emit("tap");
  expect(onNodeTap).toHaveBeenCalledExactlyOnceWith("billing:fee");
});

it("applies the proposal's cone on initial sync and retains its distance through state updates", async () => {
  setLabel("Caller");
  const { settle } = mount({
    litIds: ["billing:fee"],
    cone: {
      seeds: ["billing:changed"],
      entries: [{ node_id: "billing:fee", distance: 2 }],
      edges_followed: "exact",
      max_depth: 3,
      max_nodes: 40,
      omitted_by_depth: 0,
      omitted_by_limit: 0,
      tests_reaching: 0,
    },
  });
  await settle("resolved");
  const cy = mocks.instances[0];
  if (!cy) throw new Error("Graph was not created");
  expect(cy.getElementById("billing:fee").hasClass("cone")).toBe(true);
  expect(cy.getElementById("billing:fee").data("distance")).toBe(2);
  setLabel("After an event");
  expect(cy.getElementById("billing:fee").hasClass("cone")).toBe(true);
  expect(cy.getElementById("billing:fee").data("distance")).toBe(2);
});

it("reframes on entering review from live diff at watch zoom, and never on incoming events or selection", async () => {
  setLabel("Caller");
  const { settle } = mount({ frameKey: null });
  await settle("resolved");
  const cy = mocks.instances[0];
  if (!cy) throw new Error("Graph was not created");
  cy.zoom(0.5);
  // Whatever the starting camera was, the person's watch camera is the one to come back to.
  const watchCamera = { zoom: cy.zoom(), pan: { ...cy.pan() } };
  const frame = vi.spyOn(camera, "frameCamera").mockImplementation(() => null);
  const result = {
    zoom: 0.8,
    pan: { x: 0, y: 0 },
    framed: new Set(["billing:changed"]),
    outOfView: new Set(["billing:fee"]),
  };
  frame.mockReturnValue(result);
  const onFramed = vi.fn();
  const live = { panelOpen: true, frameKey: "change-1", frameIds: ["billing:changed"], onFramed };
  rerender(live);
  expect(frame).toHaveBeenLastCalledWith(cy, live.frameIds, false, {
    coneIds: undefined,

    restoreCamera: undefined,
  });
  // The narrowed live diff needed to zoom out. Review must retain the original watch zoom.
  cy.zoom(0.2);
  frame.mockClear();
  const review = {
    ...live,
    frameKey: "review:change-1",
    litIds: ["billing:changed", "billing:fee"],
    cone: {
      seeds: ["billing:changed"],
      entries: [{ node_id: "billing:fee", distance: 1 }],
      edges_followed: "exact" as const,
      max_depth: 3,
      max_nodes: 40,
      omitted_by_depth: 0,
      omitted_by_limit: 0,
      tests_reaching: 0,
    },
  };
  rerender(review);
  expect(frame).toHaveBeenCalledExactlyOnceWith(cy, live.frameIds, false, {
    coneIds: ["billing:fee"],

    restoreCamera: undefined,
  });
  paint();
  expect(onFramed).toHaveBeenLastCalledWith({
    ...result,
    zoom: cy.zoom(),
    pan: cy.pan(),
    // The live viewport report replaces the opening target's stale framed set too.
    framed: new Set(),
    outOfView: new Set(["billing:changed", "billing:fee"]),
  });
  onFramed.mockClear();
  frame.mockClear();
  setLabel("After an event");
  rerender({ ...review, cone: { ...review.cone }, selectedId: "billing:fee" });
  expect(frame).not.toHaveBeenCalled();
  expect(onFramed).not.toHaveBeenCalled();
  paint();
  paint();
  expect(onFramed).toHaveBeenCalledOnce();
  rerender({ panelOpen: false, frameKey: null });
  expect(frame).toHaveBeenCalledExactlyOnceWith(cy, [], false, {
    coneIds: undefined,

    restoreCamera: watchCamera,
  });
});

it("restores the person's exact watch camera after opening and closing review", async () => {
  setLabel("Caller");
  const { settle } = mount();
  await settle("resolved");
  const cy = mocks.instances[0];
  if (!cy) throw new Error("Graph was not created");
  const width = vi.spyOn(cy, "width").mockReturnValue(1600);
  vi.spyOn(cy, "height").mockReturnValue(1028);
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  const watch = { zoom: 1.017, pan: { x: -17, y: 52 } };
  cy.viewport(watch);
  width.mockReturnValue(928);
  rerender({
    panelOpen: true,
    frameKey: "review:change-1",
    frameIds: ["billing:fee"],
    cone: {
      seeds: ["billing:fee"],
      entries: [],
      edges_followed: "exact",
      max_depth: 3,
      max_nodes: 40,
      omitted_by_depth: 0,
      omitted_by_limit: 0,
      tests_reaching: 0,
    },
  });
  expect(cy.pan()).not.toEqual(watch.pan);
  width.mockReturnValue(1600);
  rerender({ panelOpen: false, frameKey: null });
  expect(cy.zoom()).toBe(watch.zoom);
  expect(cy.pan()).toEqual(watch.pan);
});

it.each([false, true])(
  "mounting with a panel open respects reduced motion (%s) and restores the measured start camera",
  async (reduced) => {
    setLabel("Caller");
    vi.stubGlobal("matchMedia", () => ({ matches: reduced }));
    const startAreaRef = {
      current: {
        getBoundingClientRect: () => ({ left: 24, top: 80, right: 876, bottom: 810 }),
      } as HTMLDivElement,
    };
    const start = vi.spyOn(camera, "startCamera");
    const frame = vi.spyOn(camera, "frameCamera").mockImplementation((cy) => {
      cy.viewport({ zoom: 0.5, pan: { x: -100, y: -200 } });
      return null;
    });
    const { settle } = mount({ panelOpen: true, frameIds: ["billing:fee"], startAreaRef });
    await settle("resolved");
    const cy = mocks.instances[0];
    if (!cy) throw new Error("Graph was not created");
    expect(frame).toHaveBeenCalledOnce();
    expect(frame.mock.calls[0]?.[1]).toEqual(["billing:fee"]);
    expect(frame.mock.calls[0]?.[2]).toBe(reduced);
    // The initial overlay measurement must precede framing, rather than being skipped in a panel.
    expect(start.mock.calls.at(-1)?.[1]).toEqual({ x1: 24, y1: 80, x2: 876, y2: 810 });
    const openingPositions = cy.nodes().map((node) => ({ ...node.position() }));
    // Compute the real start at that same measured area, independently of the saved ref.
    camera.startCamera(cy, { x1: 24, y1: 80, x2: 876, y2: 810 });
    const expected = { zoom: cy.zoom(), pan: { ...cy.pan() } };
    cy.viewport({ zoom: 0.6, pan: { x: -500, y: 300 } });
    frame.mockClear();
    rerender({ panelOpen: false, startAreaRef });
    expect(frame).toHaveBeenCalledExactlyOnceWith(cy, [], reduced, {
      coneIds: undefined,
      restoreCamera: expected,
    });
    expect(cy.nodes().map((node) => ({ ...node.position() }))).toEqual(openingPositions);
  },
);

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
