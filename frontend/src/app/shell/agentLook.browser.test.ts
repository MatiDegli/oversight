import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium, expect as check } from "@playwright/test";
import type { Browser } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { beforeAll, afterAll, it, expect } from "vitest";
import type { Slot } from "@contracts";
import { agentLook, agentSlots, themes } from "../../theme";
import type { GraphProbeHost } from "../../graph";

let server: ViteDevServer, browser: Browser, html: string, url: string;
const out = fileURLToPath(new URL("../../../../.shots/agent-look/", import.meta.url));
beforeAll(async () => {
  await mkdir(out, { recursive: true });
  server = await createServer({
    root: fileURLToPath(new URL("../../../", import.meta.url)),
    cacheDir: `${out}vite-cache`,
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    `<!doctype html><html><body><script type="module">
    import "/src/theme/index.css";
    import { showAgentLook } from "/src/app/shell/agentLook.browser.fixture.tsx";
    window.showAgentLook = showAgentLook;
    </script></body></html>`,
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await chromium.launch();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

it.each(Object.keys(themes) as (keyof typeof themes)[])(
  "captures A–F cards, rail avatars, review pills and pending node borders in %s",
  async (theme) => {
    for (const slot of agentSlots) {
      const page = await browser.newPage({
        viewport: { width: 1920, height: 1080 },
        reducedMotion: "reduce",
      });
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      try {
        await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
        await page.goto(url);
        await page.waitForFunction(() => "showAgentLook" in window);
        await page.evaluate(
          ({ slot, theme }) => {
            document.documentElement.dataset.theme = theme;
            (
              window as unknown as { showAgentLook: (slot: Slot, review: boolean) => void }
            ).showAgentLook(slot, false);
          },
          { slot, theme },
        );
        await page.evaluate(() => document.fonts.ready);
        const card = page.getByRole("article", { name: `Agent ${slot}`, exact: true });
        await check(card).toBeVisible();
        await check(card.getByRole("status")).toHaveText("Ready for review");
        const topLine = await card.evaluate(
          (node) => getComputedStyle(node, "::before").backgroundColor,
        );
        const rgb = (hex: string) =>
          `rgb(${[1, 3, 5].map((offset) => parseInt(hex.slice(offset, offset + 2), 16)).join(", ")})`;
        expect(topLine).toBe(rgb(themes[theme][agentLook[slot].family]));
        await page.waitForFunction(
          () =>
            (document
              .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
              ?.oversightGraph?.nodeBounds("node.pending").length ?? 0) > 0,
        );
        await page.screenshot({ path: `${out}${slot}-${theme}-card.png`, animations: "disabled" });
        await page.evaluate((slot) => {
          (
            window as unknown as { showAgentLook: (slot: Slot, review: boolean) => void }
          ).showAgentLook(slot, true);
        }, slot);
        const panel = page.getByRole("complementary", { name: "Review panel", exact: true });
        await check(panel).toBeVisible();
        await check(panel.getByText("Ready for review", { exact: true })).toBeVisible();
        await check(
          page.getByRole("group", { name: `Agent ${slot}: Ready for review`, exact: true }),
        ).toBeVisible();
        await panel.locator(".monaco-editor .view-lines").first().waitFor();
        await page.screenshot({
          path: `${out}${slot}-${theme}-review.png`,
          animations: "disabled",
        });
        expect(errors).toEqual([]);
      } finally {
        await page.close();
      }
    }
  },
  120_000,
);
