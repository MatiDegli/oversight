import { fileURLToPath } from "node:url";

import { expect as browserExpect } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect } from "vitest";

import type {} from "./dotField.browser.fixture";
import { fixtureCacheDir } from "../../shots/browserCache.support";
import type { GraphProbeHost } from "../graph";

let server: ViteDevServer | undefined;
let browser: Browser | undefined;
let url: string;
let html: string;
beforeAll(async () => {
  const { launchBrowser } = await import("../browserLaunch.support");
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fixtureCacheDir(expect.getState().testPath),
    server: {
      host: "127.0.0.1",
      port: 0,
      strictPort: false,
      fs: { allow: [fileURLToPath(new URL("../../../", import.meta.url))] },
    },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import { fieldTest } from "/src/app/dotField.browser.fixture.tsx"; window.fieldTest = fieldTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser({ webGL: true });
}, 60_000);
afterAll(async () => {
  await browser?.close();
  await server?.close();
});

/** A box in CSS px from the graph's top left. */
interface Rect {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

interface Options {
  reducedMotion?: "reduce" | "no-preference";
  noWebGL?: boolean;
  /** The renderer's draw call throws: the one error the field may log, once. */
  failingDraw?: boolean;
  scene?: "watch" | "live-diff" | "review" | "waiting" | "rest" | "both";
  /** The page's clock is the test's: time moves when the test says so. */
  clock?: boolean;
  viewport?: { width: number; height: number };
  /** The theme the page is mounted in; the graph reads it when it is made. */
  theme?: "day";
  /** Install barriers before the scene's effects and lazy imports start. */
  beforeMount?: (page: Page) => Promise<void>;
}

async function withPage(options: Options, test: (page: Page) => Promise<void>) {
  if (!browser) throw new Error("Browser was not started");
  const page = await browser.newPage({
    viewport: options.viewport ?? { width: 1600, height: 900 },
    reducedMotion: options.reducedMotion ?? "no-preference",
  });
  page.setDefaultTimeout(15_000);
  const errors: string[] = [];
  const stopped: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    // Vite's client wraps console.error, so the page's own errors name it as their source.
    if (message.text().startsWith("The dot field stopped drawing")) stopped.push(message.text());
    else if (!message.location().url.endsWith("/@vite/client")) errors.push(message.text());
  });
  try {
    if (options.noWebGL) {
      // A browser with WebGL switched off: every canvas refuses a webgl context.
      await page.addInitScript(() => {
        // eslint-disable-next-line @typescript-eslint/unbound-method -- called back with its own canvas
        const original = HTMLCanvasElement.prototype.getContext;
        HTMLCanvasElement.prototype.getContext = function (
          this: HTMLCanvasElement,
          type: string,
          ...rest: unknown[]
        ) {
          if (/webgl/.test(type)) return null;
          return (original as (...args: unknown[]) => unknown).call(this, type, ...rest);
        } as typeof original;
      });
    }
    if (options.failingDraw) {
      await page.addInitScript(() => {
        WebGL2RenderingContext.prototype.drawArrays = () => {
          throw new Error("test: the draw call fails");
        };
      });
    }
    if (options.clock) await page.clock.install();
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    await page.waitForFunction(() => "fieldTest" in window);
    await options.beforeMount?.(page);
    const scene = options.scene ?? "watch";
    if (options.theme) {
      await page.evaluate((name) => {
        document.documentElement.dataset.theme = name;
      }, options.theme);
    }
    await page.evaluate((name) => {
      window.fieldTest.mount(name);
    }, scene);
    await page.waitForFunction(
      () =>
        (document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.nodeCenters().length ?? 0) > 0,
    );
    // The field loads after the graph; every field assertion needs its initialized probe.
    await page.waitForFunction(() => !!window.fieldTest.elements().field.oversightField);
    if (options.clock) {
      // Stop time where the scene stands, once the camera has settled: from here the test moves it.
      await settledGraph(page);
      const now = await page.evaluate(() => Date.now());
      await page.clock.pauseAt(now + 1_000);
    }
    await test(page);
    expect(errors).toEqual([]);
    // The field says so once when it gives up, and not at all otherwise.
    if (options.failingDraw) await browserExpect.poll(() => stopped.length).toBe(1);
    expect(stopped).toHaveLength(options.failingDraw ? 1 : 0);
  } finally {
    await page.close();
  }
}

/** The lit node's centre and the probes' view of the page. */
function look(page: Page) {
  return page.evaluate(
    ({ changed }) => {
      const { graph, field } = window.fieldTest.elements();
      const probe = field.oversightField;
      const center = graph.oversightGraph?.nodeCenters().find(({ id }) => id === changed);
      if (!probe || !center) throw new Error("The field or its lit node is missing");
      const near = probe.lamp(center.x, center.y);
      // The cell farthest from the node, in a corner of the canvas.
      const corners = [
        [2, 2],
        [field.clientWidth - 2, 2],
        [2, field.clientHeight - 2],
        [field.clientWidth - 2, field.clientHeight - 2],
      ] as const;
      const farthest = corners
        .map(([x, y]) => ({ x, y, d: Math.hypot(x - center.x, y - center.y) }))
        .sort((a, b) => b.d - a.d)[0];
      if (!farthest) throw new Error("No corner");
      const cx = Math.floor(center.x / 7) * 7 + 3.5;
      const cy = Math.floor(center.y / 7) * 7 + 3.5;
      return {
        state: probe.state(),
        draws: probe.draws(),
        center,
        near,
        // What the GPU drew at the middle of that cell, read back.
        gpu: probe.pixel(cx, cy),
        far: probe.lamp(farthest.x, farthest.y),
        farDistance: farthest.d,
      };
    },
    { changed: "shop.pricing:compute_price" },
  );
}

