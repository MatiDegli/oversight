import { fileURLToPath } from "node:url";

import { chromium, expect as browserExpect } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import type { Core } from "cytoscape";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

let server: ViteDevServer;
let browser: Browser;
let url: string;
let html: string;
declare global {
  interface Window {
    edgeLabelGraph: Core;
    edgeLabels: {
      themes: typeof import("../theme").themes;
      headerLayers: typeof import("./layers").headerLayers;
      showSelection: typeof import("./selection").showSelection;
      tick: typeof import("./folds.browser.fixture").foldTest.tick;
    };
  }
}
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/edge-labels-test-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<html data-theme="dark"><body style="margin:0"><script type="module">import "/src/theme/index.css"; import { foldTest } from "/src/graph/folds.browser.fixture.tsx"; import { themes } from "/src/theme/index.ts"; import { headerLayers } from "/src/graph/layers.ts"; import { showSelection } from "/src/graph/selection.ts"; window.edgeLabels = { themes, headerLayers, showSelection, tick: foldTest.tick };</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await chromium.launch();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

async function graphState(page: Page) {
  return page.evaluate(() => {
    const cy = window.edgeLabelGraph;
    return {
      positions: cy.nodes().map((n) => ({ id: n.id(), ...n.position() })),
      routes: cy.edges().map((e) => ({
        id: e.id(),
        route: e.data("route") as unknown,
        style: Object.fromEntries(
          Object.entries(e.style()).filter(([key]) => key !== "label" && key !== "content"),
        ),
      })),
      pan: { ...cy.pan() },
      zoom: cy.zoom(),
      focus: document.activeElement?.id,
    };
  });
}

it("reveals counts by pointer and app selection without moving or restyling routes", async () => {
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  try {
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    // Test-only access to the real instance; the product's read-only probe stays unchanged.
    await page.route("**/src/graph/GraphView.tsx*", async (route) => {
      const response = await route.fetch();
      await route.fulfill({
        response,
        body: (await response.text()).replace(
          "const cy = cytoscape({",
          "const cy = window.edgeLabelGraph = cytoscape({",
        ),
      });
    });
    await page.route("**/src/graph/folds.browser.fixture.tsx*", async (route) => {
      const response = await route.fetch();
      await route.fulfill({
        response,
        body: (await response.text()).replace(/node_ids: \[[\s\S]*?\]/, "node_ids: []"),
      });
    });
    await page.goto(url);
    await page.waitForFunction(() => (window.edgeLabelGraph as Core | undefined)?.nodes().length);
    // A folded presentation with three modules and two directed aggregate pairs.

    await page.evaluate(() => {
      const input = document.createElement("input");
      input.id = "hover-focus";
      input.style.position = "fixed";
      document.body.append(input);
      input.focus({ preventScroll: true });
    });
    const before = await graphState(page);
    expect(
      await page.evaluate(() => window.edgeLabelGraph.edges(".aggregate").length),
    ).toBeGreaterThan(0);
    expect(
      await page.evaluate(() =>
        window.edgeLabelGraph.edges(".aggregate").map((e) => String(e.style("label"))),
      ),
    ).toEqual(["", ""]);
    const points = await page.evaluate(() => {
      const e = window.edgeLabelGraph.edges(".aggregate").first();
      return [e.renderedMidpoint(), ...e.renderedSegmentPoints()];
    });
    for (const point of points) {
      await page.mouse.move(point.x, point.y);
      if (await page.evaluate(() => window.edgeLabelGraph.edges(".count-hover").length)) break;
    }
    await browserExpect
      .poll(() => page.evaluate(() => window.edgeLabelGraph.edges(".count-hover").length))
      .toBe(1);
    expect(
      await page.evaluate(
        () => window.edgeLabelGraph.edges(".count-hover").first().style("label") as string,
      ),
    ).toMatch(/^×\d+$/);
    expect(await page.evaluate(() => window.edgeLabelGraph.elements(":selected").length)).toBe(0);
    expect(await graphState(page)).toEqual(before);
    await page.evaluate(() => {
      window.edgeLabels.tick();
    });
    expect(await page.evaluate(() => window.edgeLabelGraph.edges(".count-hover").length)).toBe(1);
    await page.mouse.move(1395, 895);
    expect(await page.evaluate(() => window.edgeLabelGraph.edges(".count-hover").length)).toBe(0);
    expect(
      await page.evaluate(() =>
        window.edgeLabelGraph.edges(".aggregate").map((e) => String(e.style("label"))),
      ),
    ).toEqual(["", ""]);
    const after = await graphState(page);
    expect(after).toEqual(before);
    await page.evaluate(() => {
      const { showSelection } = window.edgeLabels;
      showSelection(window.edgeLabelGraph, "cart");
    });
    expect(
      await page.evaluate(() =>
        window.edgeLabelGraph.edges(".aggregate.touching").map((e) => String(e.style("label"))),
      ),
    ).toEqual(["×2"]);
    expect(
      await page.evaluate(() => window.edgeLabelGraph.edges(":selectable, :selected").length),
    ).toBe(0);
    await page.evaluate(() => {
      const { showSelection } = window.edgeLabels;
      showSelection(window.edgeLabelGraph, null);
    });
    expect(
      await page.evaluate(() =>
        window.edgeLabelGraph.edges(".aggregate").map((e) => String(e.style("label"))),
      ),
    ).toEqual(["", ""]);
  } finally {
    await page.close();
  }
});

it("reserves the real title/count gap including the folded chevron", async () => {
  const page = await browser.newPage();
  try {
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    const results = await page.evaluate(async () => {
      const { themes, headerLayers } = window.edgeLabels;
      const theme = themes.dark;
      await document.fonts.load(
        `${String(theme["frame-title-weight"])} ${theme["frame-title-size"]} ${theme["text-font"]}`,
      );
      await document.fonts.load(
        `${String(theme["caption-weight"])} ${theme["frame-count-size"]} ${theme["text-font"]}`,
      );
      return [false, true].map((folded) => {
        const size = { width: 281, height: 44 };
        const layers = headerLayers(
          theme,
          "NamedTupleInputParameter",
          "4 methods",
          size,
          "empty",
          undefined,
          folded,
        );
        const bounds = layers.map((layer) => {
          const svg = document.createElement("div");
          svg.innerHTML = decodeURIComponent(
            layer.image.slice("data:image/svg+xml,".length),
          ).replace(/<style>.*?<\/style>/, "");
          document.body.append(svg);
          const text = svg.querySelector("text");
          if (!text) throw new Error("Missing header text");
          const box = text.getBBox();
          const left = (layer.x === "100%" ? size.width - layer.width : 0) + layer.offsetX;
          const result = {
            left: left + box.x,
            right: left + box.x + box.width,
            text: text.textContent,
          };
          svg.remove();
          return result;
        });
        return {
          gap: (bounds[1]?.left ?? 0) - (bounds[0]?.right ?? 0),
          text: bounds[0]?.text,
          minimum: parseFloat(theme["node-word-gap"]),
        };
      });
    });
    for (const result of results) {
      expect(result.text).toMatch(/…$/);
      expect(result.gap).toBeGreaterThanOrEqual(result.minimum);
    }
  } finally {
    await page.close();
  }
});
