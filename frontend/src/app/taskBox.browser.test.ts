import { fileURLToPath } from "node:url";

import { chromium, expect as browserExpect } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

import type { StartAnswer } from "./taskBox.browser.fixture";

let server: ViteDevServer | undefined;
let browser: Browser | undefined;
let url: string;
let html: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/app-vite-cache", import.meta.url)),
    // The fixture reads the recorded run from the repository's fixtures/.
    server: {
      host: "127.0.0.1",
      port: 0,
      strictPort: false,
      fs: { allow: [fileURLToPath(new URL("../../../", import.meta.url))] },
    },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import { taskBoxTest } from "/src/app/taskBox.browser.fixture.tsx"; window.taskBoxTest = taskBoxTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await chromium.launch();
}, 60_000);
afterAll(async () => {
  await browser?.close();
  await server?.close();
});

async function withPage(test: (page: Page) => Promise<void>) {
  if (!browser) throw new Error("Browser was not started");
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  page.setDefaultTimeout(15_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && !message.location().url.endsWith("/@vite/client"))
      errors.push(message.text());
  });
  try {
    // Avoid starting the application's transport: only the frame is mounted.
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    await page.waitForFunction(() => "taskBoxTest" in window);
    await page.evaluate(() => {
      window.taskBoxTest.mount();
    });
    await page.locator("textarea").waitFor();
    await test(page);
    expect(errors).toEqual([]);
  } finally {
    await page.close();
  }
}

const box = (page: Page) => page.getByLabel("Task for the new agent");
const start = (page: Page) => page.getByRole("button", { name: "Start agent" });
const calls = (page: Page) => page.evaluate(() => window.taskBoxTest.calls());
const answer = (page: Page, result: StartAnswer) =>
  page.evaluate((value) => {
    window.taskBoxTest.answer(value);
  }, result);
const apply = (page: Page, seq: number) =>
  page.evaluate((value) => {
    window.taskBoxTest.apply(value);
  }, seq);
/** Waits until the fixture has received this many start requests. */
const sent = (page: Page, count: number) =>
  page.waitForFunction((expected) => window.taskBoxTest.calls().length === expected, count);
const focused = (page: Page) =>
  page.evaluate(() => document.activeElement?.tagName.toLowerCase() ?? "none");

it(
  "shows no choice for one slot, then preselects B when A is braked; a manual A sends its slot and shows spend_brake",
  { timeout: 40_000 },
  async () => {
    await withPage(async (page) => {
      await browserExpect(page.getByRole("group", { name: "Agent slot" })).toHaveCount(0);
      await box(page).fill("Keep the task");
      await box(page).focus();
      await page.evaluate(() => {
        window.taskBoxTest.twoSlots(true);
      });
      const a = page.getByRole("button", { name: "A Muse Spark", exact: true });
      const b = page.getByRole("button", { name: "B Muse Spark Open", exact: true });
      await browserExpect(a).toHaveAttribute("aria-pressed", "false");
      await browserExpect(b).toHaveAttribute("aria-pressed", "true");
      await browserExpect(a).toHaveAttribute("aria-disabled", "false");
      await browserExpect(box(page)).toBeFocused();
      await browserExpect(page.getByRole("group", { name: "Spend", exact: true })).toContainText(
        "US$136.00",
      );
      await a.click();
      await browserExpect(a).toHaveAttribute("aria-pressed", "true");
      await start(page).click();
      await sent(page, 1);
      expect(await calls(page)).toEqual([{ task: "Keep the task", slot: "A" }]);
      // A pending selection cannot be changed, including a forced click on an aria-disabled control.
      await b.click({ force: true });
      await browserExpect(a).toHaveAttribute("aria-pressed", "true");
      await answer(page, {
        status: 403,
        code: "spend_brake",
        message: "Meta spending is above 90%.",
      });
      await browserExpect(page.getByRole("alert")).toHaveText("Meta spending is above 90%.");
      await browserExpect(box(page)).toHaveValue("Keep the task");
      await b.click();
      await browserExpect(page.getByRole("alert")).toHaveCount(0);
      await start(page).click();
      await sent(page, 2);
      expect((await calls(page))[1]).toEqual({ task: "Keep the task", slot: "B" });
      await answer(page, "created");
    });
  },
);

