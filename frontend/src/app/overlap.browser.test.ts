import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import type { GraphProbeHost } from "../graph";
import { launchBrowser } from "../browserLaunch.support";
import type {} from "./overlap.browser.fixture";

let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/live-overlap-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html data-theme="dark"><body><script type="module">import "/src/theme/index.css"; import { overlapTest } from "/src/app/overlap.browser.fixture.tsx"; window.overlapTest = overlapTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});
async function open(width = 1920, height = 1080, theme = "dark") {
  const page = await browser.newPage({ viewport: { width, height }, reducedMotion: "reduce" });
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.goto(url);
  await page.waitForFunction(() => !!window.overlapTest);
  await page.evaluate((theme) => {
    document.documentElement.dataset.theme = theme;
    window.overlapTest.mount();
  }, theme);
  await page.evaluate(() => document.fonts.ready);
  await page.waitForFunction(
    () =>
      !!document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]')?.oversightGraph,
  );
  return page;
}
const card = (page: Page, slot: string) =>
  page.getByRole("article", { name: `Agent ${slot}`, exact: true });

async function camera(page: Page) {
  return page.evaluate(() => {
    const probe = document.querySelector<GraphProbeHost>(
      '[aria-label="Repository graph"]',
    )?.oversightGraph;
    return { pan: probe?.pan(), zoom: probe?.zoom() };
  });
}

it.each(["read", "same-node"] as const)(
  "shows %s facts without focus or navigation changes",
  async (moment) => {
    const page = await open();
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    try {
      const focus = card(page, "B").getByRole("list", { name: "Agent B steps, newest first" });
      await focus.focus();
      const before = await camera(page);
      await page.evaluate((value) => {
        window.overlapTest.apply(value);
      }, moment);
      await check(focus).toBeFocused();
      await check(page.getByLabel("Focus panel", { exact: true })).toHaveCount(0);
      expect(await page.evaluate(() => window.overlapTest.navigation())).toBe(0);
      expect(await camera(page)).toEqual(before);
      if (moment === "read") {
        await check(
          card(page, "B").getByText("Read compute_price, which Agent A is changing", {
            exact: true,
          }),
        ).toBeVisible();
        await check(card(page, "A").getByText(/Also changed by|which Agent/)).toHaveCount(0);
      } else {
        for (const [slot, other] of [
          ["A", "B"],
          ["B", "A"],
        ] as const) {
          await check(
            card(page, slot).getByText(`Also changed by Agent ${other}: compute_price`, {
              exact: true,
            }),
          ).toBeVisible();
          await check(card(page, slot).getByText(/which Agent/)).toHaveCount(0);
        }
      }
      const size = await card(page, "B").boundingBox();
      await page.evaluate(() => {
        window.overlapTest.ready();
      });
      await check(card(page, "B").getByRole("status")).toHaveText("Ready for review");
      expect(await card(page, "B").boundingBox()).toEqual(size);
      await check(
        card(page, "B").getByText(
          moment === "read"
            ? "Read compute_price, which Agent A is changing"
            : "Also changed by Agent A: compute_price",
          { exact: true },
        ),
      ).toBeVisible();
      expect(errors).toEqual([]);
    } finally {
      await page.close();
    }
  },
);

it.each([1, 2])("contains long multilingual names at %s× scale", async (scale) => {
  const page = await open(1280, 800);
  try {
    const name = "compute_é_日本語_مرحبا_🧪_<>&".repeat(8);
    await page.evaluate(
      ({ name, scale }) => {
        document.body.style.zoom = String(scale);
        window.overlapTest.apply("same-node", name);
      },
      { name, scale },
    );
    for (const [slot, other] of [
      ["A", "B"],
      ["B", "A"],
    ] as const) {
      const line = card(page, slot).getByText(`Also changed by Agent ${other}: ${name}`, {
        exact: true,
      });
      await check(line).toHaveAttribute("title", `Also changed by Agent ${other}: ${name}`);
      expect(
        await line.evaluate((element) => ({
          truncated: element.scrollWidth > element.clientWidth,
          ellipsis: getComputedStyle(element).textOverflow,
          height: element.closest(".agent-card-body")?.clientHeight,
        })),
      ).toEqual({ truncated: true, ellipsis: "ellipsis", height: 144 });
    }
  } finally {
    await page.close();
  }
});

it.each([1920, 1280])("captures both moments in both themes at %s px", async (width) => {
  if (process.env.LIVE_OVERLAP_SHOTS !== "1") return;
  const shots = fileURLToPath(new URL("../../../.shots/live-overlap/", import.meta.url));
  await mkdir(shots, { recursive: true });
  for (const theme of ["dark", "day"]) {
    const page = await open(width, width === 1920 ? 1080 : 800, theme);
    try {
      for (const moment of ["read", "same-node"] as const) {
        await page.evaluate((moment) => {
          window.overlapTest.apply(moment);
        }, moment);
        await page
          .getByRole("button", { name: "Fit the graph to the view", exact: true })
          .press("Enter");
        await card(page, "B").scrollIntoViewIfNeeded();
        await check(card(page, "B")).toBeInViewport();
        await page.screenshot({
          path: `${shots}${moment}-${theme}-${String(width)}.png`,
          animations: "disabled",
        });
      }
    } finally {
      await page.close();
    }
  }
});
