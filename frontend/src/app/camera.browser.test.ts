import { launchBrowser } from "../browserLaunch.support";
import { fileURLToPath } from "node:url";

import { expect as browserExpect } from "@playwright/test";
import type { Browser, Locator, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

import { assertPanelFrame } from "../../shots/framing";

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
    cacheDir: fileURLToPath(new URL("../../../.shots/camera-vite-cache", import.meta.url)),
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
  browser = await launchBrowser();
  sharedPage = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  const page = sharedPage;
  page.setDefaultTimeout(5_000);
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.includes("monaco-editor") || path.endsWith("/editor/monaco.ts"))
      editorRequests.push(request.url());
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

it.each(["after-mount", "at-mount"] as const)(
  "starts the demo preset without splitting frames at canvas, heading or footer edges (%s)",
  async (demo) => {
    await withPage(
      "reduce",
      async (page) => {
        const view = await page.evaluate(() => {
          const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
          const probe = host?.oversightGraph;
          const overlays = document.querySelector("main")?.lastElementChild;
          if (!host || !probe || !overlays) throw new Error("Missing watch graph");
          const screen = host.getBoundingClientRect();
          const top = overlays.firstElementChild?.getBoundingClientRect();
          const bottom = overlays.lastElementChild?.getBoundingClientRect();
          const bounds = overlays.getBoundingClientRect();
          if (!top || !bottom) throw new Error("Missing watch overlays");
          const gap = Number.parseFloat(getComputedStyle(overlays).rowGap);
          const area = {
            x1: bounds.left - screen.left,
            y1: top.bottom + gap - screen.top,
            x2: bounds.right - screen.left,
            y2: bottom.top - gap - screen.top,
          };
          const pan = probe.pan();
          return {
            area,
            size: { width: host.clientWidth, height: host.clientHeight },
            zoom: probe.zoom(),
            frames: probe.nodeBounds(".module, .class").map((b) => ({
              id: b.id,
              // The start now zooms out: transform model boxes before testing their rendered edges.
              x1: b.x1 * probe.zoom() + pan.x,
              y1: b.y1 * probe.zoom() + pan.y,
              x2: b.x2 * probe.zoom() + pan.x,
              y2: b.y2 * probe.zoom() + pan.y,
            })),
          };
        });
        // The demo now starts at whole-graph Fit zoom, keeping its six frames whole.
        expect(view.zoom).toBeGreaterThan(0);
        expect(view.zoom).toBeLessThan(1);
        expect(view.area.y1).toBeGreaterThan(0);
        for (const b of view.frames) {
          const a = view.area;
          const contained = b.x1 >= a.x1 && b.y1 >= a.y1 && b.x2 <= a.x2 && b.y2 <= a.y2;
          const excluded =
            b.x2 <= 0 || b.y2 <= 0 || b.x1 >= view.size.width || b.y1 >= view.size.height;
          expect(contained || excluded, `${b.id}: ${JSON.stringify(b)}`).toBe(true);
        }
        const hub = view.frames.find((b) => b.id === "shop.pricing");
        expect(hub?.y1).toBeGreaterThanOrEqual(view.area.y1);
        expect(hub?.y2).toBeLessThanOrEqual(view.area.y2);
      },
      demo,
    );
  },
);

/** The nodes the camera frames when the change opens: the change's own, in the recorded run. */
const wanted = (page: Page) => page.evaluate(() => [window.graphTest.changed]);

it("live diff lights its callers and dims unrelated nodes, then clears that scope on close", async () => {
  await withPage("reduce", async (page) => {
    const scope = () =>
      page.evaluate(() => {
        const probe = document.querySelector<GraphProbeHost>(
          '[aria-label="Repository graph"]',
        )?.oversightGraph;
        if (!probe) throw new Error("The graph is not mounted");
        return {
          callers: probe.nodeCenters("node.cone:not(.dimmed)").map(({ id }) => id),
          dimmed: probe.nodeCenters("node.dimmed").map(({ id }) => id),
        };
      });
    expect(await scope()).toEqual({ callers: [], dimmed: [] });
    await page.evaluate(() => {
      window.graphTest.open();
    });
    const live = await scope();
    const caller = await page.evaluate(() => window.graphTest.caller);
    const visibleCaller = (await report(page, [caller])).centers;
    expect(visibleCaller).toHaveLength(1);
    expect(live.callers).toContain(visibleCaller[0]?.id);
    expect(live.dimmed.length).toBeGreaterThan(0);
    await browserExpect(page.getByRole("region", { name: "Possibly affected" })).toHaveCount(0);
    await page.evaluate(() => {
      window.graphTest.close();
    });
    expect(await scope()).toEqual({ callers: [], dimmed: [] });
  });
});

