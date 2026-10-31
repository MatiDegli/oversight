import { mkdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import type { GraphProbeHost } from "../graph";
import type {} from "./nodeChip.browser.fixture";

const out = fileURLToPath(new URL("../../../.shots/node-chip/", import.meta.url));
let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  await mkdir(out, { recursive: true });
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: `${out}cache`,
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import { nodeChipTest } from "/src/app/nodeChip.browser.fixture.tsx"; window.nodeChipTest = nodeChipTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60000);
afterAll(async () => {
  await browser.close();
  await server.close();
});
const chip = (page: Page) =>
  page.getByRole("button", { name: /^(9 of 10 differ|varied on 1 of 2 inputs), compute_price$/ });
const samples = (page: Page) =>
  page.getByRole("region", { name: "compute_price behavior samples" });
const panel = (page: Page) => page.getByRole("complementary", { name: "Review panel" });
const geometry = (page: Page) =>
  page.evaluate(() => {
    const graph = document.querySelector<GraphProbeHost>(
      '[aria-label="Repository graph"]',
    )?.oversightGraph;
    return {
      zoom: graph?.zoom(),
      pan: graph?.pan(),
      positions: graph?.positions(),
    };
  });
async function ready(page: Page) {
  await page.waitForFunction(
    () =>
      document
        .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
        ?.oversightGraph?.animating() === false,
  );
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            resolve();
          }),
        ),
      ),
  );
}
async function withPage(
  theme: string,
  width: number,
  measured: boolean,
  test: (page: Page) => Promise<void>,
) {
  const page = await browser.newPage({
    viewport: { width, height: width === 1920 ? 1080 : 800 },
    reducedMotion: "reduce",
  });
  page.setDefaultTimeout(10000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    await page.waitForFunction(() => "nodeChipTest" in window);
    await page.evaluate(
      ({ theme, measured }) => {
        document.documentElement.dataset.theme = theme;
        window.nodeChipTest.mount(measured ? 21 : 20, measured);
      },
      { theme, measured },
    );
    await page.evaluate(() => document.fonts.ready);
    await ready(page);
    await page.getByRole("button", { name: "Functions", exact: true }).click();
    await ready(page);
    await test(page);
    expect(errors).toEqual([]);
  } finally {
    await page.close();
  }
}
async function tokens(page: Page) {
  const actual = await chip(page).evaluate((button) => {
    const css = getComputedStyle(button),
      probe = document.createElement("span");
    button.append(probe);
    const resolve = (token: string) => {
      probe.style.color = `var(--${token})`;
      return getComputedStyle(probe).color;
    };
    const expected = [resolve("border"), resolve("ground"), resolve("text")];
    probe.remove();
    return { actual: [css.borderTopColor, css.backgroundColor, css.color], expected };
  });
  expect(actual.actual).toEqual(actual.expected);
}
async function anchored(page: Page) {
  const bounds = await chip(page).boundingBox();
  const node = await page.evaluate(() => {
    const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
    const box = host?.oversightGraph?.renderedBox("shop.pricing:compute_price");
    const rect = host?.getBoundingClientRect();
    return box && rect ? { x: rect.left + box.x1, y: rect.top + box.y1 } : null;
  });
  expect(bounds).not.toBeNull();
  expect(node).not.toBeNull();
  expect(Math.abs((bounds?.x ?? 0) - (node?.x ?? 0) - 12)).toBeLessThanOrEqual(1);
  expect(
    Math.abs((bounds?.y ?? 0) + (bounds?.height ?? 0) / 2 - (node?.y ?? 0)),
  ).toBeLessThanOrEqual(1);
}
async function clamped(page: Page) {
  const box = await samples(page).boundingBox(),
    stage = await page.getByRole("main", { name: "Graph stage" }).boundingBox();
  expect(box && stage).toBeTruthy();
  if (!box || !stage) throw new Error("Missing boxes");
  expect(box.width).toBeLessThanOrEqual(440);
  expect(box.x).toBeGreaterThanOrEqual(stage.x + 7);
  expect(box.y).toBeGreaterThanOrEqual(stage.y + 7);
  expect(box.x + box.width).toBeLessThanOrEqual(stage.x + stage.width - 7);
  expect(box.y + box.height).toBeLessThanOrEqual(stage.y + stage.height - 7);
}
async function capture(page: Page, name: string) {
  await ready(page);
  if (await panel(page).isVisible())
    await panel(page).locator(".monaco-editor .view-lines").first().waitFor();
  await page.screenshot({ path: `${out}${name}.png` });
}

