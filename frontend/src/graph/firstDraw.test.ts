import type { Core } from "cytoscape";
import { afterEach, expect, it, vi } from "vitest";

afterEach(() => vi.unstubAllGlobals());

async function setup() {
  vi.resetModules();
  const firstDraw = await import("./firstDraw");
  let render!: () => void;
  let frame!: FrameRequestCallback;
  const nodes: unknown[] = [];
  const cy = {
    on: vi.fn((_event: string, callback: () => void) => {
      render = callback;
    }),
    off: vi.fn(),
    nodes: () => nodes,
    width: () => 800,
    height: () => 600,
  };
  const request = vi.fn((callback: FrameRequestCallback) => {
    frame = callback;
    return 9;
  });
  vi.stubGlobal("requestAnimationFrame", request);
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  return {
    ...firstDraw,
    cy,
    nodes,
    request,
    render: () => {
      render();
    },
    paint: () => {
      frame(16);
    },
  };
}

it("waits for populated render and the following frame, then signals only once", async () => {
  const scene = await setup();
  const ready = vi.fn();
  scene.afterFirstGraphDraw(ready);
  scene.observeFirstGraphDraw(scene.cy as unknown as Core);
  scene.render();
  expect(scene.request).not.toHaveBeenCalled();
  scene.nodes.push({});
  scene.render();
  scene.render();
  expect(scene.request).toHaveBeenCalledOnce();
  expect(ready).not.toHaveBeenCalled();
  scene.paint();
  await scene.whenFirstGraphDrawn();
  expect(ready).toHaveBeenCalledOnce();
  scene.observeFirstGraphDraw(scene.cy as unknown as Core);
  expect(scene.cy.on).toHaveBeenCalledOnce();
});

it("cancels observation and subscribers when a graph or app session unmounts", async () => {
  const scene = await setup();
  const ready = vi.fn();
  const unsubscribe = scene.afterFirstGraphDraw(ready);
  const stop = scene.observeFirstGraphDraw(scene.cy as unknown as Core);
  scene.nodes.push({});
  scene.render();
  unsubscribe();
  stop();
  expect(cancelAnimationFrame).toHaveBeenCalledWith(9);
  expect(scene.cy.off).toHaveBeenCalledWith("render", expect.any(Function));
  // A later mounted graph can complete the page's first draw.
  scene.observeFirstGraphDraw(scene.cy as unknown as Core);
  scene.render();
  scene.paint();
  await scene.whenFirstGraphDrawn();
  expect(ready).not.toHaveBeenCalled();
});
