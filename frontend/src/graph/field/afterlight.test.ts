import { describe, expect, it } from "vitest";

import { themes } from "../../theme";
import { afterlightOf, fadeShare, MergeLife } from "./afterlight";
import type { FieldMerge } from "./lights";

const look = afterlightOf(themes.dark);
const arrival = look.delay + look.travel;
const merge = (key: string, age = 0): FieldMerge => ({
  key,
  slot: "A",
  source: "agent-a",
  id: "shop.pricing:compute_price",
  age,
});
/** A merge life that was primed on an empty state, then told of one merge at time 0. */
function merged(age = 0, jump = false) {
  const life = new MergeLife(look);
  life.update([], 0, jump, true);
  life.update([merge("change-1:22", age)], 0, jump, false);
  return life;
}

describe("the share of light left", () => {
  it("follows (1 - p) to the curve, full at the start and gone from the end", () => {
    expect(look.curve).toBe(1.6);
    expect(fadeShare(0, look.curve)).toBe(1);
    expect(fadeShare(0.5, look.curve)).toBeCloseTo(0.5 ** 1.6, 10);
    expect(fadeShare(1, look.curve)).toBe(0);
    expect(fadeShare(1.5, look.curve)).toBe(0);
    expect(fadeShare(-1, look.curve)).toBe(1);
    const shares = [0, 0.1, 0.3, 0.6, 0.9].map((p) => fadeShare(p, look.curve));
    expect(shares).toEqual([...shares].sort((a, b) => b - a));
  });
});

describe("a merge's pulse and light", () => {
  it("waits, travels along its route, fades its trail, then lights the node", () => {
    const life = merged();
    expect(look.duration).toBe(90_000);
    expect(life.pulses(look.delay - 1)).toEqual([]);
    expect(life.lights(look.delay - 1)).toEqual([]);
    const half = life.pulses(look.delay + look.travel / 2);
    expect(half).toHaveLength(1);
    expect(half[0]?.progress).toBeCloseTo(0.5, 10);
    expect(half[0]?.opacity).toBe(1);
    // The light comes only when the pulse has arrived.
    expect(life.lights(arrival - 1)).toEqual([]);
    const landed = life.pulses(arrival + look.trailFade / 2);
    expect(landed[0]?.progress).toBe(1);
    expect(landed[0]?.opacity).toBeCloseTo(0.5, 10);
    expect(life.pulses(arrival + look.trailFade)).toEqual([]);
  });

  it("fades the node's light over 90 seconds along (1 - p) to 1.6, and then it is gone", () => {
    const life = merged();
    const at = (since: number) => life.lights(arrival + since)[0]?.weight;
    expect(at(0)).toBe(0);
    expect(at(look.rise)).toBeCloseTo(look.weight * fadeShare(look.rise / look.duration, 1.6), 10);
    expect(at(look.duration / 2)).toBeCloseTo(look.weight * 0.5 ** 1.6, 10);
    expect(at(look.duration * 0.9)).toBeCloseTo(look.weight * 0.1 ** 1.6, 10);
    const [light] = life.lights(arrival + 1000);
    expect(light).toMatchObject({
      id: "shop.pricing:compute_price",
      source: "agent-a",
      pulses: false,
      steady: true,
    });
    expect(light?.marker).toBeUndefined();
    expect(at(look.duration - 1)).toBeGreaterThan(0);
    expect(life.active(arrival + look.duration - 1)).toBe(true);
    expect(life.lights(arrival + look.duration)).toEqual([]);
    expect(life.active(arrival + look.duration)).toBe(false);
  });

  it("travels while the pulse or its trail shows, and fades after that without frames", () => {
    const life = merged();
    expect(life.travelling(0)).toBe(true);
    expect(life.travelling(arrival + look.trailFade - 1)).toBe(true);
    expect(life.travelling(arrival + look.trailFade)).toBe(false);
    expect(life.active(arrival + look.trailFade)).toBe(true);
  });

  it("under reduced motion has no pulse: the light is whole at once and fades as long", () => {
    const life = merged(0, true);
    expect(life.pulses(look.delay + look.travel / 2)).toEqual([]);
    expect(life.travelling(0)).toBe(false);
    expect(life.lights(0)[0]?.weight).toBe(look.weight);
    expect(life.lights(look.duration / 2)[0]?.weight).toBeCloseTo(look.weight * 0.5 ** 1.6, 10);
    expect(life.lights(look.duration)).toEqual([]);
  });

  it("counts the time since the event: a merge seen late starts as old as it is", () => {
    const life = merged(1000);
    expect(life.pulses(look.delay - 1000 + look.travel / 2)[0]?.progress).toBeCloseTo(0.5, 10);
    const old = merged(arrival + look.duration);
    expect(old.lights(0)).toEqual([]);
    expect(old.active(0)).toBe(false);
  });
});

describe("which merges start", () => {
  it("treats the merges the field mounts on as history, and never starts a key twice", () => {
    const life = new MergeLife(look);
    life.update([merge("a")], 0, false, true);
    expect(life.active(0)).toBe(false);
    life.update([merge("a"), merge("b")], 10, false, false);
    expect(life.pulses(10 + look.delay + 10)).toHaveLength(1);
    life.update([merge("a"), merge("b")], 5000, false, false);
    expect(life.pulses(5000 + look.delay + 10)).toHaveLength(0);
  });

  it("starts a merge again when a replay removes it from the state and plays it again", () => {
    const life = merged();
    expect(life.active(arrival + 5000)).toBe(true);
    life.update([], 100_000, false, false);
    expect(life.active(100_000)).toBe(false);
    life.update([merge("change-1:22")], 200_000, false, false);
    expect(life.active(200_000)).toBe(true);
    expect(life.pulses(200_000 + look.delay + 10)).toHaveLength(1);
  });
});
