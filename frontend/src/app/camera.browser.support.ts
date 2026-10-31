import type { Locator, Page } from "@playwright/test";
import { expect } from "vitest";

import type {} from "./camera.browser.fixture";
import type { GraphProbeHost } from "../graph";

type CameraPageState = {
  sharedPage: Page | undefined;
  errors: string[];
  editorRequests: string[];
};

declare global {
  interface Window {
    cameraFrames: { paint: (ms: number) => void };
    cameraClicks?: { trusted: boolean[]; controller: AbortController };
  }
}

/** Like GraphView.test.ts: each paint runs only the callbacks queued for that frame. */
async function controlFrames(page: Page) {
  await page.addInitScript(() => {
    let now = 0;
    let id = 0;
    const frames = new Map<number, FrameRequestCallback>();
    Object.defineProperty(performance, "now", { value: () => now });
    window.requestAnimationFrame = (callback) => {
      frames.set(++id, callback);
      return id;
    };
    window.cancelAnimationFrame = (key) => frames.delete(key);
    window.cameraFrames = {
      paint(ms) {
        now += ms;
        const pending = [...frames.values()];
        frames.clear();
        for (const callback of pending) callback(now);
      },
    };
    // Camera geometry needs Cytoscape's real 2d canvas, not the unrelated field's software GPU.
    // WebGL rendering has its own dotField.browser.test.ts coverage.
    // eslint-disable-next-line @typescript-eslint/unbound-method -- called with its canvas below
    const getContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (
      this: HTMLCanvasElement,
      type: string,
      ...rest: unknown[]
    ) {
      if (type === "webgl2") return null;
      return (getContext as (...args: unknown[]) => unknown).call(this, type, ...rest);
    } as typeof getContext;
  });
}

const paint = (page: Page, ms = 16) =>
  page.evaluate((delta) => {
    window.cameraFrames.paint(delta);
  }, ms);

async function withPage(
  { sharedPage, errors, editorRequests }: CameraPageState,
  reducedMotion: "reduce" | "no-preference",
  test: (page: Page) => Promise<void>,
  demo: false | "after-mount" | "at-mount" = false,
) {
  errors.length = 0;
  editorRequests.length = 0;
  if (!sharedPage) throw new Error("Browser was not started");
  const page = sharedPage;
  await page.setViewportSize(demo ? { width: 1920, height: 1080 } : { width: 1600, height: 900 });
  await page.emulateMedia({ reducedMotion });
  try {
    await page.evaluate((demo) => {
      if (demo === "at-mount") window.graphTest.loadDemo();
      else if (demo) window.graphTest.mountEmpty();
      else window.graphTest.mount();
    }, demo);
    if (demo === "after-mount") {
      await page.waitForFunction(
        () =>
          !!document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
            ?.oversightGraph,
        undefined,
        { polling: 10 },
      );
      // Like SSE: update the store before React commits the heading and footer for that graph.
      await page.evaluate(() => {
        window.graphTest.loadDemo();
      });
    }
    // The graph is created once its font settles.
    await page.waitForFunction(
      () =>
        (document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.nodeCenters().length ?? 0) > 0,
      undefined,
      { polling: 10 },
    );
    await paint(page);
    // Camera setup must not load an editor: cold Monaco modules and its worker made this
    // camera test depend on unrelated module/editor startup under machine load.
    expect(editorRequests).toEqual([]);
    await test(page);
    // Loading the cone for review or isolated focus checks must also stay editor-free.
    expect(editorRequests).toEqual([]);
    expect(errors).toEqual([]);
  } finally {
    await page.evaluate(() => {
      window.cameraClicks?.controller.abort();
      delete window.cameraClicks;
      window.graphTest.unmount();
    });
    // Dispose the old renderer before the next case, including its queued frame.
    await paint(page);
  }
}

/** Keep Playwright's visibility, stability and hit tests while its click awaits paint frames. */
async function click(page: Page, target: Locator) {
  const progress = { finished: false };
  const clicking = target.click().finally(() => {
    progress.finished = true;
  });
  await Promise.all([
    clicking,
    (async () => {
      while (!progress.finished) await paint(page);
    })(),
  ]);
}

/** The graph's own report: where the nodes are on screen, in the graph element's pixels. */
function report(page: Page, ids: string[]) {
  return page.evaluate((wanted) => {
    const element = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
    const probe = element?.oversightGraph;
    if (!element || !probe) throw new Error("The graph is not mounted");
    const drawn = probe.nodeCenters();
    const outside = (list: typeof drawn) =>
      list
        .filter(({ x, y }) => x < 0 || y < 0 || x > element.clientWidth || y > element.clientHeight)
        .map(({ id }) => id);
    return {
      cutOff: wanted.filter((id) => outside(drawn).includes(probe.representative(id))),
      centers: drawn.filter(({ id }) => wanted.some((node) => probe.representative(node) === id)),
      size: { width: element.clientWidth, height: element.clientHeight },
      leftInset: (() => {
        const card = document.querySelector('[aria-label="Possibly affected"]');
        return card ? card.getBoundingClientRect().right - element.getBoundingClientRect().left : 0;
      })(),
      cutOffAnywhere: outside(drawn),
      animating: probe.animating(),
      zoom: probe.zoom(),
      pan: probe.pan(),
      positions: probe.positions(),
    };
  }, ids);
}