async function reviewFlow(page: Page, name: string) {
  await page.getByRole("button", { name: "Review change", exact: true }).click();
  await check(panel(page)).toBeVisible();
  await ready(page);
  await anchored(page);
  await chip(page).focus();
  const before = await geometry(page);
  await chip(page).press("Enter");
  await check(samples(page)).toBeVisible();
  await check(chip(page)).toHaveAttribute("aria-expanded", "true");
  await check(samples(page).locator("li")).toHaveCount(8);
  await check(samples(page).getByText("8 of 9 differing inputs shown")).toBeVisible();
  await check(samples(page).locator("button, input, textarea, [tabindex]")).toHaveCount(0);
  await clamped(page);
  expect(await geometry(page)).toEqual(before);
  await check(chip(page)).toBeFocused();
  await capture(page, `review-${name}`);
  await chip(page).press("Escape");
  await check(samples(page)).toHaveCount(0);
  await check(panel(page)).toBeVisible();
  await check(chip(page)).toBeFocused();
  await chip(page).click();
  await check(samples(page)).toBeVisible();
  await chip(page).click();
  await check(samples(page)).toHaveCount(0);
  expect(await geometry(page)).toEqual(before);
  await check(chip(page)).toBeFocused();
  await chip(page).click();
  await page.evaluate(() => {
    window.nodeChipTest.emit(22);
  });
  await check(chip(page)).toHaveText("varied on 1 of 2 inputs");
  await check(chip(page)).toHaveCSS("font-weight", "400");
  await check(samples(page)).toBeVisible();
  await check(samples(page).locator("li")).toHaveCount(1);
  await check(
    samples(page).getByText("Results varied between runs; this is not an established difference."),
  ).toBeVisible();
  await check(
    samples(page).getByText("Behavior probing was unavailable; no further reason was published."),
  ).toBeVisible();
  expect(await geometry(page)).toEqual(before);
  await check(chip(page)).toBeFocused();
  await capture(page, `partial-${name}`);
  await chip(page).press("Escape");
  await check(panel(page)).toBeVisible();
  await chip(page).press("Escape");
  await check(panel(page)).toHaveCount(0);
}

it.each([
  ["dark", 1920],
  ["day", 1920],
  ["dark", 1280],
  ["day", 1280],
] as const)(
  "keeps behavior chips still, accessible and token-colored in %s at %i",
  async (theme, width) => {
    const name = `${String(width)}-${theme}`;
    await withPage(theme, width, false, async (page) => {
      await check(chip(page)).toHaveCount(0);
      const before = await geometry(page);
      const focused = await page.evaluateHandle(() => document.activeElement);
      await page.evaluate(() => {
        window.nodeChipTest.emit(21);
      });
      await check(chip(page)).toBeVisible();
      await anchored(page);
      await tokens(page);
      expect(await geometry(page)).toEqual(before);
      expect(await page.evaluate((element) => element === document.activeElement, focused)).toBe(
        true,
      );
      await check(samples(page)).toHaveCount(0);
      await capture(page, `watch-${name}`);
      await reviewFlow(page, name);
      await chip(page).click();
      await check(samples(page)).toBeVisible();
      await page.getByRole("main", { name: "Graph stage" }).click({ position: { x: 8, y: 8 } });
      await check(samples(page)).toHaveCount(0);
      await chip(page).click();
      await check(samples(page)).toBeVisible();
      await page.evaluate(() => {
        window.nodeChipTest.emit(23);
      });
      await check(chip(page)).toHaveCount(0);
      await check(samples(page)).toHaveCount(0);
    });
    await withPage(theme, width, true, async (page) => {
      await page.getByRole("button", { name: "Review change", exact: true }).click();
      await check(panel(page)).toBeVisible();
      await check(chip(page)).toHaveCount(0);
      await check(page.locator("[data-chip-node]")).toHaveCount(0);
      await capture(page, `measured-${name}`);
    });
  },
  120000,
);

