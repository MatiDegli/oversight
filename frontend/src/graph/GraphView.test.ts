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
