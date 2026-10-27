import { fileURLToPath } from "node:url";

import { chromium, expect as browserExpect } from "@playwright/test";
import type { Browser, Locator, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

import type {} from "./camera.browser.fixture";
import type { GraphProbeHost } from "../graph";

let server: ViteDevServer | undefined;
let browser: Browser | undefined;
let sharedPage: Page | undefined;
const errors: string[] = [];
const editorRequests: string[] = [];
let url: string;
let html: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/app-vite-cache", import.meta.url)),
    // The fixture reads the recorded run from the repository's fixtures/.
    server: {
      host: "127.0.0.1",
      port: 0,
      strictPort: false,
      fs: { allow: [fileURLToPath(new URL("../../../", import.meta.url))] },
    },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import { graphTest } from "/src/app/camera.browser.fixture.tsx"; window.graphTest = graphTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await chromium.launch();
  sharedPage = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  const page = sharedPage;
  page.setDefaultTimeout(5_000);
  page.on("request", (request) => {
    if (request.url().includes("monaco")) editorRequests.push(request.url());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && !message.location().url.endsWith("/@vite/client"))
      errors.push(message.text());
  });
  await controlFrames(page);
  // Load the graph-only modules once; each case still mounts and disposes its own graph.
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.goto(url);
  await page.waitForFunction(() => "graphTest" in window, undefined, { polling: 10 });
}, 60_000);
afterAll(async () => {
  await sharedPage?.close();
  await browser?.close();
  await server?.close();
});

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
  reducedMotion: "reduce" | "no-preference",
  test: (page: Page) => Promise<void>,
) {
  errors.length = 0;
  editorRequests.length = 0;
  if (!sharedPage) throw new Error("Browser was not started");
  const page = sharedPage;
  await page.emulateMedia({ reducedMotion });
  try {
    await page.evaluate(() => {
      window.graphTest.mount();
    });
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
    const centers = probe.nodeCenters();
    const outside = (list: typeof centers) =>
      list
        .filter(({ x, y }) => x < 0 || y < 0 || x > element.clientWidth || y > element.clientHeight)
        .map(({ id }) => id);
    return {
      cutOff: outside(centers.filter(({ id }) => wanted.includes(id))),
      centers: centers.filter(({ id }) => wanted.includes(id)),
      size: { width: element.clientWidth, height: element.clientHeight },
      leftInset: (() => {
        const card = document.querySelector('[aria-label="Possibly affected"]');
        return card ? card.getBoundingClientRect().right - element.getBoundingClientRect().left : 0;
      })(),
      cutOffAnywhere: outside(centers),
      animating: probe.animating(),
      zoom: probe.zoom(),
      positions: probe.positions(),
    };
  }, ids);
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

it("opening the panel centers the changed node at the same zoom; closing restores the camera", async () => {
  await withPage("no-preference", async (page) => {
    const ids = await wanted(page);
    expect(ids).toHaveLength(1);
    const wide = await report(page, ids);
    // The graph starts at 100%: nothing is fitted, and what does not fit lies beyond the edge.
    expect(wide.zoom).toBe(1);
    expect(wide.cutOffAnywhere.length).toBeGreaterThan(0);
    const everything = Object.keys(wide.positions);
    const watch = (await report(page, everything)).centers;

    // The panel narrows the graph; with the camera left alone the far right is cut off.
    const eased = await page.evaluate(() => {
      window.graphTest.open();
      return document
        .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
        ?.oversightGraph?.animating();
    });
    expect(eased).toBe(true);
    // No wall-clock wait can finish the ease while frames are under test control.
    await paint(page);
    const start = await report(page, ids);
    await paint(page, 120);
    const halfway = await report(page, ids);
    expect(halfway.animating).toBe(true);
    expect(halfway.centers).not.toEqual(start.centers);
    await settled(page);
    const open = await report(page, ids);
    await browserExpect(page.getByRole("region", { name: "Possibly affected" })).toHaveCount(0);
    const callers = await page.evaluate(() =>
      document
        .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
        ?.oversightGraph?.nodeCenters("node.cone:not(.dimmed)"),
    );
    expect(callers?.length).toBeGreaterThan(0);
    const caller = await page.evaluate(() => window.graphTest.caller);
    expect(callers?.map(({ id }) => id)).toContain(caller);
    expect(open.cutOffAnywhere).toContain(caller);
    expect(open.cutOff).toEqual([]);
    expect(open.positions).toEqual(wide.positions);
    // The labels keep their size: the zoom is the watch fit's, not a fit of the narrowed graph.
    expect(open.zoom).toBeCloseTo(wide.zoom, 6);
    // Live diff centers only the changed node, even with real callers beyond its frame.
    const [center] = open.centers;
    expect(center?.x).toBeCloseTo((open.leftInset + open.size.width) / 2, 0);
    expect(center?.y).toBeCloseTo(open.size.height / 2, 0);

    await page.evaluate(() => {
      window.graphTest.close();
    });
    await settled(page);
    const closed = await report(page, everything);
    expect(closed.positions).toEqual(wide.positions);
    expect(closed.zoom).toBeCloseTo(wide.zoom, 6);
    // The person's own camera comes back, not a fit: every node is on screen where it was.
    expect(closed.centers).toEqual(watch);
  });
});

