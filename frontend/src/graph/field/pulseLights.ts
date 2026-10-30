// The field's single pulse-light input. Travel supplies it today; other emitters can later
// contribute the same point-light values without changing the lamp accumulator.
import type { AgentFamily } from "../../theme";
import type { XY } from "../orthogonal";
import type { Pulse } from "./afterlight";
import { easeOut } from "./afterlight";
import { along } from "./marker";
import { pathLength } from "./travel";

export interface PulseLight {
  x: number;
  y: number;
  intensity: number;
  radius: number;
  source: AgentFamily;
}

export interface PulseLightTheme {
  "pulse-light-count": number;
  "pulse-light-spacing": string;
  "pulse-light-intensity": number;
  "pulse-light-curve": number;
  "pulse-light-radius": string;
  "pulse-light-limit": number;
}

/** Head plus trailing lights along the actual route; none once the head has arrived. */
export function pulseLights(
  pulses: readonly Pulse[],
  paths: ReadonlyMap<string, readonly XY[]>,
  theme: PulseLightTheme,
): PulseLight[] {
  const lights: PulseLight[] = [];
  const count = theme["pulse-light-count"];
  const spacing = Number.parseFloat(theme["pulse-light-spacing"]);
  const radius = Number.parseFloat(theme["pulse-light-radius"]);
  for (const pulse of pulses) {
    if (pulse.progress <= 0 || pulse.progress >= 1 || pulse.opacity <= 0) continue;
    const path = paths.get(pulse.key);
    if (!path || path.length < 2) continue;
    const length = pathLength(path);
    if (length <= 0) continue;
    const head = easeOut(pulse.progress) * length;
    for (let k = 0; k < count && lights.length < theme["pulse-light-limit"]; k++) {
      const point = along(path, Math.max(0, head - k * spacing) / length);
      lights.push({
        ...point,
        intensity: theme["pulse-light-intensity"] * (1 - k / count) ** theme["pulse-light-curve"],
        radius,
        source: pulse.merge.source,
      });
    }
    if (lights.length >= theme["pulse-light-limit"]) break;
  }
  return lights;
}
