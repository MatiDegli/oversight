import cytoscape from "cytoscape";

import { zoomBy } from "./camera";
import { slideRows } from "./foldMotion";

const frame = () =>
  new Promise<void>((resolve) =>
    requestAnimationFrame(() => {
      resolve();
    }),
  );

/** Exercise the real canvas renderer and native step events through both animation entry points. */
export async function runAnimation(kind: "camera" | "element") {
  const host = document.createElement("div");
  host.style.cssText = "width:600px;height:400px";
  document.body.append(host);
  const cy = cytoscape({
    container: host,
    layout: { name: "preset", fit: false },
    elements: [{ data: { id: "row" }, position: { x: 100, y: 100 } }],
  });
  try {
    const node = cy.getElementById("row");
    const initialZoom = cy.zoom();
    let renders = 0;
    const rendersPerStep: number[] = [];
    const forceRender = cy.forceRender.bind(cy);
    cy.forceRender = () => {
      renders++;
      return forceRender();
    };
    cy.on("step", () => {
      if (!cy.animated() && !node.animated()) return;
      const before = renders;
      // Observe after every listener's microtasks, including the installed workaround's.
      queueMicrotask(() => {
        queueMicrotask(() => rendersPerStep.push(renders - before));
      });
    });
    if (kind === "camera") zoomBy(cy, 1.5, false);
    else slideRows(cy, new Map([["row", { x: 200, y: 100 }]]), false);
    while (cy.animated() || node.animated()) await frame();
    await frame();
    await frame();
    const animationRenders = renders;
    await frame();
    await frame();
    return {
      rendersPerStep,
      animationRenders,
      idleRenders: renders - animationRenders,
      zoomRatio: cy.zoom() / initialZoom,
      x: node.position("x"),
    };
  } finally {
    cy.destroy();
    host.remove();
  }
}
