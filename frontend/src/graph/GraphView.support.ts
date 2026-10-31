import type { Core } from "cytoscape";
import { vi } from "vitest";

import { createInitialState, stateStore } from "../state";
import type { GraphViewProps } from "./GraphView";
import { GraphView } from "./GraphView";

type Mocks = {
  effects: (() => undefined | (() => void))[];
  instances: Core[];
  refs: { current: unknown }[];
  refIndex: number;
};

const frames = new Map<number, FrameRequestCallback>();
function paint() {
  const pending = [...frames.values()];
  frames.clear();
  for (const callback of pending) callback(performance.now());
}

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

function mount(
  mocks: Mocks,
  onCleanup: (value: undefined | (() => void)) => void,
  props?: GraphViewProps,
) {
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
  onCleanup(mocks.effects.at(-1)?.());
  const settle = async (result: "resolved" | "rejected") => {
    if (result === "resolved") resolve([]);
    else reject(new Error("Font unavailable"));
    await pending.catch(() => undefined);
    await Promise.resolve();
    paint();
  };
  return { load, observer, settle, resize: () => onResize?.() };
}

function rerender(mocks: Mocks, props: GraphViewProps) {
  mocks.refIndex = 0;
  mocks.effects.length = 0;
  GraphView(props);
  // Run the props and camera effects; the creation effect's [] dependencies have not changed.
  mocks.effects.slice(0, -1).forEach((effect) => effect());
}

export function setupGraphView(mocks: Mocks, onCleanup: (value: undefined | (() => void)) => void) {
  return { mount: mount.bind(null, mocks, onCleanup), rerender: rerender.bind(null, mocks) };
}

export { frames, paint, setLabel };
