import type { GraphSnapshot } from "@contracts";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { beforeAll, afterAll, expect, it } from "vitest";
import { expect as check, type Browser } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { launchBrowser } from "../browserLaunch.support";
// eslint-disable-next-line no-restricted-imports -- Original optional offline snapshot, never a substitute graph.
import { readBoltonsSnapshot } from "../graph/exactFixtures.testSupport";
import type { GraphProbeHost } from "../graph";
import type { TeamScene } from "./shell/agentLook.browser.fixture";

const enabled = process.env.TEAM_COLORS_SHOTS === "1";
const boltons = readBoltonsSnapshot();
const shots = fileURLToPath(new URL("../../../.shots/team-colors/", import.meta.url));
function requireGraph() {
  if (!boltons) throw new Error("Missing Boltons d31d821 snapshot");
  return boltons;
}
let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  if (!enabled) return;
  if (!boltons) throw new Error("Missing Boltons d31d821 snapshot; no substitute is permitted");
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: `${shots}cache`,
    server: { host: "127.0.0.1", port: 0 },
  });
  html = await server.transformIndexHtml(
    "/",
    '<html><body><script type="module">import "/src/theme/index.css"; import {showTeamColors} from "/src/app/shell/agentLook.browser.fixture.tsx"; window.showTeamColors = showTeamColors;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser({ webGL: true, softwareRendering: true });
  await mkdir(shots, { recursive: true });
}, 60_000);
afterAll(async () => {
  if (enabled) {
    await browser.close();
    await server.close();
  }
});
for (const width of [1920, 1280])
  for (const theme of ["dark", "day"])
    for (const scene of [
      "graph-busy",
      "graph-rest",
      "inbox",
      "seventh-team",
      "chat-flat",
    ] as TeamScene[]) {
      it.skipIf(!enabled)(
        `captures ${scene} ${String(width)} ${theme}`,
        async () => {
          const page = await browser.newPage({
            viewport: { width, height: width === 1280 ? 800 : 1080 },
            reducedMotion: "reduce",
          });
          const errors: string[] = [];
          page.on("pageerror", (error) => errors.push(error.message));
          try {
            await page.route("**/", (route) =>
              route.fulfill({ contentType: "text/html", body: html }),
            );
            await page.goto(url);
            await page.waitForFunction(() => "showTeamColors" in window);
            await page.evaluate(
              ({ graph, scene, theme }) => {
                document.documentElement.dataset.theme = theme;
                (
                  window as unknown as {
                    showTeamColors: (graph: GraphSnapshot, scene: TeamScene) => void;
                  }
                ).showTeamColors(graph, scene);
              },
              { graph: requireGraph(), scene, theme },
            );
            await check(
              page.getByText("Handwritten events over Boltons d31d821", { exact: true }),
            ).toBeVisible();
            await page.evaluate(() => document.fonts.ready);
            await check
              .poll(() =>
                page.evaluate(() =>
                  document
                    .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
                    ?.oversightGraph?.animating(),
                ),
              )
              .toBe(false);
            if (scene === "chat-flat")
              await page.getByRole("button", { name: "Chat", exact: true }).click();
            else {
              await page.getByRole("button", { name: "Fit the graph to the view" }).click();
              await check
                .poll(() =>
                  page.evaluate(() =>
                    document
                      .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
                      ?.oversightGraph?.animating(),
                  ),
                )
                .toBe(false);
            }
            if (scene === "inbox" || scene === "seventh-team")
              await check(page.getByRole("region", { name: "Decisions inbox" })).toBeVisible();
            if (scene === "chat-flat") {
              await check(page.locator(".chat-agent-row")).toHaveCount(6);
              await check(page.locator(".chat-agent-row").filter({ hasText: "A·2" })).toBeVisible();
            }
            if (scene === "seventh-team")
              await check(page.getByText("G·1", { exact: true }).first()).toBeVisible();
            await page.screenshot({
              path: `${shots}${scene}${width === 1280 ? "-1280" : ""}-${theme}.png`,
            });
            expect(errors).toEqual([]);
          } finally {
            await page.close();
          }
        },
        60_000,
      );
    }