it("places the captured graph evidence beside its approved sketches", async () => {
  const page = await browser.newPage({ viewport: { width: 3840, height: 1080 } });
  try {
    for (const [live, sketch] of [
      ["review", "behavior-dark"],
      ["watch", "watch-ready-dark"],
      ["measured", "measured-dark"],
    ] as const) {
      const path = new URL(`../../../docs/sketches/graph-evidence/${sketch}.png`, import.meta.url);
      const reference = await readFile(path).catch((error: unknown) => {
        if (live !== "measured") throw error;
        return readFile(`${out}reference-measured-dark.png`).catch(() => null);
      });
      if (!reference) {
        console.info("Measured sketch comparison awaits the parallel front's reference.");
        continue;
      }
      const actual = await readFile(`${out}${live}-1920-dark.png`);
      await page.setContent(
        `<body style="margin:0;display:flex"><img src="data:image/png;base64,${reference.toString("base64")}"><img src="data:image/png;base64,${actual.toString("base64")}"></body>`,
      );
      await page
        .locator("img")
        .evaluateAll((images) =>
          Promise.all(images.map((img) => (img as HTMLImageElement).decode())),
        );
      await page.screenshot({ path: `${out}compare-${live}.png`, fullPage: true });
    }
  } finally {
    await page.close();
  }
});

it("handles long inputs, empty samples, two chips, folded and removed nodes", async () => {
  await withPage("dark", 1920, false, async (page) => {
    await page.evaluate(() => {
      window.nodeChipTest.emit(21);
      window.nodeChipTest.amend("long");
    });
    await chip(page).click();
    await check(samples(page)).toBeVisible();
    await clamped(page);
    expect(await samples(page).evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
    await page.evaluate(() => {
      window.nodeChipTest.amend("empty");
    });
    await check(samples(page).locator("li")).toHaveCount(0);
    await check(samples(page).getByText("0 of 9 differing inputs shown")).toBeVisible();
    await page.evaluate(() => {
      window.nodeChipTest.amend("two");
    });
    for (let index = 0; index < 2; index++)
      await page.getByRole("button", { name: "Zoom in", exact: true }).click();
    const buttons = page.locator("[data-chip-node] button");
    await check(buttons.nth(1)).toBeVisible();
    await check(buttons).toHaveText(["A 9 of 10 differ", "B 9 of 10 differ"]);
    const first = await buttons.nth(0).boundingBox(),
      second = await buttons.nth(1).boundingBox();
    expect(first && second && first.x + first.width <= second.x).toBe(true);
    await buttons.nth(1).evaluate((button) => {
      (button as HTMLButtonElement).focus({ preventScroll: true });
    });
    await buttons.nth(1).press("Enter");
    await check(page.locator("[data-chip-popover]")).toHaveCount(1);
    await check(buttons.nth(0)).toHaveAttribute("aria-expanded", "false");
  });
  for (const mode of ["folded", "gone"] as const)
    await withPage("dark", 1920, false, async (page) => {
      await page.evaluate((mode) => {
        window.nodeChipTest.emit(21);
        window.nodeChipTest.amend(mode);
      }, mode);
      await check(page.locator("[data-chip-node] button")).toBeHidden();
    });
});
it("hides chips too wide for the drawn node and closes their popover", async () => {
  await withPage("dark", 1920, false, async (page) => {
    await page.evaluate(() => {
      window.nodeChipTest.emit(21);
    });
    await chip(page).click();
    await check(samples(page)).toBeVisible();
    for (let index = 0; index < 8; index++)
      await page.getByRole("button", { name: "Zoom out", exact: true }).click();
    await check(chip(page)).toBeHidden();
    await check(samples(page)).toHaveCount(0);
  });
});
