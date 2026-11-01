import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import type {} from "./chatWhere.browser.fixture";

let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/chat-where/cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><div id="root" style="height:100vh"></div><script type="module">import "/src/theme/index.css";import "/src/app/chatWhere.browser.fixture.tsx";</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});
async function open(working = true, width = 1280, theme = "dark") {
  const page = await browser.newPage({
    viewport: { width, height: width === 1920 ? 1080 : 800 },
    reducedMotion: "reduce",
  });
  const requests: string[] = [];
  page.on("request", (r) => requests.push(new URL(r.url()).pathname));
  await page.route("**/", (r) => r.fulfill({ contentType: "text/html", body: html }));
  await page.goto(url);
  await page.waitForFunction(() => !!window.chatWhereTest);
  await page.evaluate(
    ({ working, theme }) => {
      document.documentElement.dataset.theme = theme;
      window.chatWhereTest.mount(working);
    },
    { working, theme },
  );
  await page.evaluate(() => document.fonts.ready);
  return { page, requests };
}
const switchTo = (p: Page, name: string) =>
  p.getByRole("group", { name: "Workspace view" }).getByRole("button", { name, exact: true });
async function resolutionsKeepAttention(page: Page) {
  const draft = page.getByRole("textbox", { name: "Message Agent A" });
  const row = page.getByRole("treeitem", { name: "pricing.py", exact: true });
  const search = page.getByRole("button", {
    name: "Find a function, file or command",
    exact: true,
  });
  await search.focus();
  for (const action of ["proposal", "conflict", "revision", "proposal", "rejection"] as const) {
    await page.evaluate((action) => {
      if (action === "proposal") window.chatWhereTest.stage("proposed");
      if (action === "conflict") window.chatWhereTest.resolve("approved", "conflict");
      if (action === "revision") window.chatWhereTest.resolve("changes_requested");
      if (action === "rejection") window.chatWhereTest.resolve("rejected");
    }, action);
    await check(search).toBeFocused();
    await check(draft).toHaveValue("Preserve this draft");
    await check(row.locator('[data-agent-id="agent-1"]')).toHaveCount(
      action === "proposal" || action === "conflict" ? 1 : 0,
    );
    if (action === "conflict")
      await check(page.locator(".work-agent").last()).toHaveText(
        "Agent A · merge conflicts with main",
      );
  }
}
it("shares presence without refetch, focus, folding, scroll or draft changes; defers the graph", async () => {
  const { page, requests } = await open();
  try {
    const draft = page.getByRole("textbox", { name: "Message Agent A" });
    await draft.fill("Preserve this draft");
    await page.evaluate(() => {
      window.chatWhereTest.stage("working");
      window.chatWhereTest.stage("proposed");
      window.chatWhereTest.resolve("changes_requested");
    });
    await check(draft).toBeFocused();
    await check(draft).toHaveValue("Preserve this draft");
    const original = await draft.elementHandle();
    await page.getByRole("button", { name: "Files", exact: true }).click();
    const tree = page.getByRole("tree", { name: "Repository files" });
    const shop = tree.getByRole("treeitem", { name: "shop", exact: true });
    await check(shop).toHaveAttribute("aria-expanded", "false");
    await tree.focus();
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("Enter");
    expect(await page.evaluate(() => window.chatWhereTest.opened())).toEqual(["shop/inventory.py"]);
    await tree.evaluate((el) => {
      el.scrollTop = 100;
    });
    const selected = await tree.getAttribute("aria-activedescendant");
    const rowBefore = await tree
      .getByRole("treeitem", { name: "pricing.py", exact: true })
      .boundingBox();
    await page.evaluate(() => {
      window.chatWhereTest.resume();
    });
    await page.evaluate(() => {
      window.chatWhereTest.focus("agent-1", "edit", "shop/pricing.py");
      window.chatWhereTest.focus("agent-2", "read", "shop/pricing.py");
    });
    const row = tree.getByRole("treeitem", { name: "pricing.py", exact: true });
    await check(row.locator(".file-agent-mark")).toHaveCount(2);
    expect(await row.boundingBox()).toEqual(rowBefore);
    await check(row.getByLabel("Agent A · Editing")).toHaveAttribute("data-agent-id", "agent-1");
    await check(row.getByLabel("Agent B · Reading")).toBeAttached();
    await check(page.locator('.work-group[data-mode="edit"] .work-path')).toHaveText(
      "shop/pricing.py",
    );
    await check(tree).toBeFocused();
    await check(tree).toHaveJSProperty("scrollTop", 100);
    await page.evaluate(() => {
      window.chatWhereTest.stage("proposed");
      window.chatWhereTest.resolve("changes_requested");
    });
    await check(tree).toBeFocused();
    await check(tree).toHaveJSProperty("scrollTop", 100);
    expect(await tree.getAttribute("aria-activedescendant")).toBe(selected);
    await resolutionsKeepAttention(page);
    expect(await page.evaluate(() => window.chatWhereTest.loads())).toBe(1);
    expect(
      requests.filter((s) =>
        /GraphView|DotField|cytoscape|\/field\/controller|OversightStage/.test(s),
      ),
    ).toEqual([]);
    await switchTo(page, "Oversight").click();
    await check(page.getByLabel("Repository graph", { exact: true })).toBeVisible();
    await switchTo(page, "Chat").click();
    await check(switchTo(page, "Chat")).toBeFocused();
    await check(draft).toHaveValue("Preserve this draft");
    expect(await original.evaluate((el) => el.isConnected)).toBe(true);
    await tree.focus();
    await page.keyboard.press("Home");
    await page.keyboard.press("ArrowLeft");
    await check(shop).toHaveAttribute("aria-expanded", "false");
  } finally {
    await page.close();
  }
});
async function measure(page: Page) {
  return page.evaluate(() => {
    const css = (s: string) => {
      const el = document.querySelector(s);
      if (!el) return null;
      const c = getComputedStyle(el);
      return {
        width: el.getBoundingClientRect().width,
        padding: c.padding,
        gap: c.columnGap,
        radius: c.borderRadius,
        font: c.fontFamily,
        size: c.fontSize,
        lineHeight: c.lineHeight,
      };
    };
    return {
      column: css(".chat-work"),
      header: css(".chat-work > header"),
      group: css(".work-group"),
      badge: css(".work-badge"),
      row: css(".work-file"),
      path: css(".work-path"),
      detail: css(".work-range"),
      overflow: [...document.querySelectorAll(".work-path")].some(
        (el) => el.scrollWidth > el.clientWidth,
      ),
    };
  });
}
const directory = fileURLToPath(new URL("../../../.shots/chat-where/", import.meta.url));
it.each(
  [1920, 1280].flatMap((width) =>
    ["dark", "day"].flatMap((theme) =>
      ["empty", "picker", "working", "proposed", "merged"].map((state) => ({
        width,
        theme,
        state,
      })),
    ),
  ),
)("measures $state-$width-$theme", async ({ width, theme, state }) => {
  const { page } = await open(state !== "empty" && state !== "picker", width, theme);
  try {
    await page.evaluate((state) => {
      if (state === "empty" || state === "picker") return;
      window.chatWhereTest.stage("working");
      if (state !== "working") window.chatWhereTest.stage("proposed");
      if (state === "merged") window.chatWhereTest.stage("merged");
    }, state);
    await check(page.locator(".where-they-work")).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    const measured = await measure(page);
    expect(measured.column?.width).toBe(width === 1920 ? 300 : 260);
    expect(measured.overflow).toBe(false);
    if (state === "merged") {
      await check(page.locator(".work-empty")).toHaveText("Nobody is reading or editing now.");
      await check(page.locator('.work-group[data-agent-id="agent-2"]')).toHaveCount(0);
    }
    if (state === "working" || state === "proposed")
      await check(page.locator(".work-empty")).toHaveCount(0);
    if (state === "proposed" || state === "merged") {
      const path = page.locator(".work-path").filter({ hasText: "tests/test_pricing.py" });
      await check(path).toHaveText("tests/test_pricing.py");
      await check(page.locator('.work-group[data-mode="pending"] .work-statistics')).toHaveText([
        "+6 −0",
        "+15 −0",
      ]);
      if (width === 1280) expect((await path.boundingBox())?.height ?? 0).toBeGreaterThan(30);
    }
    if (process.env.CHAT_WHERE_SHOTS === "1") {
      await mkdir(directory, { recursive: true });
      const name = `${state}-${String(width)}-${theme}`;
      await page.screenshot({ path: `${directory}${name}.png`, animations: "disabled" });
      await writeFile(`${directory}${name}.json`, JSON.stringify(measured, null, 2));
      if (state === "working") {
        await page.evaluate(() => {
          window.chatWhereTest.focus("agent-2", "read", "shop/pricing.py");
        });
        await switchTo(page, "Oversight").click();
        await page.getByRole("button", { name: "Files", exact: true }).click();
        await page.getByRole("treeitem", { name: "shop", exact: true }).click();
        await check(page.locator(".file-agent-mark")).toHaveCount(2);
        await page.waitForFunction(
          () =>
            !!document.querySelector<HTMLElement & { oversightGraph?: unknown }>(
              '[aria-label="Repository graph"]',
            )?.oversightGraph,
        );
        await page.screenshot({
          path: `${directory}files-${String(width)}-${theme}.png`,
          animations: "disabled",
        });
      }
    }
    if (state === "proposed") {
      await page.evaluate(() => {
        window.chatWhereTest.many();
      });
      expect(
        await page
          .locator(".chat-work .chat-side-body")
          .evaluate((el) => el.scrollHeight > el.clientHeight && el.scrollWidth === el.clientWidth),
      ).toBe(true);
      const composer = await page.locator(".conversation-composer").boundingBox();
      await page.locator(".chat-work .chat-side-body").evaluate((el) => {
        el.scrollTop = 200;
      });
      expect(await page.locator(".conversation-composer").boundingBox()).toEqual(composer);
    }
  } finally {
    await page.close();
  }
});