it(
  "retains an override through updates and folding, disables a held slot with its holder, and never moves focus",
  { timeout: 40_000 },
  async () => {
    await withPage(async (page) => {
      await page.evaluate(() => {
        window.taskBoxTest.twoSlots();
      });
      const a = page.getByRole("button", { name: /^A Muse Spark/ });
      const b = page.getByRole("button", { name: "B Muse Spark Open", exact: true });
      await browserExpect(a).toHaveAttribute("aria-pressed", "true");
      await b.click();
      await box(page).fill("Second task");
      await box(page).focus();
      await page.evaluate(() => {
        window.taskBoxTest.holdA();
      });
      await browserExpect(a).toHaveAttribute("aria-disabled", "true");
      await browserExpect(a).toContainText("Agent A is working.");
      await browserExpect(b).toHaveAttribute("aria-pressed", "true");
      await browserExpect(box(page)).toBeFocused();
      await a.click({ force: true });
      await browserExpect(b).toHaveAttribute("aria-pressed", "true");
      await apply(page, 14);
      await page.evaluate(() => {
        window.taskBoxTest.fold();
      });
      await page.getByLabel("Live diff panel").waitFor();
      await page.evaluate(() => {
        window.taskBoxTest.unfold();
      });
      await browserExpect(b).toHaveAttribute("aria-pressed", "true");
      await browserExpect(box(page)).toHaveValue("Second task");
      await box(page).focus();
      await page.keyboard.press("Control+Enter");
      await sent(page, 1);
      expect(await calls(page)).toEqual([{ task: "Second task", slot: "B" }]);
      await answer(page, "created");
    });
  },
);

it(
  "starts with Ctrl+Enter, sends the trimmed task, and keeps focus and the box through the answer and the new card",
  { timeout: 40_000 },
  async () => {
    await withPage(async (page) => {
      await box(page).click();
      await page.keyboard.type("  Cap the discount");
      // Enter alone is a newline, not a start.
      await page.keyboard.press("Enter");
      await page.keyboard.type("at 30%  ");
      expect(await box(page).inputValue()).toBe("  Cap the discount\nat 30%  ");
      expect(await calls(page)).toEqual([]);
      await page.evaluate(() => {
        window.boxNode = document.querySelector("textarea");
      });

      await page.keyboard.press("Control+Enter");
      await sent(page, 1);
      expect(await calls(page)).toEqual([{ task: "Cap the discount\nat 30%" }]);
      // Pending: read-only, the button refuses, and a second Ctrl+Enter sends nothing.
      await browserExpect(box(page)).toHaveJSProperty("readOnly", true);
      await browserExpect(start(page)).toHaveAttribute("aria-disabled", "true");
      await browserExpect(page.getByText("Starting the agent.")).toBeVisible();
      await page.keyboard.press("Control+Enter");
      expect(await calls(page)).toHaveLength(1);

      await answer(page, "created");
      await browserExpect(box(page)).toHaveValue("");
      await browserExpect(box(page)).toHaveJSProperty("readOnly", false);
      expect(await focused(page)).toBe("textarea");

      // The card arrives from events, above the box; the same element keeps the person's focus.
      await apply(page, 4);
      await page.getByRole("article", { name: "Agent A" }).waitFor();
      const same = await page.evaluate(() => ({
        node: document.querySelector("textarea") === window.boxNode,
        focus: document.activeElement === window.boxNode,
        cardFirst:
          ((document
            .querySelector("article")
            ?.compareDocumentPosition(window.boxNode ?? document.body) ?? 0) &
            Node.DOCUMENT_POSITION_FOLLOWING) !==
          0,
      }));
      expect(same).toEqual({ node: true, focus: true, cardFirst: true });
      // No slot is free now: the person can still write, the button says why and refuses.
      await page.keyboard.type("next task");
      await browserExpect(box(page)).toHaveValue("next task");
      await browserExpect(start(page)).toHaveAttribute("aria-disabled", "true");
      await browserExpect(page.getByText("Agent A is working.")).toBeVisible();
      await page.keyboard.press("Control+Enter");
      // Playwright waits for an enabled button; a forced click is the person's click on the refusal.
      await start(page).click({ force: true });
      expect(await calls(page)).toHaveLength(1);
    });
  },
);

