import { describe, expect, it } from "vitest";

import { fieldInput, fieldScope } from "./fieldScope";
import { through } from "./replay.fixture";
import { initialView } from "./view";
import type { View } from "./view";

const DIFF = { changeId: "change-1", path: "shop/pricing.py" };
const FILE = { path: "shop/pricing.py", agentId: null, startLine: 1, endLine: 20 };

const reviewing: View = { ...initialView, review: "change-1" };
const watching: View = { ...initialView, diff: DIFF };
const reading: View = { ...initialView, selected: "shop.pricing:compute_price", file: FILE };

describe("the field's scope follows what the person has open", () => {
  it("is watch with nothing open", () => {
    expect(fieldScope(through(12), initialView)).toEqual({ mode: "watch" });
    expect(fieldInput(through(12), initialView).mode).toBe("watch");
  });

  it("is review on the change in review, and live diff on the change in the live diff", () => {
    expect(fieldScope(through(20), reviewing)).toEqual({ mode: "review", changeId: "change-1" });
    expect(fieldScope(through(12), watching)).toEqual({ mode: "live-diff", changeId: "change-1" });
  });

  it("is the file view on the selected node", () => {
    expect(fieldScope(through(12), reading)).toEqual({
      mode: "file",
      nodeId: "shop.pricing:compute_price",
    });
    expect(fieldScope(through(12), { ...reading, selected: null })).toEqual({
      mode: "file",
      nodeId: null,
    });
  });

  it("goes back to watch once the change is decided, as the graph's dimming does", () => {
    expect(fieldScope(through(21), reviewing)).toEqual({ mode: "watch" });
    expect(fieldScope(through(21), watching)).toEqual({ mode: "watch" });
    expect(fieldInput(through(21), reviewing)).toEqual({ mode: "watch", lights: [] });
  });

  it("lights the review's change and its cone, and the live diff's editing node", () => {
    const review = fieldInput(through(20), reviewing);
    expect(review.mode).toBe("review");
    expect(review.lights.map((light) => light.id)).toContain("shop.pricing:compute_price");
    expect(review.lights.every((light) => !light.pulses)).toBe(true);
    const live = fieldInput(through(12), watching);
    expect(live.mode).toBe("live-diff");
    expect(live.lights.map((light) => light.id)).toContain("shop.pricing:compute_price");
  });
});