function panelFrame(page: Page) {
  return page.evaluate(() => {
    const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
    const view = host?.oversightGraph;
    const clear = document.querySelector("[data-graph-clear-area]")?.getBoundingClientRect();
    if (!host || !view || !clear) throw new Error("Missing graph clear area");
    const graph = host.getBoundingClientRect();
    const overlay = document
      .querySelector('[aria-label="Possibly affected"]')
      ?.getBoundingClientRect();
    const card = overlay
      ? {
          x1: overlay.left - graph.left,
          y1: overlay.top - graph.top,
          x2: overlay.right - graph.left,
          y2: overlay.bottom - graph.top,
        }
      : undefined;
    return {
      zoom: view.zoom(),
      labelSizes: {
        name: Number.parseFloat(
          getComputedStyle(document.documentElement).getPropertyValue("--node-name-size"),
        ),
        state: Number.parseFloat(
          getComputedStyle(document.documentElement).getPropertyValue("--node-word-size"),
        ),
      },
      pan: view.pan(),
      size: { width: host.clientWidth, height: host.clientHeight },
      card,
      area: {
        x1: (card?.x2 ?? 0) + 40,
        y1: 40,
        x2: host.clientWidth - 40,
        y2: Math.min(host.clientHeight - 40, clear.bottom - host.getBoundingClientRect().top),
      },
      focus: view.nodeBounds("node.pending"),
      cone: view.nodeBounds("node.cone"),
      graph: view.nodeBounds(),
      modules: view.nodeBounds(".module"),
    };
  });
}

/** Independent start oracle: Fit's labeled bounds in the measured clear area, capped by legibility. */
function expectedWatchZoom(page: Page) {
  return page.evaluate(() => {
    const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
    const probe = host?.oversightGraph;
    const clear = document.querySelector("[data-graph-clear-area]")?.getBoundingClientRect();
    if (!host || !probe || !clear) throw new Error("Missing watch graph or clear area");
    const rect = host.getBoundingClientRect();
    const { width, height } = probe.metrics().viewport;
    const boxes = probe.nodeBounds();
    const fit = Math.min(
      (Math.min(width - 40, clear.right - rect.left) - 40) /
        Math.max(1, Math.max(...boxes.map((b) => b.x2)) - Math.min(...boxes.map((b) => b.x1))),
      (Math.min(height - 40, clear.bottom - rect.top) - Math.max(40, clear.top - rect.top)) /
        Math.max(1, Math.max(...boxes.map((b) => b.y2)) - Math.min(...boxes.map((b) => b.y1))),
    );
    const tokens = getComputedStyle(document.documentElement);
    const floor =
      11 /
      Math.min(
        Number.parseFloat(tokens.getPropertyValue("--node-name-size")),
        Number.parseFloat(tokens.getPropertyValue("--node-word-size")),
      );
    return Math.max(floor, Math.min(1, fit));
  });
}

function emptyCanvasPoint(page: Page) {
  return page.evaluate(() => {
    const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
    const probe = host?.oversightGraph;
    if (!probe) throw new Error("Missing graph");
    const { width, height } = probe.metrics().viewport;
    const zoom = probe.zoom();
    const pan = probe.pan();
    const boxes = probe.nodeBounds();
    for (let y = height - 90; y > height / 2; y -= 20)
      for (let x = width - 90; x > 0; x -= 20)
        if (
          !boxes.some(
            (b) =>
              x >= b.x1 * zoom + pan.x - 5 &&
              x <= b.x2 * zoom + pan.x + 5 &&
              y >= b.y1 * zoom + pan.y - 5 &&
              y <= b.y2 * zoom + pan.y + 5,
          ) &&
          host.contains(
            document.elementFromPoint(
              host.getBoundingClientRect().left + x,
              host.getBoundingClientRect().top + y,
            ),
          )
        )
          return { x, y };
    throw new Error("No empty canvas for panning");
  });
}

async function settled(page: Page) {
  // The first frame starts Cytoscape's animation; the next reaches the theme's duration.
  await paint(page);
  await page.evaluate(() => {
    const duration = Number.parseFloat(
      getComputedStyle(document.documentElement).getPropertyValue("--transition-duration"),
    );
    window.cameraFrames.paint(duration);
  });
  // Viewport reporting is batched onto the following frame, as in the actual browser.
  await paint(page);
  expect((await report(page, [])).animating).toBe(false);
}

/** The nodes the camera frames when the change opens: the change's own, in the recorded run. */
const wanted = (page: Page) => page.evaluate(() => [window.graphTest.changed]);

/** A real Cytoscape background drag, starting clear of the card and every node. */
async function panAway(page: Page) {
  // Start in empty canvas below the card, then drag far enough to hide the revealed caller.
  const graph = page.getByRole("img", { name: "Repository graph" });
  const box = await graph.boundingBox();
  if (!box) throw new Error("Missing graph box");
  const empty = await emptyCanvasPoint(page);
  await page.mouse.move(box.x + empty.x, box.y + empty.y);
  await page.mouse.down();
  await page.mouse.move(box.x + empty.x, box.y + empty.y - box.height, { steps: 8 });
  await page.mouse.up();
  await paint(page);
}

export {
  click,
  controlFrames,
  emptyCanvasPoint,
  expectedWatchZoom,
  paint,
  panelFrame,
  panAway,
  report,
  settled,
  wanted,
  withPage,
};