it("opening the panel frames the changed node and its callers; closing restores the camera", async () => {
  await withPage("no-preference", async (page) => {
    const ids = await wanted(page);
    expect(ids).toHaveLength(1);
    const wide = await report(page, ids);
    // This wide fixture cannot fit legibly: start at the floor, leaving distant nodes off-screen.
    const labels = (await panelFrame(page)).labelSizes;
    expect(wide.zoom * Math.min(labels.name, labels.state)).toBe(11);
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
    const visibleCaller = (await report(page, [caller])).centers;
    expect(visibleCaller).toHaveLength(1);
    expect(callers?.map(({ id }) => id)).toContain(visibleCaller[0]?.id);
    expect(open.cutOffAnywhere).toContain(visibleCaller[0]?.id);
    expect(open.cutOff).toEqual([]);
    expect(open.positions).toEqual(wide.positions);
    const framed = await panelFrame(page);
    // Camera-frame's oracle checks both axes: bounded centering, preferring uncut modules.
    assertPanelFrame(framed, "live-diff");
    const floor = 11 / Math.min(framed.labelSizes.name, framed.labelSizes.state);
    expect(floor).toBe(11 / 16);
    expect(open.zoom).toBe(floor);

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
    const before = await report(page, ids);
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
    // Restore the wide fixture's legible start, rather than the former 100% start.
    expect(closed.zoom).toBe(before.zoom);
  });
});

it.each(["open", "review"] as const)(
  "%s eases the change and cone once; closing restores the personal camera",
  async (mode) => {
    await withPage(
      "no-preference",
      async (page) => {
        const ids = await wanted(page);
        const wide = await report(page, ids);
        // The demo fits legibly below 100%; closing must restore that same camera.
        expect(wide.zoom).toBeLessThan(1);
        const focus = page.getByRole("button", { name: "Zoom in", exact: true });
        await focus.focus();
        // Drive browser frames explicitly so renderer load cannot skip the entire 240ms move.
        const easing = await page.evaluate(async (mode) => {
          await window.graphTest[mode]();
          return document
            .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
            ?.oversightGraph?.animating();
        }, mode);
        expect(easing).toBe(true);
        await paint(page, 16);
        const start = await report(page, ids);
        await paint(page, 120);
        const middle = await report(page, ids);
        await paint(page, 160);
        const opened = await report(page, ids);
        const framed = await panelFrame(page);
        expect(
          Math.min(framed.labelSizes.name, framed.labelSizes.state) * opened.zoom,
        ).toBeGreaterThanOrEqual(11 - 1e-6);
        assertPanelFrame(framed, mode === "open" ? "live-diff" : "review");
        expect(opened.zoom).toBeLessThan(1);
        expect(opened.cutOff).toEqual([]);
        expect(opened.positions).toEqual(wide.positions);
        expect(start.zoom).toBeGreaterThan(middle.zoom);
        expect(middle.zoom).toBeGreaterThan(opened.zoom);
        // cubic-bezier(0.2, 0.7, 0.2, 1) passes the linear midpoint by 120ms.
        expect((1 - middle.zoom) / (1 - opened.zoom)).toBeGreaterThan(0.6);
        expect(opened.animating).toBe(false);
        await browserExpect(focus).toBeFocused();
        if (mode === "open") {
          await page.evaluate(() => {
            window.graphTest.update();
          });
          await paint(page, 32);
          const afterEdit = await report(page, ids);
          expect(afterEdit.pan).toEqual(opened.pan);
          expect(afterEdit.zoom).toBe(opened.zoom);
          expect(afterEdit.animating).toBe(false);
        }
        await page.evaluate(() => {
          window.graphTest.close();
        });
        await paint(page, 300);
        await settled(page);
        const closed = await report(page, ids);
        expect(closed.positions).toEqual(wide.positions);
        expect(closed.zoom).toBe(wide.zoom);
        expect(closed.pan).toEqual(wide.pan);
      },
      "at-mount",
    );
  },
);

it.each(["open", "review"] as const)(
  "%s jumps under reduced motion and restores without easing",
  async (mode) => {
    await withPage(
      "reduce",
      async (page) => {
        const ids = await wanted(page);
        const before = await report(page, ids);
        await page.evaluate(async (mode) => window.graphTest[mode](), mode);
        const opened = await report(page, ids);
        expect(opened.animating).toBe(false);
        expect(opened.cutOff).toEqual([]);
        expect(opened.positions).toEqual(before.positions);
        await page.evaluate(() => {
          window.graphTest.close();
        });
        const closed = await report(page, ids);
        expect(closed.animating).toBe(false);
        expect(closed.zoom).toBe(before.zoom);
        expect(closed.pan).toEqual(before.pan);
      },
      "at-mount",
    );
  },
);

