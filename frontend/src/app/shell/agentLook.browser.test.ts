import { fileURLToPath } from "node:url";
import { expect as check, type Browser } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { beforeAll, afterAll, it, expect } from "vitest";
import { launchBrowser } from "../../browserLaunch.support";
import { themes } from "../../theme";
// eslint-disable-next-line no-restricted-imports -- Read the original offline graph for presentation regressions.
import { readBoltonsSnapshot } from "../../graph/exactFixtures.testSupport";
import type { GraphSnapshot } from "@contracts";

const graph = readBoltonsSnapshot();
function requireGraph() {
  if (!graph) throw new Error("Missing Boltons d31d821 snapshot");
  return graph;
}
let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  if (!graph) throw new Error("Missing Boltons d31d821 snapshot");
  server = await createServer({
    root: fileURLToPath(new URL("../../../", import.meta.url)),
    server: { host: "127.0.0.1", port: 0 },
  });
  html = await server.transformIndexHtml(
    "/",
    '<html><body><script type="module">import "/src/theme/index.css"; import {showTeamColors} from "/src/app/shell/agentLook.browser.fixture.tsx"; window.showTeamColors = showTeamColors;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});
it.each(["dark", "day"] as const)(
  "shares team paint across unrelated seats in %s",
  async (theme) => {
    const page = await browser.newPage({
      viewport: { width: 1920, height: 1080 },
      reducedMotion: "reduce",
    });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    try {
      await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
      await page.goto(url);
      await page.waitForFunction(() => "showTeamColors" in window);
      await page.evaluate(
        ({ graph, theme }) => {
          document.documentElement.dataset.theme = theme;
          (
            window as unknown as { showTeamColors: (graph: GraphSnapshot, scene: string) => void }
          ).showTeamColors(graph, "graph-busy");
        },
        { graph: requireGraph(), theme },
      );
      await page.evaluate(() => document.fonts.ready);
      const first = page.getByRole("article", { name: "Agent A·1", exact: true });
      const second = page.getByRole("article", { name: "Agent A·2", exact: true });
      const ready = page.getByRole("article", { name: "Agent A·3", exact: true });
      for (const card of [first, second, ready]) await check(card).toBeVisible();
      const rgb = (hex: string) =>
        `rgb(${[1, 3, 5].map((offset) => parseInt(hex.slice(offset, offset + 2), 16)).join(", ")})`;
      const paint = (card: typeof first) =>
        card.evaluate((node) => getComputedStyle(node, "::before").backgroundColor);
      expect(await paint(first)).toBe(rgb(themes[theme]["agent-a"]));
      expect(await paint(second)).toBe(await paint(first));
      await check(ready.getByRole("status")).toHaveCSS("color", rgb(themes[theme]["agent-a-text"]));
      await check(ready.getByRole("button", { name: "Review change", exact: true })).toHaveCSS(
        "color",
        rgb(themes[theme].text),
      );
      await check(page.getByRole("article", { name: "Agent C", exact: true })).toBeVisible();
      await page.getByRole("button", { name: "Chat", exact: true }).click();
      await check(page.locator(".chat-agent-row")).toHaveCount(6);
      await check(page.locator(".chat-agent-badge").filter({ hasText: "A·2" })).toHaveCSS(
        "color",
        rgb(themes[theme]["agent-a-text"]),
      );
      expect(errors).toEqual([]);
    } finally {
      await page.close();
    }
  },
  60_000,
);
