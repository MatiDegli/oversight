import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { chromium, expect as browserExpect } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

import type {} from "./dotField.browser.fixture";
import type { GraphProbeHost } from "../graph";

let server: ViteDevServer | undefined;
let browser: Browser | undefined;
let url: string;
let html: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/dot-field-vite-cache", import.meta.url)),
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
  browser = await chromium.launch();
}, 60_000);
afterAll(async () => {
  await browser?.close();
  await server?.close();
});

interface Options {
  reducedMotion?: "reduce" | "no-preference";
  noWebGL?: boolean;
  /** The renderer's draw call throws: the one error the field may log, once. */
  failingDraw?: boolean;
  scene?: "watch" | "live-diff" | "review";
  viewport?: { width: number; height: number };
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
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    await page.waitForFunction(() => "fieldTest" in window);
    const scene = options.scene ?? "watch";
    await page.evaluate((name) => {
      window.fieldTest.mount(name);
    }, scene);
    await page.waitForFunction(
      () =>
        (document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.nodeCenters().length ?? 0) > 0,
    );
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

it("sits under the Cytoscape canvas, takes no pointer and no focus, and shows through it", async () => {
  await withPage({ reducedMotion: "reduce" }, async (page) => {
    const stack = await page.evaluate(() => {
      const { graph, field } = window.fieldTest.elements();
      const wrapper = graph.parentElement;
      const rf = field.getBoundingClientRect();
      const rg = graph.getBoundingClientRect();
      const style = getComputedStyle(field);
      return {
        before: (field.compareDocumentPosition(graph) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0,
        sameBox:
          [rf.x, rf.y, rf.width, rf.height].join() === [rg.x, rg.y, rg.width, rg.height].join(),
        stacking: [style.position, style.zIndex, getComputedStyle(graph).zIndex],
        pointer: style.pointerEvents,
        hidden: field.getAttribute("aria-hidden"),
        focusable: field.tabIndex,
        cytoscapeCanvases: graph.querySelectorAll("canvas").length,
        atCenter: document.elementFromPoint(rg.x + rg.width / 2, rg.y + rg.height / 2) === field,
        wrapperBackground: wrapper ? getComputedStyle(wrapper).backgroundColor : null,
        graphBackground: getComputedStyle(graph).backgroundColor,
      };
    });
    expect(stack.before).toBe(true);
    expect(stack.sameBox).toBe(true);
    expect(stack.stacking).toEqual(["absolute", "auto", "auto"]);
    expect(stack.pointer).toBe("none");
    expect(stack.hidden).toBe("true");
    expect(stack.focusable).toBe(-1);
    expect(stack.cytoscapeCanvases).toBeGreaterThan(0);
    expect(stack.atCenter).toBe(false);
    // The ground is the wrapper's; the Cytoscape element over the field is transparent.
    expect(stack.wrapperBackground).not.toBe("rgba(0, 0, 0, 0)");
    expect(stack.graphBackground).toBe("rgba(0, 0, 0, 0)");

    // The field shows through the transparent graph canvas: it turns the page warm around the lit node.
    expect(await warmPixelsFromField(page)).toBeGreaterThan(500);
  });
}, 30_000);

async function settledGraph(page: Page) {
  // The camera has settled: nothing moves between two screenshots.
  await page.waitForFunction(
    () => window.fieldTest.elements().graph.oversightGraph?.animating() === false,
  );
}

/**
 * How many pixels of the graph's area turn warm (agent A's orange) because of the field: the page
 * is shot with the field's canvas shown and with it hidden, and the two are compared.
 */
async function warmPixelsFromField(page: Page): Promise<number> {
  await settledGraph(page);
  const rect = await page.evaluate(() => {
    const { x, y, width, height } = window.fieldTest.elements().graph.getBoundingClientRect();
    return { x, y, width, height };
  });
  const shown = await page.screenshot({ clip: rect });
  await page.evaluate(() => {
    window.fieldTest.elements().field.style.visibility = "hidden";
  });
  const hidden = await page.screenshot({ clip: rect });
  await page.evaluate(() => {
    window.fieldTest.elements().field.style.visibility = "";
  });
  return page.evaluate(
    async ({ withField, withoutField }) => {
      const decode = async (base64: string) => {
        const bitmap = await createImageBitmap(
          new Blob([Uint8Array.from(atob(base64), (c) => c.charCodeAt(0))]),
        );
        const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
        const context = canvas.getContext("2d");
        if (!context) throw new Error("No 2d context");
        context.drawImage(bitmap, 0, 0);
        return context.getImageData(0, 0, bitmap.width, bitmap.height).data;
      };
      const [a, b] = [await decode(withField), await decode(withoutField)];
      let warm = 0;
      for (let at = 0; at < a.length; at += 4) {
        const changed = a[at] !== b[at] || a[at + 1] !== b[at + 1] || a[at + 2] !== b[at + 2];
        if (changed && (a[at] ?? 0) > (a[at + 2] ?? 0) + 20) warm += 1;
      }
      return warm;
    },
    { withField: shown.toString("base64"), withoutField: hidden.toString("base64") },
  );
}

it("lights lamps near a lit node in its color and leaves the far cells dark", async () => {
  await withPage({ reducedMotion: "reduce" }, async (page) => {
    const seen = await look(page);
    expect(seen.state).toBe("webgl");
    expect(seen.draws).toBeGreaterThan(0);
    // The still light at the node is its full weight: at least level 2 of 3, in agent A's orange.
    expect(seen.near?.level).toBeGreaterThanOrEqual(2);
    const [r, , b] = seen.near?.color ?? [0, 0, 0];
    expect(r).toBeGreaterThan(b);
    // The GPU drew the lamp the model computed.
    const lit = seen.near?.color ?? [];
    for (const channel of [0, 1, 2]) {
      expect(Math.abs((seen.gpu?.[channel] ?? -99) - (lit[channel] ?? 99))).toBeLessThanOrEqual(2);
    }
    // Far from every lit node the lamp is off.
    expect(seen.farDistance).toBeGreaterThan(500);
    expect(seen.far?.level).toBe(0);
  });
}, 30_000);

it("keeps the graph on plain ground where there is no WebGL", async () => {
  await withPage({ noWebGL: true }, async (page) => {
    const seen = await page.evaluate(() => {
      const { graph, field } = window.fieldTest.elements();
      const probe = field.oversightField;
      return {
        state: probe?.state(),
        draws: probe?.draws(),
        pixel: probe?.pixel(10, 10),
        lamp: probe?.lamp(10, 10),
        animating: probe?.animating(),
        nodes: graph.oversightGraph?.nodeCenters().length ?? 0,
      };
    });
    expect(seen).toMatchObject({ state: "plain", draws: 0, pixel: null, lamp: null });
    expect(seen.animating).toBe(false);
    expect(seen.nodes).toBeGreaterThan(0);
    // Away from every node the page is the wrapper's ground, and the field adds nothing warm.
    await settledGraph(page);
    const rect = await page.evaluate(() => {
      const { x, y, width } = window.fieldTest.elements().graph.getBoundingClientRect();
      return { x: x + width - 16, y: y + 12, width: 4, height: 4 };
    });
    const png = await page.screenshot({ clip: rect });
    const ground = await page.evaluate(async (base64) => {
      const bitmap = await createImageBitmap(
        new Blob([Uint8Array.from(atob(base64), (c) => c.charCodeAt(0))]),
      );
      const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
      const context = canvas.getContext("2d");
      if (!context) throw new Error("No 2d context");
      context.drawImage(bitmap, 0, 0);
      const { graph } = window.fieldTest.elements();
      return {
        pixel: [...context.getImageData(1, 1, 1, 1).data],
        ground: getComputedStyle(graph.parentElement ?? graph).backgroundColor,
      };
    }, png.toString("base64"));
    const [r, g, b] = ground.pixel;
    expect(ground.ground).toBe(`rgb(${String(r)}, ${String(g)}, ${String(b)})`);
  });
}, 30_000);

it("gives up quietly when a draw fails: the graph stays and the ground is plain", async () => {
  await withPage({ failingDraw: true }, async (page) => {
    const seen = await page.evaluate(() => {
      const { graph, field } = window.fieldTest.elements();
      const probe = field.oversightField;
      // Cytoscape keeps rendering after the field has failed.
      return {
        state: probe?.state(),
        animating: probe?.animating(),
        draws: probe?.draws(),
        nodes: graph.oversightGraph?.nodeCenters().length ?? 0,
      };
    });
    expect(seen).toMatchObject({ state: "plain", animating: false, draws: 0 });
    expect(seen.nodes).toBeGreaterThan(0);
  });
}, 30_000);

it("falls back to plain ground after a real WebGL context loss", async () => {
  await withPage({}, async (page) => {
    await page.evaluate(() => {
      const { field } = window.fieldTest.elements();
      const gl = field.getContext("webgl2");
      const extension = gl?.getExtension("WEBGL_lose_context");
      if (!extension) throw new Error("No context-loss test extension");
      extension.loseContext();
    });
    await browserExpect
      .poll(() =>
        page.evaluate(() => {
          const { field } = window.fieldTest.elements();
          return {
            state: field.oversightField?.state(),
            hidden: field.hidden,
            animating: field.oversightField?.animating(),
            lamp: field.oversightField?.lamp(0, 0),
          };
        }),
      )
      .toEqual({ state: "plain", hidden: true, animating: false, lamp: null });
    expect(
      await page.evaluate(
        () => window.fieldTest.elements().graph.oversightGraph?.nodeCenters().length ?? 0,
      ),
    ).toBeGreaterThan(0);
  });
}, 30_000);

it("moves with breathing and rings, and stops moving under reduced motion", async () => {
  // With motion, frames run and what the lamps show changes: a ring spreads from the node.
  await withPage({ reducedMotion: "no-preference" }, async (page) => {
    const sample = () =>
      page.evaluate(
        ({ changed }) => {
          const { graph, field } = window.fieldTest.elements();
          const probe = field.oversightField;
          const center = graph.oversightGraph?.nodeCenters().find(({ id }) => id === changed);
          if (!probe || !center) throw new Error("The field or its lit node is missing");
          // A line of cells out from the node, to the right: the ring crosses it.
          const line: string[] = [];
          for (let x = 0; x < 340; x += 7) {
            const lamp = probe.lamp(center.x + x, center.y);
            line.push(lamp ? [lamp.level, ...lamp.color].join(",") : "-");
          }
          return { animating: probe.animating(), draws: probe.draws(), line: line.join(" ") };
        },
        { changed: "shop.pricing:compute_price" },
      );
    const first = await sample();
    expect(first.animating).toBe(true);
    await browserExpect
      .poll(async () => (await sample()).draws, { timeout: 15_000 })
      .toBeGreaterThan(first.draws + 3);
    await browserExpect
      .poll(async () => (await sample()).line, { timeout: 15_000 })
      .not.toBe(first.line);
    // A tab that is hidden stops the frames, and showing it again starts them.
    await page.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect((await sample()).animating).toBe(false);
    await page.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect((await sample()).animating).toBe(true);
  });

  // Under reduced motion no frame is scheduled, and the light is the still light.
  await withPage({ reducedMotion: "reduce" }, async (page) => {
    const sample = () =>
      page.evaluate(
        ({ changed }) => {
          const { graph, field } = window.fieldTest.elements();
          const probe = field.oversightField;
          const center = graph.oversightGraph?.nodeCenters().find(({ id }) => id === changed);
          if (!probe || !center) throw new Error("The field or its lit node is missing");
          // The same cells as above, computed afresh by a redraw at the current clock.
          probe.pixel(0, 0);
          const line: string[] = [];
          for (let x = 0; x < 340; x += 7) {
            const lamp = probe.lamp(center.x + x, center.y);
            line.push(lamp ? [lamp.level, ...lamp.color].join(",") : "-");
          }
          return { animating: probe.animating(), line: line.join(" ") };
        },
        { changed: "shop.pricing:compute_price" },
      );
    await settledGraph(page);
    const first = await sample();
    expect(first.animating).toBe(false);
    // Frames go by, and the clock with them; the lamps stay the still light.
    await page.evaluate(
      () =>
        new Promise<void>((resolve) => {
          let frames = 0;
          const count = () => {
            frames += 1;
            if (frames >= 20) resolve();
            else requestAnimationFrame(count);
          };
          requestAnimationFrame(count);
        }),
    );
    expect(await sample()).toEqual(first);
  });
}, 30_000);

it.each(["review", "live-diff"] as const)(
  "draws the %s light on its change and cone",
  async (scene) => {
    await withPage({ reducedMotion: "reduce", scene }, async (page) => {
      const seen = await look(page);
      expect(seen.state).toBe("webgl");
      expect(seen.near?.level).toBeGreaterThanOrEqual(2);
      const graph = await page.evaluate(() => {
        const probe = window.fieldTest.elements().graph.oversightGraph;
        return {
          cone: probe?.nodeCenters("node.cone:not(.dimmed)").length ?? 0,
          dimmed: probe?.nodeCenters("node.dimmed").length ?? 0,
          zoom: probe?.zoom() ?? 0,
          labelSizes: ["--node-name-size", "--node-word-size"].map((token) =>
            Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue(token)),
          ),
        };
      });
      expect(graph.cone).toBeGreaterThan(0);
      expect(graph.dimmed).toBeGreaterThan(0);
      expect(Math.min(...graph.labelSizes) * graph.zoom).toBeGreaterThanOrEqual(11);
      expect(graph.zoom).toBeLessThanOrEqual(1);
    });
  },
  30_000,
);

it("measures a frame at 1920 by 1080", async () => {
  // The lamps are computed on the CPU and drawn on the GPU; headless Chromium's GPU is a software
  // one, so the draw is an upper bound of what a real GPU takes.
  await withPage({ viewport: { width: 1920, height: 1080 } }, async (page) => {
    const cost = await page.evaluate(() => {
      const { field } = window.fieldTest.elements();
      const probe = field.oversightField;
      if (!probe) throw new Error("No probe");
      const runs = 60;
      const start = performance.now();
      // Each pixel call computes the lamps, uploads them, draws and reads back: a whole frame.
      for (let k = 0; k < runs; k += 1) probe.pixel(0, 0);
      return {
        browserFrameMs: (performance.now() - start) / runs,
        width: field.clientWidth,
        height: field.clientHeight,
        cells: Math.ceil(field.clientWidth / 7) * Math.ceil(field.clientHeight / 7),
      };
    });
    const dir = fileURLToPath(new URL("../../../.shots/", import.meta.url));
    mkdirSync(dir, { recursive: true });
    writeFileSync(`${dir}dot-field-frame-cost.json`, JSON.stringify(cost, null, 2));
    expect(cost.cells).toBeGreaterThan(30_000);
    expect(Number.isFinite(cost.browserFrameMs)).toBe(true);
  });
}, 60_000);
