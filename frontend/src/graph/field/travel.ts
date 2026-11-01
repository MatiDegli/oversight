// The merge's pulse on the graph: the way it takes from the agent's card to the merged node, and
// the trail behind its head, as plain geometry in the graph's pixels; and the SVG layer that
// draws it above the graph. The layer never takes the pointer or the focus, and the field's mask
// clips it, so a pulse never shows through the title, the legend or a card.
import { getActiveTheme } from "../../theme";
import type { XY } from "../orthogonal";

import type { Afterlight, Pulse } from "./afterlight";
import type { Halo } from "./milestones";
import { easeOut } from "./afterlight";
import { along } from "./marker";
import { unmaskedPath } from "./mask";
import type { Box } from "./model";

/** The length of a path in px. */
export function pathLength(points: readonly XY[]): number {
  let total = 0;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1];
    const b = points[i];
    if (a && b) total += Math.hypot(b.x - a.x, b.y - a.y);
  }
  return total;
}

/** The part of a path from arc length `from` to `to`, corners kept. Empty when `to` is not past `from`. */
export function slicePath(points: readonly XY[], from: number, to: number): XY[] {
  const total = pathLength(points);
  const low = Math.max(0, from);
  const high = Math.min(total, to);
  if (high <= low) return [];
  const length = total || 1;
  const result: XY[] = [along(points, low / length)];
  let walked = 0;
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1];
    const b = points[i];
    if (!a || !b) continue;
    walked += Math.hypot(b.x - a.x, b.y - a.y);
    if (walked > low && walked < high) result.push(b);
  }
  result.push(along(points, high / length));
  return result;
}

/**
 * The way a pulse takes to the node at `target`: it leaves `start`, runs level to the far end of
 * the edge into the node that is closest to it, and follows that edge. A node with no edge is
 * reached by a level run to its right side and a short vertical one.
 */
export function approachPath(start: XY, target: Box, routes: readonly (readonly XY[])[]): XY[] {
  const middle = (target.y1 + target.y2) / 2;
  const usable = routes.filter((route) => route.length >= 2);
  const first = (route: readonly XY[]) => route[0] ?? start;
  const best = usable.reduce<readonly XY[] | null>(
    (chosen, route) =>
      chosen === null ||
      Math.hypot(first(route).x - start.x, first(route).y - start.y) <
        Math.hypot(first(chosen).x - start.x, first(chosen).y - start.y)
        ? route
        : chosen,
    null,
  );
  if (best) return [start, { x: first(best).x, y: start.y }, ...best];
  const end = { x: target.x2, y: middle };
  const bend = { x: (start.x + end.x) / 2, y: start.y };
  return [start, bend, { x: bend.x, y: middle }, end];
}

/** A stretch of the trail, drawn with one opacity. */
export interface TrailPiece {
  points: XY[];
  opacity: number;
}

/**
 * The trail behind a head at arc length `head`: `bright` px that fade from full to a little over
 * half, then `faint` px that fade out with the square of what is left, cut into `count` pieces from
 * the head backwards.
 */
export function trailPieces(
  points: readonly XY[],
  head: number,
  look: Pick<Afterlight, "bright" | "faint">,
  count: number,
): TrailPiece[] {
  const reach = look.bright + look.faint;
  const step = reach / count;
  const pieces: TrailPiece[] = [];
  for (let i = 0; i < count; i++) {
    const middle = (i + 0.5) * step;
    const opacity =
      middle < look.bright
        ? 1 - 0.4 * (middle / look.bright)
        : 0.6 * (1 - (middle - look.bright) / look.faint) ** 2;
    const slice = slicePath(points, head - (i + 1) * step, head - i * step);
    if (slice.length >= 2 && opacity > 0) pieces.push({ points: slice, opacity });
  }
  return pieces;
}

const ns = "http://www.w3.org/2000/svg";
let nextId = 0;
const PIECES = 12;
const attr = (element: Element, key: string, value: string) => {
  if (element.getAttribute(key) !== value) element.setAttribute(key, value);
};
const d = (points: readonly XY[]) =>
  points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join("");

interface Drawn {
  group: SVGGElement;
  pieces: SVGPathElement[];
  head: SVGCircleElement;
}

/** An SVG over the graph that draws the pulses; clipped by the field's mask, and never interactive. */
export class TravelLayer {
  private readonly svg?: SVGSVGElement;
  private readonly clip?: SVGPathElement;
  private readonly haloItems = new Map<string, SVGRectElement>();
  private readonly items = new Map<string, Drawn>();

