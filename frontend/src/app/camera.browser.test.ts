import { fileURLToPath } from "node:url";

import { chromium, expect as browserExpect } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

import { assertPanelFrame } from "../../shots/framing";

import type {} from "./camera.browser.fixture";
import type { GraphProbeHost } from "../graph";

let server: ViteDevServer | undefined;
let browser: Browser | undefined;
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
}, 60_000);
afterAll(async () => {
  await browser?.close();
  await server?.close();
});

async function withPage(
  reducedMotion: "reduce" | "no-preference",
  test: (page: Page) => Promise<void>,
  demo: false | "after-mount" | "at-mount" = false,
) {
  if (!browser) throw new Error("Browser was not started");
  const page = await browser.newPage({
    viewport: demo ? { width: 1920, height: 1080 } : { width: 1600, height: 900 },
    reducedMotion,
  });
  page.setDefaultTimeout(15_000);
  const errors: string[] = [];
  const editorRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("monaco")) editorRequests.push(request.url());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && !message.location().url.endsWith("/@vite/client"))
      errors.push(message.text());
  });
  try {
    // Avoid starting the application's transport: only the graph harness is mounted.
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    // Install before Cytoscape imports capture the browser's performance clock.
    if (reducedMotion === "no-preference")
      await page.clock.install({ time: new Date("2026-10-27T15:00:00Z") });
    await page.goto(url);
    await page.waitForFunction(() => "graphTest" in window);
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
    );
    await page.evaluate(
      () =>
        new Promise<void>((resolve) => {
          requestAnimationFrame(() => {
            requestAnimationFrame(() => {
              resolve();
            });
          });
        }),
    );
    // Camera setup must not load an editor: cold Monaco modules and its worker made this
    // camera test depend on unrelated module/editor startup under machine load.
    expect(editorRequests).toEqual([]);
    await test(page);
    // Loading the cone for review or isolated focus checks must also stay editor-free.
    expect(editorRequests).toEqual([]);
    expect(errors).toEqual([]);
  } finally {
    await page.close();
  }
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
    };
  });
}

function emptyCanvasPoint(page: Page) {
  return page.evaluate(() => {
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
}

async function settled(page: Page) {
  await browserExpect
    .poll(async () => (await report(page, [])).animating, { timeout: 15_000 })
    .toBe(false);
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
              x1: b.x1 + pan.x,
              y1: b.y1 + pan.y,
              x2: b.x2 + pan.x,
              y2: b.y2 + pan.y,
            })),
          };
        });
        expect(view.zoom).toBe(1);
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
  30_000,
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
}, 30_000);

it("opening the panel frames the changed node and its callers; closing restores the camera", async () => {
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
    await settled(page);
    const open = await report(page, ids);
    await browserExpect(page.getByRole("region", { name: "Possibly affected" })).toHaveCount(0);
    const caller = await page.evaluate(() => window.graphTest.caller);
    const callers = (await report(page, [caller])).centers;
    expect(callers).toHaveLength(1);
    expect(open.cutOff).toEqual([]);
    expect(open.positions).toEqual(wide.positions);
    assertPanelFrame(await panelFrame(page), "live-diff");

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
}, 30_000);

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
}, 30_000);

