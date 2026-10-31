import { mkdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import type { FieldProbe, GraphProbeHost } from "../graph";
import type {} from "./measuredCone.browser.fixture";

let server: ViteDevServer, browser: Browser, html: string, url: string;
const shots = fileURLToPath(new URL("../../../.shots/measured-cone-graph/", import.meta.url));
beforeAll(async () => {
  await mkdir(shots, { recursive: true });
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: `${shots}vite-cache`,
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import { measuredConeTest } from "/src/app/measuredCone.browser.fixture.tsx"; window.measuredConeTest = measuredConeTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser({ webGL: true, softwareRendering: true });
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

async function open(width: number, theme: string, seq = 21, folded = false) {
  const page = await browser.newPage({
    viewport: { width: 1920, height: 1080 },
    reducedMotion: "reduce",
  });
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.goto(url);
  await page.waitForFunction(() => !!window.measuredConeTest);
  await page.evaluate((theme) => {
    document.documentElement.dataset.theme = theme;
    window.measuredConeTest.mount(6);
  }, theme);
  await page.waitForFunction(
    () =>
      !!document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]')?.oversightGraph,
  );
  if (!folded) await toggleCart(page);
  // Prepare the same fold choices, then let the built review camera frame at the capture size.
  await page.setViewportSize({ width, height: width === 1920 ? 1080 : 800 });
  // Retain the modules opened by the earlier read, as a streamed run does, then enter review.
  await page.evaluate((seq) => {
    window.measuredConeTest.advance(seq);
  }, seq);
  await page
    .getByRole("complementary", { name: "Review panel", exact: true })
    .locator(".monaco-editor .view-lines")
    .first()
    .waitFor();
  await page.evaluate(() => document.fonts.ready);
  return page;
}

async function toggleCart(page: Page) {
  const point = await page.evaluate(() => {
    const probe = document.querySelector<GraphProbeHost>(
      '[aria-label="Repository graph"]',
    )?.oversightGraph;
    return probe?.nodeCenters('node.class[title="Cart"]')[0];
  });
  if (!point) throw new Error("Missing Cart row");
  const graph = await page.getByLabel("Repository graph", { exact: true }).boundingBox();
  if (!graph) throw new Error("Missing graph");
  await page.mouse.click(graph.x + point.x, graph.y + point.y);
}

async function geometry(page: Page) {
  return page.evaluate(() => {
    const p = document.querySelector<GraphProbeHost>(
      '[aria-label="Repository graph"]',
    )?.oversightGraph;
    return {
      pan: p?.pan(),
      zoom: p?.zoom(),
      positions: p?.positions(),
      routes: p?.routes(),
      borders: p?.appearance("node"),
    };
  });
}

async function fieldLight(page: Page) {
  return page.evaluate(() => {
    const canvas = document.querySelector<HTMLCanvasElement & { oversightField?: FieldProbe }>(
      ".graph-stage canvas",
    );
    const field = canvas?.oversightField;
    if (!field || field.state() !== "webgl") throw new Error("Missing rendered field");
    field.pixel(0, 0);
    const lamps = [];
    for (let y = 0; y < canvas.clientHeight; y += 49)
      for (let x = 0; x < canvas.clientWidth; x += 49) lamps.push(field.lamp(x, y));
    return { life: field.life(), lamps };
  });
}

async function compare(sketch: string, capture: string) {
  const reference = fileURLToPath(
    new URL(`../../../docs/sketches/graph-evidence/${sketch}-dark.png`, import.meta.url),
  );
  const sources = await Promise.all(
    [reference, `${shots}${capture}`].map(async (path) =>
      (await readFile(path)).toString("base64"),
    ),
  );
  const page = await browser.newPage({ viewport: { width: 3840, height: 1080 } });
  try {
    await page.setContent(
      `<body style="margin:0;display:flex">${sources.map((src) => `<img width="1920" height="1080" src="data:image/png;base64,${src}">`).join("")}</body>`,
    );
    await page.evaluate(async () => {
      await Promise.all([...document.images].map((img) => img.decode()));
    });
    await page.screenshot({ path: `${shots}compare-${sketch}.png` });
  } finally {
    await page.close();
  }
}

it("observes all four measured words drawn to a canvas", async () => {
  const page = await browser.newPage();
  try {
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    await page.waitForFunction(() => !!window.measuredConeTest);
    const words = await page.evaluate(async () => {
      const canvas = document.createElement("canvas");
      const context = canvas.getContext("2d");
      if (!context) throw new Error("Missing canvas context");
      for (const word of ["differs", "same", "not measured", "varied"]) {
        const image = new Image();
        image.src = `data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="100" height="28"><text y="14">${word}</text></svg>`)}`;
        await image.decode();
        context.drawImage(image, 0, 0);
      }
      return window.measuredConeTest.drawnWords();
    });
    expect(words).toEqual(["differs", "not measured", "same", "varied"]);
  } finally {
    await page.close();
  }
});

it("updates facts in place without camera, nodes, routes, borders or focus moving", async () => {
  const page = await open(1920, "dark");
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.waitForFunction(
      () =>
        document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.nodeBounds('node.measured-differs[label="quote_add"]').length === 1,
    );
    const comment = page.getByRole("textbox", { name: "Comment for the agent" });
    await comment.fill("Keep this comment");
    await check
      .poll(() => page.evaluate(() => window.measuredConeTest.drawnWords()))
      .toEqual(["differs"]);
    const before = await geometry(page);
    const light = await fieldLight(page);
    for (const seq of [22, 23, 24]) {
      await page.evaluate((seq) => {
        window.measuredConeTest.advance(seq);
      }, seq);
      await check(comment).toBeFocused();
      await check(comment).toHaveValue("Keep this comment");
      await check
        .poll(() =>
          page.evaluate(
            () =>
              document
                .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
                ?.oversightGraph?.nodeBounds("node.measured-varied").length,
          ),
        )
        .toBe(seq === 22 ? 1 : 0);
      if (seq === 22) {
        // The unchanged differs texture is cached; only varied is newly drawn.
        await check
          .poll(() => page.evaluate(() => window.measuredConeTest.drawnWords()))
          .toEqual(["varied"]);
        expect(
          await page.evaluate(
            () =>
              document
                .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
                ?.oversightGraph?.nodeBounds('node.measured-differs[label="quote_add"]').length,
          ),
        ).toBe(1);
      } else {
        await page.evaluate(
          () =>
            new Promise<void>((resolve) =>
              requestAnimationFrame(() => {
                requestAnimationFrame(() => {
                  resolve();
                });
              }),
            ),
        );
        expect(await page.evaluate(() => window.measuredConeTest.drawnWords())).toEqual([]);
      }
      expect(await geometry(page)).toEqual(before);
      expect(await fieldLight(page)).toEqual(light);
      if (seq >= 23)
        expect(
          await page.evaluate(() =>
            document
              .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
              ?.oversightGraph?.nodeBounds(
                "node.measured-differs, node.measured-same, node.measured-varied, node.measured-unmeasured",
              ),
          ),
        ).toEqual([]);
    }
    expect(errors).toEqual([]);
  } finally {
    await page.close();
  }
});

it.each([1920, 1280])(
  "captures complete and partial evidence in both themes at %s",
  async (width) => {
    for (const theme of ["dark", "day"]) {
      for (const seq of [21, 22]) {
        const page = await open(width, theme, seq);
        try {
          await page.waitForFunction(
            () =>
              document
                .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
                ?.oversightGraph?.nodeBounds('node.measured-differs[label="quote_add"]').length ===
              1,
          );
          await page.mouse.move(0, 0);
          await page.screenshot({
            path: `${shots}seq-${String(seq)}-${theme}-${String(width)}.png`,
            animations: "disabled",
          });
          if (width === 1920 && theme === "dark")
            await compare(seq === 21 ? "measured" : "partial", `seq-${String(seq)}-dark-1920.png`);
        } finally {
          await page.close();
        }
      }
    }
    if (width === 1920) {
      const page = await open(1920, "dark", 21, true);
      try {
        expect(
          await page.evaluate(
            () =>
              document
                .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
                ?.oversightGraph?.nodeBounds('node.folded.measured-differs[title="Cart"]').length,
          ),
        ).toBe(1);
        await page.screenshot({
          path: `${shots}seq-21-folded-dark-1920.png`,
          animations: "disabled",
        });
        await compare("folded", "seq-21-folded-dark-1920.png");
      } finally {
        await page.close();
      }
    }
  },
  120_000,
);
