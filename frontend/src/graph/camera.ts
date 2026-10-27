import type { AnimationOptions, Core, CollectionReturnValue } from "cytoscape";

import { getActiveTheme } from "../theme";

/** The first fit's padding in updateGraph, so closing the panel returns to the same picture. */
const PADDING = 40;

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

/** Full boxes only: the fit area has padding; the visible area does not. */
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
  const visible = { x1: 0, y1: 0, x2: size.width, y2: size.height };
  const area = {
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

/** Fit the lit nodes subject to the readable floor; the whole-graph watch fit has no floor. */
function cameraFor(
  cy: Core,
  eles: CollectionReturnValue,
  zoom: number,
  cone?: CollectionReturnValue,
  readable = false,
  occlusion?: Box,
): Framing | null {
  const theme = getActiveTheme();
  const bounds = (nodes: CollectionReturnValue) =>
    nodes.map((node) => ({
      id: node.id(),
      ...node.boundingBox({ includeLabels: true, includeOverlays: false }),
      distance: node.data("distance") as number | undefined,
    }));
  return frameBox(
    bounds(eles),
    { width: cy.width(), height: cy.height() },
    zoom,
    {
      padding: PADDING,
      occlusion,
      maxZoom: cy.maxZoom(),
      leftInset: readable
        ? Number.parseFloat(theme["cone-card-width"]) + Number.parseFloat(theme["spacing-overlay"])
        : 0,
      minZoom: readable
        ? Number.parseFloat(theme["cursor-name-size"]) / Number.parseFloat(theme["body-size"])
        : 0,
    },
    cone ? bounds(cone) : [],
  );
}

/**
 * Moves the camera after the left panel opened or closed, and nothing else: no node moves and no
 * layout runs. Opening keeps the zoom the watch view had, so labels stay at their size, and centers
 * the nodes in `focusIds` (the change's nodes) in the graph's visible area, between the panel and
 * the rail. With a review cone, it zooms out only as far as needed, keeping labels at least 12px.
 * At the floor, maximize complete callers by distance in the area to the right of the cone card.
 * Farther callers stay lit and listed in the cone card. Closing, or opening with no node to frame,
 * restores the saved watch camera, or fits the whole graph if none was saved. The move eases over
 * the theme's transition and jumps under reduced motion.
 */
export function frameCamera(
  cy: Core,
  focusIds: readonly string[],
  reduceMotion: boolean,
  {
    coneIds,
    watchZoom = cy.zoom(),
    restoreCamera,
    occlusion,
  }: {
    coneIds?: readonly string[];
    watchZoom?: number;
    restoreCamera?: Camera;
    occlusion?: Box;
  } = {},
): Framing | null {
  // The container just changed size; Cytoscape only learns it when told.
  cy.resize();
  const focus = focusIds.reduce((nodes, id) => nodes.union(cy.getElementById(id)), cy.collection());
  const cone = cy.nodes().filter((node) => coneIds?.includes(node.id()) ?? false);
  // Empty review cones still reserve their card and use the label floor.
  const camera: Framing | null = restoreCamera
    ? { ...restoreCamera, framed: new Set(), outOfView: new Set() }
    : focus.nonempty() || cone.nonempty()
      ? cameraFor(cy, focus, watchZoom, cone, coneIds !== undefined, occlusion)
      : cy.nodes().nonempty()
        ? cameraFor(cy, cy.nodes().filter("node"), Number.POSITIVE_INFINITY)
        : null;
  if (!camera) return null;
  camera.outOfView = new Set([
    ...camera.outOfView,
    ...[...focusIds, ...(coneIds ?? [])].filter((id) => cy.getElementById(id).empty()),
  ]);
  cy.stop();
  if (camera.zoom < cy.minZoom()) cy.minZoom(camera.zoom);
  const viewport = { zoom: camera.zoom, pan: camera.pan };
  if (reduceMotion) {
    cy.viewport(viewport);
    return camera;
  }
  const theme = getActiveTheme();
  cy.animate(viewport, {
    duration: Number.parseFloat(theme["transition-duration"]),
    // Cytoscape accepts the CSS form at run time; its types list only the named easings.
    easing: theme["transition-easing"] as AnimationOptions["easing"],
  });
  return camera;
}
