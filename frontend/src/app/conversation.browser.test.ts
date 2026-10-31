import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import type { GraphProbeHost } from "../graph";
import type {} from "./conversation.browser.fixture";

let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/conversation-tab-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html data-theme="dark"><body><script type="module">import "/src/theme/index.css"; import { conversationTabTest } from "/src/app/conversation.browser.fixture.tsx"; window.conversationTabTest = conversationTabTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

async function open(commands = true, width = 1920, height = 1080, theme = "dark") {
  const page = await browser.newPage({ viewport: { width, height }, reducedMotion: "reduce" });
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.goto(url);
  await page.waitForFunction(() => !!window.conversationTabTest);
  await page.evaluate(
    ({ commands, theme }) => {
      document.documentElement.dataset.theme = theme;
      window.conversationTabTest.mount(commands);
    },
    { commands, theme },
  );
  await page.evaluate(() => document.fonts.ready);
  await page.waitForFunction(
    () =>
      !!document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]')?.oversightGraph,
  );
  return page;
}
const card = (page: Page) => page.getByRole("article", { name: "Agent A", exact: true });
const panel = (page: Page) =>
  page.getByRole("region", { name: "Agent A conversation", exact: true });
const tabA = (page: Page) => page.getByRole("tab", { name: /Agent A · conversation$/ });
async function conversation(page: Page) {
  await card(page).getByRole("button", { name: "Conversation", exact: true }).click();
  await check(panel(page)).toBeVisible();
}

