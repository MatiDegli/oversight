import cytoscape from "cytoscape";
import type { Core } from "cytoscape";
import { afterEach, describe, expect, it, vi } from "vitest";

import { drawEveryAnimationFrame } from "./animationFrames";

const graphs: Core[] = [];
function create() {
  const cy = cytoscape({ headless: true, styleEnabled: true });
  graphs.push(cy);
  return cy;
}
afterEach(() => {
  for (const cy of graphs.splice(0)) cy.destroy();
  vi.restoreAllMocks();
});

describe("animation frame redraw workaround", () => {
  it("requests a render after the step returns while the camera animates", async () => {
    const cy = create();
    const render = vi.spyOn(cy, "forceRender");
    drawEveryAnimationFrame(cy);
    cy.animate({ zoom: 2 }, { duration: 1000 });
    expect(cy.animated()).toBe(true);
    cy.emit("step");
    // Cytoscape's loop must have time to clear its redraw flag first.
    expect(render).not.toHaveBeenCalled();
    await Promise.resolve();
    expect(render).toHaveBeenCalledTimes(1);
  });

  it("requests a render for an element animation even when the camera is idle", async () => {
    const cy = create();
    const node = cy.add({ data: { id: "row" } });
    const render = vi.spyOn(cy, "forceRender");
    drawEveryAnimationFrame(cy);
    node.animate({ position: { x: 100, y: 0 } }, { duration: 1000 });
    expect(node.animated()).toBe(true);
    expect(cy.animated()).toBe(false);
    cy.emit("step");
    expect(render).not.toHaveBeenCalled();
    await Promise.resolve();
    expect(render).toHaveBeenCalledTimes(1);
  });

  it("requests no microtask or render when nothing animates", async () => {
    const cy = create();
    const render = vi.spyOn(cy, "forceRender");
    const schedule = vi.spyOn(globalThis, "queueMicrotask");
    drawEveryAnimationFrame(cy);
    cy.emit("step");
    await Promise.resolve();
    expect(schedule).not.toHaveBeenCalled();
    expect(render).not.toHaveBeenCalled();
  });

  it("installs once when both animation entry points use it", async () => {
    const cy = create();
    const render = vi.spyOn(cy, "forceRender");
    drawEveryAnimationFrame(cy);
    drawEveryAnimationFrame(cy);
    cy.animate({ zoom: 2 }, { duration: 1000 });
    cy.emit("step");
    await Promise.resolve();
    expect(render).toHaveBeenCalledTimes(1);
  });

  it("does not render a graph destroyed before the microtask runs", async () => {
    const cy = create();
    const render = vi.spyOn(cy, "forceRender");
    drawEveryAnimationFrame(cy);
    cy.animate({ zoom: 2 }, { duration: 1000 });
    cy.emit("step");
    cy.destroy();
    await Promise.resolve();
    expect(render).not.toHaveBeenCalled();
  });
});

it("guards Cytoscape's real private animation pool against an incompatible upgrade", () => {
  const cy = create();
  const node = cy.add({ data: { id: "row" } });
  const pool = (cy as unknown as { _private: { aniEles?: cytoscape.CollectionReturnValue } })
    ._private.aniEles;
  expect(pool).toBeDefined();
  expect(pool?.length).toBe(0);
  node.animate({ position: { x: 100, y: 0 } }, { duration: 1000 });
  expect(pool?.contains(node)).toBe(true);
});
