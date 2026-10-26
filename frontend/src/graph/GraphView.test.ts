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

import { stateStore } from "../state";
import { fontFamilies } from "../theme";

import { GraphView } from "./GraphView";
import { updateGraph } from "./updateGraph";

let cleanup: undefined | (() => void);
afterEach(() => {
  cleanup?.();
  cleanup = undefined;
  mocks.effects.length = 0;
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

function mount(load: () => Promise<FontFace[]>) {
  const cy = { style: vi.fn(), resize: vi.fn(), destroy: vi.fn() };
  const observer = { observe: vi.fn(), disconnect: vi.fn() };
  mocks.create.mockReturnValue(cy);
  vi.stubGlobal("document", { fonts: { load } });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe = observer.observe;
      disconnect = observer.disconnect;
    },
  );
  GraphView();
  cleanup = mocks.effects[0]?.();
  return { cy, observer };
}

it("creates the graph immediately and reapplies theme styles when the font arrives", async () => {
  let loaded!: (fonts: FontFace[]) => void;
  const load = vi.fn(
    () =>
      new Promise<FontFace[]>((resolve) => {
        loaded = resolve;
      }),
  );
  const { cy } = mount(load);
  expect(load).toHaveBeenCalledWith(`14px ${fontFamilies.sans}`);
  expect(mocks.create).toHaveBeenCalledTimes(1);
  expect(cy.style).toHaveBeenCalledTimes(1);
  loaded([]);
  await Promise.resolve();
  expect(mocks.create).toHaveBeenCalledTimes(1);
  expect(cy.style).toHaveBeenCalledTimes(2);
  expect(cy.style).toHaveBeenLastCalledWith(
    expect.arrayContaining([expect.objectContaining({ selector: "node" })]),
  );
  expect(cy.resize).toHaveBeenCalledTimes(1);
});

it("creates and keeps updating the graph when the font load rejects", async () => {
  const { cy, observer } = mount(() => Promise.reject(new Error("Font unavailable")));
  await Promise.resolve();
  expect(mocks.create).toHaveBeenCalledTimes(1);
  expect(cy.style).toHaveBeenCalledTimes(1);
  expect(updateGraph).toHaveBeenCalledTimes(1);
  stateStore.setState({ state: { ...stateStore.getState().state } });
  expect(updateGraph).toHaveBeenCalledTimes(2);
  cleanup?.();
  cleanup = undefined;
  stateStore.setState({ state: { ...stateStore.getState().state } });
  expect(updateGraph).toHaveBeenCalledTimes(2);
  expect(observer.disconnect).toHaveBeenCalledTimes(1);
  expect(cy.destroy).toHaveBeenCalledTimes(1);
});

it("does not reapply font styles to a destroyed graph after unmount", async () => {
  let loaded!: (fonts: FontFace[]) => void;
  const { cy } = mount(
    () =>
      new Promise<FontFace[]>((resolve) => {
        loaded = resolve;
      }),
  );
  cleanup?.();
  cleanup = undefined;
  loaded([]);
  await Promise.resolve();
  expect(mocks.create).toHaveBeenCalledTimes(1);
  expect(cy.destroy).toHaveBeenCalledTimes(1);
  expect(cy.style).toHaveBeenCalledTimes(1);
  expect(cy.resize).not.toHaveBeenCalled();
});
