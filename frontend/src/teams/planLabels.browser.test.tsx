import { fileURLToPath } from "node:url";
import { mkdir } from "node:fs/promises";
import { renderToStaticMarkup } from "react-dom/server";
import { expect as check, type Browser } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import { event, fold, plan, seed, status } from "../app/chatAgents.fixture";
import { graphElements } from "../graph/elements";
import { MapLayer } from "../graph/MapLayer";
import { mapModel, moduleLayout } from "../graph/mapModel";
import { themes } from "../theme";

let server: ViteDevServer, browser: Browser, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

function twoPlans() {
  return fold([
    ...seed,
    ...["shop.pricing:compute_price", "shop.inventory:_check_quantity"].flatMap((nodeId, i) => {
      const id = `agent-${String(i)}`,
        planId = `plan-${String(i)}`;
      const source = plan(planId, "lead");
      if (source.type !== "plan.updated") throw new Error("Missing plan");
      return [
        {
          ...source,
          payload: {
            ...source.payload,
            subtasks: [
              {
                agent_id: id,
                task: "Same subtask",
                zone: { node_ids: [], paths: [] },
                depends_on: [],
                state: "running" as const,
              },
            ],
          },
        },
        status(id, { plan_id: planId }),
        event("agent.focus", { agent_id: id, mode: "edit", node_ids: [nodeId], ranges: [] }),
      ];
    }),
  ]);
}

for (const width of [1280, 1920])
  it.each(["dark", "day"] as const)(
    `fits colliding plan labels on separate lines at zoom 0.2 and ${String(width)} in %s`,
    async (theme) => {
      const model = mapModel(graphElements(twoPlans()), themes[theme]);
      const frame = { model, zoom: 0.2, pan: { x: 100, y: 100 }, level: "map" as const };
      const markup = renderToStaticMarkup(<MapLayer frame={frame} />);
      const html = await server.transformIndexHtml(
        "/",
        `<!doctype html>
        <html data-theme="${theme}"><head><meta charset="utf-8"></head><body>${markup}<script type="module">
        import "/src/theme/index.css"; document.body.dataset.ready = "true";
        </script></body></html>`,
      );
      const page = await browser.newPage({
        viewport: { width, height: 800 },
        reducedMotion: "reduce",
      });
      try {
        await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
        await page.goto(url);
        await page.waitForSelector('body[data-ready="true"]');
        await page.evaluate(() => document.fonts.ready);
        await check(page.locator(".map-agent")).toHaveCount(2);
        for (const group of model.groups)
          for (const module of group.modules) {
            if (!module.agents.length) continue;
            const agent = module.agents[0];
            if (!agent?.planContext) throw new Error("Missing colliding plan context");
            const chip = page.locator(`[data-map-module="${module.id}"] .map-agent`);
            // Measure first: the old qualified single line overflows at the map floor.
            expect(await chip.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
            const caption = chip.locator(".map-agent-caption");
            await check(caption).toHaveText(agent.planContext.caption);
            expect(await chip.evaluate((el) => el.firstChild?.textContent)).toBe("A·1 editing");
            expect(await caption.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
            const bounds = await chip.boundingBox(),
              line = await caption.boundingBox();
            if (!bounds || !line) throw new Error("Missing chip bounds");
            expect(line.y).toBeGreaterThan(bounds.y);
            // The model's hit/line geometry must reserve the caption's rendered height too.
            const layout = moduleLayout(module, model, frame.zoom, frame.pan);
            const cell = await page.locator(`[data-map-module="${module.id}"]`).boundingBox();
            if (!cell) throw new Error("Missing module bounds");
            expect(cell.y + cell.height).toBeLessThanOrEqual(layout.y2);
          }
        if (process.env.TEAM_COLORS_PLAN_LABEL_SHOTS === "1") {
          const directory = new URL("../../../.shots/team-colors/", import.meta.url);
          await mkdir(directory, { recursive: true });
          await page.screenshot({
            path: fileURLToPath(
              new URL(`map-plan-labels-${String(width)}-${theme}.png`, directory),
            ),
          });
        }
      } finally {
        await page.close();
      }
    },
  );