async function settledGraph(page: Page) {
  // The camera has settled: nothing moves between two screenshots.
  await page.waitForFunction(
    () => window.fieldTest.elements().graph.oversightGraph?.animating() === false,
  );
}

/**
 * What the field draws over the graph's area, found by shooting the page with and without its
 * canvas: how many pixels it changes, how many of those are not gray, how many are warm (agent A's
 * orange), and how many lie inside an overlay, which must stay clean.
 */
async function fieldPixels(page: Page) {
  await settledGraph(page);
  const { rect, overlays } = await page.evaluate(() => {
    const { graph } = window.fieldTest.elements();
    const box = graph.getBoundingClientRect();
    const boxes = [
      ...document.querySelectorAll(
        '.pointer-events-auto, [data-field-mask], dialog[open], [role="dialog"], .shell-sidebar, .agents-expanded, .narrow-notice',
      ),
    ]
      .map((element) => element.getBoundingClientRect())
      .filter((r) => r.width > 0 && r.height > 0)
      .map((r) => ({
        // Inset by a pixel: an edge that falls inside a device pixel is not "under" the overlay.
        x1: r.left - box.x + 1,
        y1: r.top - box.y + 1,
        x2: r.right - box.x - 1,
        y2: r.bottom - box.y - 1,
      }));
    return {
      rect: { x: box.x, y: box.y, width: box.width, height: box.height },
      overlays: boxes,
    };
  });
  const shot = await page.screenshot({ clip: rect });
  await page.evaluate(() => {
    window.fieldTest.elements().field.style.visibility = "hidden";
  });
  const bare = await page.screenshot({ clip: rect });
  await page.evaluate(() => {
    window.fieldTest.elements().field.style.visibility = "";
  });
  return page.evaluate(
    async ({ withField, withoutField, boxes }) => {
      const decode = async (base64: string) => {
        const bitmap = await createImageBitmap(
          new Blob([Uint8Array.from(atob(base64), (c) => c.charCodeAt(0))]),
        );
        const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
        const context = canvas.getContext("2d");
        if (!context) throw new Error("No 2d context");
        context.drawImage(bitmap, 0, 0);
        return {
          width: bitmap.width,
          data: context.getImageData(0, 0, bitmap.width, bitmap.height).data,
        };
      };
      const [a, b] = [await decode(withField), await decode(withoutField)];
      const count = { changed: 0, colored: 0, warm: 0, inside: 0, sum: 0 };
      for (let at = 0; at < a.data.length; at += 4) {
        const [r, g, bl] = [a.data[at] ?? 0, a.data[at + 1] ?? 0, a.data[at + 2] ?? 0];
        const before = [b.data[at] ?? 0, b.data[at + 1] ?? 0, b.data[at + 2] ?? 0];
        if (r === before[0] && g === before[1] && bl === before[2]) continue;
        count.changed += 1;
        count.sum += Math.abs(r - (before[0] ?? 0)) + Math.abs(g - (before[1] ?? 0));
        if (Math.max(r, g, bl) - Math.min(r, g, bl) > 6) count.colored += 1;
        if (r > bl + 20) count.warm += 1;
        const x = (at / 4) % a.width;
        const y = Math.floor(at / 4 / a.width);
        if (boxes.some((box) => x >= box.x1 && x < box.x2 && y >= box.y1 && y < box.y2)) {
          count.inside += 1;
        }
      }
      return count;
    },
    { withField: shot.toString("base64"), withoutField: bare.toString("base64"), boxes: overlays },
  );
}

/**
 * How much light the field has computed: the sum of the lamp levels on a 14 px grid over the
 * canvas. It reads the field's own cells, so the graph's styling over them cannot move it.
 */
function lightMass(page: Page) {
  return page.evaluate(() => {
    const { field } = window.fieldTest.elements();
    const probe = field.oversightField;
    let mass = 0;
    for (let y = 7; y < field.clientHeight; y += 14)
      for (let x = 7; x < field.clientWidth; x += 14) mass += probe?.lamp(x, y)?.level ?? 0;
    return mass;
  });
}

/**
 * A few pixels of the graph's own antialiased edges sit over a contour line and blend with it, so
 * they change with the field hidden: that is not light. A lit node changes hundreds.
 */
const STRAY = 30;

const lifeOf = (page: Page) =>
  page.evaluate(() => window.fieldTest.elements().field.oversightField?.life());

export { browser, withPage, look, settledGraph, fieldPixels, lightMass, STRAY, lifeOf };
export type { Rect };