it(
  "keeps focus on the Start agent button when the start succeeds",
  { timeout: 40_000 },
  async () => {
    await withPage(async (page) => {
      await box(page).fill("Cap the discount");
      await start(page).click();
      await sent(page, 1);
      expect(await focused(page)).toBe("button");
      await answer(page, "created");
      await browserExpect(box(page)).toHaveValue("");
      // The button turned aria-disabled for the blank box, not disabled: it still has the focus.
      await browserExpect(start(page)).toHaveAttribute("aria-disabled", "true");
      expect(await focused(page)).toBe("button");
      await browserExpect(start(page)).toBeFocused();
    });
  },
);

it(
  "shows each refusal and a lost connection under the box, keeps the task and re-enables",
  { timeout: 40_000 },
  async () => {
    const refusals: [StartAnswer, string][] = [
      [
        { status: 403, code: "spend_brake", message: "Spending is above 90%." },
        "Spending is above 90%.",
      ],
      [{ status: 409, code: "slot_busy", message: "Slot A is busy." }, "Slot A is busy."],
      [
        { status: 422, code: "invalid_request", message: "The task is empty." },
        "The task is empty.",
      ],
      [
        { status: 500, code: "internal", message: "Boom." },
        "The server failed to start the agent: Boom.",
      ],
      ["network", "The request did not reach the server. Check the connection and try again."],
    ];
    await withPage(async (page) => {
      await box(page).click();
      await page.keyboard.type("Cap the discount");
      let count = 0;
      for (const [result, line] of refusals) {
        await page.keyboard.press("Control+Enter");
        await sent(page, ++count);
        await answer(page, result);
        await browserExpect(page.getByRole("alert")).toHaveText(line);
        await browserExpect(start(page)).toHaveAttribute("aria-disabled", "false");
        await browserExpect(box(page)).toHaveValue("Cap the discount");
        await browserExpect(box(page)).toHaveJSProperty("readOnly", false);
        expect(await focused(page)).toBe("textarea");
      }
      // Editing the task drops the stale line.
      await page.keyboard.type("!");
      await browserExpect(page.getByRole("alert")).toHaveCount(0);
    });
  },
);

it(
  "keeps the draft when the agents panel folds into the rail and opens again",
  { timeout: 40_000 },
  async () => {
    await withPage(async (page) => {
      await box(page).fill("Cap the discount");
      await apply(page, 14);
      await page.evaluate(() => {
        window.taskBoxTest.fold();
      });
      await page.getByLabel("Live diff panel").waitFor();
      await browserExpect(box(page)).toHaveCount(0);
      await page.evaluate(() => {
        window.taskBoxTest.unfold();
      });
      await browserExpect(box(page)).toHaveValue("Cap the discount");
    });
  },
);

it(
  "keeps the focus where it was when the card's step list goes at the proposal, and never takes it",
  { timeout: 40_000 },
  async () => {
    await withPage(async (page) => {
      await apply(page, 18);
      const steps = page.getByRole("list", { name: /steps, newest first/ });
      const card = page.getByRole("article", { name: "Agent A" });
      // In the step list: it unmounts at the proposal, and the card, where the focus was, keeps it.
      await steps.focus();
      await browserExpect(steps).toBeFocused();
      await apply(page, 20);
      await browserExpect(steps).toHaveCount(0);
      await browserExpect(card).toBeFocused();
    });
    await withPage(async (page) => {
      await apply(page, 18);
      // Typing in the task box when the list goes: the box keeps the focus.
      await box(page).focus();
      await apply(page, 20);
      await browserExpect(page.getByRole("list", { name: /steps, newest first/ })).toHaveCount(0);
      await browserExpect(box(page)).toBeFocused();
      expect(await focused(page)).toBe("textarea");
    });
  },
);
