import type { AnimationOptions, Core, CollectionReturnValue } from "cytoscape";

import { getActiveTheme } from "../theme";

/** The first fit's padding in updateGraph, so closing the panel returns to the same picture. */
const PADDING = 40;

export interface Camera {
  zoom: number;
  pan: { x: number; y: number };
}

interface Box {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

interface Limits {
  padding: number;
  minZoom: number;
  maxZoom: number;
}

/**
 * Where to put the camera so `box` (in graph units) sits in the middle of a viewport of the given
 * size. The zoom is `zoom` while the box fits inside the padding, and smaller only as far as needed
 * for it to fit, so the type size the person is reading does not change for no reason; it is never
 * larger than `zoom`. Null when the viewport leaves no room inside the padding.
 */
export function frameBox(
  box: Box,
  size: { width: number; height: number },
  zoom: number,
  limits: Limits,
): Camera | null {
  const room = [size.width - 2 * limits.padding, size.height - 2 * limits.padding] as const;
  if (!(room[0] > 0 && room[1] > 0)) return null;
  const fit = Math.min(
    room[0] / Math.max(box.x2 - box.x1, 1),
    room[1] / Math.max(box.y2 - box.y1, 1),
  );
  const framed = Math.min(Math.max(Math.min(fit, zoom), limits.minZoom), limits.maxZoom);
  return {
    zoom: framed,
    pan: {
      x: size.width / 2 - ((box.x1 + box.x2) / 2) * framed,
      y: size.height / 2 - ((box.y1 + box.y2) / 2) * framed,
    },
  };
}

export function prefersReducedMotion(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** The camera that shows `eles` whole in the current viewport, at `zoom` or smaller. */
function cameraFor(cy: Core, eles: CollectionReturnValue, zoom: number): Camera | null {
  const box = eles.boundingBox({ includeLabels: true, includeOverlays: false });
  return frameBox(box, { width: cy.width(), height: cy.height() }, zoom, {
    padding: PADDING,
    minZoom: cy.minZoom(),
    maxZoom: cy.maxZoom(),
  });
}

/**
 * Moves the camera after the left panel opened or closed, and nothing else: no node moves and no
 * layout runs. Opening keeps the zoom the watch view had, so labels stay at their size, and centers
 * the nodes in `focusIds` (the change's nodes) in the graph's visible area, between the panel and
 * the rail; it zooms out only as far as needed for them to fit with the padding. The nodes around
 * them may run past the edges. Closing, or opening with no node to frame, shows the whole graph
 * again. The move eases over the theme's transition and jumps under reduced motion.
 */
export function frameCamera(cy: Core, focusIds: readonly string[], reduceMotion: boolean): void {
  // The container just changed size; Cytoscape only learns it when told.
  cy.resize();
  const focus = cy.nodes().filter((node) => focusIds.includes(node.id()));
  const camera = focus.nonempty()
    ? cameraFor(cy, focus, cy.zoom())
    : cy.nodes().nonempty()
      ? cameraFor(cy, cy.elements(), Number.POSITIVE_INFINITY)
      : null;
  if (!camera) return;
  cy.stop();
  if (reduceMotion) {
    cy.viewport(camera);
    return;
  }
  const theme = getActiveTheme();
  cy.animate(camera, {
    duration: Number.parseFloat(theme["transition-duration"]),
    // Cytoscape accepts the CSS form at run time; its types list only the named easings.
    easing: theme["transition-easing"] as AnimationOptions["easing"],
  });
}
