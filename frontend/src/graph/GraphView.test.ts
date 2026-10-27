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
          : { getBoundingClientRect: () => ({ left: 0, top: 0 }) },
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
import type { GraphViewProps } from "./GraphView";
import * as camera from "./camera";

let cleanup: undefined | (() => void);
afterEach(() => {
  cleanup?.();
  cleanup = undefined;
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
  let resolve!: (fonts: FontFace[]) => void;
  let reject!: (error: Error) => void;
  const pending = new Promise<FontFace[]>((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  const load = vi.fn(() => pending);
  const observer = { observe: vi.fn(), disconnect: vi.fn() };
  let onResize: (() => void) | undefined;
  vi.stubGlobal("document", { fonts: { load } });
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

it.each(["resolved", "rejected"] as const)(
  "creates exactly one graph after the font load is %s, syncs current state and disposes synchronously",
  async (result) => {
    setLabel("Before loading");
    const { load, observer, settle } = mount();
    expect(load).toHaveBeenCalledExactlyOnceWith(`${theme["body-size"]} ${theme["text-font"]}`);
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

    const style = vi.spyOn(cy, "style");
    setLabel("After loading");
    expect(cy.getElementById("billing:fee").data("label")).toBe("After loading");
    await Promise.resolve();
    expect(mocks.instances).toHaveLength(1);
    expect(style).not.toHaveBeenCalled();

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
    watchZoom: 0.5,
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
    watchZoom: 0.5,
    restoreCamera: undefined,
  });
  expect(onFramed).toHaveBeenLastCalledWith({
    ...result,
    outOfView: new Set(["billing:changed", "billing:fee"]),
  });
  onFramed.mockClear();
  frame.mockClear();
  setLabel("After an event");
  rerender({ ...review, cone: { ...review.cone }, selectedId: "billing:fee" });
  expect(frame).not.toHaveBeenCalled();
  expect(onFramed).not.toHaveBeenCalled();
  rerender({ panelOpen: false, frameKey: null });
  expect(frame).toHaveBeenCalledExactlyOnceWith(cy, [], false, {
    coneIds: undefined,
    watchZoom: 0.5,
    restoreCamera: { zoom: 0.5, pan: { x: 0.5, y: 0.5 } },
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
  expect(onFramed).toHaveBeenLastCalledWith(expect.objectContaining({ outOfView: new Set() }));
  bottom = 500;
  resize();
  expect(onFramed).toHaveBeenLastCalledWith(
    expect.objectContaining({ outOfView: new Set(["billing:fee"]) }),
  );
  bottom = 300;
  resize();
  expect(onFramed).toHaveBeenLastCalledWith(expect.objectContaining({ outOfView: new Set() }));
});