it.each(["open", "review"] as const)(
  "%s eases the change and cone once; closing restores the personal camera",
  async (mode) => {
    await withPage(
      "no-preference",
      async (page) => {
        const ids = await wanted(page);
        const wide = await report(page, ids);
        expect(wide.zoom).toBe(1);
        const focus = page.getByRole("button", { name: "Zoom in", exact: true });
        await focus.focus();
        // Drive browser frames explicitly so renderer load cannot skip the entire 240ms move.
        const clockStart = new Date("2026-10-27T15:00:00Z");
        await page.clock.pauseAt(new Date(clockStart.getTime() + 60_000));
        const easing = await page.evaluate(async (mode) => {
          await window.graphTest[mode]();
          return document
            .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
            ?.oversightGraph?.animating();
        }, mode);
        expect(easing).toBe(true);
        await page.clock.runFor(16);
        const start = await report(page, ids);
        await page.clock.runFor(120);
        const middle = await report(page, ids);
        await page.clock.runFor(160);
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
          await page.clock.runFor(32);
          const afterEdit = await report(page, ids);
          expect(afterEdit.pan).toEqual(opened.pan);
          expect(afterEdit.zoom).toBe(opened.zoom);
          expect(afterEdit.animating).toBe(false);
        }
        await page.evaluate(() => {
          window.graphTest.close();
        });
        await page.clock.runFor(300);
        await settled(page);
        const closed = await report(page, ids);
        expect(closed.positions).toEqual(wide.positions);
        expect(closed.zoom).toBe(wide.zoom);
        expect(closed.pan).toEqual(wide.pan);
      },
      "at-mount",
    );
  },
  30_000,
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
  30_000,
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
      await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
      await page.evaluate(async (mode) => window.graphTest[mode](), mode);
      await page.clock.runFor(32);
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
      // Allow wheel delivery, then demand cancellation before the 240ms move could finish.
      await page.clock.runFor(32);
      const manual = await report(page, []);
      expect(manual.animating).toBe(false);
      expect({ pan: manual.pan, zoom: manual.zoom }).not.toEqual({
        pan: moving.pan,
        zoom: moving.zoom,
      });
      // Run past the interrupted move; no queued easing may take back the camera.
      await page.clock.runFor(300);
      const after = await report(page, []);
      expect(after.pan).toEqual(manual.pan);
      expect(after.zoom).toBe(manual.zoom);
      expect(after.positions).toEqual(before.positions);
    });
  },
  30_000,
);

it("refreshes a revealed cone row after pan, zoom and Fit without moving the camera itself", async () => {
  await withPage("reduce", async (page) => {
    const id = await page.evaluate(() => window.graphTest.caller);
    await page.evaluate(() => window.graphTest.review());
    const row = page.getByRole("region", { name: "Possibly affected", exact: true }).getByTitle(id);
    await browserExpect(row).toContainText("Out of view");
    await row.click();
    await browserExpect(row).not.toContainText("Out of view");
    const positions = (await report(page, [])).positions;
    // Start in empty canvas below the card, then drag far enough to hide the revealed caller.
    const graph = page.getByRole("img", { name: "Repository graph" });
    const box = await graph.boundingBox();
    if (!box) throw new Error("Missing graph box");
    const empty = await emptyCanvasPoint(page);
    await page.mouse.move(box.x + empty.x, box.y + empty.y);
    await page.mouse.down();
    await page.mouse.move(box.x + empty.x, box.y + empty.y - box.height, { steps: 8 });
    await page.mouse.up();
    await browserExpect(row).toContainText("Out of view");
    await browserExpect(row).toHaveRole("button");
    expect((await report(page, [id])).cutOff).toContain(id);
    // Let another browser frame pass: reporting visibility must never recenter the camera.
    const away = await report(page, [id]);
    await page.evaluate(() => new Promise(requestAnimationFrame));
    expect((await report(page, [id])).centers).toEqual(away.centers);
    await page.getByRole("button", { name: "Fit the graph to the view" }).click();
    await browserExpect(row).not.toContainText("Out of view");
    expect((await report(page, [id])).cutOff).toEqual([]);
    // Zooming at the viewport center hides the distant caller without a pan gesture.
    for (let step = 0; step < 5; step++)
      await page.getByRole("button", { name: "Zoom in", exact: true }).click();
    await browserExpect(row).toContainText("Out of view");
    await page.getByRole("button", { name: "Fit the graph to the view" }).click();
    await browserExpect(row).not.toContainText("Out of view");
    expect((await report(page, [])).positions).toEqual(positions);
  });
}, 30_000);

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
}, 30_000);
