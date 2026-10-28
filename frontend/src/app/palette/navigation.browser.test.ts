import { fileURLToPath } from "node:url";
import { chromium, expect as check } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import type {} from "./navigation.browser.fixture";
import type { GraphProbeHost } from "../../graph";
import { fixtureCacheDir } from "../../../shots/browserCache.support";

let server: ViteDevServer | undefined;
let browser: Browser | undefined;
let html: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../../", import.meta.url)),
    cacheDir: fixtureCacheDir(expect.getState().testPath),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import { paletteNavigation } from "/src/app/palette/navigation.browser.fixture.tsx"; window.paletteNavigation = paletteNavigation;</script></body></html>',
  );
  await server.listen();
  browser = await chromium.launch();
}, 60_000);
afterAll(async () => {
  await browser?.close();
  await server?.close();
});
async function withPage(test: (page: Page) => Promise<void>) {
  if (!browser || !server) throw new Error("Browser fixture did not start");
  const page = await browser.newPage({
    viewport: { width: 1920, height: 1080 },
    reducedMotion: "reduce",
  });
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(error.message));
  try {
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(server.resolvedUrls?.local[0] ?? "");
    await page.waitForFunction(() => Boolean(window.paletteNavigation));
    await test(page);
    expect(failures).toEqual([]);
  } finally {
    await page.close();
  }
}
async function mount(page: Page, seq: number, recorded = false) {
  await page.evaluate(
    ({ seq, recorded }) => {
      window.paletteNavigation.mount(seq, recorded);
    },
    { seq, recorded },
  );
  await page.waitForFunction(
    () =>
      (document
        .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
        ?.oversightGraph?.nodeCenters().length ?? 0) > 0,
  );
}
async function choose(page: Page, text: string) {
  await page.getByRole("button", { name: "Find a function, file or command", exact: true }).click();
  await page.getByRole("combobox").fill(text);
  await page.getByRole("combobox").press("Enter");
}
async function clickPrice(page: Page) {
  const point = await page.evaluate(() => {
    const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
    const graph = host?.oversightGraph;
    const node = graph
      ?.nodeCenters()
      .find((node) => node.id === graph.representative("shop.pricing:compute_price"));
    if (!node || !host) throw new Error("Missing price node");
    const box = host.getBoundingClientRect();
    return { x: box.left + node.x, y: box.top + node.y };
  });
  await page.mouse.click(point.x, point.y);
}
it.each([
  [14, "Live diff panel"],
  [20, "Review panel"],
  [23, "File view"],
] as const)(
  "at seq %s choosing a function opens exactly the node click's %s and frames it",
  async (seq, panel) => {
    await withPage(async (page) => {
      await mount(page, seq);
      await clickPrice(page);
      await check(page.getByRole("complementary", { name: panel, exact: true })).toBeVisible();
      const clicked = await page.getByRole("tab", { selected: true }).textContent();
      await mount(page, seq);
      await choose(page, "compute_price");
      await check(page.getByRole("complementary", { name: panel, exact: true })).toBeVisible();
      expect(await page.getByRole("tab", { selected: true }).textContent()).toBe(clicked);
      await check
        .poll(() =>
          page.evaluate(() => {
            const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
            const graph = host?.oversightGraph;
            const node = graph
              ?.nodeCenters()
              .find((node) => node.id === graph.representative("shop.pricing:compute_price"));
            return (
              !!host &&
              !!node &&
              !graph?.animating() &&
              node.x > 0 &&
              node.x < host.clientWidth &&
              node.y > 0 &&
              node.y < host.clientHeight
            );
          }),
        )
        .toBe(true);
    });
  },
);
it("recorded Files are searchable without a backend; unavailable commands do nothing", async () => {
  await withPage(async (page) => {
    const requests: string[] = [];
    page.on("request", (request) => {
      if (/\/(tree|files|graph\/rescan)(\?|$)/.test(request.url())) requests.push(request.url());
    });
    await mount(page, 20, true);
    await page.getByRole("button", { name: "Files", exact: true }).click();
    await page.getByRole("treeitem", { name: "shop", exact: true }).click();
    await check(page.getByRole("treeitem", { name: "pricing.py", exact: true })).toBeVisible();
    await page
      .getByRole("button", { name: "Find a function, file or command", exact: true })
      .click();
    await page.getByRole("combobox").fill("Rescan graph");
    await check(page.getByRole("option")).toHaveAttribute("aria-disabled", "true");
    await page.getByRole("combobox").press("Enter");
    await check(page.getByRole("dialog")).toBeVisible();
    await page.getByRole("combobox").fill("shop/pricing.py");
    await page.getByRole("combobox").press("Enter");
    await check(page.getByRole("complementary", { name: "File view", exact: true })).toBeVisible();
    await check(page.getByRole("heading", { name: "shop/pricing.py", exact: true })).toBeVisible();
    expect(requests).toEqual([]);
  });
});
