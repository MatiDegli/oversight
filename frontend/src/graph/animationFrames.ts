import type { Core } from "cytoscape";

const installed = new WeakSet<Core>();

/**
 * Work around cytoscape.js#3544: 3.34 clears an animation step's redraw flag in the same frame.
 * Request the next render in a microtask, after that flag has been cleared. Remove this helper
 * with Cytoscape 3.34.5 or later. Install once, before either camera or element animations start.
 */
export function drawEveryAnimationFrame(cy: Core): void {
  if (installed.has(cy)) return;
  // cy.animated() covers only the camera; this pool covers all animating elements.
  const pool = (cy as unknown as { _private: { aniEles?: { length: number } } })._private.aniEles;
  if (!pool || typeof pool.length !== "number") {
    throw new Error("Cytoscape animation pool changed; revisit the #3544 workaround");
  }
  installed.add(cy);
  cy.on("step", () => {
    if (!cy.animated() && pool.length === 0) return;
    queueMicrotask(() => {
      if (!cy.destroyed()) cy.forceRender();
    });
  });
}