it("opens colored agent tabs beside the persistent graph and folds cards into the rail", async () => {
  const page = await open();
  try {
    const graph = await page.locator('[aria-label="Repository graph"]').elementHandle();
    await conversation(page);
    await check(tabA(page)).toHaveAttribute("aria-selected", "true");
    await check(page.locator('.focus-tab[data-slot="A"]')).toBeVisible();
    await check(page.locator(".agents-expanded")).toHaveCount(0);
    await check(page.getByRole("complementary", { name: "Agents", exact: true })).toBeVisible();
    expect(
      await panel(page)
        .getByRole("textbox")
        .evaluate((el) => el === document.activeElement),
    ).toBe(false);
    const bounds = await panel(page).boundingBox();
    const graphBounds = await page.getByLabel("Graph stage", { exact: true }).boundingBox();
    expect(bounds && graphBounds && graphBounds.x >= bounds.x + bounds.width).toBe(true);
    await page.evaluate(() => {
      window.conversationTabTest.open("agent-2");
    });
    await check(page.getByRole("tab", { name: /Agent B · conversation$/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await check(page.getByRole("tab")).toHaveCount(2);
    await tabA(page).click();
    await check(panel(page)).toBeVisible();
    await page.evaluate(() => {
      window.conversationTabTest.open("agent-1");
    });
    await check(page.getByRole("tab")).toHaveCount(2);
    expect(
      await graph.evaluate(
        (el) => el === document.querySelector('[aria-label="Repository graph"]'),
      ),
    ).toBe(true);
  } finally {
    await page.close();
  }
});

it("keeps another tab and keyboard focus active as later events arrive", async () => {
  const page = await open();
  try {
    await conversation(page);
    await page.getByRole("button", { name: /Open decisions inbox$/ }).click();
    const active = page.getByRole("tab", { name: "Decisions", exact: true });
    await active.focus();
    await page.evaluate(() => {
      window.conversationTabTest.advance(63);
    });
    await check(active).toHaveAttribute("aria-selected", "true");
    await check(active).toBeFocused();
    await check(tabA(page)).toHaveAttribute("aria-selected", "false");
    await check(page.getByRole("tab")).toHaveCount(2);
  } finally {
    await page.close();
  }
});

it("sends, pauses, resumes and stops the tab's agent through the supplied commands", async () => {
  const page = await open();
  try {
    await conversation(page);
    const text = "Read pricing.py";
    await panel(page).getByRole("textbox").fill(text);
    await panel(page).getByRole("button", { name: "Send", exact: true }).click();
    await panel(page).getByRole("button", { name: "Pause", exact: true }).click();
    await page.evaluate(() => {
      const state = window.conversationTabTest.state();
      const agent = state.agents["agent-1"];
      if (!agent) throw new Error("Missing agent");
      window.conversationTabTest.apply({
        seq: state.lastSeq + 1,
        ts: state.clock ?? "",
        type: "agent.status",
        payload: { ...agent, state: "paused" },
      });
    });
    await panel(page).getByRole("button", { name: "Resume", exact: true }).click();
    await panel(page).getByRole("button", { name: "Stop and discard", exact: true }).click();
    await panel(page)
      .getByRole("button", { name: "Confirm stop and discard", exact: true })
      .click();
    expect(await page.evaluate(() => window.conversationTabTest.calls())).toEqual([
      { command: "message", id: "agent-1", text },
      { command: "pause", id: "agent-1" },
      { command: "resume", id: "agent-1" },
      { command: "stop", id: "agent-1" },
    ]);
  } finally {
    await page.close();
  }
});

it.each([true, false])(
  "opens View in diff with commands=%s and labels replay controls",
  async (commands) => {
    const page = await open(commands);
    try {
      await conversation(page);
      if (!commands) {
        await check(panel(page).getByRole("textbox")).toHaveCount(0);
        await check(
          panel(page).getByText("Recorded run: messages, pause and stop are off.", { exact: true }),
        ).toBeVisible();
      }
      await panel(page).getByRole("button", { name: "View in diff", exact: true }).first().click();
      await check(page.getByRole("tab", { name: /Live diff · pricing.py/ })).toHaveAttribute(
        "aria-selected",
        "true",
      );
      await check(page.getByLabel("Live diff panel", { exact: true })).toBeVisible();
    } finally {
      await page.close();
    }
  },
);

it("interleaves only A's accepted messages with its steps in event order", async () => {
  const page = await open();
  try {
    await conversation(page);
    const input = panel(page).getByRole("textbox");
    await input.focus();
    await page.evaluate(() => {
      const state = window.conversationTabTest.state();
      const ts = state.clock ?? "";
      const step = state.steps["agent-1"]?.at(-1);
      if (!step) throw new Error("Missing step");
      window.conversationTabTest.apply({
        seq: 36,
        ts,
        type: "agent.message",
        payload: { agent_id: "agent-1", text: "Read pricing.py" },
      });
      window.conversationTabTest.apply({
        seq: 37,
        ts,
        type: "agent.message",
        payload: { agent_id: "agent-2", text: "Read inventory.py" },
      });
      window.conversationTabTest.apply({
        seq: 38,
        ts,
        type: "agent.step",
        payload: {
          ...step,
          step: step.step + 1,
          tool: "read_file",
          summary: "Read test_pricing.py",
        },
      });
    });
    const rows = panel(page).getByRole("listitem");
    const texts = await rows.allTextContents();
    const message = texts.findIndex((text) => text.includes("From you"));
    expect(texts[message]).toContain("Read pricing.py");
    expect(texts[message - 1]).toContain("Edited compute_price");
    expect(texts[message + 1]).toContain("Read test_pricing.py");
    await check(panel(page).getByText("Read inventory.py", { exact: true })).toHaveCount(0);
    await check(input).toBeFocused();
    await check(tabA(page)).toHaveAttribute("aria-selected", "true");
  } finally {
    await page.close();
  }
});

it("keeps a conversation tab open with the unavailable line after a new run", async () => {
  const page = await open();
  try {
    await conversation(page);
    await page.evaluate(() => {
      const state = window.conversationTabTest.state();
      if (!state.run) throw new Error("Missing run");
      window.conversationTabTest.apply({
        seq: 1,
        ts: state.clock ?? "",
        type: "run.started",
        payload: { ...state.run, run_id: "new-run" },
      });
    });
    await check(
      page.getByText("This agent is not in the current run.", { exact: true }),
    ).toBeVisible();
    expect(await page.evaluate(() => window.conversationTabTest.view().activeTab)).toBe(
      "conversation:agent-1",
    );
  } finally {
    await page.close();
  }
});

it.each(["dark", "day"])("captures the conversation and card states in %s", async (theme) => {
  if (process.env.CONVERSATION_TAB_SHOTS !== "1") return;
  const shots = fileURLToPath(new URL("../../../.shots/conversation-tab/", import.meta.url));
  await mkdir(shots, { recursive: true });
  for (const width of [1920, 1280]) {
    const page = await open(true, width, width === 1920 ? 1080 : 800, theme);
    try {
      if (width === 1920) {
        await card(page).screenshot({ path: `${shots}card-working-${theme}.png` });
        await page.evaluate(() => {
          window.conversationTabTest.advance(63);
        });
        await card(page).screenshot({ path: `${shots}card-ready-${theme}.png` });
        await page.evaluate(() => {
          window.conversationTabTest.mount();
        });
      }
      await conversation(page);
      await page.screenshot({ path: `${shots}conversation-${String(width)}-${theme}.png` });
      if (width === 1920) {
        await page.evaluate(() => {
          window.conversationTabTest.mount(false);
        });
        await conversation(page);
        await page.screenshot({ path: `${shots}replay-${theme}.png` });
      }
    } finally {
      await page.close();
    }
  }
});
