import { fileURLToPath } from "node:url";
import { mkdir } from "node:fs/promises";
import { expect as check } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../../browserLaunch.support";
import type {} from "./conversation.browser.fixture";

let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../../.shots/conversation/cache", import.meta.url)),
    server: {
      host: "127.0.0.1",
      port: 0,
      strictPort: false,
      fs: { allow: [fileURLToPath(new URL("../../../../", import.meta.url))] },
    },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html data-theme="dark"><body><script type="module">import "/src/theme/index.css"; import { conversationTest } from "/src/agents/conversation/conversation.browser.fixture.tsx"; window.conversationTest = conversationTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});
async function pageFor(
  mode: Parameters<Window["conversationTest"]["mount"]>[0] = "working",
  width = 880,
  theme = "dark",
  timezoneId = "America/Argentina/Buenos_Aires",
) {
  const page = await browser.newPage({ viewport: { width, height: 962 }, timezoneId });
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.goto(url);
  await page.waitForFunction(() => !!window.conversationTest);
  await page.evaluate(
    ({ mode, theme }) => {
      document.documentElement.dataset.theme = theme;
      window.conversationTest.mount(mode);
    },
    { mode, theme },
  );
  await page.evaluate(() => document.fonts.ready);
  return page;
}
const box = (page: Page) => page.getByRole("textbox", { name: "Message Agent A" });
const send = (page: Page) => page.getByRole("button", { name: "Send", exact: true });
const timeline = (page: Page) => page.getByRole("list", { name: "Conversation timeline" });

it.each([
  "working",
  "revising",
  "paused",
  "starting",
  "ready_for_review",
  "halted",
  "stopped",
  "done",
  "replay",
] as const)("composer in %s", async (mode) => {
  const page = await pageFor(mode);
  try {
    if (mode === "replay") {
      await check(page.getByText("Recorded run: messages, pause and stop are off.")).toBeVisible();
      await check(box(page)).toHaveCount(0);
    } else if (["working", "revising", "paused"].includes(mode)) {
      await check(box(page)).toBeEnabled();
      const name = mode === "paused" ? "Resume" : "Pause";
      await page.getByRole("button", { name, exact: true }).click();
      expect(await page.evaluate(() => window.conversationTest.requests())).toEqual([name]);
      await check(
        page.getByRole("button", { name: "Stop and discard", exact: true }),
      ).toBeVisible();
    } else {
      await check(box(page)).toBeDisabled();
      await check(send(page)).toBeDisabled();
      await check(page.getByText("Agent A is not running.")).toBeVisible();
      await check(
        page.getByRole("button", { name: /^(Pause|Resume|Stop and discard)$/ }),
      ).toHaveCount(0);
    }
  } finally {
    await page.close();
  }
});

it("sends only by button, guards pending, retains failed text and clears on success", async () => {
  const page = await pageFor();
  try {
    await box(page).fill("Point to _COUPONS.");
    await box(page).press("Enter");
    await box(page).press("Control+Enter");
    expect(await page.evaluate(() => window.conversationTest.requests())).toEqual([]);
    const text = await box(page).inputValue();
    expect(text).toContain("\n");
    await send(page).click();
    await check(send(page)).toBeDisabled();
    await check(send(page)).toBeFocused();
    expect(await page.evaluate(() => window.conversationTest.requests())).toEqual([text]);
    await page.evaluate(() => {
      window.conversationTest.answer(false);
    });
    await check(page.getByRole("alert")).toHaveText(
      "Send did not reach the server. Check the connection and try again.",
    );
    await check(box(page)).toHaveValue(text);
    await send(page).click();
    await page.evaluate(() => {
      window.conversationTest.answer(true);
    });
    await check(box(page)).toHaveValue("");
    await check(send(page)).toBeFocused();
  } finally {
    await page.close();
  }
});

it("discloses structured test results and opens the change diff", async () => {
  const page = await pageFor();
  try {
    await page.getByRole("button", { name: /^Round 1 ·/ }).click();
    const summary = page.getByText("Test output", { exact: true });
    const output = page.getByText("28 passed in 0.40s", { exact: true });
    await check(output).not.toBeVisible();
    await summary.click();
    await check(output).toBeVisible();
    await check(page.getByText("tests/test_pricing.py · 12 passed", { exact: true })).toHaveCount(
      0,
    );
    await page.getByRole("button", { name: "View in diff" }).first().click();
    expect(await page.evaluate(() => window.conversationTest.requests())).toEqual(["change-1"]);
  } finally {
    await page.close();
  }
});

