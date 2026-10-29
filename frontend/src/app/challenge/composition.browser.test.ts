import { fileURLToPath } from "node:url";
import { chromium, expect as check } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import type { GraphProbeHost } from "../../graph";

let browser: Browser | undefined;
const servers: ViteDevServer[] = [];
beforeAll(async () => {
  browser = await chromium.launch({ headless: true });
  for (const seam of [false, true]) {
    const server = await createServer({
      root: fileURLToPath(new URL("../../../", import.meta.url)),
      mode: "replay",
      cacheDir: fileURLToPath(
        new URL(`../../../../.shots/challenge/cache-${String(seam)}`, import.meta.url),
      ),
      resolve: {
        alias: seam
          ? [
              {
                find: /^\.\.\/transport$/,
                replacement: fileURLToPath(new URL("../../../e2e/transport.ts", import.meta.url)),
              },
            ]
          : [],
      },
      server: { host: "127.0.0.1", port: 0, strictPort: false },
    });
    servers.push(server);
    await server.listen();
  }
}, 30_000);
afterAll(async () => {
  await browser?.close();
  await Promise.all(servers.map((server) => server.close()));
});
async function visit(index: number, path: string, run: (page: Page) => Promise<void>) {
  if (!browser) throw new Error("Browser has not started");
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  try {
    const origin = servers[index]?.resolvedUrls?.local[0];
    if (!origin) throw new Error("Missing Vite server");
    await page.goto(new URL(path, origin).href);
    await run(page);
  } finally {
    await page.close();
  }
}
it.each(["/", "/level2"])("withholds practice on the aliased %s seam", async (path) => {
  await visit(1, path, async (page) => {
    await check(page.getByLabel("Status line")).toContainText(
      path === "/" ? "Replay · hand-written demo fixture" : "Live",
    );
    await check(page.locator("[data-practice-entry], .phone-practice-entry")).toHaveCount(0);
  });
});
it("keeps the same graph and positions through practice entry, resizing and exit", async () => {
  await visit(0, "/", async (page) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    const graph = page.getByLabel("Repository graph", { exact: true });
    await check
      .poll(() =>
        graph.evaluate(
          (host: GraphProbeHost) => Object.keys(host.oversightGraph?.positions() ?? {}).length,
        ),
      )
      .toBeGreaterThan(0);
    // Keep folding fixed so opening a case changes framing, not the set of visible nodes.
    await page.getByRole("button", { name: "Details", exact: true }).click();
    await check
      .poll(() => graph.evaluate((host: GraphProbeHost) => host.oversightGraph?.animating()))
      .toBe(false);
    const instance = await graph.evaluateHandle((host: GraphProbeHost) => host.oversightGraph);
    const positions = await instance.evaluate((probe) => probe?.positions());
    const sameGraph = async () => {
      expect(
        await instance.evaluate(
          (probe) =>
            probe ===
            document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
              ?.oversightGraph,
        ),
      ).toBe(true);
      expect(await instance.evaluate((probe) => probe?.positions())).toEqual(positions);
      await check(graph).toHaveCount(1);
    };
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: "Try two-change practice", exact: true }).click();
    await page.getByRole("button", { name: "Start practice", exact: true }).click();
    await sameGraph();
    await check(page.getByLabel("Decision-time graph", { exact: true })).toBeVisible();
    await check(graph).toBeHidden();
    await page.setViewportSize({ width: 1279, height: 844 });
    await check(page.locator(".phone-practice")).toHaveCSS("width", "430px");
    await page.setViewportSize({ width: 1920, height: 1080 });
    await check(page.locator(".practice-phone")).toHaveCount(0);
    await sameGraph();
    await page.getByRole("button", { name: "Return to ordinary replay" }).click();
    await check(
      page.getByRole("button", { name: "Two-change practice", exact: true }),
    ).toBeVisible();
    // Restarting the ordinary replay restores its initial Modules setting.
    await page.getByRole("button", { name: "Details", exact: true }).click();
    await sameGraph();
  });
}, 30_000);

it("opens source on demand and accepts a closed-diff phone decision", async () => {
  await visit(0, "/", async (page) => {
    await page.getByRole("button", { name: "Try two-change practice", exact: true }).click();
    await page.getByRole("button", { name: "Start practice", exact: true }).click();
    const toggle = page.getByRole("button", { name: /Show diff/ });
    await check(toggle).toHaveAttribute("aria-expanded", "false");
    await check(page.locator(".pp-code")).toHaveCount(0);
    await page.locator("summary").filter({ hasText: "Recorded task" }).click();
    await check(toggle).toHaveAttribute("aria-expanded", "false");
    await toggle.click();
    await check(page.getByRole("button", { name: /Hide diff/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    await check(page.locator(".pp-code").first()).toBeVisible();
    await page.getByRole("button", { name: /Hide diff/ }).click();
    await page.getByRole("button", { name: "Reject", exact: true }).click();
    await check(page.locator(".pp-saved")).toHaveText("Your choiceReject");
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await check(page.getByRole("button", { name: /Show diff/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    await check(page.locator(".pp-code")).toHaveCount(0);
  });
});

it.each(["Approve", "Reject"])("accepts %s without ever opening source", async (answer) => {
  await visit(0, "/", async (page) => {
    await page.getByRole("button", { name: "Try two-change practice", exact: true }).click();
    await page.getByRole("button", { name: "Start practice", exact: true }).click();
    await check(page.getByRole("button", { name: /Show diff/ })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    await check(page.locator(".pp-code")).toHaveCount(0);
    await page.getByRole("button", { name: answer, exact: true }).click();
    await check(page.locator(".pp-saved")).toContainText(answer);
    await check(page.locator(".pp-code")).toHaveCount(0);
  });
});
