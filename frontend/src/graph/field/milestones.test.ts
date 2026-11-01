import { describe, expect, it } from "vitest";
import { themes } from "../../theme";
import { MilestoneLife, milestoneRing, type Milestone } from "./milestones";
import { createLampField, fieldConfig, parseHex, placeLights } from "./model";
import { afterlightOf } from "./afterlight";
import { MergeLife } from "./milestones";
const theme = themes.dark;
const box = { x1: 140, y1: 140, x2: 280, y2: 182 };
const boxes = new Map([["node", box]]);
const event = (
  key = "1",
  kind: Milestone["kind"] = "ready",
  source: Milestone["source"] = "agent-a",
  id = "node",
): Milestone => ({ key, kind, source, slot: source === "agent-a" ? "A" : "B", id, age: 0 });
const start = (events: Milestone[]) => {
  const life = new MilestoneLife(theme);
  life.update([], 0, false, true);
  life.update(events, 1000, false, false);
  return life;
};
describe("milestone rings", () => {
  it("starts Ready at half the rendered width and grows, and lands both Needs you beats there", () => {
    const ready = event();
    expect(milestoneRing(ready, box, 0, theme).radius).toBe(70);
    expect(milestoneRing(ready, box, 350, theme).radius).toBeGreaterThan(70);
    const needs = event("2", "needs");
    for (const age of [350, 700]) {
      const ring = milestoneRing(needs, box, age, theme);
      expect(ring.radius).toBe(70);
      expect(ring.intensity).toBeGreaterThanOrEqual(milestoneRing(ready, box, 0, theme).intensity);
    }
    expect(milestoneRing(needs, box, 351, theme).radius).toBeGreaterThan(200);
  });
  it("coalesces a team's events within one second, and a merge replaces its ring", () => {
    const life = start([event()]);
    life.update([event("1", "ready", "agent-a"), { ...event("2"), age: 0 }], 1200, false, false);
    expect(life.rings(1200, boxes)).toHaveLength(1);
    life.update([event(), event("3", "merged")], 1300, false, false);
    expect(life.rings(1300, boxes)).toEqual([]);
    expect(life.visible("3", 1300)).toBe(true);
    life.update([event("3", "merged"), event("4")], 2300, false, false);
    expect(life.rings(2300, boxes)).toHaveLength(1);
  });
  it("lets Merged replace Needs you from the same team within one second", () => {
    const needs = event("needs", "needs");
    const merged = event("merged", "merged");
    const life = start([needs]);
    life.update([needs, merged], 1200, false, false);
    expect(life.rings(1200, boxes)).toEqual([]);
    expect(life.visible(merged.key, 1200)).toBe(true);
  });
  it("keeps Merged when three Needs you events evict Ready", () => {
    const events = [
      event("ready"),
      event("merged", "merged", "agent-b", "b"),
      event("c", "needs", "agent-c", "c"),
      event("d", "needs", "agent-d", "d"),
      event("e", "needs", "agent-e", "e"),
    ];
    const life = start(events);
    expect(life.visible("ready", 1000)).toBe(false);
    expect(life.visible("merged", 1000)).toBe(true);
    expect(events.filter((e) => life.visible(e.key, 1000))).toHaveLength(4);
  });
  it("offsets a second team's ring on the same node by 350 ms", () => {
    const life = start([event(), event("2", "needs", "agent-b")]);
    expect(life.rings(1349, boxes)).toHaveLength(1);
    expect(life.rings(1350, boxes)).toHaveLength(2);
  });
  it("drops Ready before Merged, and defers a fifth Needs you instead of dropping it", () => {
    const sources = ["agent-a", "agent-b", "agent-c", "agent-d", "agent-e", "agent-f"] as const;
    const all = sources.map((source, i) =>
      event(String(i), i === 0 ? "ready" : i === 1 ? "merged" : "needs", source, String(i)),
    );
    const life = start(all);
    expect(life.visible("0", 1000)).toBe(false);
    expect(life.visible("1", 1000)).toBe(false);
    expect(all.filter((e) => life.visible(e.key, 1000))).toHaveLength(4);
    const needs = sources.map((source, i) => event(String(i), "needs", source, String(i)));
    const waiting = start(needs);
    expect(needs.filter((e) => waiting.visible(e.key, 1000))).toHaveLength(4);
    expect(needs.filter((e) => waiting.visible(e.key, 1860))).toHaveLength(2);
    expect(needs.every((e) => waiting.visible(e.key, 1000) || waiting.visible(e.key, 1860))).toBe(
      true,
    );
  });
  it("never starts history or reduced-motion events, forgets departed keys for replay", () => {
    const life = new MilestoneLife(theme);
    life.update([event()], 0, false, true);
    expect(life.rings(0, boxes)).toEqual([]);
    life.update([], 10, false, false);
    life.update([event()], 20, false, false);
    expect(life.rings(20, boxes)).toHaveLength(1);
    life.update([], 30, false, false);
    life.update([event()], 40, true, false);
    expect(life.rings(40, boxes)).toEqual([]);
  });
  it("dips only its team's lights and swells Needs you on landing", () => {
    const life = start([event("1", "needs")]);
    expect(life.gain("agent-a", 1100)).toBeCloseTo(0.56);
    expect(life.gain("agent-b", 1100)).toBe(1);
    expect(life.gain("agent-a", 1700)).toBeCloseTo(1.3);
  });
  it("feeds circular rings through the lamp accumulator even with zero emitter slots", () => {
    const life = start([event()]);
    const ring = life.rings(1000, boxes);
    const grid = createLampField()(
      { ...fieldConfig(theme, "watch"), limit: 0 },
      600,
      400,
      [],
      0,
      [],
      ring,
    );
    expect(grid.data.some((v, i) => i % 4 === 3 && v > 0)).toBe(true);
    const at = (23 * grid.cols + 40) * 4;
    expect(grid.data[at + 3]).toBeGreaterThan(0); // just beyond the row's right edge, first frame
  });
  it("gives two resting teams a clean edge with unchanged additive brightness", () => {
    const lamps = createLampField();
    const lights = placeLights(
      theme,
      [
        { id: "node", source: "agent-a", weight: 1, pulses: false },
        { id: "other", source: "agent-b", weight: 1, pulses: false },
      ],
      new Map([...boxes, ["other", { ...box, x1: 300, x2: 440 }]]),
    );
    const grid = lamps(fieldConfig(theme, "watch"), 600, 400, lights, null);
    for (const [col, source] of [
      [24, "agent-a"],
      [52, "agent-b"],
    ] as const) {
      const at = (23 * grid.cols + col) * 4;
      const rgb = parseHex(theme[source]);
      expect(grid.data[at + 3]).toBe(255);
      rgb.forEach((v, i) => {
        expect(grid.data[at + i]).toBeCloseTo(v + (255 - v) * 0.2, 0);
      });
    }
  });
  it("starts the halo exactly at shared arrival and omits it under reduced motion and history", () => {
    const merge = { ...event(), pulse: true };
    const life = new MergeLife(afterlightOf(theme));
    life.update([merge], 1000, false, false);
    expect(life.halos(2699, 550)).toEqual([]);
    expect(life.halos(2700, 550)[0]?.progress).toBe(0);
    expect(life.halos(2975, 550)[0]?.progress).toBe(0.5);
    expect(life.halos(3250, 550)).toEqual([]);
    for (const [reduced, history] of [
      [true, false],
      [false, true],
    ] as const) {
      const still = new MergeLife(afterlightOf(theme));
      still.update([merge], 0, reduced, history);
      expect(still.halos(1700, 550)).toEqual([]);
      expect(still.pulses(1000)).toEqual([]);
    }
  });
});

