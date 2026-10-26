import type { AnimationOptions, BoundingBox12, Core, CollectionReturnValue } from "cytoscape";

import { getActiveTheme } from "../theme";

/** The first fit's padding in updateGraph, so closing the panel returns to the same picture. */
const PADDING = 40;

interface Camera {
  zoom: number;
  pan: { x: number; y: number };
}

export function prefersReducedMotion(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Zoom and pan that show `eles` whole in the current viewport, never zoomed in past `maxZoom`. */
function cameraFor(cy: Core, eles: CollectionReturnValue, maxZoom: number): Camera | null {
  const box: BoundingBox12 & { w: number; h: number } = eles.boundingBox({
    includeLabels: true,
    includeOverlays: false,
  });
  const [width, height] = [cy.width(), cy.height()];
  const room = [width - 2 * PADDING, height - 2 * PADDING] as const;
  if (!(room[0] > 0 && room[1] > 0)) return null;
  const fit = Math.min(room[0] / Math.max(box.w, 1), room[1] / Math.max(box.h, 1));
  const zoom = Math.min(Math.max(Math.min(fit, maxZoom), cy.minZoom()), cy.maxZoom());
  return {
    zoom,
    pan: {
      x: width / 2 - ((box.x1 + box.x2) / 2) * zoom,
      y: height / 2 - ((box.y1 + box.y2) / 2) * zoom,
    },
  };
}

/**
 * Moves the camera after the left panel opened or closed, and nothing else: no node moves and no
 * layout runs. Opening keeps the node the person is looking at and its neighbours in view, at the
 * current zoom or smaller; closing, or opening with no node, shows the whole graph again. The move
 * eases over the theme's transition and jumps under reduced motion.
 */
export function frameCamera(cy: Core, focusId: string | null, reduceMotion: boolean): void {
  // The container just changed size; Cytoscape only learns it when told.
  cy.resize();
  const focus = focusId === null ? cy.collection() : cy.getElementById(focusId);
  const camera =
    focus.nonempty() && cy.nodes().nonempty()
      ? cameraFor(cy, focus.closedNeighborhood(), cy.zoom())
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
