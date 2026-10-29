import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
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
  scene?: "watch" | "live-diff" | "review" | "waiting" | "rest";
  /** The page's clock is the test's: time moves when the test says so. */
  clock?: boolean;
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
    if (options.clock) await page.clock.install();
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
    // The live diff opens on an agent that is editing: the working light, at its strongest. Review
    // opens on a change that waits on an open inbox entry: the steady, dimmer light.
    await withPage({ reducedMotion: "reduce", scene }, async (page) => {
      const seen = await look(page);
      expect(seen.state).toBe("webgl");
      expect(seen.near?.level).toBeGreaterThanOrEqual(scene === "review" ? 1 : 2);
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

/** The graph's area as a PNG, with the field as it stands. */
async function groundShot(page: Page) {
  await settledGraph(page);
  const rect = await page.evaluate(() => {
    const { x, y, width, height } = window.fieldTest.elements().graph.getBoundingClientRect();
    return { x, y, width, height };
  });
  return page.screenshot({ clip: rect });
}

/** How far two shots of the same area differ: the sum of the channel differences of their pixels. */
function differing(page: Page, a: Buffer, b: Buffer) {
  return page.evaluate(
    async ({ first, second }) => {
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
      const [x, y] = [await decode(first), await decode(second)];
      let sum = 0;
      for (let at = 0; at < x.length; at += 4)
        sum +=
          Math.abs((x[at] ?? 0) - (y[at] ?? 0)) +
          Math.abs((x[at + 1] ?? 0) - (y[at + 1] ?? 0)) +
          Math.abs((x[at + 2] ?? 0) - (y[at + 2] ?? 0));
      return sum;
    },
    { first: a.toString("base64"), second: b.toString("base64") },
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

it("draws the rest ground: gray lines, no colored light, and nothing under an overlay", async () => {
  await withPage({ scene: "rest", reducedMotion: "reduce" }, async (page) => {
    expect(await lifeOf(page)).toEqual({ pulses: 0, lights: 0, contours: true });
    const seen = await fieldPixels(page);
    // Lines over the empty ground, in every channel alike, and none of agent A's orange.
    expect(seen.changed).toBeGreaterThan(2000);
    expect(seen.colored).toBe(0);
    expect(seen.warm).toBe(0);
    // The mask holds: no line shows through the title, the legend, the zoom controls or a panel.
    expect(seen.inside).toBe(0);
  });
}, 60_000);

it("still draws the lines in the day theme, as ink on paper", async () => {
  await withPage({ scene: "rest", reducedMotion: "reduce" }, async (page) => {
    await page.evaluate(() => {
      document.documentElement.dataset.theme = "day";
    });
    await page.waitForFunction(
      () => window.fieldTest.elements().field.oversightField?.life().contours === true,
    );
    const seen = await fieldPixels(page);
    expect(seen.changed).toBeGreaterThan(2000);
    expect(seen.colored).toBe(0);
    expect(seen.inside).toBe(0);
  });
}, 60_000);

it("keeps the lines still under reduced motion, and lets them drift otherwise, without frames", async () => {
  await withPage({ scene: "rest", reducedMotion: "reduce", clock: true }, async (page) => {
    const before = await groundShot(page);
    await page.clock.fastForward(240_000);
    expect(await differing(page, before, await groundShot(page))).toBe(0);
    expect((await lifeOf(page))?.contours).toBe(true);
  });
  await withPage({ scene: "rest", clock: true }, async (page) => {
    const draws = () =>
      page.evaluate(() => window.fieldTest.elements().field.oversightField?.draws() ?? 0);
    const still = await draws();
    await page.clock.runFor(2_000);
    // The slow timer repaints, and no animation frame is running: the drift is not a loop.
    expect(await draws()).toBeGreaterThanOrEqual(still + 3);
    expect(
      await page.evaluate(() => window.fieldTest.elements().field.oversightField?.animating()),
    ).toBe(false);
    const first = await groundShot(page);
    await page.clock.runFor(1_000);
    const second = await groundShot(page);
    await page.clock.fastForward(240_000);
    const third = await groundShot(page);
    const seconds = await differing(page, first, second);
    const minutes = await differing(page, first, third);
    // A second moves the ground by a hair; minutes change its shape.
    expect(minutes).toBeGreaterThan(500);
    expect(seconds).toBeLessThan(minutes / 2);
  });
}, 120_000);

it("sends a merge's pulse along the graph to its node, then lights it and fades the light", async () => {
  await withPage({ scene: "waiting", clock: true }, async (page) => {
    const nodeCenters = () =>
      page.evaluate(() => window.fieldTest.elements().graph.oversightGraph?.nodeCenters());
    const camera = () =>
      page.evaluate(() => {
        const graph = window.fieldTest.elements().graph.oversightGraph;
        return [graph?.zoom(), graph?.pan(), document.activeElement?.tagName];
      });
    // The change waits on an open entry: its steady light is on before the decision.
    expect(await lifeOf(page)).toMatchObject({ pulses: 0, lights: 0 });
    const waiting = await lightMass(page);
    expect(waiting).toBeGreaterThan(50);
    expect((await fieldPixels(page)).warm).toBeGreaterThan(200);
    await page.evaluate(() => {
      window.fieldTest.advance(21);
    });
    // The merge is told, but the pulse waits a moment before it leaves the card.
    expect((await lifeOf(page))?.pulses).toBe(0);
    await page.clock.runFor(250 + 725);
    expect((await lifeOf(page))?.pulses).toBe(1);
    // The decision itself settles the graph (the node's pending look goes); from here on, the pulse
    // and the light must leave the camera and the nodes exactly where they are.
    const still = [await camera(), await nodeCenters()];
    const mid = await page.evaluate(() => {
      const layer = document.querySelector("[data-field-travel]");
      const head = layer?.querySelector("circle");
      return {
        clipped: layer?.getAttribute("clip-path"),
        pieces: [...(layer?.querySelectorAll("path[stroke]") ?? [])]
          .map((path) => path.getAttribute("d") ?? "")
          .filter((d) => d !== "").length,
        stroke: layer?.querySelector("path[stroke]")?.getAttribute("stroke"),
        color: getComputedStyle(document.documentElement).getPropertyValue("--agent-a").trim(),
        head: [head?.getAttribute("cx"), head?.getAttribute("cy")],
        pointer: layer ? getComputedStyle(layer).pointerEvents : null,
      };
    });
    expect(mid.clipped).toMatch(/^url\(#field-travel-/);
    expect(mid.pieces).toBeGreaterThan(2);
    expect(mid.stroke?.toLowerCase()).toBe(mid.color.toLowerCase());
    expect(mid.pointer).toBe("none");
    expect(mid.head.every((value) => Number.isFinite(Number(value)))).toBe(true);
    // The light holds its level through the travel: no dip when the entry closes, no flare either.
    const travelling = await lightMass(page);
    expect(travelling).toBeGreaterThan(waiting * 0.9);
    expect(travelling).toBeLessThan(waiting * 1.1);
    // Never moves the camera, the focus or a node.
    expect([await camera(), await nodeCenters()]).toEqual(still);
    // The pulse arrives at the node, and its trail fades; then the node's light is up and warm.
    await page.clock.fastForward(725 + 350 + 1500);
    expect(await lifeOf(page)).toMatchObject({ pulses: 0, lights: 1 });
    expect(
      await page.evaluate(() => document.querySelectorAll("[data-field-travel] g").length),
    ).toBe(0);
    const lit = await lightMass(page);
    expect(lit).toBeGreaterThan(waiting * 0.9);
    expect(lit).toBeLessThan(waiting * 1.1);
    // Soft: the light is not a block, its colour stays off the overlays, and it fades as time goes.
    await page.clock.fastForward(45_000);
    const half = await fieldPixels(page);
    expect(half.warm).toBeGreaterThan(0);
    expect(await lightMass(page)).toBeLessThan(lit * 0.8);
    expect(half.inside).toBe(0);
    await page.clock.fastForward(46_000);
    expect(await lifeOf(page)).toMatchObject({ pulses: 0, lights: 0 });
    expect((await fieldPixels(page)).warm).toBeLessThan(STRAY);
    expect([await camera(), await nodeCenters()]).toEqual(still);
  });
}, 120_000);

it("has no travel under reduced motion: the light stays where it was and fades as long", async () => {
  await withPage({ scene: "waiting", reducedMotion: "reduce", clock: true }, async (page) => {
    const waiting = await lightMass(page);
    expect(waiting).toBeGreaterThan(50);
    await page.evaluate(() => {
      window.fieldTest.advance(21);
    });
    await page.clock.runFor(100);
    expect(await lifeOf(page)).toMatchObject({ pulses: 0, lights: 1 });
    expect(
      await page.evaluate(() => document.querySelectorAll("[data-field-travel] g").length),
    ).toBe(0);
    const lit = await lightMass(page);
    expect(lit).toBeGreaterThan(waiting * 0.9);
    expect(lit).toBeLessThan(waiting * 1.1);
    await page.clock.fastForward(45_000);
    // The lamps thin out as the light fades; some are still lit, and fewer than before.
    expect((await fieldPixels(page)).warm).toBeGreaterThan(0);
    expect(await lightMass(page)).toBeLessThan(lit * 0.8);
    await page.clock.fastForward(46_000);
    expect(await lifeOf(page)).toMatchObject({ lights: 0 });
    expect((await fieldPixels(page)).warm).toBeLessThan(STRAY);
  });
}, 120_000);

/** The lamp level at the lit node while its agent is editing: the strongest light the field shows. */
async function workingLevel() {
  let level = 0;
  await withPage({ scene: "watch", reducedMotion: "reduce" }, async (page) => {
    level = (await look(page)).near?.level ?? 0;
  });
  return level;
}

it("holds a steady, dimmer light while the change waits on an inbox entry, however long", async () => {
  await withPage({ scene: "waiting", clock: true }, async (page) => {
    const working = await workingLevel();
    const seen = await look(page);
    expect(seen.near?.level).toBeGreaterThan(0);
    expect(seen.near?.level).toBeLessThan(working);
    const lit = await lightMass(page);
    expect(lit).toBeGreaterThan(50);
    expect((await fieldPixels(page)).warm).toBeGreaterThan(200);
    // No ring and no frames: the light has no motion, and a long wait changes nothing.
    expect(
      await page.evaluate(() => window.fieldTest.elements().field.oversightField?.animating()),
    ).toBe(false);
    await page.clock.fastForward(600_000);
    expect(await lifeOf(page)).toEqual({ pulses: 0, lights: 0, contours: true });
    expect(await lightMass(page)).toBe(lit);
  });
}, 60_000);

it("keeps the waiting light in review, and shows none once the change is done", async () => {
  await withPage({ scene: "review", reducedMotion: "reduce" }, async (page) => {
    expect((await look(page)).near?.level).toBeGreaterThan(0);
    expect((await fieldPixels(page)).warm).toBeGreaterThan(200);
  });
  await withPage({ scene: "rest", reducedMotion: "reduce" }, async (page) => {
    expect((await look(page)).near?.level).toBe(0);
    expect((await fieldPixels(page)).warm).toBeLessThan(STRAY);
  });
}, 60_000);

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

const median = (values: number[]) => {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)] ?? 0;
};
const p95 = (values: number[]) => {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * 0.95) - 1)] ?? 0;
};
const summary = (values: number[]) => ({
  samples: values.length,
  medianMs: Number(median(values).toFixed(2)),
  p95Ms: Number(p95(values).toFixed(2)),
});

/** The time of whole frames: lamps, ground, upload, draw and read back, one call to the probe each. */
function wholeFrames(page: Page, runs: number) {
  return page.evaluate((count) => {
    const probe = window.fieldTest.elements().field.oversightField;
    if (!probe) throw new Error("No probe");
    const times: number[] = [];
    for (let k = 0; k < count; k += 1) {
      const start = performance.now();
      probe.pixel(0, 0);
      times.push(performance.now() - start);
    }
    return times;
  }, runs);
}

it("measures the frame with contours at rest and during a merge travel, at 1920 by 1080", async () => {
  const viewport = { width: 1920, height: 1080 };
  const report: Record<string, unknown> = { viewport };
  // The whole frame is a bound: it waits for the GPU. The callback is what the page's own frames cost.
  await withPage({ scene: "rest", viewport }, async (page) => {
    report.renderer = await page.evaluate(() => {
      const gl = document.createElement("canvas").getContext("webgl2");
      const info = gl?.getExtension("WEBGL_debug_renderer_info");
      return info ? String(gl?.getParameter(info.UNMASKED_RENDERER_WEBGL)) : "unknown";
    });
    expect((await lifeOf(page))?.contours).toBe(true);
    await wholeFrames(page, 10);
    report.restWholeFrame = summary(await wholeFrames(page, 100));
  });
  // Frames drawn in real time while the pulse travels and then while the light fades.
  await withPage({ scene: "waiting", viewport }, async (page) => {
    await page.evaluate(() => {
      window.fieldTest.advance(21);
    });
    const { travel, afterlight } = await page.evaluate(() => {
      const probe = window.fieldTest.elements().field.oversightField;
      if (!probe) throw new Error("No probe");
      const travel: number[] = [];
      const afterlight: number[] = [];
      const end = performance.now() + 3_000;
      while (performance.now() < end) {
        const start = performance.now();
        probe.pixel(0, 0);
        const took = performance.now() - start;
        const { pulses, lights } = probe.life();
        if (pulses > 0) travel.push(took);
        else if (lights > 0 && travel.length > 0) afterlight.push(took);
      }
      return { travel, afterlight };
    });
    report.travelWholeFrame = summary(travel);
    report.afterlightWholeFrame = summary(afterlight);
  });
  // The page's own frames while a pulse travels, in real time: the time inside each frame callback.
  await withPage({ scene: "waiting", viewport }, async (page) => {
    await page.evaluate(() => {
      const times: number[] = [];
      Object.assign(window, { frameTimes: times });
      const request = window.requestAnimationFrame.bind(window);
      window.requestAnimationFrame = (callback) =>
        request((stamp) => {
          const start = performance.now();
          callback(stamp);
          times.push(performance.now() - start);
        });
    });
    await page.evaluate(() => {
      window.fieldTest.advance(21);
    });
    await page.waitForFunction(
      () => window.fieldTest.elements().field.oversightField?.life().pulses === 1,
    );
    await page.waitForFunction(
      () => window.fieldTest.elements().field.oversightField?.life().pulses === 0,
    );
    const times = await page.evaluate(
      () => (window as unknown as { frameTimes: number[] }).frameTimes,
    );
    report.travelFrameCallback = summary(times);
  });
  const dir = fileURLToPath(new URL("../../../.shots/life-look/r2/", import.meta.url));
  mkdirSync(dir, { recursive: true });
  writeFileSync(`${dir}frame-cost.json`, JSON.stringify(report, null, 2));
  for (const key of [
    "restWholeFrame",
    "travelWholeFrame",
    "afterlightWholeFrame",
    "travelFrameCallback",
  ])
    expect((report[key] as { samples: number }).samples).toBeGreaterThan(10);
}, 180_000);

/**
 * Puts one area of the approved sketch and one of the product's capture side by side in one image,
 * both at their own size, so lines and lamps compare pixel for pixel. The two layouts differ, so
 * each area is given by its own corner.
 */
async function beside(
  sketch: { path: string; x: number; y: number },
  shot: { path: string; x: number; y: number },
  out: string,
) {
  if (!browser) throw new Error("Browser was not started");
  const [width, height] = [960, 600];
  const area = ({ path, x, y }: { path: string; x: number; y: number }) =>
    `<img src="data:image/png;base64,${readFileSync(path).toString("base64")}" style="width:${String(width)}px;height:${String(height)}px;object-fit:none;object-position:-${String(x)}px -${String(y)}px">`;
  const page = await browser.newPage({ viewport: { width: width * 2, height } });
  try {
    await page.setContent(
      `<body style="margin:0;display:flex;background:#000">${area(sketch)}${area(shot)}</body>`,
    );
    await page.waitForFunction(() => [...document.images].every((image) => image.complete));
    writeFileSync(out, await page.screenshot());
  } finally {
    await page.close();
  }
}

/** Renders the approved interactive reference at 1920 by 1080, still, in a state and a theme. */
async function referenceShot(state: "rest" | "watch", theme: "dark" | "day", out: string) {
  if (!browser) throw new Error("Browser was not started");
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  try {
    const file = fileURLToPath(new URL("../../../.shots/life-look/reference/life.html", import.meta.url));
    await page.goto(`file://${file}?theme=${theme}&freeze=1#${state}`);
    await page.waitForFunction(() => typeof (window as unknown as { __ft?: number }).__ft === "number");
    writeFileSync(out, await page.screenshot());
  } finally {
    await page.close();
  }
}

it("captures rest and busy at 1920 by 1080, dark and day, beside their sketches", async () => {
  const viewport = { width: 1920, height: 1080 };
  const shots = fileURLToPath(new URL("../../../.shots/life-look/r2/", import.meta.url));
  const sketches = fileURLToPath(new URL("../../../docs/sketches/life/", import.meta.url));
  mkdirSync(shots, { recursive: true });
  const shoot = async (page: Page, name: string) => {
    await settledGraph(page);
    writeFileSync(`${shots}${name}.png`, await page.screenshot());
  };
  const inDay = async (page: Page) => {
    const draws = () =>
      page.evaluate(() => window.fieldTest.elements().field.oversightField?.draws() ?? 0);
    const before = await draws();
    await page.evaluate(() => {
      document.documentElement.dataset.theme = "day";
    });
    await page.waitForFunction(
      (count) => (window.fieldTest.elements().field.oversightField?.draws() ?? 0) > count + 1,
      before,
    );
  };
  await withPage({ scene: "rest", viewport }, (page) => shoot(page, "rest-dark"));
  await withPage({ scene: "rest", viewport }, async (page) => {
    await inDay(page);
    await shoot(page, "rest-day");
  });
  await withPage({ scene: "watch", viewport, reducedMotion: "reduce" }, (page) =>
    shoot(page, "busy-dark"),
  );
  await withPage({ scene: "watch", viewport, reducedMotion: "reduce" }, async (page) => {
    await inDay(page);
    await shoot(page, "busy-day");
  });
  await withPage({ scene: "waiting", viewport, clock: true }, async (page) => {
    await page.evaluate(() => {
      window.fieldTest.advance(21);
    });
    await page.clock.runFor(250 + 725);
    await shoot(page, "merge-mid");
  });
  await referenceShot("rest", "dark", `${shots}reference-rest-dark.png`);
  await referenceShot("rest", "day", `${shots}reference-rest-day.png`);
  await referenceShot("watch", "dark", `${shots}reference-busy-dark.png`);
  await referenceShot("watch", "day", `${shots}reference-busy-day.png`);
  const pair = (
    sketch: string,
    sketchAt: [number, number],
    shot: string,
    shotAt: [number, number],
    name: string,
  ) =>
    beside(
      { path: sketch, x: sketchAt[0], y: sketchAt[1] },
      { path: `${shots}${shot}.png`, x: shotAt[0], y: shotAt[1] },
      `${shots}compare-${name}.png`,
    );
  await pair(`${sketches}rest-contour.png`, [60, 200], "rest-dark", [560, 400], "rest-dark");
  await pair(`${sketches}rest-day.png`, [60, 200], "rest-day", [560, 400], "rest-day");
  await pair(`${shots}reference-busy-dark.png`, BUSY_AT.sketch, "busy-dark", BUSY_AT.shot, "busy-dark");
  await pair(`${shots}reference-busy-day.png`, BUSY_AT.sketch, "busy-day", BUSY_AT.shot, "busy-day");
  await pair(`${sketches}merge-mid.png`, [480, 200], "merge-mid", [560, 200], "merge-mid");
}, 240_000);

/** Where each busy capture's lit node sits: the corner of the area compared, in px. */
const BUSY_AT = { sketch: [480, 200] as [number, number], shot: [560, 200] as [number, number] };
