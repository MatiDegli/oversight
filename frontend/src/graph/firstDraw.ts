import type { Core } from "cytoscape";

let finish: () => void = () => undefined;
let drawn = false;
const firstDraw = new Promise<void>((resolve) => {
  finish = resolve;
});

/** The first populated Cytoscape render has had a frame to reach the screen. */
export const whenFirstGraphDrawn = () => firstDraw;

export function afterFirstGraphDraw(callback: () => void): () => void {
  let active = true;
  void firstDraw.then(() => {
    if (active) callback();
  });
  return () => {
    active = false;
  };
}

/** Attached with the graph's box source, independently of WebGL or the dot field. */
export function observeFirstGraphDraw(cy: Core): () => void {
  if (drawn) return () => undefined;
  let frame: number | undefined;
  const render = () => {
    if (frame !== undefined || cy.nodes().length === 0 || cy.width() === 0 || cy.height() === 0)
      return;
    frame = requestAnimationFrame(() => {
      drawn = true;
      cy.off("render", render);
      finish();
    });
  };
  cy.on("render", render);
  return () => {
    cy.off("render", render);
    if (frame !== undefined) cancelAnimationFrame(frame);
  };
}
