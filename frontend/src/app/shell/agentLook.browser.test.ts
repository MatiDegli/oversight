import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Page, type Locator } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { beforeAll, afterAll, it, expect } from "vitest";
import { launchBrowser } from "../../browserLaunch.support";
import { themes } from "../../theme";
import { readFileSync } from "node:fs";
import { parseEvent } from "../../transport";
import type { GraphSnapshot } from "@contracts";

// Ordinary paint regressions use the committed demo graph. Only the opt-in capture suite
// requires the original Boltons snapshot; it retains its no-substitute guard.
const graph = readFileSync(
  new URL("../../../../fixtures/events/recorded-demo.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map(parseEvent)
  .find((event) => event.type === "graph.snapshot")?.payload;
if (!graph) throw new Error("Missing committed demo graph");
let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../../", import.meta.url)),
    server: { host: "127.0.0.1", port: 0 },
  });
  html = await server.transformIndexHtml(
    "/",
    '<html><body><script type="module">import "/src/theme/index.css"; import {showTeamColors} from "/src/app/shell/agentLook.browser.fixture.tsx"; import {showTeamPaint} from "/src/app/shell/agentPaint.browser.fixture.tsx"; window.showTeamColors = showTeamColors; window.showTeamPaint = showTeamPaint;</script></body></html>',
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
            window as unknown as {
              showTeamColors: (graph: GraphSnapshot, scene: string, workspace: string) => void;
            }
          ).showTeamColors(graph, "graph-busy", "demo-repo");
        },
        { graph, theme },
      );
      await page.evaluate(() => document.fonts.ready);
      await check(
        page.getByText("Handwritten events over demo-repo", { exact: true }),
      ).toBeVisible();
      await check(page.getByRole("region", { name: "New agent" })).toContainText(
        "Replay of a handwritten event log over demo-repo",
      );
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

async function assertFamilyPaint(page: Page, theme: "dark" | "day", index: number) {
  const letter = ["A", "B", "C", "D", "E", "F"][index];
  const family = (["agent-a", "agent-b", "agent-c", "agent-d", "agent-e", "agent-f"] as const)[
    index
  ];
  if (!letter || !family) throw new Error("Missing expected team family");
  const token = themes[theme];
  const rgb = (hex: string) =>
    `rgb(${[1, 3, 5].map((offset) => parseInt(hex.slice(offset, offset + 2), 16)).join(", ")})`;
  const tint = token[`${family}-tint`];
  const section = page.locator(`[data-paint-agent="agent-${String(6 - index)}"]`);
  const card = section.getByRole("article", { name: `Agent ${letter}`, exact: true });
  await check(card).toBeVisible();
  expect(await card.evaluate((node) => getComputedStyle(node, "::before").backgroundColor)).toBe(
    rgb(token[family]),
  );
  const pill = card.getByRole("status");
  await check(pill).toHaveText("Ready for review");
  await check(pill).toHaveCSS("color", rgb(token[`${family}-text`]));
  await assertTint(pill, tint);
  const tile = page.locator(`.agents-rail [data-agent-origin="agent-${String(6 - index)}"]`);
  await check(tile).toHaveText(letter);
  await check(tile).toHaveCSS("color", rgb(token[`${family}-text`]));
  await assertTint(tile, tint);
  await check(tile).toHaveCSS("border-top-color", rgb(token[family]));
  const review = section.locator(".review-header-status");
  await check(review).toHaveText("Ready for review");
  await check(review).toHaveCSS("color", rgb(token[`${family}-text`]));
  await assertTint(review, tint);
  const badge = section.locator(".review-header-badge");
  await check(badge).toHaveText(letter);
  await check(badge).toHaveCSS("color", rgb(token[`${family}-text`]));
  await assertTint(badge, tint);
  const chat = page
    .locator(".chat-agent-row")
    .filter({ has: page.getByText(`Agent ${letter}`, { exact: true }) });
  await check(chat.locator(".chat-agent-badge")).toHaveCSS("color", rgb(token[`${family}-text`]));
}

async function assertTint(node: Locator, tint: { color: string; alpha: number }) {
  // Chromium serializes dark color-mix tokens as color(srgb), day tokens as rgba.
  // Read both through canvas to compare the actual channels, including alpha.
  const channels = await node.evaluate((node, tint) => {
    const context = document.createElement("canvas").getContext("2d");
    if (!context) throw new Error("Missing color reader");
    const read = (color: string) => {
      context.clearRect(0, 0, 1, 1);
      context.fillStyle = color;
      context.fillRect(0, 0, 1, 1);
      return [...context.getImageData(0, 0, 1, 1).data];
    };
    const rgb = [1, 3, 5].map((offset) => parseInt(tint.color.slice(offset, offset + 2), 16));
    return {
      actual: read(getComputedStyle(node).backgroundColor),
      expected: read(`rgba(${rgb.join(", ")}, ${String(tint.alpha)})`),
    };
  }, tint);
  expect(channels.actual).toEqual(channels.expected);
}

it.each(["dark", "day"] as const)(
  "paints every team family across surfaces in %s",
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
      await page.waitForFunction(() => "showTeamPaint" in window);
      await page.evaluate(
        ({ graph, theme }) => {
          document.documentElement.dataset.theme = theme;
          (window as unknown as { showTeamPaint(graph: GraphSnapshot): void }).showTeamPaint(graph);
        },
        { graph, theme },
      );
      await page.evaluate(() => document.fonts.ready);
      await check(page.getByRole("article")).toHaveCount(6);
      for (let index = 0; index < 6; index++) await assertFamilyPaint(page, theme, index);
      expect(errors).toEqual([]);
    } finally {
      await page.close();
    }
  },
  60_000,
);