  constructor(canvas: HTMLCanvasElement) {
    if (!canvas.parentElement) return;
    this.svg = document.createElementNS(ns, "svg");
    this.svg.setAttribute("aria-hidden", "true");
    this.svg.setAttribute("data-field-travel", "");
    this.svg.style.cssText =
      "position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:1";
    const defs = document.createElementNS(ns, "defs");
    const clip = document.createElementNS(ns, "clipPath");
    const id = `field-travel-${String(++nextId)}`;
    clip.id = id;
    this.clip = document.createElementNS(ns, "path");
    clip.append(this.clip);
    defs.append(clip);
    this.svg.append(defs);
    this.svg.setAttribute("clip-path", `url(#${id})`);
    canvas.parentElement.append(this.svg);
  }

  /** How many pulses are on the page now. */
  count(): number {
    return this.items.size;
  }

  draw(
    pulses: readonly Pulse[],
    paths: ReadonlyMap<string, readonly XY[]>,
    masks: readonly Box[],
    width: number,
    height: number,
    look: Afterlight,
  ) {
    const { svg, clip } = this;
    if (!svg || !clip || (pulses.length === 0 && this.items.size === 0)) return;
    const theme = getActiveTheme();
    attr(clip, "d", unmaskedPath(masks, width, height));
    const shown = new Set<string>();
    for (const pulse of pulses) {
      const path = paths.get(pulse.key);
      if (!path || path.length < 2) continue;
      shown.add(pulse.key);
      let drawn = this.items.get(pulse.key);
      if (!drawn) {
        const group = document.createElementNS(ns, "g");
        const pieces = Array.from({ length: PIECES }, () => {
          const piece = document.createElementNS(ns, "path");
          piece.setAttribute("fill", "none");
          piece.setAttribute("stroke-linecap", "round");
          piece.setAttribute("stroke-linejoin", "round");
          return piece;
        });
        const head = document.createElementNS(ns, "circle");
        group.append(...pieces, head);
        svg.append(group);
        drawn = { group, pieces, head };
        this.items.set(pulse.key, drawn);
      }
      const color = theme[pulse.merge.source];
      const total = pathLength(path);
      const at = easeOut(pulse.progress) * total;
      attr(drawn.group, "opacity", String(pulse.opacity));
      const trail = trailPieces(path, at, look, PIECES);
      drawn.pieces.forEach((piece, i) => {
        const part = trail[i];
        attr(piece, "d", part ? d(part.points) : "");
        attr(piece, "stroke", color);
        attr(piece, "stroke-width", String(look.width));
        attr(piece, "stroke-opacity", String(part?.opacity ?? 0));
      });
      const point = along(path, total > 0 ? at / total : 1);
      attr(drawn.head, "cx", point.x.toFixed(1));
      attr(drawn.head, "cy", point.y.toFixed(1));
      attr(drawn.head, "r", String(look.width * 1.5));
      attr(drawn.head, "fill", color);
    }
    for (const [key, drawn] of this.items)
      if (!shown.has(key)) {
        drawn.group.remove();
        this.items.delete(key);
      }
  }

  halos(
    halos: readonly Halo[],
    boxes: ReadonlyMap<string, Box>,
    masks: readonly Box[],
    width: number,
    height: number,
  ) {
    if (!this.svg || !this.clip) return;
    const theme = getActiveTheme();
    attr(this.clip, "d", unmaskedPath(masks, width, height));
    const shown = new Set<string>();
    for (const halo of halos) {
      const box = boxes.get(halo.merge.id);
      if (!box) continue;
      shown.add(halo.key);
      let rect = this.haloItems.get(halo.key);
      if (!rect) {
        rect = document.createElementNS(ns, "rect");
        rect.setAttribute("data-merge-halo", "");
        this.svg.append(rect);
        this.haloItems.set(halo.key, rect);
      }
      const band = theme["merge-halo-width"] * (1 - halo.progress);
      attr(rect, "x", String(box.x1 - band / 2));
      attr(rect, "y", String(box.y1 - band / 2));
      attr(rect, "width", String(box.x2 - box.x1 + band));
      attr(rect, "height", String(box.y2 - box.y1 + band));
      attr(rect, "rx", String(Number.parseFloat(theme["radius-card"])));
      attr(rect, "fill", "none");
      attr(rect, "stroke", theme[halo.merge.source]);
      attr(rect, "stroke-width", String(band));
      attr(rect, "opacity", String(theme["merge-halo-opacity"]));
    }
    for (const [key, rect] of this.haloItems)
      if (!shown.has(key)) {
        rect.remove();
        this.haloItems.delete(key);
      }
  }

  dispose() {
    this.svg?.remove();
    this.items.clear();
    this.haloItems.clear();
  }
}