it("pins new entries only at the bottom and never moves focus", async () => {
  const page = await pageFor();
  try {
    const list = timeline(page);
    const distance = () =>
      list.evaluate((node) => node.scrollHeight - node.scrollTop - node.clientHeight);
    expect(await distance()).toBeLessThanOrEqual(2);
    await page.getByRole("button", { name: /^Round 1 ·/ }).click();
    await timeline(page).evaluate((node) => {
      node.scrollTop = node.scrollHeight;
      node.dispatchEvent(new Event("scroll"));
    });
    expect(await distance()).toBeLessThanOrEqual(2);
    await box(page).focus();
    await page.evaluate(() => {
      window.conversationTest.append();
    });
    expect(await distance()).toBeLessThanOrEqual(2);
    await check(box(page)).toBeFocused();
    await list.evaluate((node) => {
      node.scrollTop = 50;
      node.dispatchEvent(new Event("scroll"));
    });
    await page.evaluate(() => {
      window.conversationTest.append();
    });
    expect(await list.evaluate((node) => node.scrollTop)).toBe(50);
    await check(box(page)).toBeFocused();
    expect(await distance()).toBeGreaterThan(20);
  } finally {
    await page.close();
  }
});

it.each(["disclosure", "resize"] as const)(
  "does not pin after a %s moves the bottom without scrolling",
  async (change) => {
    const page = await pageFor();
    try {
      const list = timeline(page);
      expect(
        await list.evaluate((node) => node.scrollHeight - node.scrollTop - node.clientHeight),
      ).toBeLessThanOrEqual(2);
      await page.getByRole("button", { name: /^Round 1 ·/ }).click();
      await timeline(page).evaluate((node) => {
        node.scrollTop = node.scrollHeight;
        node.dispatchEvent(new Event("scroll"));
      });
      const before = await list.evaluate((node) => node.scrollTop);
      expect(
        await list.evaluate((node) => node.scrollHeight - node.scrollTop - node.clientHeight),
      ).toBeLessThanOrEqual(2);
      if (change === "disclosure") {
        await list.locator("details").evaluate((node) => {
          node.setAttribute("open", "");
        });
      } else {
        await page.setViewportSize({ width: 880, height: 842 });
      }
      // Let the browser lay out the change and deliver resize/toggle notifications.
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
      expect(
        await list.evaluate((node) => node.scrollHeight - node.scrollTop - node.clientHeight),
      ).toBeGreaterThan(2);
      expect(await list.evaluate((node) => node.scrollTop)).toBe(before);
      await page.evaluate(() => {
        window.conversationTest.append();
      });
      expect(await list.evaluate((node) => node.scrollTop)).toBe(before);
    } finally {
      await page.close();
    }
  },
);

it.each([
  ["America/Argentina/Buenos_Aires", "13:58"],
  ["UTC", "16:58"],
])("uses local 24-hour event time in %s", async (zone, time) => {
  const page = await pageFor("working", 880, "dark", zone);
  try {
    await check(page.locator("time").first()).toHaveText(time);
  } finally {
    await page.close();
  }
});

it.each([608, 304])(
  "keeps long content inside a %i px panel with usable targets",
  async (width) => {
    const page = await pageFor("revising", width);
    try {
      await page.getByRole("button", { name: /^Round 1 ·/ }).click();
      await page.getByRole("heading").evaluate((node) => {
        node.textContent = node.textContent.repeat(4);
      });
      await page.getByText(/^Cross-review ·/).evaluate((node) => {
        node.textContent = `Cross-review · ${"MuseSparkOpen".repeat(30)} · 1 note, nothing blocking`;
      });
      const list = timeline(page);
      expect(await list.evaluate((node) => node.scrollWidth)).toBe(
        await list.evaluate((node) => node.clientWidth),
      );
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
      await check(box(page)).toBeInViewport();
      for (const target of [
        page.getByRole("button", { name: "View in diff" }).first(),
        page.getByText("Test output", { exact: true }),
      ]) {
        expect(
          await target.evaluate((node) => node.getBoundingClientRect().height),
        ).toBeGreaterThanOrEqual(24);
      }
    } finally {
      await page.close();
    }
  },
);

it.each([
  [880, "dark", false],
  [608, "dark", false],
  [880, "day", false],
  [608, "day", false],
  [880, "dark", true],
] as const)("captures %i %s replay=%s", async (width, theme, replay) => {
  const page = await pageFor(replay ? "replay" : "revising", width, theme);
  try {
    await timeline(page).evaluate((node) => {
      node.scrollTop = 0;
    });
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width);
    const shots = fileURLToPath(new URL("../../../../.shots/conversation/", import.meta.url));
    await mkdir(shots, { recursive: true });
    await page.screenshot({
      path: `${shots}${String(width)}-${theme}${replay ? "-replay" : ""}.png`,
    });
  } finally {
    await page.close();
  }
});