it("under reduced motion the camera jumps and nothing eases", async () => {
  await withPage("reduce", async (page) => {
    const ids = await wanted(page);
    const jumped = await page.evaluate(() => {
      window.graphTest.open();
      return document
        .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
        ?.oversightGraph?.animating();
    });
    expect(jumped).toBe(false);
    expect((await report(page, ids)).cutOff).toEqual([]);
    await page.evaluate(() => {
      window.graphTest.close();
    });
    // Closing jumps back to the camera the watch view had, with no animation.
    const closed = await report(page, ids);
    expect(closed.animating).toBe(false);
    expect(closed.zoom).toBe(1);
  });
});

/** A real Cytoscape background drag, starting clear of the card and every node. */
async function panAway(page: Page) {
  // Start in empty canvas below the card, then drag far enough to hide the revealed caller.
  const graph = page.getByRole("img", { name: "Repository graph" });
  const box = await graph.boundingBox();
  if (!box) throw new Error("Missing graph box");
  const empty = await page.evaluate(() => {
    const probe = document.querySelector<GraphProbeHost>(
      '[aria-label="Repository graph"]',
    )?.oversightGraph;
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
          )
        )
          return { x, y };
    throw new Error("No empty canvas for panning");
  });
  await page.mouse.move(box.x + empty.x, box.y + empty.y);
  await page.mouse.down();
  await page.mouse.move(box.x + empty.x, box.y + empty.y - box.height, { steps: 8 });
  await page.mouse.up();
  await paint(page);
}

it("refreshes a revealed cone row after pan, zoom and Fit without moving the camera itself", async () => {
  await withPage("reduce", async (page) => {
    const id = await page.evaluate(() => window.graphTest.caller);
    await page.evaluate(() => window.graphTest.review());
    await paint(page);
    expect(
      await page.evaluate(
        () =>
          document
            .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
            ?.oversightGraph?.nodeCenters("node.cone:not(.dimmed)").length,
      ),
    ).toBeGreaterThan(0);
    const row = page.getByRole("region", { name: "Possibly affected", exact: true }).getByTitle(id);
    // Synthetic dispatch must not replace the real input/actionability coverage of these controls.
    await page.evaluate(() => {
      const trusted: boolean[] = [];
      const controller = new AbortController();
      document.addEventListener(
        "click",
        (event) => {
          if (event.target instanceof Element && event.target.closest("button"))
            trusted.push(event.isTrusted);
        },
        { signal: controller.signal },
      );
      window.cameraClicks = { trusted, controller };
    });
    await browserExpect(row).toContainText("Out of view");
    await click(page, row);
    await paint(page);
    await browserExpect(row).not.toContainText("Out of view");
    const positions = (await report(page, [])).positions;
    await panAway(page);
    await browserExpect(row).toContainText("Out of view");
    await browserExpect(row).toHaveRole("button");
    expect((await report(page, [id])).cutOff).toContain(id);
    // Let another browser frame pass: reporting visibility must never recenter the camera.
    const away = await report(page, [id]);
    await paint(page);
    expect((await report(page, [id])).centers).toEqual(away.centers);
    await click(page, page.getByRole("button", { name: "Fit the graph to the view" }));
    await paint(page);
    await browserExpect(row).not.toContainText("Out of view");
    expect((await report(page, [id])).cutOff).toEqual([]);
    // Zooming at the viewport center hides the distant caller without a pan gesture.
    for (let step = 0; step < 5; step++)
      await click(page, page.getByRole("button", { name: "Zoom in", exact: true }));
    await paint(page);
    await browserExpect(row).toContainText("Out of view");
    await click(page, page.getByRole("button", { name: "Fit the graph to the view" }));
    await paint(page);
    await browserExpect(row).not.toContainText("Out of view");
    expect((await report(page, [])).positions).toEqual(positions);
    expect(await page.evaluate(() => window.cameraClicks?.trusted)).toEqual(
      Array<boolean>(8).fill(true),
    );
  });
});

it("the impact cone component keeps keyboard focus on a revealed row and never takes it on prop updates", async () => {
  await withPage("reduce", async (page) => {
    const id = await page.evaluate(async () => {
      await window.graphTest.mountCone();
      return window.graphTest.caller;
    });
    const row = page.getByTitle(id);
    await row.focus();
    await row.press("Enter");
    await browserExpect(row).not.toContainText("Out of view");
    await browserExpect(row.locator("..")).toBeFocused();
    const other = page.getByRole("button", { name: "Other control" });
    await other.focus();
    await page.evaluate(() => window.graphTest.mountCone(true));
    await browserExpect(other).toBeFocused();
    await page.evaluate(() => window.graphTest.mountCone(false));
    await browserExpect(other).toBeFocused();
  });
});

it("clears late diagnostics between shared-page cases", async () => {
  errors.push("Late error from the previous case");
  editorRequests.push("Late editor request from the previous case");
  await withPage("reduce", async (page) => {
    await paint(page);
    expect(errors).toEqual([]);
    expect(editorRequests).toEqual([]);
  });
});
