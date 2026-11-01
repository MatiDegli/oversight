import { describe, expect, it } from "vitest";
import { initialView, navigateView, panelOpen, surfaceTab, tabbedView, viewReducer } from "./view";
import type { FocusTab } from "./view";
import { through } from "./replay.fixture";

const file = (id: string): FocusTab => ({
  id,
  selected: id,
  diff: null,
  review: null,
  file: { path: `${id}.py`, agentId: null, startLine: 1, endLine: 5 },
  frameIds: [id],
  frameKey: `file:${id}`,
});
const open = (view: typeof tabbedView, id: string) =>
  viewReducer(view, { type: "open", tab: file(id) });

describe("person-owned focus tabs", () => {
  it("opens, activates and retains each tab's scope without mutating its input", () => {
    const a = open(tabbedView, "a");
    const b = open(a, "b");
    const active = viewReducer(b, { type: "activate", id: "a" });
    expect(a.tabs?.map((tab) => tab.id)).toEqual(["a"]);
    expect(active.activeTab).toBe("a");
    expect(active.file?.path).toBe("a.py");
    expect(active.tabs?.[0]?.frameIds).toEqual(["a"]);
    expect(active.tabs?.[0]?.frameKey).toBe("file:a");
    expect(viewReducer(active, { type: "activate", id: "missing" })).toBe(active);
    expect(open(b, "a").tabs).toHaveLength(2);
  });
  it("closes to the previous active tab, preserves active on background close, then watches", () => {
    const tabs = open(open(open(tabbedView, "a"), "b"), "c");
    const active = viewReducer(tabs, { type: "activate", id: "a" });
    const back = viewReducer(active, { type: "close", id: "a" });
    expect(back.activeTab).toBe("c");
    const c = viewReducer(back, { type: "close", id: "b" });
    expect(c.activeTab).toBe("c");
    expect(viewReducer(c, { type: "close", id: "missing" })).toBe(c);
    const watch = viewReducer(c, { type: "close", id: "c" });
    expect(watch.tabs).toEqual([]);
    expect(watch.activeTab).toBeNull();
    expect(panelOpen(watch)).toBe(false);
    expect(watch.selected).toBe("c");
  });
  it("caps at six and evicts the oldest inactive even when the oldest tab is active", () => {
    const six = ["a", "b", "c", "d", "e", "f"].reduce(open, tabbedView);
    const active = viewReducer(six, { type: "activate", id: "a" });
    const seven = open(active, "g");
    expect(seven.tabs?.map((tab) => tab.id)).toEqual(["a", "c", "d", "e", "f", "g"]);
    expect(seven.activeTab).toBe("g");
    expect(viewReducer(seven, { type: "watch" }).tabs).toEqual([]);
  });
  it("captures distinct file, diff and review tabs only on navigation", () => {
    const state = through(20);
    const diff = {
      ...initialView,
      selected: "shop.pricing:compute_price",
      diff: { changeId: "change-1", path: "shop/pricing.py" },
    };
    const a = navigateView(tabbedView, { ...initialView, file: file("cart").file }, state);
    const b = navigateView(a, diff, state);
    const c = navigateView(b, { ...initialView, review: "change-1" }, state);
    expect(c.tabs?.map((tab) => tab.id)).toEqual([
      "file:main:cart.py",
      "diff:change-1:shop/pricing.py",
      "review:change-1",
    ]);
    expect(c.tabs?.[1]?.frameIds).toEqual(["shop.pricing:compute_price"]);
    // Applying later events changes data, never this person's tab state.
    const saved = structuredClone(c);
    through(23);
    expect(c).toEqual(saved);
  });
});

it("Usage retains selection, navigation history and the six-tab limit", () => {
  const a = open(tabbedView, "a");
  const usage = viewReducer(a, { type: "open", tab: surfaceTab(a, "usage") });
  expect(panelOpen(usage)).toBe(true);
  expect(usage.selected).toBe("a");
  expect(usage.tabs?.at(-1)?.frameIds).toEqual([]);
  const b = open(usage, "b");
  expect(viewReducer(b, { type: "activate", id: "usage" }).selected).toBe("b");
  expect(viewReducer(b, { type: "close", id: "b" }).activeTab).toBe("usage");
  expect(viewReducer(usage, { type: "close", id: "usage" }).activeTab).toBe("a");
  let many = usage;
  for (let n = 0; n < 7; n++) many = open(many, String(n));
  expect(many.tabs).toHaveLength(6);
});
