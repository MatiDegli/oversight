import { expect, it, vi } from "vitest";

import { createInitialState } from "../state";
import * as themeApi from "../theme";

import { elementSnapshot } from "./elementSnapshot";

it("reuses fold input until state or the graph's visual scope changes", () => {
  const snapshot = elementSnapshot();
  const state = createInitialState();
  const first = snapshot(state, {});
  expect(snapshot(state, {})).toBe(first);
  const next = snapshot({ ...state }, {});
  expect(next).not.toBe(first);
  const scoped = snapshot(state, { litIds: ["module"] });
  expect(scoped).not.toBe(next);
  const live = snapshot(state, { field: { mode: "live-diff", lights: [] } });
  expect(live).not.toBe(scoped);
});

it("rebuilds fold input when geometry changes on the same theme object", () => {
  const snapshot = elementSnapshot();
  const state = createInitialState();
  const theme = { ...themeApi.themes.dark };
  const active = vi.spyOn(themeApi, "getActiveTheme").mockReturnValue(theme);
  try {
    const first = snapshot(state, {});
    Object.assign(theme, { "node-height": "56px" });
    expect(snapshot(state, {})).not.toBe(first);
  } finally {
    active.mockRestore();
  }
});
