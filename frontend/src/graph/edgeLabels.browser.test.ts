import { launchBrowser } from "../browserLaunch.support";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect as browserExpect } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import type { Core } from "cytoscape";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

import { readBoltonsSnapshot } from "./exactFixtures.testSupport";

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
  browser = await launchBrowser();
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
          Object.entries(e.style()).filter(
            ([key]) =>
              ![
                "label",
                "content",
                "line-color",
                "lineColor",
                "target-arrow-color",
                "targetArrowColor",
                "z-index",
                "zIndex",
                "z-compound-depth",
                "zCompoundDepth",
              ].includes(key),
          ),
        ),
      })),
      pan: { ...cy.pan() },
      zoom: cy.zoom(),
      focus: document.activeElement?.id,
    };
  });
}

it("lifts the hovered path and count without moving routes, positions or focus", async () => {
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
    expect(
      await page.evaluate(() => {
        const edge = window.edgeLabelGraph.edges(".count-hover").first();
        return {
          color: String(edge.style("line-color")),
          arrow: String(edge.style("target-arrow-color")),
          z: Number(edge.numericStyle("z-index")),
          transition: String(edge.style("transition-property")),
        };
      }),
    ).toEqual({ color: "rgb(240,240,235)", arrow: "rgb(240,240,235)", z: 4, transition: "none" });
    expect(await graphState(page)).toEqual(before);
    await page.evaluate(() => {
      window.edgeLabels.tick();
    });
    expect(await page.evaluate(() => window.edgeLabelGraph.edges(".count-hover").length)).toBe(1);
    await page.mouse.move(-10, -10);
    expect(await page.evaluate(() => window.edgeLabelGraph.edges(".count-hover").length)).toBe(0);
    expect(
      await page.evaluate(() => String(window.edgeLabelGraph.edges().first().style("line-color"))),
    ).toBe("rgb(106,106,101)");
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

async function inspectFaintLines(page: Page) {
  return page.evaluate(() => {
    const cy = window.edgeLabelGraph;
    const { headerLayers, themes } = window.edgeLabels;
    const cards = cy.nodes(".module");
    let textBoxes = 0;
    for (const node of cards) {
      const box = node.boundingBox({ includeLabels: false, includeOverlays: false });
      // Actual font glyph bounds from the renderer's own SVG header layers.
      for (const layer of headerLayers(
        themes.dark,
        String(node.data("title")),
        String(node.data("count")),
        { width: node.width(), height: node.height() },
        "empty",
        undefined,
        true,
      )) {
        const svg = document.createElement("div");
        svg.innerHTML = decodeURIComponent(layer.image.split(",").slice(1).join(",")).replace(
          /<style>.*?<\/style>/,
          "",
        );
        document.body.append(svg);
        const glyph = svg.querySelector("text")?.getBBox();
        if (glyph) {
          const left = (layer.x === "100%" ? node.width() - layer.width : 0) + layer.offsetX;
          if (left + glyph.x < 0 || left + glyph.x + glyph.width > box.w)
            throw new Error("Unmasked header text");
          textBoxes++;
        }
        svg.remove();
      }
      if (node.numericStyle("background-opacity") !== 1) throw new Error("Translucent card");
    }
    for (const edge of cy.edges(".faint")) {
      if (
        edge.style("curve-style") !== "straight" ||
        edge.style("target-arrow-shape") !== "none" ||
        edge.numericStyle("width") !== 1 ||
        edge.style("line-style") !== "solid"
      )
        throw new Error("Invalid faint line");
      // Lines are clipped at endpoints and masked by every opaque card above them.
      for (const [point, node] of [
        [edge.sourceEndpoint(), edge.source()],
        [edge.targetEndpoint(), edge.target()],
      ] as const) {
        const b = node.boundingBox({ includeLabels: false, includeOverlays: false });
        const borderDistance = Math.min(
          Math.abs(point.x - b.x1),
          Math.abs(point.x - b.x2),
          Math.abs(point.y - b.y1),
          Math.abs(point.y - b.y2),
        );
        if (borderDistance > 2) throw new Error(`Endpoint off border: ${String(borderDistance)}`);
      }
      if (cards.toArray().some((n) => n.numericStyle("z-index") <= edge.numericStyle("z-index")))
        throw new Error("Line above card text");
    }
    // DOM titles, headers, counters and legend have opaque plates above the graph canvas.
    for (const plate of document.querySelectorAll(".graph-text-shield"))
      if (getComputedStyle(plate).backgroundColor.includes("rgba"))
        throw new Error("Translucent text plate");
    return {
      textBoxes,
      exact: cy.edges(".exact").filter((e) => e.visible()).length,
      faint: cy.edges(".faint").length,
    };
  });
}

it.for(["boltons", "demo"])(
  "%s: straight faint endpoints and opaque text masks at start and Modules fit",
  async (name, { skip }) => {
    const root = new URL("../../../", import.meta.url);
    const events = readFileSync(new URL("fixtures/events/recorded-demo.jsonl", root), "utf8")
      .trim()
      .split("\n")
      .map((s) => JSON.parse(s) as import("@contracts").ServerEvent);
    const demo = events.find((e) => e.type === "graph.snapshot");
    if (demo?.type !== "graph.snapshot") throw new Error("Missing snapshot");
    const graph = name === "demo" ? demo.payload : readBoltonsSnapshot(root);
    if (graph === null) skip();
    const page = await browser.newPage({
      viewport: { width: 1920, height: 1080 },
      reducedMotion: "reduce",
    });
    try {
      await page.route("**/src/graph/GraphView.tsx*", async (route) => {
        const response = await route.fetch();
        await route.fulfill({
          response,
          body:
            'import {headerLayers as exactHeaders} from "/src/graph/layers.ts"; import {themes as exactThemes} from "/src/theme/index.ts"; window.edgeLabels = {headerLayers: exactHeaders, themes: exactThemes};\n' +
            (await response.text()).replace(
              "const cy = cytoscape({",
              "const cy = window.edgeLabelGraph = cytoscape({",
            ),
        });
      });
      await page.route("**/graph", (route) =>
        route.fulfill({
          json: {
            run_id: events[0]?.type === "run.started" ? events[0].payload.run_id : "test",
            seq: 0,
            graph,
          },
        }),
      );
      await page.route("**/events?*", (route) =>
        route.fulfill({
          contentType: "text/event-stream",
          body: [events[0], { ...demo, payload: graph }]
            .map((e) => `id: ${String(e?.seq)}\ndata: ${JSON.stringify(e)}\n\n`)
            .join(""),
        }),
      );
      await page.goto(url);
      await page.waitForFunction(() => (window.edgeLabelGraph as Core | undefined)?.nodes().length);
      await page.evaluate(() => document.fonts.ready);
      await page.evaluate(
        () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
      );
      for (const fit of [false, true]) {
        if (fit) await page.getByRole("button", { name: "Modules", exact: true }).click();
        const result = await inspectFaintLines(page);
        expect(result.exact).toBe(name === "boltons" ? 13 : 4);
        expect(result.faint).toBe(name === "boltons" ? 9 : 3);
        expect(result.textBoxes).toBeGreaterThan(0);
      }
    } finally {
      await page.close();
    }
  },
);
