import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import type {} from "./startupSplit.browser.fixture";
import { freshAttempt } from "./challenge/attempt";

let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    mode: "replay",
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html data-theme="dark"><body><div id="root" style="height:100vh"></div><script type="module">import "/src/theme/index.css"; import "/src/app/startupSplit.browser.fixture.tsx";</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

const tabs = [
  ["review", "/src/review/ReviewPanel.tsx", "Review panel", ".review-body"],
  ["inbox", "/src/review/DecisionInbox.tsx", "Decisions inbox", ".inbox-entries"],
  [
    "conversation",
    "/src/agents/conversation/ConversationPanel.tsx",
    "Agent A conversation",
    '[aria-label="Conversation timeline"]',
  ],
  ["file-view", "/src/editor/implementation.ts", "File view", ".monaco-editor"],
  [
    "practice",
    "/src/app/challenge/PracticePanel.tsx",
    "Two-change practice",
    "button:has-text('Start practice')",
  ],
] as const;

it.each([1920, 1280, 390])(
  "opens practice at %ipx without fetching decision chunks",
  async (width) => {
    const page = await browser.newPage({ viewport: { width, height: 1080 } });
    const requests: string[] = [];
    await page.route("**/{PhonePractice,ReviewBody,ReviewDecisionBar}.tsx*", (route) => {
      requests.push(new URL(route.request().url()).pathname);
      return route.abort("failed");
    });
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    try {
      await page.goto(url);
      await page
        .getByRole("button", {
          name: width < 1280 ? "Try two-change practice" : "Two-change practice",
          exact: true,
        })
        .click();
      const panel = page.getByLabel("Two-change practice", { exact: true });
      if (width < 1280) {
        await check(panel.getByRole("alert")).toContainText("This panel could not load.");
        expect(requests).toEqual(["/src/app/challenge/PhonePractice.tsx"]);
      } else {
        await check(
          panel.getByRole("button", { name: "Start practice", exact: true }),
        ).toBeVisible();
        expect(requests).toEqual([]);
        await panel.getByRole("button", { name: "Start practice", exact: true }).click();
        await check(panel.getByRole("heading", { name: "Decision 1 of 2" })).toBeVisible();
        expect(requests).toEqual([]);
      }
    } finally {
      await page.close();
    }
  },
);

it("opens the phone introduction while review evidence is unavailable", async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  const requests: string[] = [];
  await page.route("**/{ReviewBody,ReviewDecisionBar}.tsx*", (route) => {
    requests.push(new URL(route.request().url()).pathname);
    return route.abort("failed");
  });
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  try {
    await page.goto(url);
    await page.getByRole("button", { name: "Try two-change practice", exact: true }).click();
    await check(page.getByRole("button", { name: "Start practice", exact: true })).toBeVisible();
    expect(requests).toEqual([]);
  } finally {
    await page.close();
  }
});

it("loads evidence before painting the first phone decision without a layout shift", async () => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/ReviewBody.tsx*", async (route) => {
    await gate;
    await route.continue();
  });
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  try {
    await page.goto(url);
    await page.getByRole("button", { name: "Try two-change practice", exact: true }).click();
    const start = page.getByRole("button", { name: "Start practice", exact: true });
    await check(start).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    const introduction = await page.locator(".pp-main").boundingBox();
    const requested = page.waitForRequest("**/ReviewBody.tsx*");
    await start.click();
    await requested;
    await check(start).toBeVisible();
    await check(page.locator(".pp-progress")).toHaveCount(0);
    expect(await page.locator(".pp-main").boundingBox()).toEqual(introduction);
    const firstDecision = page.evaluate(
      () =>
        new Promise((resolve) => {
          const observer = new MutationObserver(() => {
            if (!document.querySelector(".pp-progress")) return;
            observer.disconnect();
            resolve(
              Array.from(
                document.querySelectorAll(".pp-main, .review-body, .pp-decision-rows"),
                (element) => {
                  const { x, y, width, height } = element.getBoundingClientRect();
                  return { x, y, width, height };
                },
              ),
            );
          });
          observer.observe(document.body, { childList: true, subtree: true });
        }),
    );
    await page.evaluate(() => true);
    release();
    const initial = await firstDecision;
    await check(page.getByRole("region", { name: "Review lead" })).toBeVisible();
    const loaded = await page.evaluate(() =>
      Array.from(
        document.querySelectorAll(".pp-main, .review-body, .pp-decision-rows"),
        (element) => {
          const { x, y, width, height } = element.getBoundingClientRect();
          return { x, y, width, height };
        },
      ),
    );
    expect(loaded).toEqual(initial);
  } finally {
    release();
    await page.close();
  }
});

it.each(["failed", "left"])("handles a pending phone decision when %s", async (outcome) => {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/ReviewBody.tsx*", async (route) => {
    await gate;
    if (outcome === "failed") await route.abort("failed");
    else await route.continue();
  });
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  try {
    await page.goto(url);
    const open = page.getByRole("button", { name: "Try two-change practice", exact: true });
    await open.click();
    const requested = page.waitForRequest("**/ReviewBody.tsx*");
    await page.getByRole("button", { name: "Start practice", exact: true }).click();
    await requested;
    if (outcome === "left")
      await page.getByRole("button", { name: "Leave practice", exact: true }).click();
    release();
    if (outcome === "failed") {
      await check(page.getByRole("alert")).toHaveText(
        "This panel could not load. Reload the page to try again.",
      );
    } else {
      await page.evaluate<undefined>("import('/src/review/ReviewBody.tsx').then(() => undefined)");
      await open.click();
      await check(page.getByRole("button", { name: "Start practice", exact: true })).toBeVisible();
      await check(page.locator(".pp-progress")).toHaveCount(0);
    }
  } finally {
    release();
    await page.close();
  }
});

