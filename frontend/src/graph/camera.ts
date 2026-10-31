import type { AnimationOptions, Core, CollectionReturnValue } from "cytoscape";

import { getActiveTheme } from "../theme";

import { drawEveryAnimationFrame } from "./animationFrames";
import { changeFrame, changeZoom } from "./changeFrame";
import { visibleId } from "./foldVisibility";
import { FIT_PADDING } from "./geometry";
import { startingView } from "./startingView";

export { startingView } from "./startingView";

/** The padding the camera keeps around what it frames, in screen px. */
const PADDING = FIT_PADDING;

export interface Camera {
  zoom: number;
  pan: { x: number; y: number };
}

export interface Box {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

interface Limits {
  padding: number;
  maxZoom: number;
  minZoom?: number;
  /** Width of the left overlay plus its inset, before the usual padding. */
  leftInset?: number;
  /** The overlay's actual rendered box; independent of the column reserved for fitting. */
  occlusion?: Box;
  area?: Box;
}

export interface FrameNode extends Box {
  id: string;
  distance?: number;
}

function union(box: Box, other: Box): Box {
  return {
    x1: Math.min(box.x1, other.x1),
    y1: Math.min(box.y1, other.y1),
    x2: Math.max(box.x2, other.x2),
    y2: Math.max(box.y2, other.y2),
  };
}

export interface Framing extends Camera {
  framed: ReadonlySet<string>;
  outOfView: ReadonlySet<string>;
}

const EPSILON = 1e-7;

/** Full boxes only: visibility excludes overlays, without the fit area's side padding. */
export function nodesInViewport(
  nodes: readonly FrameNode[],
  camera: Camera,
  area: Box,
  occlusion?: Box,
): ReadonlySet<string> {
  return new Set(
    nodes
      .filter(
        (node) =>
          node.x1 * camera.zoom + camera.pan.x >= area.x1 - EPSILON &&
          node.x2 * camera.zoom + camera.pan.x <= area.x2 + EPSILON &&
          node.y1 * camera.zoom + camera.pan.y >= area.y1 - EPSILON &&
          node.y2 * camera.zoom + camera.pan.y <= area.y2 + EPSILON &&
          (!occlusion ||
            node.x2 * camera.zoom + camera.pan.x <= occlusion.x1 + EPSILON ||
            node.x1 * camera.zoom + camera.pan.x >= occlusion.x2 - EPSILON ||
            node.y2 * camera.zoom + camera.pan.y <= occlusion.y1 + EPSILON ||
            node.y1 * camera.zoom + camera.pan.y >= occlusion.y2 - EPSILON),
      )
      .map((node) => node.id),
  );
}

/**
 * Keep watch zoom or fit the whole cone down to the floor. Below that, sweep window edges to
 * maximize complete callers lexicographically by distance, keeping every change inside the fit
 * area. Equal counts choose the pan closest to centering the change (then x/y for determinism).
 */
export function frameBox(
  changes: readonly FrameNode[],
  size: { width: number; height: number },
  zoom: number,
  limits: Limits,
  cone: readonly FrameNode[] = [],
): Framing | null {
  const visible = { x1: 0, y1: 0, x2: size.width, y2: limits.area?.y2 ?? size.height };
  const area = limits.area ?? {
    x1: (limits.leftInset ?? 0) + limits.padding,
    y1: limits.padding,
    x2: size.width - limits.padding,
    y2: size.height - limits.padding,
  };
  const room = { width: area.x2 - area.x1, height: area.y2 - area.y1 };
  if (!(room.width > 0 && room.height > 0)) return null;
  const changedIds = new Set(changes.map((node) => node.id));
  const callers = cone.filter((node) => !changedIds.has(node.id));
  const nodes = [...changes, ...callers];
  const first = nodes[0];
  if (!first) return null;
  const whole = nodes.reduce(union, first);
  const fullFit = Math.min(
    room.width / Math.max(whole.x2 - whole.x1, 1),
    room.height / Math.max(whole.y2 - whole.y1, 1),
  );
  const floor = limits.minZoom ?? 0;
  const targetZoom = Math.min(limits.maxZoom, Math.max(floor, Math.min(fullFit, zoom)));
  const center = (box: Box) => ({
    x: (area.x1 + area.x2 - (box.x1 + box.x2) * targetZoom) / 2,
    y: (area.y1 + area.y2 - (box.y1 + box.y2) * targetZoom) / 2,
  });
  let pan = center(whole);
  if (fullFit < floor) {
    const changed = changes[0] ? changes.reduce(union, changes[0]) : undefined;
    const preferred = center(changed ?? whole);
    pan = preferred;
    // No pan can contain an oversized set of changes. Center that set as a stable fallback;
    // visibility below still reports any partially clipped box honestly.
    const range = changed
      ? {
          x1: area.x1 - changed.x1 * targetZoom,
          x2: area.x2 - changed.x2 * targetZoom,
          y1: area.y1 - changed.y1 * targetZoom,
          y2: area.y2 - changed.y2 * targetZoom,
        }
      : { x1: -Infinity, x2: Infinity, y1: -Infinity, y2: Infinity };
    if (range.x1 <= range.x2 && range.y1 <= range.y2) {
      const candidates = (axis: "x" | "y", start: "x1" | "y1", end: "x2" | "y2") => {
        const clamp = (value: number) => Math.max(range[start], Math.min(range[end], value));
        return [
          ...new Set([
            clamp(preferred[axis]),
            ...nodes.flatMap((node) => [
              clamp(area[start] - node[start] * targetZoom),
              clamp(area[end] - node[end] * targetZoom),
            ]),
          ]),
        ].sort((a, b) => a - b);
      };
      const distances = [...new Set(callers.map((node) => node.distance ?? Infinity))].sort(
        (a, b) => a - b,
      );
      let best: number[] | undefined;
      let bestOffset = Infinity;
      for (const x of candidates("x", "x1", "x2")) {
        for (const y of candidates("y", "y1", "y2")) {
          const shown = nodesInViewport(callers, { zoom: targetZoom, pan: { x, y } }, area);
          const score = distances.map(
            (distance) =>
              callers.filter(
                (node) => (node.distance ?? Infinity) === distance && shown.has(node.id),
              ).length,
          );
          const difference = best ? score.findIndex((count, i) => count !== best?.[i]) : -1;
          const offset = (x - preferred.x) ** 2 + (y - preferred.y) ** 2;
          if (
            !best ||
            (difference >= 0 && (score[difference] ?? 0) > (best[difference] ?? 0)) ||
            (difference < 0 && offset < bestOffset - EPSILON)
          ) {
            best = score;
            bestOffset = offset;
            pan = { x, y };
          }
        }
      }
    }
  }
  const camera = { zoom: targetZoom, pan };
  const onScreen = nodesInViewport(nodes, camera, visible, limits.occlusion);
  return {
    ...camera,
    framed: nodesInViewport(nodes, camera, area),
    outOfView: new Set(nodes.filter((node) => !onScreen.has(node.id)).map((node) => node.id)),
  };
}

export function prefersReducedMotion(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * The graph's hub: the function or method that the most calls reach, its first by id when several
 * tie. The demo repository has one central function with many callers, and the agents work around
 * it, so the starting view looks at the module that holds it. Null when nothing is called.
 */
export function hubOf(cy: Core): string | null {
  const callers = new Map<string, number>();
  for (const edge of cy.edges().toArray()) {
    if (edge.hasClass("imports") || edge.hasClass("inherits")) continue;
    const target = edge.target();
    if (!target.hasClass("function") && !target.hasClass("method")) continue;
    callers.set(target.id(), (callers.get(target.id()) ?? 0) + 1);
  }
  let hub: string | null = null;
  for (const [id, count] of callers) {
    const best = hub === null ? 0 : (callers.get(hub) ?? 0);
    if (count > best || (count === best && hub !== null && id < hub)) hub = id;
  }
  return hub;
}

/** Fit the whole graph when legible; otherwise keep an uncut view of the hub at the floor. */
export function startCamera(cy: Core, area?: Box): void {
  const nodes = cy.nodes();
  if (nodes.empty()) return;
  const hub = (cy.scratch("fullHub") as string | undefined) ?? hubOf(cy);
  const target = hub === null ? undefined : cy.getElementById(hub);
  const home = target?.ancestors().nonempty() ? target.ancestors().last() : target;
  const bounds = (collection: Pick<CollectionReturnValue, "boundingBox">): Box => {
    const box = collection.boundingBox({ includeLabels: false, includeOverlays: false });
    return { x1: box.x1, y1: box.y1, x2: box.x2, y2: box.y2 };
  };
  const whole = bounds(nodes);
  const focus = home?.nonempty() ? bounds(home) : whole;
  const frames = cy
    .nodes(".module, .class")
    .filter((frame) => frame.parent().empty())
    .map((frame) => bounds(frame));
  const size = { width: cy.width(), height: cy.height() };
  // Match the explicit Fit control's area, including its padding and the measured overlays.
  const fitArea = area
    ? {
        x1: PADDING,
        y1: Math.max(PADDING, area.y1),
        x2: Math.min(size.width - PADDING, area.x2),
        y2: Math.min(size.height - PADDING, area.y2),
      }
    : undefined;
  const fit = fitCamera(cy, fitArea);
  const limits = changeZoom();
  if (fit && fit.zoom >= limits.min && fit.zoom < limits.max) {
    cy.viewport(fit);
    return;
  }
  const zoom = fit && fit.zoom < limits.min ? limits.min : limits.max;
  const scale = (box: Box): Box => ({
    x1: box.x1 * zoom,
    y1: box.y1 * zoom,
    x2: box.x2 * zoom,
    y2: box.y2 * zoom,
  });
  const camera = startingView(
    scale(whole),
    scale(focus),
    { width: cy.width(), height: cy.height() },
    PADDING,
    frames.map(scale),
    area,
  );
  cy.viewport({ ...camera, zoom });
}

/** Expand around the current view's centre, retaining its context and every node's position. */
export function includeNewNodes(cy: Core, ids: readonly string[], reduceMotion: boolean): void {
  const nodes = cy.nodes().filter((node) => ids.includes(node.id()));
  if (nodes.empty()) return;
  const box = nodes.renderedBoundingBox({ includeLabels: true, includeOverlays: false });
  const width = cy.width();
  const height = cy.height();
  if (box.x1 >= 0 && box.y1 >= 0 && box.x2 <= width && box.y2 <= height) return;
  if (width <= PADDING * 2 || height <= PADDING * 2) return;
  const center = { x: width / 2, y: height / 2 };
  const scale = Math.min(
    1,
    (center.x - PADDING) / Math.max(Math.abs(box.x1 - center.x), Math.abs(box.x2 - center.x)),
    (center.y - PADDING) / Math.max(Math.abs(box.y1 - center.y), Math.abs(box.y2 - center.y)),
  );
  moveCamera(
    cy,
    {
      zoom: cy.zoom() * scale,
      pan: {
        x: center.x + (cy.pan().x - center.x) * scale,
        y: center.y + (cy.pan().y - center.y) * scale,
      },
    },
    reduceMotion,
  );
}

/** The zoom the person's zoom buttons keep the graph between: wide enough to see all, never huge. */
export const ZOOM_RANGE = { min: 0.2, max: 3 } as const;

/**
 * Zooms by a factor around the middle of the graph's view and moves no node. The result stays in
 * ZOOM_RANGE; the move eases, or jumps under reduced motion.
 */
export function zoomBy(cy: Core, factor: number, reduceMotion: boolean): void {
  const zoom = Math.min(ZOOM_RANGE.max, Math.max(ZOOM_RANGE.min, cy.zoom() * factor));
  const scale = zoom / cy.zoom();
  const center = { x: cy.width() / 2, y: cy.height() / 2 };
  moveCamera(
    cy,
    {
      zoom,
      pan: {
        x: center.x + (cy.pan().x - center.x) * scale,
        y: center.y + (cy.pan().y - center.y) * scale,
      },
    },
    reduceMotion,
  );
}

/**
 * Moves the camera, never past the zoom range the instance was made with: a target outside it is
 * brought to its edge about the middle of the view, so the view's middle stays where it was meant
 * to be. The range is the person's navigation limit; no move changes it.
 */
function moveCamera(cy: Core, camera: Camera, reduceMotion: boolean): void {
  cy.stop();
  const zoom = Math.min(cy.maxZoom(), Math.max(cy.minZoom(), camera.zoom));
  const scale = zoom / camera.zoom;
  const center = { x: cy.width() / 2, y: cy.height() / 2 };
  const viewport = {
    zoom,
    pan:
      scale === 1
        ? camera.pan
        : {
            x: center.x + (camera.pan.x - center.x) * scale,
            y: center.y + (camera.pan.y - center.y) * scale,
          },
  };
  if (reduceMotion) {
    cy.viewport(viewport);
    return;
  }
  const theme = getActiveTheme();
  drawEveryAnimationFrame(cy);
  cy.animate(viewport, {
    duration: Number.parseFloat(theme["transition-duration"]),
    // Cytoscape accepts the CSS form at run time; its types list only the named easings.
    easing: theme["transition-easing"] as AnimationOptions["easing"],
  });
}

/** The width the review's cone card takes at the graph's left, with its inset: not for nodes. */
function coneInset(theme: ReturnType<typeof getActiveTheme>): number {
  return Number.parseFloat(theme["cone-card-width"]) + Number.parseFloat(theme["spacing-overlay"]);
}

/**
 * Pans, at the zoom the camera has, so the node lies in the middle of the part of the view that the
 * cone card leaves free. It answers a click on a cone row that says "Out of view" and nothing else:
 * no event, no review and no resize calls it. It moves no node and keeps the zoom range. Returns
 * the camera it moves to, or null for a node that is not on the graph.
 */
export function revealNode(cy: Core, id: string, reduceMotion: boolean): Camera | null {
  const node = cy.getElementById(visibleId(cy, id));
  if (node.empty()) return null;
  const box = node.boundingBox({ includeLabels: true, includeOverlays: false });
  const zoom = cy.zoom();
  const camera = {
    zoom,
    pan: {
      x: (coneInset(getActiveTheme()) + cy.width()) / 2 - ((box.x1 + box.x2) / 2) * zoom,
      y: cy.height() / 2 - ((box.y1 + box.y2) / 2) * zoom,
    },
  };
  moveCamera(cy, camera, reduceMotion);
  return camera;
}

/** Fit all nodes for the explicit Fit control, within the person's navigation range. */
function fitCamera(cy: Core, area?: Box, occlusion?: Box): Framing | null {
  return frameBox(
    cy.nodes().map((node) => ({
      id: node.id(),
      ...node.boundingBox({ includeLabels: true, includeOverlays: false }),
    })),
    { width: cy.width(), height: cy.height() },
    Infinity,
    { padding: PADDING, minZoom: cy.minZoom(), maxZoom: cy.maxZoom(), area, occlusion },
  );
}

/** One mode-change move; closing restores the person's exact camera. No node is positioned. */
export function frameCamera(
  cy: Core,
  focusIds: readonly string[],
  reduceMotion: boolean,
  {
    coneIds,
    maxDepth = Infinity,
    restoreCamera,
    occlusion,
    area,
  }: {
    coneIds?: readonly string[];
    maxDepth?: number;
    restoreCamera?: Camera;
    occlusion?: Box;
    area?: Box;
  } = {},
): Framing | null {
  // The container just changed size; Cytoscape only learns it when told.
  cy.resize();
  const nodes = cy.nodes().map((node) => ({
    id: node.id(),
    ...node.boundingBox({ includeLabels: true, includeOverlays: false }),
  }));
  const available = [...focusIds, ...(coneIds ?? [])].some((id) =>
    cy.getElementById(visibleId(cy, id)).nonempty(),
  );
  const target =
    restoreCamera ??
    (available
      ? changeFrame({
          nodes,
          changed: focusIds,
          cone: (coneIds ?? []).map((id) => ({
            node_id: id,
            distance:
              (cy.getElementById(visibleId(cy, id)).data("distance") as number | undefined) ?? 1,
          })),
          maxDepth,
          representatives: cy.scratch("foldAliases") as ReadonlyMap<string, string> | undefined,
          graph: cy.nodes().boundingBox({ includeLabels: true, includeOverlays: false }),
          frames: cy
            .nodes(".module")
            .map((node) => node.boundingBox({ includeLabels: true, includeOverlays: false })),
          size: { width: cy.width(), height: cy.height() },
          area: area ?? {
            x1: (occlusion ? occlusion.x2 : 0) + PADDING,
            y1: PADDING,
            x2: cy.width() - PADDING,
            y2: cy.height() - PADDING,
          },
        })
      : fitCamera(cy, area, occlusion));
  const onScreen = target
    ? nodesInViewport(
        nodes,
        target,
        { x1: 0, y1: 0, x2: cy.width(), y2: area?.y2 ?? cy.height() },
        occlusion,
      )
    : new Set<string>();
  const camera = target
    ? {
        ...target,
        framed: new Set(
          [...focusIds, ...(coneIds ?? [])]
            .map((id) => visibleId(cy, id))
            .filter((id) => onScreen.has(id)),
        ),
        outOfView: new Set(nodes.filter((node) => !onScreen.has(node.id)).map((node) => node.id)),
      }
    : null;
  if (!camera) return null;
  camera.outOfView = new Set(
    [...focusIds, ...(coneIds ?? [])].filter(
      (id) =>
        camera.outOfView.has(visibleId(cy, id)) || cy.getElementById(visibleId(cy, id)).empty(),
    ),
  );
  moveCamera(cy, camera, reduceMotion);
  return camera;
}
