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

it.each([1280, 1920])("excludes the phone rows and their styles at %ipx", async (width) => {
  await visit(0, "/", async (page) => {
    await page.setViewportSize({ width, height: 1080 });
    await page.getByRole("button", { name: "Two-change practice", exact: true }).click();
    await page.getByRole("button", { name: "Start practice", exact: true }).click();
    await check(page.locator(".practice-panel")).toBeVisible();
    await check(page.locator(".phone-practice, .pp-decision-row")).toHaveCount(0);
    const rules = await page.evaluate(() => {
      const sheet = document.querySelector<HTMLStyleElement>(
        'style[data-vite-dev-id$="/phoneRows.css"]',
      )?.sheet;
      if (!sheet) throw new Error("Missing decision-row stylesheet");
      return [...sheet.cssRules].map((rule) => ({
        media: rule instanceof CSSMediaRule,
        active: rule instanceof CSSMediaRule ? matchMedia(rule.conditionText).matches : true,
      }));
    });
    expect(rules.length).toBeGreaterThan(0);
    expect(rules.every((rule) => rule.media && !rule.active)).toBe(true);
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

async function tokenColor(page: Page, token: string) {
  return page.locator(".phone-practice").evaluate((element, token) => {
    const sample = document.createElement("span");
    sample.style.color = `var(--${token})`;
    element.append(sample);
    const color = getComputedStyle(sample).color;
    sample.remove();
    return color;
  }, token);
}
async function checkDecisionRows(page: Page) {
  const rows = page.locator(".pp-decision-row");
  const list = page.locator(".pp-decision-rows");
  const divider = await tokenColor(page, "divider");
  await check(list).toHaveCSS("border-top", `1px solid ${divider}`);
  await check(list).toHaveCSS("border-bottom", `1px solid ${divider}`);
  for (const row of await rows.all()) {
    for (const side of ["top", "right", "bottom", "left"])
      await check(row).toHaveCSS(`border-${side}-width`, "0px");
    await check(row).toHaveCSS("border-radius", "0px");
    await check(row).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
    const summary = row.locator("summary");
    expect((await summary.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    expect((await summary.boundingBox())?.width).toBe((await list.boundingBox())?.width);
    await check(summary.locator(".lucide-chevron-down")).toHaveCount(1);
    await check(summary.locator(".lucide-chevron-right")).toHaveCount(0);
    const preview = summary.locator(".pp-row-text");
    await check(preview).toHaveCSS("white-space", "nowrap");
    await check(preview).toHaveCSS("text-overflow", "ellipsis");
    await check(preview).toHaveCSS("overflow", "hidden");
    const muted = await tokenColor(page, "muted");
    await check(preview).toHaveCSS("color", muted);
    await check(preview.locator(".pp-row-label")).toHaveCSS("color", muted);
    await check(summary).toHaveCSS("font-size", "13px");
  }
  const task = page.locator(".pp-task");
  await check(rows.first().locator("summary")).toHaveText(
    `Recorded task · ${(await task.textContent()) ?? ""}`,
  );
  const folds = await page.locator(".pp-fold > span:first-child").allTextContents();
  if ((await rows.count()) === 2) {
    await check(rows.last().locator("summary")).toHaveText(
      `Possibly affected · ${folds.join(", ")}`,
    );
    const separator = await rows.last().evaluate((element) => {
      const style = getComputedStyle(element, "::before");
      return { color: style.backgroundColor, height: style.height, content: style.content };
    });
    expect(separator).toEqual({ color: divider, height: "1px", content: '""' });
  }
}
async function checkDockClearance(page: Page) {
  const row = await page.locator(".pp-decision-row").last().boundingBox();
  const dock = await page.locator(".pp-dock").boundingBox();
  if (!row || !dock) throw new Error("Missing decision row or dock");
  expect(row.y + row.height).toBeLessThan(dock.y);
}
async function checkRowExpansion(page: Page) {
  const rows = page.locator(".pp-decision-row");
  const graph = await page.locator(".pp-graph").boundingBox();
  const dock = await page.locator(".pp-dock").boundingBox();
  const list = await page.locator(".pp-decision-rows").elementHandle();
  for (const row of await rows.all()) {
    const summary = row.locator("summary");
    await check(row).not.toHaveAttribute("open");
    const before = await row.boundingBox();
    await summary.click();
    await check(row).toHaveAttribute("open");
    expect((await row.boundingBox())?.height).toBeGreaterThan(before?.height ?? 0);
    await check(summary.locator("svg")).toHaveCSS("transform", "matrix(-1, 0, 0, -1, 0, 0)");
    expect(await page.locator(".pp-graph").boundingBox()).toEqual(graph);
    expect(await page.locator(".pp-dock").boundingBox()).toEqual(dock);
    expect(
      await list.evaluate((element) => element === document.querySelector(".pp-decision-rows")),
    ).toBe(true);
    await page.locator(".pp-main").evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    await checkDockClearance(page);
    await summary.click();
    await check(row).not.toHaveAttribute("open");
    await page.locator(".pp-main").evaluate((element) => {
      element.scrollTop = 0;
    });
  }
}
it.each([
  [390, 844, "dark"],
  [390, 844, "day"],
  [430, 932, "dark"],
  [430, 932, "day"],
] as const)(
  "shows list rows above the dock on first view at %ix%i in %s",
  async (width, height, theme) => {
    await visit(0, "/", async (page) => {
      await page.setViewportSize({ width, height });
      await page.getByRole("button", { name: "Try two-change practice", exact: true }).click();
      await page.getByRole("button", { name: "Start practice", exact: true }).click();
      await page.evaluate(async (theme) => {
        await document.fonts.ready;
        document.documentElement.dataset.theme = theme;
      }, theme);
      await check(page.locator(".pp-decision-row")).toHaveCount(1);
      await checkDecisionRows(page);
      await checkDockClearance(page);
      await page.getByRole("button", { name: "Reject", exact: true }).click();
      await page.getByRole("button", { name: "Next", exact: true }).click();
      await check(page.locator(".pp-decision-row")).toHaveCount(2);
      expect(await page.locator(".pp-main").evaluate((element) => element.scrollTop)).toBe(0);
      await checkDecisionRows(page);
      await checkDockClearance(page);
      await checkRowExpansion(page);
    });
  },
);

it.each([
  [390, 844],
  [430, 932],
])(
  "keeps the first reveal comparison clear of the dock at %ix%i and expands evidence in place",
  async (width, height) => {
    await visit(0, "/", async (page) => {
      await page.setViewportSize({ width, height });
      await page.getByRole("button", { name: "Try two-change practice", exact: true }).click();
      await page.getByRole("button", { name: "Start practice", exact: true }).click();
      await page.getByRole("button", { name: "Approve", exact: true }).click();
      await page.getByRole("button", { name: "Next", exact: true }).click();
      await page.getByRole("button", { name: "Reject", exact: true }).click();
      await page.getByRole("button", { name: "Show findings", exact: true }).click();
      await page.evaluate(() => document.fonts.ready);
      for (const theme of ["dark", "day"]) {
        await page.evaluate((theme) => {
          document.documentElement.dataset.theme = theme;
        }, theme);
        const comparison = await page.locator(".pp-comparison").first().boundingBox();
        const dock = await page.locator(".pp-dock").boundingBox();
        if (!comparison || !dock) throw new Error("Missing comparison or dock");
        expect(comparison.y + comparison.height).toBeLessThan(dock.y);
        const rows = page.locator(".pp-result").first().locator(".evidence-row");
        await check(rows).toHaveCount(4);
        await check(rows.first()).not.toHaveAttribute("open");
        const summary = rows.first().locator("summary");
        expect((await summary.boundingBox())?.height).toBeGreaterThanOrEqual(44);
        await check(summary).toContainText("2 runs, last 29 of 29 passed");
        await summary.click();
        await check(rows.first()).toHaveAttribute("open");
        await check(rows.first()).toContainText(
          "Step 10 · passed · 29 passed · 0 failed · 0 skipped · 236 ms",
        );
        await check(summary).toBeFocused();
        expect(await page.locator(".pp-dock").boundingBox()).toEqual(dock);
        await summary.click();
        await page.locator(".pp-main").evaluate((element) => {
          element.scrollTop = 0;
        });
      }
    });
  },
);
