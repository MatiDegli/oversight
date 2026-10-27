import { describe, expect, it } from "vitest";

import { themes } from "../../theme";
import {
  breathing,
  fieldClock,
  motionOf,
  ringFade,
  ringIntensity,
  ringRadiusAt,
  shouldAnimate,
} from "./motion";
import type { AnimationState } from "./motion";

const motion = motionOf(themes.dark);

describe("motion from the tokens", () => {
  it("reads DESIGN.md's numbers: 72 to 100% every 2.9 s, 55 px/s to 340 px, a 10px band at 90%", () => {
    expect(motion).toEqual({
      breathingMin: 0.72,
      breathingMax: 1,
      breathingPeriod: 2.9,
      ringSpeed: 55,
      ringRadius: 340,
      ringBand: 10,
      ringStrength: 0.9,
      ringFadeStart: 60,
      ringFadeEnd: 340,
    });
  });
});

describe("breathing", () => {
  it("swings between the minimum and the maximum once per period", () => {
    expect(breathing(motion, 0)).toBeCloseTo(0.72, 10);
    expect(breathing(motion, 1.45)).toBeCloseTo(1, 10);
    expect(breathing(motion, 2.9)).toBeCloseTo(0.72, 10);
    expect(breathing(motion, 2.9 * 7 + 0.4)).toBeCloseTo(breathing(motion, 0.4), 10);
    for (let time = 0; time < 6; time += 0.05) {
      expect(breathing(motion, time)).toBeGreaterThanOrEqual(0.72 - 1e-12);
      expect(breathing(motion, time)).toBeLessThanOrEqual(1 + 1e-12);
    }
  });
});

describe("rings", () => {
  it("grow at 55 px per second from the center to 340 px, then start again", () => {
    expect(ringRadiusAt(motion, 0)).toBe(0);
    expect(ringRadiusAt(motion, 2)).toBe(110);
    expect(ringRadiusAt(motion, 340 / 55 - 0.001)).toBeCloseTo(339.945, 3);
    expect(ringRadiusAt(motion, 340 / 55 + 1)).toBeCloseTo(55, 6);
  });

  it("fade out between 60 px and 340 px", () => {
    expect(ringFade(motion, 0)).toBe(1);
    expect(ringFade(motion, 60)).toBe(1);
    expect(ringFade(motion, 200)).toBeCloseTo(0.5, 10);
    expect(ringFade(motion, 340)).toBe(0);
  });

  it("are a soft band 10 px wide at 90% strength", () => {
    expect(ringIntensity(motion, 50, 50)).toBeCloseTo(0.9, 10);
    expect(ringIntensity(motion, 200, 200)).toBeCloseTo(0.45, 10);
    // Inside the band the light falls off smoothly; at its edge and beyond there is none.
    expect(ringIntensity(motion, 50, 52.5)).toBeCloseTo(0.45, 10);
    expect(ringIntensity(motion, 50, 55)).toBe(0);
    expect(ringIntensity(motion, 50, 44)).toBe(0);
    expect(ringIntensity(motion, 50, 0)).toBe(0);
  });
});

describe("shouldAnimate", () => {
  const pulsing = { id: "a", source: "agent-a", weight: 1, pulses: true } as const;
  const still = { ...pulsing, pulses: false };
  const base: AnimationState = {
    reducedMotion: false,
    hidden: false,
    frozen: false,
    lights: [pulsing],
  };

  it("runs frames only while a light pulses, motion is allowed, the tab is seen and the clock is free", () => {
    expect(shouldAnimate(base)).toBe(true);
    expect(shouldAnimate({ ...base, reducedMotion: true })).toBe(false);
    expect(shouldAnimate({ ...base, hidden: true })).toBe(false);
    expect(shouldAnimate({ ...base, frozen: true })).toBe(false);
    expect(shouldAnimate({ ...base, lights: [still] })).toBe(false);
    expect(shouldAnimate({ ...base, lights: [] })).toBe(false);
  });
});

describe("fieldClock", () => {
  it("reads seconds from the dev override and nothing else", () => {
    expect(fieldClock("?field-time=2.5")).toBe(2.5);
    expect(fieldClock("?edge-shape=taxi&field-time=0")).toBe(0);
    expect(fieldClock("")).toBeNull();
    expect(fieldClock("?field-time=")).toBeNull();
    expect(fieldClock("?field-time=soon")).toBeNull();
    expect(fieldClock("?field-time=-1")).toBeNull();
  });
});
