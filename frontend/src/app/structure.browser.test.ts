import { fileURLToPath } from "node:url";
import { chromium, expect as browserExpect } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import type { GraphProbeHost } from "../graph";
import type { structureTest } from "./structure.browser.fixture";
declare global {
  interface Window {
    structureTest: typeof structureTest;
  }
}
let server: ViteDevServer, browser: Browser, url: string, html: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/structure-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html data-theme="dark"><body style="margin:0"><script type="module">import "/src/theme/index.css"; import { structureTest } from "/src/app/structure.browser.fixture.tsx"; window.structureTest = structureTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await chromium.launch();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});
async function frame(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            resolve();
          }),
        ),
      ),
  );
  await browserExpect
    .poll(() =>
      page.evaluate(() =>
        document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.animating(),
      ),
    )
    .toBe(false);
}
async function withPage(run: (page: Page) => Promise<void>) {
  const page = await browser.newPage({
    viewport: { width: 1920, height: 1080 },
    reducedMotion: "reduce",
  });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    await page.waitForFunction(
      () =>
        !!document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]')?.oversightGraph,
    );
    await frame(page);
    await run(page);
    expect(errors).toEqual([]);
  } finally {
    await page.close();
  }
}
async function read(page: Page) {
  return page.evaluate(() => {
    const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
    const p = host?.oversightGraph;
    if (!host || !p) throw new Error("Missing probe");
    return {
      bounds: p.nodeBounds(),
      modules: p.nodeBounds(".module"),
      leaves: p.nodeBounds(":childless"),
      centers: p.nodeCenters(),
      cost: p.cost(),
      routes: p.routes(),
      positions: p.positions(),
      zoom: p.zoom(),
      metrics: p.metrics(),
      draggable: p.draggable(),
      pan: p.pan(),
      host: host.getBoundingClientRect().toJSON() as { x: number; y: number },
    };
  });
}
async function toggle(page: Page, id: string) {
  const r = await read(page),
    b = r.bounds.find((n) => n.id === id);
  if (!b) throw new Error(`Missing ${id}`);
  await page.mouse.click(
    r.host.x + (b.x1 + 40) * r.zoom + r.pan.x,
    r.host.y + (b.y1 + 28) * r.zoom + r.pan.y,
  );
  await frame(page);
}
function clearRoutes(r: Awaited<ReturnType<typeof read>>) {
  const gutters = new Map<number, Set<number>>();
  for (const edge of r.routes) {
    if (edge.possible) continue; // Straight context lines pass underneath opaque cards.
    const frame = r.modules.find((n) => n.id === edge.target.split(":")[0]);
    const tap = edge.points.at(-2);
    if (frame && tap && edge.points.length > 2 && tap.x < frame.x1) {
      const lanes = gutters.get(frame.x1) ?? new Set<number>();
      lanes.add(Math.round(tap.x * 1e5) / 1e5);
      gutters.set(frame.x1, lanes);
    }
    expect(edge.points.length, `route ${edge.id}`).toBeGreaterThan(1);
    for (const box of r.modules.filter((b) => b.id !== edge.source && b.id !== edge.target)) {
      // A row may enter its own module, never a third module.
      if (edge.source.startsWith(`${box.id}:`) || edge.target.startsWith(`${box.id}:`)) continue;
      for (let i = 1; i < edge.points.length; i++) {
        const a = edge.points[i - 1],
          b = edge.points[i];
        if (!a || !b) throw new Error("Missing route point");
        const snap = (n: number) => Math.round(n * 1e5) / 1e5;
        expect(
          Math.abs(a.x - b.x) < 1e-6
            ? box.x1 < snap(a.x) &&
                snap(a.x) < box.x2 &&
                Math.max(a.y, b.y) > box.y1 &&
                Math.min(a.y, b.y) < box.y2
            : box.y1 < snap(a.y) &&
                snap(a.y) < box.y2 &&
                Math.max(a.x, b.x) > box.x1 &&
                Math.min(a.x, b.x) < box.x2,
          `${edge.id} crosses ${box.id}`,
        ).toBe(false);
      }
    }
  }
  for (const lanes of gutters.values()) {
    const sorted = [...lanes].sort((a, b) => a - b);
    for (let i = 1; i < sorted.length; i++)
      expect((sorted[i] ?? 0) - (sorted[i - 1] ?? 0)).toBeGreaterThanOrEqual(12 - 1e-5);
  }
}
function clearBoxes(r: Awaited<ReturnType<typeof read>>) {
  // Compound containment is intentional; distinct leaf boxes and module boxes cannot intersect.
  for (const boxes of [r.modules, r.leaves])
    for (let i = 0; i < boxes.length; i++)
      for (const b of boxes.slice(i + 1)) {
        const a = boxes[i];
        if (!a) throw new Error("Missing box");
        expect(
          a.x1 < b.x2 && a.x2 > b.x1 && a.y1 < b.y2 && a.y2 > b.y1,
          `${a.id} intersects ${b.id}`,
        ).toBe(false);
      }
}
it(
  "starts with folded module cards, legible labels and overlay clearance",
  async () =>
    withPage(async (page) => {
      const r = await read(page);
      await browserExpect(
        page.getByRole("button", { name: "Modules", exact: true }),
      ).toHaveAttribute("aria-pressed", "true");
      await browserExpect(
        page.getByRole("button", { name: "Functions", exact: true }),
      ).toHaveAttribute("aria-pressed", "false");
      expect(r.leaves).toHaveLength(6);
      expect(r.modules).toHaveLength(6);
      expect(r.zoom).toBe(1);
      expect(r.metrics.minLabelPx).toBeGreaterThanOrEqual(11);
      expect(r.draggable.sort()).toEqual(r.modules.map((n) => n.id).sort());
      clearBoxes(r);
      clearRoutes(r);
      const overlays = await page
        .locator('[aria-label="Legend"], [aria-label="Zoom"], [aria-label="Graph detail"], h2')
        .evaluateAll((nodes) =>
          nodes.map(
            (n) =>
              n.getBoundingClientRect().toJSON() as {
                x: number;
                y: number;
                width: number;
                height: number;
              },
          ),
        );
      for (const b of r.leaves)
        for (const o of overlays)
          expect(
            b.x1 * r.zoom + r.pan.x + r.host.x < o.x + o.width &&
              b.x2 * r.zoom + r.pan.x + r.host.x > o.x &&
              b.y1 * r.zoom + r.pan.y + r.host.y < o.y + o.height &&
              b.y2 * r.zoom + r.pan.y + r.host.y > o.y,
          ).toBe(false);
      await browserExpect(page.getByLabel("Zoom percentage")).toHaveText("100%");
    }),
  30_000,
);
it(
  "counts both directions and keeps every other module fixed on header toggles",
  async () =>
    withPage(async (page) => {
      const before = await read(page),
        expected = await page.evaluate(() => window.structureTest.counts());
      expect(
        Object.fromEntries(before.routes.map((e) => [`${e.source}>${e.target}`, e.count])),
      ).toEqual(expected);
      expect(expected["shop.pricing>shop.cart"]).toBe(1);
      expect(expected["shop.cart>shop.pricing"]).toBeGreaterThan(1);
      await toggle(page, "shop.pricing");
      const open = await read(page);
      for (const module of before.modules.filter((n) => n.id !== "shop.pricing"))
        expect(open.modules.find((n) => n.id === module.id)).toEqual(module);
      clearBoxes(open);
      clearRoutes(open);
      expect(
        Object.fromEntries(
          open.routes.filter((e) => e.count > 1).map((e) => [`${e.source}>${e.target}`, e.count]),
        ),
      ).toEqual(Object.fromEntries(Object.entries(expected).filter(([, count]) => count > 1)));
      await toggle(page, "shop.pricing");
      expect((await read(page)).positions).toEqual(before.positions);
    }),
  30_000,
);
it(
  "detail and zoom controls preserve the preset and restore exact folds",
  async () =>
    withPage(async (page) => {
      const before = await read(page);
      await page.getByRole("button", { name: "Details", exact: true }).click();
      await frame(page);
      const details = await read(page);
      expect(details.leaves.length).toBeGreaterThan(before.leaves.length);
      clearBoxes(details);
      clearRoutes(details);
      const positions = details.positions;
      await page.getByRole("button", { name: "Zoom in", exact: true }).click();
      await frame(page);
      expect((await read(page)).positions).toEqual(positions);
      await browserExpect(page.getByLabel("Zoom percentage")).not.toHaveText("100%");
      await page.getByRole("button", { name: "Modules", exact: true }).click();
      await frame(page);
      expect((await read(page)).positions).toEqual(before.positions);
    }),
  30_000,
);
it(
  "reroutes while dragging with a median below 16ms and no frame reaching 100ms",
  async () =>
    withPage(async (page) => {
      await page.getByRole("button", { name: "Functions", exact: true }).click();
      await frame(page);
      await page.getByRole("button", { name: "Fit the graph to the view" }).click();
      await frame(page);
      const before = await read(page),
        b = before.modules.find((n) => n.id === "shop.pricing");
      if (!b) throw new Error("Missing module");
      const x = before.host.x + (b.x1 + 40) * before.zoom + before.pan.x,
        y = before.host.y + (b.y1 + 28) * before.zoom + before.pan.y;
      await page.mouse.move(x, y);
      await page.mouse.down();
      // Spread a short drag over enough frames that one GC/scheduling pause
      // cannot decide the typical routing cost. Keep the cold first reroute.
      for (let i = 1; i <= 32; i++) {
        await page.mouse.move(x + i, y + i / 2);
        await page.evaluate(
          () =>
            new Promise<void>((resolve) =>
              requestAnimationFrame(() => {
                resolve();
              }),
            ),
        );
      }
      const during = await read(page);
      expect(during.routes).not.toEqual(before.routes);
      const samples = [...during.cost.dragMs].sort((a, b) => a - b);
      expect(samples.length).toBeGreaterThanOrEqual(20);
      const middle = Math.floor(samples.length / 2);
      const median =
        ((samples[Math.ceil(samples.length / 2) - 1] ?? 0) + (samples[middle] ?? 0)) / 2;
      const worst = Math.max(...samples);
      console.log(
        `Graph drag: ${String(samples.length)} frames, median ${median.toFixed(2)} ms, max ${worst.toFixed(2)} ms`,
      );
      // Keep the median below one 60Hz frame. The 100ms ceiling leaves headroom
      // above the prior 60.7ms calibration outlier without accepting long stalls.
      // Proof: 20/20 each at CDP 4x, CDP 6x, and native on cores 0-3 with eight
      // busy loops; largest per-run median 8.6ms and worst sample 23.6ms.
      // Include the cold first reroute and every later sample.
      expect(median, "median drag reroute (ms)").toBeLessThan(16);
      expect(worst, "worst drag reroute (ms)").toBeLessThan(100);
      clearRoutes(during);
      await page.mouse.up();
    }),
  30_000,
);

