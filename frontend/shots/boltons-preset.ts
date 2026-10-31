// Explicit offline load/capture check against a live Boltons server, never part of network checks.
// node frontend/shots/boltons-preset.ts <Vite URL> <capture directory>
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

import type {
  GraphResponse,
  GraphSnapshot,
  ServerEvent,
} from "../../contracts/generated/contracts.ts";
import { chromium } from "@playwright/test";
import type { Page } from "@playwright/test";
import type { GraphProbeHost } from "../src/graph/probe.ts";
import { scaleLog } from "../tools/scale-log.ts";

const [url, output] = process.argv.slice(2);
assert(url && output, "Usage: boltons-preset.ts <Vite URL> <capture directory>");
const out = resolve(output);
await mkdir(out, { recursive: true });
const preset = JSON.parse(
  await readFile(new URL("../../fixtures/layouts/boltons-d31d821.json", import.meta.url), "utf8"),
) as GraphSnapshot["positions"];
const browser = await chromium.launch();
try {
  const api = await browser.newContext();
  const response = await api.request.get(new URL("graph", url).href);
  assert(response.ok(), "GET /graph failed");
  const live = (await response.json()) as GraphResponse;
  assert.equal(
    live.graph.nodes.length,
    Object.keys(preset).length,
    `Live node count ${String(live.graph.nodes.length)} differs from preset count ${String(Object.keys(preset).length)}`,
  );
  assert.deepEqual(live.graph.positions, preset, "OVERSIGHT_LAYOUT did not reach GET /graph");
  assert.deepEqual(Object.keys(preset).sort(), live.graph.nodes.map(({ id }) => id).sort());
  const reports = [];
  for (const mode of ["preset", "fallback"] as const) {
    const page = await browser.newPage({
      viewport: { width: 1920, height: 1080 },
      reducedMotion: "reduce",
    });
    try {
      // Record the branch actually executed, without changing the repository's served module.
      await page.route("**/src/graph/updateGraph.ts", async (route) => {
        const module = await route.fetch();
        let body = await module.text();
        for (const [call, name] of [
          [/cy\.layout\(\{\s*name: "preset",\s*fit: false\s*\}\)\.run\(\);/, "preset"],
          [/cy\.layout\(fallback\)\.run\(\);/, "fallback"],
        ] as const) {
          assert(call.test(body), "Layout instrumentation no longer matches");
          body = body.replace(
            call,
            (matched) => `document.documentElement.dataset.checkedLayout = "${name}"; ${matched}`,
          );
        }
        await route.fulfill({ response: module, body });
      });
      if (mode === "fallback") await installFallback(page, live.graph);
      await page.goto(`${url}?field-time=2`, { waitUntil: "domcontentloaded" });
      await page.evaluate(() => document.fonts.ready);
      await page.waitForFunction(() => {
        const graph = document.querySelector<GraphProbeHost>(
          '[aria-label="Repository graph"]',
        )?.oversightGraph;
        return graph && graph.nodeCenters().length > 0 && !graph.animating();
      });
      assert.equal(await page.locator("html").getAttribute("data-checked-layout"), mode);
      for (const view of ["start", "modules"] as const) {
        if (view === "modules")
          await page.getByRole("button", { name: "Modules", exact: true }).click();
        await page.waitForFunction(
          () =>
            !document
              .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
              ?.oversightGraph?.animating(),
        );
        const audit = await page
          .locator('[aria-label="Repository graph"]')
          .evaluate((host: GraphProbeHost) => {
            const graph = host.oversightGraph;
            if (!graph) throw new Error("Graph not mounted");
            const cards = graph.nodeBounds(".module");
            let cardIntersections = 0;
            for (const [i, a] of cards.entries())
              for (const b of cards.slice(i + 1))
                if (a.x1 < b.x2 && a.x2 > b.x1 && a.y1 < b.y2 && a.y2 > b.y1) cardIntersections++;
            const routes = graph.routes();
            return {
              cards: cards.length,
              cardIntersections,
              routes: routes.length,
              hiddenRoutes: routes.filter((route) => !route.visible).length,
              hiddenCalls: routes
                .filter((route) => !route.visible)
                .reduce((sum, route) => sum + route.count, 0),
              metrics: graph.metrics(),
            };
          });
        reports.push({ mode, view, ...audit });
        await page.screenshot({ path: resolve(out, `${mode}-${view}.png`) });
      }
    } finally {
      await page.close();
    }
  }
  await writeFile(
    resolve(out, "audit.json"),
    JSON.stringify({ nodes: live.graph.nodes.length, reports }, null, 2) + "\n",
  );
  console.log(JSON.stringify(reports, null, 2));
} finally {
  await browser.close();
}

async function installFallback(page: Page, graph: GraphSnapshot): Promise<void> {
  const bare = { ...graph, positions: {} };
  const events = scaleLog(bare)
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as ServerEvent);
  const started = events[0];
  assert(started?.type === "run.started");
  await page.route("**/graph", (route) =>
    route.fulfill({ json: { run_id: started.payload.run_id, seq: 2, graph: bare } }),
  );
  await page.route("**/events?*", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body: events
        .map((event) => `id: ${String(event.seq)}\ndata: ${JSON.stringify(event)}\n\n`)
        .join(""),
    }),
  );
}
