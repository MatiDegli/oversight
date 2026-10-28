import type { BoxSource } from "./boxSource";
import type { FieldLight } from "./lights";
import type { Box } from "./model";
import type { XY } from "../orthogonal";
import { ease } from "./transition";

export const lightBoxKey = (light: FieldLight) =>
  light.marker ? `${light.marker}\u0000${light.id}` : light.id;

const anchor = (box: Box): XY => ({ x: box.x1, y: (box.y1 + box.y2) / 2 });

/** Arc-length sampling keeps the marker on the actual route, including its corners. */
export function along(points: readonly XY[], fraction: number): XY {
  const lengths = points.slice(1).map((point, index) => {
    const previous = points[index] ?? point;
    return Math.hypot(point.x - previous.x, point.y - previous.y);
  });
  let distance = lengths.reduce((sum, length) => sum + length, 0) * fraction;
  for (let index = 0; index < lengths.length; index++) {
    const length = lengths[index] ?? 0;
    const a = points[index];
    const b = points[index + 1];
    if (!a || !b) continue;
    if (distance <= length && length > 0) {
      const t = distance / length;
      return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
    }
    distance -= length;
  }
  return points.at(-1) ?? { x: 0, y: 0 };
}

interface Travel {
  id: string;
  from: string;
  started: number;
  origin?: XY;
}

/** Only travel geometry is local; ownership, work state and removal always come from input. */
export class MarkerMotion {
  private readonly travels = new Map<string, Travel>();
  private duration = 240;
  private easing = "";

  set(
    lights: readonly FieldLight[],
    source: BoxSource,
    now: number,
    duration: string,
    easing: string,
    jump: boolean,
  ) {
    this.duration = jump ? 0 : Number.parseFloat(duration);
    this.easing = easing;
    for (const light of lights) {
      if (!light.marker) continue;
      const previous = this.travels.get(light.marker);
      if (previous?.id === light.id) continue;
      const origin = previous && this.point(previous, source, now);
      this.travels.set(light.marker, {
        id: light.id,
        from: previous?.id ?? light.id,
        started: now,
        ...(previous && now < previous.started + this.duration ? { origin } : {}),
      });
    }
  }

  finish() {
    this.duration = 0;
  }

  private point(travel: Travel, source: BoxSource, now: number): XY | undefined {
    const boxes = source.boxes([travel.from, travel.id]);
    const target = boxes.get(travel.id);
    if (!target) return undefined;
    const end = anchor(target);
    if (this.duration === 0 || now >= travel.started + this.duration) return end;
    const before = boxes.get(travel.from);
    const start = travel.origin ?? (before ? anchor(before) : end);
    const route = travel.origin ? [] : (source.route?.(travel.from, travel.id) ?? []);
    const fraction = Math.max(0, (now - travel.started) / this.duration);
    return along([start, ...route, end], fraction === 0 ? 0 : ease(fraction, this.easing));
  }

  /** Translate the emitter's whole row box along with the marker, keeping its box-shaped light. */
  boxes(lights: readonly FieldLight[], source: BoxSource, now: number): Map<string, Box> {
    const boxes = source.boxes(lights.map((light) => light.id));
    const result = new Map(boxes);
    const retained = new Set(lights.map((light) => light.marker));
    for (const key of this.travels.keys()) if (!retained.has(key)) this.travels.delete(key);
    for (const light of lights) {
      const travel = light.marker && this.travels.get(light.marker);
      const box = boxes.get(light.id);
      const point = travel && this.point(travel, source, now);
      if (!box) continue;
      if (!point) {
        result.set(lightBoxKey(light), box);
        continue;
      }
      const end = anchor(box);
      result.set(lightBoxKey(light), {
        x1: point.x,
        x2: point.x + box.x2 - box.x1,
        y1: box.y1 + point.y - end.y,
        y2: box.y2 + point.y - end.y,
      });
    }
    return result;
  }
}