it.each([
  ["open", "pan"],
  ["open", "zoom"],
  ["review", "pan"],
  ["review", "zoom"],
] as const)(
  "%s: a person's %s interrupts the opening move and keeps control",
  async (mode, gesture) => {
    await withPage("no-preference", async (page) => {
      const before = await report(page, []);
      const graph = page.getByRole("img", { name: "Repository graph" });
      if (mode === "review") await page.evaluate(() => window.graphTest.prepareReview());
      await page.evaluate(async (mode) => window.graphTest[mode](), mode);
      await paint(page, 32);
      const moving = await report(page, []);
      expect(moving.animating).toBe(true);
      const box = await graph.boundingBox();
      if (!box) throw new Error("No graph box");
      const empty = await emptyCanvasPoint(page);
      await page.mouse.move(box.x + empty.x, box.y + empty.y);
      if (gesture === "pan") {
        await page.mouse.down();
        await page.mouse.move(box.x + empty.x - 140, box.y + empty.y + 60, { steps: 3 });
        await page.mouse.up();
      } else await page.mouse.wheel(0, -100);
      // Wait for real input delivery without advancing the opening animation to completion.
      await browserExpect.poll(async () => (await report(page, [])).animating).toBe(false);
      await paint(page, 32);
      const manual = await report(page, []);
      expect(manual.animating).toBe(false);
      expect({ pan: manual.pan, zoom: manual.zoom }).not.toEqual({
        pan: moving.pan,
        zoom: moving.zoom,
      });
      // Run past the interrupted move; no queued easing may take back the camera.
      await paint(page, 300);
      const after = await report(page, []);
      expect(after.pan).toEqual(manual.pan);
      expect(after.zoom).toBe(manual.zoom);
      expect(after.positions).toEqual(before.positions);
    });
  },
);

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

it("offers a reveal row for a caller under the bottom overlays, then clears it on reveal", async () => {
  await withPage("reduce", async (page) => {
    const id = await page.evaluate(() => window.graphTest.caller);
    await page.evaluate(() => window.graphTest.review());
    const row = page.getByRole("region", { name: "Possibly affected", exact: true }).getByTitle(id);
    await click(page, row);
    await paint(page);
    await browserExpect(row).not.toContainText("Out of view");
    const before = await report(page, []);
    const shift = await page.evaluate((id) => {
      const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
      const probe = host?.oversightGraph;
      const node = probe?.nodeBounds().find((node) => node.id === probe.representative(id));
      if (!host || !probe || !node) throw new Error("Missing caller");
      const box = host.getBoundingClientRect();
      return {
        box,
        dx: host.clientWidth - 40 - (node.x2 * probe.zoom() + probe.pan().x),
        dy: host.clientHeight - 20 - (node.y2 * probe.zoom() + probe.pan().y),
      };
    }, id);
    const empty = await emptyCanvasPoint(page);
    await page.mouse.move(shift.box.x + empty.x, shift.box.y + empty.y);
    await page.mouse.down();
    await page.mouse.move(shift.box.x + empty.x + shift.dx, shift.box.y + empty.y + shift.dy, {
      steps: 8,
    });
    await page.mouse.up();
    await paint(page);
    const occluded = await page.evaluate((id) => {
      const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
      const probe = host?.oversightGraph;
      const clear = document.querySelector("[data-graph-clear-area]")?.getBoundingClientRect();
      const node = probe?.nodeBounds().find((node) => node.id === probe.representative(id));
      if (!host || !probe || !node || !clear) throw new Error("Missing graph geometry");
      return {
        x1: node.x1 * probe.zoom() + probe.pan().x,
        x2: node.x2 * probe.zoom() + probe.pan().x,
        y1: node.y1 * probe.zoom() + probe.pan().y,
        y2: node.y2 * probe.zoom() + probe.pan().y,
        bottom: clear.bottom - host.getBoundingClientRect().top,
        width: host.clientWidth,
        height: host.clientHeight,
      };
    }, id);
    expect(occluded.x1).toBeGreaterThan(0);
    expect(occluded.x2).toBeLessThan(occluded.width);
    expect(occluded.y1).toBeGreaterThan(0);
    expect(occluded.y2).toBeLessThan(occluded.height);
    expect(occluded.y2).toBeGreaterThan(occluded.bottom);
    await browserExpect(row).toContainText("Out of view");
    await browserExpect(row).toHaveRole("button");
    await click(page, row);
    await paint(page);
    await browserExpect(row).not.toContainText("Out of view");
    expect((await report(page, [])).positions).toEqual(before.positions);
  });
});