it(
  "opening any module preserves every other frame, including stacked class modules",
  async () =>
    withPage(async (page) => {
      await page.getByRole("button", { name: "Fit the graph to the view" }).click();
      await frame(page);
      const before = await read(page);
      for (const module of before.modules) {
        await toggle(page, module.id);
        const opened = await read(page);
        for (const other of before.modules.filter((n) => n.id !== module.id))
          expect(opened.modules.find((n) => n.id === other.id)).toEqual(other);
        clearBoxes(opened);
        clearRoutes(opened);
        await toggle(page, module.id);
        expect((await read(page)).positions).toEqual(before.positions);
      }
    }),
  30_000,
);

it(
  "a hidden-method read opens its module and lights the folded class row",
  async () =>
    withPage(async (page) => {
      await page.evaluate(() => {
        window.structureTest.focus(["shop.cart:Cart.add"]);
      });
      await frame(page);
      const read = await page.evaluate(() => {
        const probe = document.querySelector<GraphProbeHost>(
          '[aria-label="Repository graph"]',
        )?.oversightGraph;
        return {
          representative: probe?.representative("shop.cart:Cart.add"),
          read: probe?.nodeCenters(".class.focus-A-read").map((n) => n.id),
          methods: probe?.nodeCenters(".method").length,
        };
      });
      expect(read).toEqual({
        representative: "shop.cart:Cart",
        read: ["shop.cart:Cart"],
        methods: 0,
      });
    }),
  30_000,
);
