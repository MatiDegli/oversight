import { afterEach, expect, it } from "vitest";
import {
  APPEARANCE_KEY,
  applyAppearance,
  browserEnv,
  chooseAppearance,
  readAppearance,
  resolveTheme,
  subscribeAppearance,
  type AppearanceEnv,
} from "./appearance";

// A computer setting the test can flip, with the listeners the module registered on it.
function fakeComputer(light: boolean) {
  const listeners = new Set<() => void>();
  const query = {
    get matches() {
      return light;
    },
    addEventListener: (_type: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_type: string, listener: () => void) => listeners.delete(listener),
  };
  const queries: string[] = [];
  return {
    listeners,
    queries,
    matchMedia: (text: string) => {
      queries.push(text);
      return query as unknown as MediaQueryList;
    },
    flip(next: boolean) {
      light = next;
      for (const listener of [...listeners]) listener();
    },
  };
}

function fakeEnv(stored: string | null, light = false) {
  const values = new Map<string, string>(stored === null ? [] : [[APPEARANCE_KEY, stored]]);
  const computer = fakeComputer(light);
  const root = { dataset: {} as DOMStringMap };
  const env: AppearanceEnv = {
    root,
    storage: {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value),
    },
    matchMedia: computer.matchMedia,
  };
  return { env, root, values, computer };
}

// The module keeps one System listener; a dark apply after each test removes it.
afterEach(() => {
  applyAppearance("dark", {});
});

it("defaults to dark when nothing is stored", () => {
  const { env, root } = fakeEnv(null, true);
  expect(readAppearance(env.storage)).toBe("dark");
  expect(applyAppearance(undefined, env)).toBe("dark");
  expect(root.dataset.theme).toBe("dark");
});

it.each(["light", "Day", "", "system ", "null"])("reads the invalid value %j as dark", (stored) => {
  const { env, root } = fakeEnv(stored, true);
  expect(readAppearance(env.storage)).toBe("dark");
  applyAppearance(undefined, env);
  expect(root.dataset.theme).toBe("dark");
});

it("reads dark when storage refuses the read", () => {
  const storage = {
    getItem: () => {
      throw new Error("SecurityError");
    },
    setItem: () => undefined,
  };
  expect(readAppearance(storage)).toBe("dark");
});

it.each([
  ["dark", false, "dark"],
  ["day", false, "day"],
  ["system", true, "day"],
  ["system", false, "dark"],
] as const)("applies the stored %s with a light computer %s as %s", (stored, light, theme) => {
  const { env, root } = fakeEnv(stored, light);
  expect(applyAppearance(undefined, env)).toBe(theme);
  expect(root.dataset.theme).toBe(theme);
});

it("asks the computer for light only for System", () => {
  const { computer } = fakeEnv(null, true);
  expect(resolveTheme("dark", computer.matchMedia)).toBe("dark");
  expect(resolveTheme("day", computer.matchMedia)).toBe("day");
  expect(computer.queries).toEqual([]);
  expect(resolveTheme("system", computer.matchMedia)).toBe("day");
  expect(computer.queries).toEqual(["(prefers-color-scheme: light)"]);
  expect(resolveTheme("system", undefined)).toBe("dark");
});

it("follows the computer while System is selected, and stops when another choice replaces it", () => {
  const { env, root, computer } = fakeEnv("system", false);
  const applied: (string | undefined)[] = [];
  const stop = subscribeAppearance(() => applied.push(root.dataset.theme));
  try {
    applyAppearance(undefined, env);
    expect(root.dataset.theme).toBe("dark");
    expect(computer.listeners.size).toBe(1);
    computer.flip(true);
    expect(root.dataset.theme).toBe("day");
    computer.flip(false);
    expect(root.dataset.theme).toBe("dark");
    // Choosing System again keeps a single listener.
    applyAppearance("system", env);
    expect(computer.listeners.size).toBe(1);
    chooseAppearance("dark", env);
    expect(computer.listeners.size).toBe(0);
    computer.flip(true);
    expect(root.dataset.theme).toBe("dark");
    expect(applied).toEqual(["dark", "day", "dark", "dark", "dark"]);
  } finally {
    stop();
  }
});

it("stores the choice and applies it at once, even when storage refuses the write", () => {
  const { env, root, values } = fakeEnv(null);
  expect(chooseAppearance("day", env)).toBe("day");
  expect(values.get(APPEARANCE_KEY)).toBe("day");
  expect(root.dataset.theme).toBe("day");
  const refusing: AppearanceEnv = {
    ...env,
    storage: {
      getItem: () => null,
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
    },
  };
  expect(chooseAppearance("system", refusing)).toBe("dark");
  expect(root.dataset.theme).toBe("dark");
});

it("does nothing without a window, storage or media queries", () => {
  expect(browserEnv()).toEqual({});
  expect(readAppearance()).toBe("dark");
  expect(applyAppearance()).toBe("dark");
  expect(chooseAppearance("system")).toBe("dark");
  expect(applyAppearance("day", {})).toBe("day");
});
