import { describe, expect, it } from "vitest";

import { through } from "./replay.fixture";
import {
  clickNode,
  closePanel,
  closesPanel,
  diffForNode,
  initialView,
  openChange,
  readyCount,
} from "./view";

const edited = "shop.pricing:compute_price";
const untouched = "shop.cart:Cart.add";
const diff = { changeId: "change-1", path: "shop/pricing.py" };

describe("panel switching over the level 1 fixture", () => {
  it("opens a node's live diff only while a pending change touches it", () => {
    expect(diffForNode(through(11), edited)).toBeNull();
    expect(diffForNode(through(12), edited)).toEqual(diff);
    expect(diffForNode(through(20), edited)).toEqual(diff);
    expect(diffForNode(through(21), edited)).toBeNull();
    expect(diffForNode(through(14), untouched)).toBeNull();
  });

  it("selects every clicked node and keeps the panel on a node no change touches", () => {
    const state = through(14);
    const open = clickNode(state, initialView, edited);
    expect(open).toEqual({ selected: edited, diff });
    expect(clickNode(state, open, untouched)).toEqual({ selected: untouched, diff });
    expect(clickNode(state, initialView, untouched)).toEqual({ selected: untouched, diff: null });
    expect(clickNode(through(21), initialView, edited)).toEqual({ selected: edited, diff: null });
  });

  it("opens the card's change at its first file only when there is one to show", () => {
    expect(openChange(through(11), initialView, "change-1")).toBeNull();
    expect(openChange(through(14), initialView, "change-1")).toEqual({ selected: edited, diff });
    expect(openChange(through(21), initialView, "change-1")).toBeNull();
    expect(openChange(through(14), initialView, "change-9")).toBeNull();
  });

  it("closes on the close button and on Escape the editor did not take, keeping the selection", () => {
    const open = { selected: edited, diff };
    expect(closePanel(open)).toEqual({ selected: edited, diff: null });
    const key = { key: "Escape", defaultPrevented: false, isComposing: false };
    expect(closesPanel(key)).toBe(true);
    expect(closesPanel({ ...key, defaultPrevented: true })).toBe(false);
    expect(closesPanel({ ...key, isComposing: true })).toBe(false);
    expect(closesPanel({ ...key, key: "Enter" })).toBe(false);
  });

  it("counts changes ready for review across agents from the proposal until the decision", () => {
    expect(readyCount(through(18))).toBe(0);
    expect(readyCount(through(19))).toBe(1);
    expect(readyCount(through(20))).toBe(1);
    expect(readyCount(through(21))).toBe(0);
  });
});
