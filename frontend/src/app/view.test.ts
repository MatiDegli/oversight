import { describe, expect, it } from "vitest";

import { through } from "./replay.fixture";
import {
  clickNode,
  closePanel,
  closesPanel,
  diffForNode,
  changeNodeIds,
  initialView,
  litNodeIds,
  nextReview,
  openChange,
  openReview,
  panelOpen,
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
    expect(open).toEqual({ selected: edited, diff, review: null });
    expect(clickNode(state, open, untouched)).toEqual({ selected: untouched, diff, review: null });
    expect(clickNode(state, initialView, untouched)).toEqual({
      ...initialView,
      selected: untouched,
    });
    expect(clickNode(through(21), initialView, edited)).toEqual({
      ...initialView,
      selected: edited,
    });
  });

  it("opens review on a node whose change is proposed, and the live diff before that", () => {
    const review = { selected: edited, diff: null, review: "change-1" };
    // Seq 18: the agent finished but has not proposed; seq 19 is change.proposed.
    expect(clickNode(through(18), initialView, edited)).toEqual({
      selected: edited,
      diff,
      review: null,
    });
    expect(clickNode(through(19), initialView, edited)).toEqual(review);
    // From the live diff the same node switches the panel to review; review survives other clicks.
    const live = clickNode(through(14), initialView, edited);
    expect(clickNode(through(20), live, edited)).toEqual(review);
    expect(clickNode(through(20), review, untouched)).toEqual({ ...review, selected: untouched });
    // Once decided, the node opens nothing and an open review stays as it was.
    expect(clickNode(through(21), review, edited)).toEqual(review);
  });

  it("opens review only for a proposed change, from the card or the queue counter", () => {
    expect(openReview(through(18), initialView, "change-1")).toBeNull();
    expect(openReview(through(20), initialView, "change-1")).toEqual({
      selected: edited,
      diff: null,
      review: "change-1",
    });
    expect(openReview(through(21), initialView, "change-1")).toBeNull();
    expect(openReview(through(20), initialView, "change-9")).toBeNull();
    expect(nextReview(through(18))).toBeNull();
    expect(nextReview(through(20))).toBe("change-1");
    expect(nextReview(through(21))).toBeNull();
  });

  it("keeps the change's nodes for review's dimming after the decision", () => {
    for (const seq of [20, 23]) {
      const change = through(seq).changes["change-1"];
      expect(change && changeNodeIds(change)).toEqual([edited]);
    }
    expect(panelOpen(initialView)).toBe(false);
    expect(panelOpen({ ...initialView, review: "change-1" })).toBe(true);
    expect(panelOpen({ ...initialView, diff })).toBe(true);
  });

  it("advances the queue from the open change to the next proposal and wraps", () => {
    const state = through(20);
    const first = state.changes["change-1"];
    if (!first) throw new Error("Missing proposal");
    state.changes["change-2"] = {
      ...first,
      change_id: "change-2",
      graph: { ...first.graph, nodes_modified: [untouched] },
    };
    const firstId = nextReview(state);
    const secondId = nextReview(state, firstId);
    expect(secondId).toBe("change-2");
    expect(openReview(state, initialView, secondId ?? "")?.review).toBe("change-2");
    expect(nextReview(state, secondId)).toBe("change-1");
    state.changes["change-1"] = { ...first, status: "approved" };
    expect(nextReview(state, "change-1")).toBe("change-2");
    expect(nextReview(state, "change-2")).toBe("change-2");
  });

  it("opens the card's change at its first file only when there is one to show", () => {
    expect(openChange(through(11), initialView, "change-1")).toBeNull();
    expect(openChange(through(14), initialView, "change-1")).toEqual({
      selected: edited,
      diff,
      review: null,
    });
    expect(openChange(through(21), initialView, "change-1")).toBeNull();
    expect(openChange(through(14), initialView, "change-9")).toBeNull();
  });

  it("closes on the close button and on Escape the editor did not take, keeping the selection", () => {
    const open = { selected: edited, diff, review: null };
    expect(closePanel(open)).toEqual({ selected: edited, diff: null, review: null });
    expect(closePanel({ selected: edited, diff: null, review: "change-1" })).toEqual({
      selected: edited,
      diff: null,
      review: null,
    });
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

  it("keeps the change's nodes lit until it is decided, then none", () => {
    const ready = through(20).changes["change-1"];
    const expected = [
      edited,
      ...(ready?.proposal?.impact.entries.map((entry) => entry.node_id) ?? []),
    ];
    expect(litNodeIds(ready)).toEqual(expected);
    expect(litNodeIds(through(18).changes["change-1"])).toEqual([edited]);
    // change.resolved at seq 21: approved and merged, so the graph goes back to its normal lights.
    expect(through(21).changes["change-1"]?.status).toBe("approved");
    expect(litNodeIds(through(21).changes["change-1"])).toBeNull();
    expect(ready && litNodeIds({ ...ready, status: "rejected" })).toBeNull();
    // An approval that did not merge still waits for the person.
    expect(ready && litNodeIds({ ...ready, status: "conflict" })).toEqual(expected);
    expect(litNodeIds(undefined)).toBeNull();
  });
});
