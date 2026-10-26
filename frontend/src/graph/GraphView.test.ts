import type { Core, CytoscapeOptions } from "cytoscape";
import { afterEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  effects: [] as (() => undefined | (() => void))[],
  instances: [] as Core[],
}));
vi.mock("react", () => ({
  // The container ref (null) stands in as a host; other refs keep their initial value.
  useRef: (...initial: unknown[]) => ({
    current: initial.length > 0 && initial[0] !== null ? initial[0] : {},
  }),
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

let cleanup: undefined | (() => void);
afterEach(() => {
  cleanup?.();
  cleanup = undefined;
  for (const cy of mocks.instances) cy.destroy();
  mocks.instances.length = 0;
  mocks.effects.length = 0;
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
  vi.stubGlobal("document", { fonts: { load } });
  vi.stubGlobal(
    "ResizeObserver",
    class {
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
  return { load, observer, settle };
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
