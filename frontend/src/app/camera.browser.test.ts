import { fileURLToPath } from "node:url";

import { chromium, expect as browserExpect } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

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
    const centers = probe.nodeCenters();
    const outside = (list: typeof centers) =>
      list
        .filter(({ x, y }) => x < 0 || y < 0 || x > element.clientWidth || y > element.clientHeight)
        .map(({ id }) => id);
    return {
      // Hidden methods are drawn and revealed through their folded class row.
      cutOff: wanted.filter((id) => outside(centers).includes(probe.representative(id))),
      centers: centers.filter(({ id }) => wanted.some((node) => probe.representative(node) === id)),
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
    await settled(page);
    const open = await report(page, ids);
    await browserExpect(page.getByRole("region", { name: "Possibly affected" })).toHaveCount(0);
    const callers = await page.evaluate(() =>
      document
        .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
        ?.oversightGraph?.nodeCenters("node.cone:not(.dimmed)"),
    );
    expect(callers?.length).toBeGreaterThan(0);
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