it("cancels a dropped merge's travel and halo together, keeping its afterlight", () => {
  const merge = { ...event("merge", "merged"), pulse: true };
  const life = new MergeLife(afterlightOf(theme));
  life.update([merge], 1000, false, false, [], new Map([[merge.key, 1000]]));
  expect(life.pulses(1500)).toHaveLength(1);
  life.update([merge], 1500, false, false, [], new Map([[merge.key, -Infinity]]));
  expect(life.pulses(1500)).toEqual([]);
  expect(life.halos(2700, 550)).toEqual([]);
  expect(life.lights(1500)).toHaveLength(1);
  const dropped = new MergeLife(afterlightOf(theme));
  dropped.update([merge], 1000, false, false, [], new Map([[merge.key, -Infinity]]));
  expect(dropped.pulses(1500)).toEqual([]);
  expect(dropped.halos(2700, 550)).toEqual([]);
  expect(dropped.lights(1000)[0]?.weight).toBe(theme["waiting-light-weight"]);
});
it("finishes motion without restarting its travel or halo when motion returns", () => {
  const merge = { ...event("merge", "merged"), pulse: true };
  const life = new MergeLife(afterlightOf(theme));
  life.update([merge], 1000, false, false);
  life.finishMotion();
  life.update([merge], 1500, false, false);
  expect(life.pulses(1500)).toEqual([]);
  expect(life.halos(2700, 550)).toEqual([]);
  expect(life.lights(1500)).toHaveLength(1);
});
