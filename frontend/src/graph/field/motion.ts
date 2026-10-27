// The field's motion: the breathing of the editing node's light and the ring that leaves it. Both
// are pure functions of a clock in seconds, so the canvas only evaluates them. With no clock (null)
// there is no motion at all: that is the still light which reduced motion, review and the file
// view draw.
import type { FieldLight } from "./lights";

/** The theme values the motion reads; the full theme satisfies it. */
export interface MotionTheme {
  "breathing-min": number;
  "breathing-max": number;
  "breathing-duration": string;
  "ring-speed": number;
  "ring-radius": string;
  "ring-band-width": string;
  "ring-strength": number;
  "ring-fade-start": string;
  "ring-fade-end": string;
}

export interface Motion {
  breathingMin: number;
  breathingMax: number;
  /** Seconds for one full swing. */
  breathingPeriod: number;
  /** Pixels per second. */
  ringSpeed: number;
  ringRadius: number;
  ringBand: number;
  ringStrength: number;
  ringFadeStart: number;
  ringFadeEnd: number;
}

const px = Number.parseFloat;

export function motionOf(theme: MotionTheme): Motion {
  return {
    breathingMin: theme["breathing-min"],
    breathingMax: theme["breathing-max"],
    // The token is in milliseconds, as CSS writes a duration.
    breathingPeriod: px(theme["breathing-duration"]) / 1000,
    ringSpeed: theme["ring-speed"],
    ringRadius: px(theme["ring-radius"]),
    ringBand: px(theme["ring-band-width"]),
    ringStrength: theme["ring-strength"],
    ringFadeStart: px(theme["ring-fade-start"]),
    ringFadeEnd: px(theme["ring-fade-end"]),
  };
}

/**
 * The share of its weight an editing light has at `time`: it swings between the token's minimum and
 * maximum once per period, starting at the minimum.
 */
export function breathing(motion: Motion, time: number): number {
  const swing = 0.5 - 0.5 * Math.cos((2 * Math.PI * time) / motion.breathingPeriod);
  return motion.breathingMin + (motion.breathingMax - motion.breathingMin) * swing;
}

/** How far the ring has grown at `time`: from the node's center to the radius, then again. */
export function ringRadiusAt(motion: Motion, time: number): number {
  return (time * motion.ringSpeed) % motion.ringRadius;
}

/** The ring's strength at a radius: full until the fade starts, nothing at the end. */
export function ringFade(motion: Motion, radius: number): number {
  if (radius <= motion.ringFadeStart) return 1;
  const span = motion.ringFadeEnd - motion.ringFadeStart;
  return span <= 0 ? 0 : Math.max(0, 1 - (radius - motion.ringFadeStart) / span);
}

/**
 * The ring's intensity at a point `distance` from the node's center, with the ring at `radius`: a
 * soft band, as wide as the token says, at the ring's strength.
 */
export function ringIntensity(motion: Motion, radius: number, distance: number): number {
  const half = motion.ringBand / 2;
  const offset = Math.abs(distance - radius);
  if (offset >= half) return 0;
  return (
    motion.ringStrength *
    ringFade(motion, radius) *
    (0.5 + 0.5 * Math.cos((Math.PI * offset) / half))
  );
}

export interface AnimationState {
  reducedMotion: boolean;
  /** The tab is in the background: nothing is seen, so no frame is spent. */
  hidden: boolean;
  /** A clock held by the dev override (`?field-time=`): every frame would be the same. */
  frozen: boolean;
  lights: readonly FieldLight[];
}

/** Frames run only while something is moving and someone could see it. */
export function shouldAnimate({ reducedMotion, hidden, frozen, lights }: AnimationState): boolean {
  return !reducedMotion && !hidden && !frozen && lights.some((light) => light.pulses);
}

/**
 * A clock the dev override holds, in seconds: `?field-time=2.2` draws every frame of the motion as
 * it is at that second, so captures repeat. Null when absent or not a number of seconds.
 */
export function fieldClock(search: string): number | null {
  const value = new URLSearchParams(search).get("field-time");
  if (value === null || value.trim() === "") return null;
  const seconds = Number(value);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}
