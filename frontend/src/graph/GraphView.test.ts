import { afterEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  effects: [] as (() => undefined | (() => void))[],
  create: vi.fn(),
}));
vi.mock("react", () => ({
  useRef: () => ({ current: {} }),
  useEffect: (effect: () => undefined | (() => void)) => mocks.effects.push(effect),
}));
vi.mock("cytoscape", () => ({ default: Object.assign(mocks.create, { use: vi.fn() }) }));
vi.mock("./updateGraph", () => ({ updateGraph: vi.fn() }));

import { GraphView } from "./GraphView";

let cleanup: undefined | (() => void);
afterEach(() => {
  cleanup?.();
  mocks.effects.length = 0;
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

it("loads the unused graph font before creating Cytoscape and applies theme styles", async () => {
  let loaded!: (fonts: FontFace[]) => void;
  const load = vi.fn(
    () =>
      new Promise<FontFace[]>((resolve) => {
        loaded = resolve;
      }),
  );
  const style = vi.fn();
  mocks.create.mockReturnValue({ style, resize: vi.fn(), destroy: vi.fn() });
  vi.stubGlobal("document", { fonts: { load, ready: Promise.resolve() } });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  GraphView();
  cleanup = mocks.effects[0]?.();
  await Promise.resolve();
  expect(load).toHaveBeenCalledWith('14px "Atkinson Hyperlegible Next"');
  expect(mocks.create).not.toHaveBeenCalled();
  loaded([]);
  await Promise.resolve();
  expect(mocks.create).toHaveBeenCalledTimes(1);
  expect(style).toHaveBeenCalledWith(
    expect.arrayContaining([expect.objectContaining({ selector: "node" })]),
  );
});

it("does not create a graph when unmounted during font loading", async () => {
  let loaded!: (fonts: FontFace[]) => void;
  vi.stubGlobal("document", {
    fonts: {
      load: () =>
        new Promise<FontFace[]>((resolve) => {
          loaded = resolve;
        }),
    },
  });
  GraphView();
  cleanup = mocks.effects[0]?.();
  cleanup?.();
  cleanup = undefined;
  loaded([]);
  await Promise.resolve();
  expect(mocks.create).not.toHaveBeenCalled();
});
