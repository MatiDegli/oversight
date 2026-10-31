import { afterEach, beforeEach, vi } from "vitest";

import type { FieldHost, FieldInput } from "./controller";
import type { BoxSource } from "./boxSource";
import { createLampRenderer } from "./renderer";
import { createLampField } from "./model";

vi.mock("./renderer", () => ({ createLampRenderer: vi.fn() }));
vi.mock("./model", async (original) => ({
  ...(await original<typeof import("./model")>()),
  createLampField: vi.fn(),
}));

let clock: { now: number };
let nextFrame = 0;
const mutations: MutationCallback[] = [];
const frames = new Map<number, FrameRequestCallback>();
const compute = vi.fn(() => ({ cols: 1, rows: 1, data: new Uint8ClampedArray(4) }));
const renderer = {
  check: vi.fn(),
  resize: vi.fn(),
  draw: vi.fn(),
  pixel: vi.fn(),
  lost: vi.fn(() => false),
  dispose: vi.fn(),
};
const media = Object.assign(new EventTarget(), { matches: false });
let canvas: FieldHost;
const source = {
  boxes: () => new Map([["a", { x1: 0, y1: 0, x2: 10, y2: 10 }]]),
  subscribe: () => () => undefined,
} as unknown as BoxSource;
const light: FieldInput = {
  mode: "watch",
  lights: [{ id: "a", source: "agent-a", weight: 1, pulses: false }],
};

beforeEach(() => {
  vi.clearAllMocks();
  frames.clear();
  mutations.length = 0;
  clock.now = 0;
  nextFrame = 0;
  media.matches = false;
  vi.mocked(createLampField).mockReturnValue(compute);
  vi.mocked(createLampRenderer).mockReturnValue(renderer);
  vi.spyOn(performance, "now").mockImplementation(() => clock.now);
  vi.stubGlobal("matchMedia", () => media);
  vi.stubGlobal("window", Object.assign(new EventTarget(), { devicePixelRatio: 1 }));
  vi.stubGlobal(
    "document",
    Object.assign(new EventTarget(), {
      hidden: false,
      documentElement: { dataset: { theme: "dark" } },
    }),
  );
  vi.stubGlobal(
    "MutationObserver",
    class {
      constructor(callback: MutationCallback) {
        mutations.push(callback);
      }
      observe() {}
      disconnect() {}
    },
  );
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frames.set(++nextFrame, callback);
    return nextFrame;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  canvas = Object.assign(new EventTarget(), {
    clientWidth: 70,
    clientHeight: 70,
    hidden: false,
  }) as FieldHost;
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function advance(ms: number) {
  clock.now = ms;
  const callbacks = [...frames.values()];
  frames.clear();
  for (const callback of callbacks) callback(clock.now);
}
/** Runs the frames the field asked for until it asks for no more, within a bound. */
function settle() {
  for (let i = 0; i < 5 && frames.size > 0; i++) advance(clock.now + 300);
}
const last = () =>
  compute.mock.calls.at(-1) as unknown as Parameters<ReturnType<typeof createLampField>>;

export function setupControllerClock(value: { now: number }) {
  clock = value;
}

export { nextFrame, mutations, frames, compute, renderer, media, canvas, source, light };
export { advance, settle, last };
