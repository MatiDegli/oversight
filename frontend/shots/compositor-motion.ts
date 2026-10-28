import type { Page } from "@playwright/test";
import type { GraphProbeHost } from "../src/graph";
import type { FieldHost } from "../src/graph/field/controller.ts";

/** Control only the JS animation clock; Chromium's trace keeps its real monotonic clock. */
export async function installMotionClock(page: Page) {
  await page.addInitScript(() => {
    const now = performance.now.bind(performance);
    const frame = requestAnimationFrame.bind(window);
    let clock: number | null = null;
    Object.defineProperty(performance, "now", { value: () => clock ?? now() });
    window.requestAnimationFrame = (callback) =>
      frame((time) => {
        callback(clock ?? time);
      });
    Reflect.set(window, "overlayClock", (value: number) => {
      clock = value;
    });
    Reflect.set(window, "overlayFrame", frame);
  });
}

/** The identical 180-frame drag pans the real graph and redraws its field under the cone. */
export async function moveCamera(page: Page) {
  return page.evaluate(async () => {
    const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
    const graph = host?.oversightGraph;
    const field = document.querySelector<FieldHost>(
      ".graph-stage > canvas, .graph-stage canvas[aria-hidden]",
    )?.oversightField;
    if (!host || !graph || field?.state() !== "webgl") throw new Error("Graph/field not ready");
    const frame = Reflect.get(window, "overlayFrame") as typeof requestAnimationFrame;
    const clock = Reflect.get(window, "overlayClock") as (time: number) => void;
    const box = host.getBoundingClientRect();
    const x = box.right - 60;
    const y = box.top + 180;
    const target = document.elementFromPoint(x, y);
    if (!target) throw new Error("Drag target missing");
    const dispatch = (type: string, offset: number, buttons: number) =>
      target.dispatchEvent(
        new MouseEvent(type, {
          bubbles: true,
          clientX: x + offset,
          clientY: y,
          button: 0,
          buttons,
          view: window,
        }),
      );
    const initial = { pan: graph.pan(), zoom: graph.zoom(), positions: graph.positions() };
    const draws = field.draws();
    const path: { x: number; y: number }[] = [];
    clock(60_000);
    dispatch("mousedown", 0, 1);
    for (let index = 0; index < 180; index++) {
      await new Promise<void>((done) =>
        frame(() => {
          done();
        }),
      );
      clock(60_000 + index * (1000 / 60));
      dispatch("mousemove", 32 * Math.sin((index / 179) * Math.PI * 2), 1);
      path.push(graph.pan());
    }
    dispatch("mouseup", 0, 0);
    await new Promise<void>((done) =>
      frame(() => {
        done();
      }),
    );
    if (!path.some((point) => Math.abs(point.x - initial.pan.x) > 16))
      throw new Error("Camera did not pan");
    if (field.draws() - draws < 100) throw new Error("Field did not follow camera motion");
    if (JSON.stringify(graph.positions()) !== JSON.stringify(initial.positions))
      throw new Error("Drag moved nodes");
    return { initial, path, fieldDraws: field.draws() - draws, frames: 180 };
  });
}