it.each([390, 1280, 1920])(
  "keeps practice frame geometry through every stage at %ipx",
  async (width) => {
    const page = await browser.newPage({ viewport: { width, height: 1080 } });
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    try {
      await page.goto(url);
      await page.waitForFunction(() => !!window.startupPracticeFrame);
      await page.evaluate(() => document.fonts.ready);
      for (const attempt of [
        freshAttempt(),
        { ...freshAttempt(), started: true },
        { ...freshAttempt(), started: true, index: 1 },
        { ...freshAttempt(), started: true, revealed: true },
        { ...freshAttempt(), started: true, revealed: true, finished: true },
      ]) {
        await page.evaluate((attempt) => window.startupPracticeFrame(attempt, false), attempt);
        const panel = page.getByLabel("Two-change practice", { exact: true });
        await check(panel).toBeVisible();
        const heading = panel.locator(width < 1280 ? "header" : "h1").first();
        const before = await panel.boundingBox();
        const header = await heading.boundingBox();
        await check(panel).not.toHaveAttribute("style");
        await page.evaluate((attempt) => window.startupPracticeFrame(attempt, true), attempt);
        await check(
          panel.locator(width < 1280 ? ".pp-main" : "#practice-only, button").first(),
        ).toBeVisible();
        expect(await panel.boundingBox()).toEqual(before);
        expect(await heading.boundingBox()).toEqual(header);
      }
    } finally {
      await page.close();
    }
  },
);

it.each(
  tabs.flatMap(([tab, module, label, content]) =>
    (tab === "file-view" ? [false] : [false, true]).map((failed) => ({
      tab,
      module,
      label,
      content,
      failed,
    })),
  ),
)(
  "keeps the $tab frame on its first cold open (failed chunk: $failed)",
  async ({ tab, module, label, content, failed }) => {
    const page = await browser.newPage({
      viewport: { width: 1920, height: 1080 },
      reducedMotion: "reduce",
    });
    let release = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route(`**${module}*`, async (route) => {
      await gate;
      if (failed) await route.abort("failed");
      else await route.continue();
    });
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    try {
      await page.goto(url);
      await check(page.getByLabel("Repository graph", { exact: true })).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      if (tab === "review")
        await page.getByRole("button", { name: "Review change", exact: true }).first().click();
      if (tab === "inbox") await page.getByRole("button", { name: /^Decisions:/ }).click();
      if (tab === "conversation")
        await page
          .getByRole("article", { name: "Agent A", exact: true })
          .getByRole("button", { name: "Conversation", exact: true })
          .click();
      if (tab === "practice")
        await page.getByRole("button", { name: "Two-change practice", exact: true }).click();
      if (tab === "file-view") {
        await page.getByRole("button", { name: "Files", exact: true }).click();
        await page.getByRole("treeitem", { name: "shop", exact: true }).click();
        await page.getByRole("treeitem", { name: "pricing.py", exact: true }).click();
      }
      const panel = page.getByLabel(label, { exact: true });
      await check(panel).toBeVisible();
      await check(panel.locator(content)).toHaveCount(0);
      const before = await panel.boundingBox();
      const heading = panel.locator(tab === "practice" ? "h1" : "header").first();
      const header = await heading.boundingBox();
      const headerMarkup = await heading.innerHTML();
      if (tab === "file-view") await check(panel).not.toContainText("Loading editor…");
      if (tab === "review" || tab === "file-view") {
        const close = panel.getByRole("button", {
          name: tab === "review" ? "Close review" : "Close file",
          includeHidden: true,
        });
        await close.evaluate((element: HTMLButtonElement) => {
          element.focus();
        });
        await check(close).not.toBeFocused();
      }
      if (tab === "inbox") {
        await check(panel.getByRole("button", { name: /^Decisions/ })).toBeDisabled();
        await check(panel.getByRole("button", { name: /^Standing grants/ })).toBeDisabled();
      }
      if (!failed && process.env.STARTUP_SPLIT_SHOTS === "1") {
        const directory = fileURLToPath(new URL("../../.shots/startup-split/", import.meta.url));
        await mkdir(directory, { recursive: true });
        await page.screenshot({ path: `${directory}/${tab}-first-open.png` });
      }
      release();
      if (failed) {
        await check(panel.getByRole("alert")).toHaveText(
          "This panel could not load. Reload the page to try again.",
        );
        await check(page.getByLabel("Repository graph", { exact: true })).toBeVisible();
        expect(await panel.boundingBox()).toEqual(before);
        expect(await heading.boundingBox()).toEqual(header);
        await page.getByRole("button", { name: "Watch", exact: true }).click();
        await check(panel).toHaveCount(0);
        await check(page.getByLabel("Repository graph", { exact: true })).toBeVisible();
        expect(errors).toEqual([]);
        return;
      }
      await check(panel.locator(content).first()).toBeVisible();
      expect(await panel.boundingBox()).toEqual(before);
      expect(await heading.boundingBox()).toEqual(header);
      if (tab === "conversation") expect(await heading.innerHTML()).toEqual(headerMarkup);
      if (tab === "review" || tab === "file-view")
        await check(
          panel.getByRole("button", { name: tab === "review" ? "Close review" : "Close file" }),
        ).toBeEnabled();
      if (tab === "inbox") {
        await panel.getByRole("button", { name: /^Standing grants/ }).click();
        await check(panel.getByRole("button", { name: /^Standing grants/ })).toHaveAttribute(
          "aria-pressed",
          "true",
        );
      }
      expect(errors).toEqual([]);
    } finally {
      release();
      await page.close();
    }
  },
  30_000,
);
