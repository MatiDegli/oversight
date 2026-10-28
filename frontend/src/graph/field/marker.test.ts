import { describe, expect, it } from "vitest";
import { themes } from "../../theme";
import type { BoxSource } from "./boxSource";
import type { FieldLight } from "./lights";
import { along, lightBoxKey, MarkerMotion } from "./marker";
import { FieldTransition } from "./transition";
import { fieldConfig } from "./model";

const boxes = new Map([
  ["a", { x1: 0, y1: 0, x2: 40, y2: 20 }],
  ["b", { x1: 100, y1: 100, x2: 140, y2: 120 }],
]);
const source = {
  boxes: (ids: readonly string[]) =>
    new Map(
      ids.flatMap((id) =>
        boxes.has(id) ? [[id, boxes.get(id) ?? { x1: 0, y1: 0, x2: 0, y2: 0 }]] : [],
      ),
    ),
  route: () => [
    { x: 0, y: 10 },
    { x: 0, y: 110 },
    { x: 100, y: 110 },
  ],
} as unknown as BoxSource;
const light: FieldLight = {
  id: "a",
  marker: "agent-1",
  source: "agent-a",
  weight: 1,
  pulses: true,
  steady: true,
};
const set = (motion: MarkerMotion, id: string, now: number, jump = false) => {
  motion.set([{ ...light, id }], source, now, "240ms", themes.dark["transition-easing"], jump);
};

it("samples routes by distance without cutting their corners", () => {
  expect(along(source.route?.("a", "b") ?? [], 0.25)).toEqual({ x: 0, y: 60 });
  expect(along(source.route?.("a", "b") ?? [], 0.75)).toEqual({ x: 50, y: 110 });
});

describe("focus travel", () => {
  it("moves one emitter box with its marker along the edge and keeps two agents independent", () => {
    const motion = new MarkerMotion();
    set(motion, "a", 0);
    set(motion, "b", 300);
    const current = { ...light, id: "b" };
    const moved = motion.boxes([current, { ...current, marker: "agent-2" }], source, 320);
    const box = moved.get(lightBoxKey(current));
    if (!box) throw new Error("Missing box");
    expect(box.x1).toBe(0);
    expect(box.y1).toBeGreaterThan(0);
    expect(box.y1).toBeLessThan(100);
    expect(box.x2 - box.x1).toBe(40);
    expect(moved.get("agent-2\u0000b")).toEqual(boxes.get("b"));
    expect(motion.boxes([current], source, 540).get(lightBoxKey(current))).toEqual(boxes.get("b"));
    const config = fieldConfig(themes.dark, "watch");
    const transition = new FieldTransition({ config, lights: [light] });
    transition.set(
      { mode: "watch", lights: [current] },
      config,
      300,
      "240ms",
      themes.dark["transition-easing"],
      false,
    );
    expect(transition.sample(340).lights).toHaveLength(1);
  });

  it("jumps with reduced motion and preserves the visible position when interrupted", () => {
    const motion = new MarkerMotion();
    set(motion, "a", 0);
    set(motion, "b", 300);
    const before = motion.boxes([{ ...light, id: "b" }], source, 340).get("agent-1\u0000b");
    set(motion, "a", 340);
    const interrupted = motion.boxes([light], source, 340).get(lightBoxKey(light));
    expect(interrupted?.x1).toBeCloseTo(before?.x1 ?? -1);
    expect(interrupted?.y1).toBeCloseTo(before?.y1 ?? -1);
    set(motion, "b", 350, true);
    expect(motion.boxes([{ ...light, id: "b" }], source, 350).get("agent-1\u0000b")).toEqual(
      boxes.get("b"),
    );
  });
});
