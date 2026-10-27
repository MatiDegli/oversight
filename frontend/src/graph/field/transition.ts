import type { FieldInput } from "./controller";
import type { FieldLight } from "./lights";
import type { FieldConfig } from "./model";

export interface FieldScene {
  lights: readonly FieldLight[];
  config: FieldConfig;
}

const mix = (a: number, b: number, t: number) => a + (b - a) * t;
const key = (light: FieldLight) => `${light.source}\u0000${light.id}`;
const pulse = (light: FieldLight) => light.pulseWeight ?? Number(light.pulses);

/** Evaluate the theme's CSS cubic-bezier at elapsed time (x), solving for its parameter first. */
export function ease(elapsed: number, easing: string): number {
  const [x1, y1, x2, y2] = easing.match(/[\d.]+/g)?.map(Number) ?? [];
  if (x1 === undefined || y1 === undefined || x2 === undefined || y2 === undefined) return elapsed;
  const curve = (t: number, a: number, b: number) =>
    3 * (1 - t) ** 2 * t * a + 3 * (1 - t) * t ** 2 * b + t ** 3;
  let low = 0;
  let high = 1;
  for (let i = 0; i < 20; i += 1) {
    const middle = (low + high) / 2;
    if (curve(middle, x1, x2) < elapsed) low = middle;
    else high = middle;
  }
  return curve((low + high) / 2, y1, y2);
}

function blendLights(
  from: readonly FieldLight[],
  to: readonly FieldLight[],
  t: number,
  pulses: boolean,
): FieldLight[] {
  const before = new Map(from.map((light) => [key(light), light]));
  const after = new Map(to.map((light) => [key(light), light]));
  return [...new Set([...before.keys(), ...after.keys()])].map((id) => {
    const a = before.get(id);
    const b = after.get(id);
    const light = b ?? a;
    if (!light) throw new Error("Missing transition light");
    const pulseWeight = pulses ? mix(a ? pulse(a) : 0, b ? pulse(b) : 0, t) : 0;
    return {
      ...light,
      weight: mix(a?.weight ?? 0, b?.weight ?? 0, t),
      pulses: pulseWeight > 0,
      pulseWeight,
    };
  });
}

/** Interrupted transitions start from the displayed scene, never from an obsolete target. */
export class FieldTransition {
  private from: FieldScene;
  private to: FieldScene;
  private started = 0;
  private duration = 0;
  private easing = "";
  private pulses = true;

  constructor(initial: FieldScene) {
    this.from = initial;
    this.to = initial;
  }

  active(now: number): boolean {
    return now < this.started + this.duration;
  }

  set(
    input: FieldInput,
    config: FieldConfig,
    now: number,
    duration: string,
    easing: string,
    jump: boolean,
  ) {
    this.from = this.sample(now);
    this.pulses = input.mode === "watch" || input.mode === "live-diff";
    const lights = this.pulses
      ? input.lights
      : input.lights.map((light) => ({ ...light, pulses: false, pulseWeight: 0 }));
    this.to = { lights, config };
    this.started = now;
    this.duration = jump ? 0 : Number.parseFloat(duration);
    this.easing = easing;
  }

  finish() {
    this.duration = 0;
  }

  sample(now: number): FieldScene {
    if (!this.active(now)) return this.to;
    const fraction = Math.max(0, (now - this.started) / this.duration);
    const t = fraction === 0 ? 0 : ease(fraction, this.easing);
    const a = this.from.config.profile;
    const b = this.to.config.profile;
    return {
      lights: blendLights(this.from.lights, this.to.lights, t, this.pulses),
      config: {
        ...this.to.config,
        profile: {
          near: mix(a.near, b.near, t),
          far: mix(a.far, b.far, t),
          gain: mix(a.gain, b.gain, t),
        },
      },
    };
  }
}
