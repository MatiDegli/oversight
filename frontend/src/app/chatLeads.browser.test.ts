import type { LeadStatus } from "@contracts";
import { compare } from "./chatLeadShots.support";
import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { beforeAll, afterAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import type {} from "./chatLeads.browser.fixture";

let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: await mkdtemp(join(tmpdir(), "chat-leads-vite-")),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html data-theme="dark"><body><div id="root" style="height:100vh"></div><script type="module">import "/src/theme/index.css"; import "/src/app/chatLeads.browser.fixture.tsx";</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});
async function open(
  name = "chat-start",
  width = 1920,
  theme = "dark",
  live = true,
  app = false,
  approved = false,
) {
  const page = await browser.newPage({
    viewport: { width, height: width === 1920 ? 1080 : width === 390 ? 844 : 800 },
    reducedMotion: "reduce",
  });
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.route("**/events?*", (route) =>
    route.fulfill({ contentType: "text/event-stream", body: ": handwritten fixture\n\n" }),
  );
  await page.goto(url);
  await page.waitForFunction(() => !!window.chatLeadsTest);
  await page.evaluate(
    ({ name, theme, live, app, approved }) => {
      document.documentElement.dataset.theme = theme;
      window.chatLeadsTest.mount(name, live, app, approved);
    },
    { name, theme, live, app, approved },
  );
  await page.evaluate(() => document.fonts.ready);
  return page;
}
const newTask = (page: Page) =>
  page.locator(".chat-start-actions").getByRole("button", { name: "New task", exact: true });
const newLead = (page: Page) =>
  page.locator(".chat-start-actions").getByRole("button", { name: "New lead", exact: true });
const leadRow = (page: Page, letter: string) =>
  page.locator(".chat-lead-row").filter({ has: page.getByText(`Lead ${letter}`, { exact: true }) });
async function startForm(page: Page) {
  await newLead(page).click();
  await check(page.getByRole("textbox", { name: "First task" })).toBeFocused();
}

async function themeColor(page: Page, token: string) {
  return page.evaluate((token) => {
    const probe = document.createElement("span");
    probe.style.color = `var(--${token})`;
    document.body.append(probe);
    const color = getComputedStyle(probe).color;
    probe.remove();
    return color;
  }, token);
}

it("keeps the lead region and header through cold loading and chunk failure", async () => {
  const page = await open("lead-new", 1280, "dark", true, false, true);
  let failChunk!: () => void;
  const heldChunk = new Promise<void>((resolve) => {
    failChunk = resolve;
  });
  await page.route("**/src/agents/conversation/ConversationPanel.tsx*", async (route) => {
    await heldChunk;
    await route.abort("failed");
  });
  try {
    await leadRow(page, "A").click();
    const frame = page.getByRole("region", { name: "Lead A conversation", exact: true });
    await check(frame).toBeVisible();
    await check(frame.getByRole("heading")).toHaveText(
      "Add input checks to the shop: reject negative prices and quantities, and zero or negative reserve quantities.",
    );
    await check(frame.getByRole("status")).toHaveText("Loading lead conversation…");
    failChunk();
    await check(frame.getByRole("alert")).toHaveText(
      "This panel could not load. Reload the page to try again.",
    );
    await check(frame.getByRole("heading")).toBeVisible();
    await check(frame.getByRole("status")).toHaveCount(0);
    await check(page.locator('p p, [role="status"] [role="alert"]')).toHaveCount(0);
  } finally {
    failChunk();
    await page.close();
  }
});

it.each([1920, 1280, 390].flatMap((width) => ["dark", "day"].map((theme) => ({ width, theme }))))(
  "styles lead-new actions at $width in $theme",
  async ({ width, theme }) => {
    const page = await open("lead-new", width, theme, true, false, true);
    try {
      await startForm(page);
      await page.getByRole("textbox", { name: "First task" }).fill("Coordinate validation");
      const start = page.getByRole("button", { name: "Start lead", exact: true });
      const height = width === 390 ? "44px" : "40px";
      await check(start).toHaveCSS("height", height);
      await check(page.getByRole("button", { name: "Cancel", exact: true })).toHaveCSS(
        "height",
        height,
      );
      await check(start).toHaveCSS("background-color", await themeColor(page, "text"));
      await check(start).toHaveCSS("color", await themeColor(page, "page"));
      await start.hover();
      await check(start).toHaveCSS("background-color", await themeColor(page, "text-hover"));
    } finally {
      await page.close();
    }
  },
);

async function publishLead(page: Page, id: string, patch: Partial<LeadStatus>) {
  await page.evaluate(
    ({ id, patch }) => {
      const event = window.chatLeadsTest
        .events()
        .findLast((e) => e.type === "lead.status" && e.payload.agent_id === id);
      if (event?.type !== "lead.status") throw new Error("Missing lead fixture");
      window.chatLeadsTest.deliver([{ ...event, payload: { ...event.payload, ...patch } }]);
    },
    { id, patch },
  );
}

it("keeps direct work first and focuses only after New task or New lead", async () => {
  const page = await open("leads-two");
  try {
    await check(page.locator(".chat-agent-name")).toHaveText([
      "Lead A",
      "A·1",
      "A·2",
      "Lead B",
      "Agent C",
    ]);
    await check(page.locator(".chat-agents > header")).toContainText("2 running");
    await check(leadRow(page, "A")).toContainText("2 agents · 1 working, 1 needs you");
    await leadRow(page, "A").click();
    await leadRow(page, "B").focus();
    await page.evaluate(() => {
      const member = window.chatLeadsTest
        .prefix("leads-two")
        .find((item) => item.type === "agent.status" && item.payload.agent_id === "agent-3");
      if (member?.type !== "agent.status") throw new Error("Missing member");
      window.chatLeadsTest.deliver([
        { ...member, payload: { ...member.payload, state: "ready_for_review" } },
      ]);
    });
    await check(leadRow(page, "B")).toBeFocused();
    await check(leadRow(page, "A")).toHaveAttribute("aria-pressed", "true");
    await check(leadRow(page, "A")).toContainText("2 agents · 2 need you");
    await newTask(page).click();
    const task = page.getByRole("textbox", { name: "Task for the new agent" });
    await check(task).toBeFocused();
    await check(
      page.getByRole("heading", { name: "What should change in demo-repo?", exact: true }),
    ).toBeVisible();
    await task.fill("Keep direct task");
    await startForm(page);
    await page.getByRole("textbox", { name: "First task" }).fill("Keep lead task");
    await check(page.getByRole("group", { name: "New lead draft" })).toContainText("Not started");
    await newTask(page).click();
    await check(task).toBeFocused();
    await check(task).toHaveValue("Keep direct task");
    await startForm(page);
    await check(page.getByRole("textbox", { name: "First task" })).toHaveValue("Keep lead task");
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await startForm(page);
    await check(page.getByRole("textbox", { name: "First task" })).toHaveValue("Keep lead task");
  } finally {
    await page.close();
  }
});
it("selects and messages the right lead in Chat and Oversight, with idle admission and independent focus", async () => {
  const page = await open("leads-two", 1280, "dark", true, true);
  const calls: { path: string; body: unknown }[] = [];
  await page.route("**/leads/*/message", async (route) => {
    calls.push({
      path: new URL(route.request().url()).pathname,
      body: route.request().postDataJSON(),
    });
    await route.fulfill({ status: 202 });
  });
  try {
    await leadRow(page, "A").click();
    const composer = page.getByRole("textbox", { name: "Message Lead A", exact: true });
    await check(composer).toBeEditable();
    await check(page.locator(".conversation-header h2")).toHaveText(
      "Make the checkout safer: validate prices and quantities, and cover the edge cases.",
    );
    await check(
      page.locator(".conversation-entries strong").filter({ hasText: /^Lead A$/ }),
    ).toBeVisible();
    await composer.fill("Review pricing next");
    await page.getByRole("button", { name: "Send message to lead" }).click();
    await check(composer).toHaveValue("");
    expect(calls).toEqual([
      { path: "/leads/agent-1/message", body: { text: "Review pricing next" } },
    ]);
    await leadRow(page, "A").focus();
    await leadRow(page, "A").press("ArrowDown");
    await check(page.locator(".chat-agent-row").nth(1)).toBeFocused();
    await page.keyboard.press("End");
    await check(page.locator(".chat-agent-row").last()).toBeFocused();
    await page.keyboard.press("Home");
    await check(leadRow(page, "A")).toBeFocused();
    await leadRow(page, "B").click();
    await check(page.getByRole("textbox", { name: "Message Lead B" })).toHaveAttribute(
      "readonly",
      "",
    );
    await check(leadRow(page, "B")).toHaveAttribute("aria-pressed", "true");
    await leadRow(page, "A").focus();
    await page.keyboard.press("Enter");
    await page.getByRole("button", { name: "Oversight", exact: true }).click();
    await check(page.getByRole("tab", { name: "Lead A · conversation" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await composer.fill("Same thread");
    await page.getByRole("button", { name: "Send message to lead" }).click();
    await check(composer).toHaveValue("");
    expect(calls.at(-1)).toEqual({ path: "/leads/agent-1/message", body: { text: "Same thread" } });
  } finally {
    await page.close();
  }
});
it("uses the backend default through App, retains refusals and never starts from Ctrl+Enter", async () => {
  const page = await open("chat-start", 1280, "day", true, true);
  const calls: unknown[] = [];
  let refuse = true;
  await page.route("**/leads", async (route) => {
    calls.push(route.request().postDataJSON());
    await route.fulfill(
      refuse
        ? {
            status: 409,
            json: { error: { code: "wrong_state", message: "No lead can start now." } },
          }
        : { status: 201, json: { agent_id: "new-lead" } },
    );
  });
  try {
    await page.locator(".chat-lead-option").getByRole("button", { name: "New lead" }).click();
    const task = page.getByRole("textbox", { name: "First task" });
    await check(task).toBeFocused();
    await check(page.getByRole("button", { name: "Start lead", exact: true })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    await check(page.getByRole("combobox", { name: "Model" })).toHaveCount(0);
    await check(page.locator(".new-lead-limits")).toHaveText(
      "Set before Oversight starts; every lead gets the same.",
    );
    await task.fill("Coordinate validation");
    await task.press("Control+Enter");
    expect(calls).toEqual([]);
    await page.getByRole("button", { name: "Start lead", exact: true }).click();
    await check(page.locator(".new-lead").getByRole("alert")).toHaveText("No lead can start now.");
    await check(task).toHaveValue("Coordinate validation");
    refuse = false;
    await page.getByRole("button", { name: "Start lead", exact: true }).click();
    await check(task).toHaveCount(0);
    expect(calls).toEqual(
      Array.from({ length: 2 }, () => ({
        task: "Coordinate validation",
      })),
    );
    await check(page.locator(".chat-lead-row")).toHaveCount(0);
    await startForm(page);
    await check(task).toHaveValue("");
  } finally {
    await page.close();
  }
});
it("transfers only into an empty task, keeps limits factual and makes replay inert", async () => {
  const page = await open();
  try {
    await startForm(page);
    await page.getByRole("textbox", { name: "First task" }).fill("Transfer me");
    await page.getByRole("button", { name: "Start a task instead" }).click();
    await check(page.getByRole("textbox", { name: "Task for the new agent" })).toHaveValue(
      "Transfer me",
    );
    await startForm(page);
    await page.getByRole("textbox", { name: "First task" }).fill("Keep both");
    await page.getByRole("button", { name: "Start a task instead" }).click();
    await check(page.getByRole("textbox", { name: "Task for the new agent" })).toHaveValue(
      "Transfer me",
    );
    await startForm(page);
    await check(page.getByRole("textbox", { name: "First task" })).toHaveValue("Keep both");
    await page.evaluate(() => {
      window.chatLeadsTest.mount("leads-two", false);
    });
    await check(page.getByRole("textbox", { name: "Message Lead A", exact: true })).toHaveAttribute(
      "readonly",
      "",
    );
    await check(page.getByRole("button", { name: "Stop lead", exact: true })).toHaveCount(0);
    await check(
      page.getByRole("button", { name: "Send message to lead", exact: true }),
    ).toHaveCount(0);
    await startForm(page);
    await check(
      page.getByText("Replay: leads cannot be started here.", { exact: true }),
    ).toBeVisible();
    await check(page.getByRole("button", { name: "Start lead", exact: true })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    const lead = (await page.evaluate(() => window.chatLeadsTest.events())).find(
      (e) => e.type === "lead.status",
    );
    if (lead?.type !== "lead.status") throw new Error("Missing lead");
    await page.evaluate(
      (event) => {
        window.chatLeadsTest.deliver([event]);
      },
      {
        ...lead,
        payload: {
          ...lead.payload,
          revision: 2,
          limits: {
            turns: 50,
            plans: 3,
            tool_calls: 12,
            model_calls: 18,
            wall_clock_s: 300.5,
            subtasks: 6,
            task_chars: 2000,
          },
        },
      },
    );
    await check(page.locator(".new-lead-limits")).toContainText("2,000 characters a task");
    await check(page.locator(".new-lead-limits")).toContainText("50 turns");
    await check(page.locator(".new-lead-limits")).toContainText("300.5 s");
  } finally {
    await page.close();
  }
});

for (const theme of ["dark", "day"])
  it(`fits Chat start at 390 in ${theme}`, async () => {
    const page = await open("chat-start", 390, theme);
    try {
      await check(page.getByRole("textbox", { name: "Task for the new agent" })).toBeVisible();
      const measure = await page.evaluate(() => {
        const rect = (selector: string) => {
          const r = document.querySelector(selector)?.getBoundingClientRect();
          return r ? [r.width, r.height] : null;
        };
        return {
          scroll: document.documentElement.scrollWidth,
          box: rect(".task-box-field"),
          cards: rect("[data-slot-card]"),
          start: rect(".task-box-actions > button"),
          header: rect(".chat-start-actions button"),
          merge: rect(".chat-merge-chip"),
          link: rect(".chat-lead-option button"),
        };
      });
      expect(measure).toEqual({
        scroll: 390,
        box: [358, 264],
        cards: [162, 44],
        start: [332, 44],
        header: [110, 44],
        merge: [189, 44],
        link: [83, 44],
      });
      await check(page.locator(".left-rail")).toBeHidden();
      await check(page.locator(".chat-work")).toBeHidden();
      await check(page.locator(".task-box-hint")).toBeHidden();
      for (const button of await page.locator(".shell-top-bar button:visible").all()) {
        const box = await button.boundingBox();
        expect(box && box.x >= 0 && box.x + box.width <= 390 && box.height >= 44).toBe(true);
      }
    } finally {
      await page.close();
    }
  });

it("lets the server choose among four slots when no slot cards are shown", async () => {
  const page = await open("chat-start", 1280, "dark", true, true, true);
  const calls: unknown[] = [];
  await page.route("**/agents", async (route) => {
    calls.push(route.request().postDataJSON());
    await route.fulfill({
      status: 201,
      json: { agent_id: "new-agent", change_id: "new-change", slot: "A" },
    });
  });
  try {
    const opening = await page.evaluate(() => {
      const event = window.chatLeadsTest.prefix("leads-two", true)[0];
      if (!event) throw new Error("Missing run opening");
      window.chatLeadsTest.deliver([event]);
      return event;
    });
    if (opening.type !== "run.started") throw new Error("Missing run opening");
    expect(opening.payload.slots.map(({ slot }) => slot)).toEqual(["A", "B", "C", "D"]);
    expect(new Set(opening.payload.slots.map(({ default_model }) => default_model)).size).toBe(2);
    await check(page.getByRole("group", { name: "Agent slot" })).toHaveCount(0);
    const task = page.getByRole("textbox", { name: "Task for the new agent" });
    await task.fill("Four-slot task");
    await page.getByRole("button", { name: "Start agent", exact: true }).click();
    await check(task).toHaveValue("");
    expect(calls).toEqual([{ task: "Four-slot task" }]);
  } finally {
    await page.close();
  }
});

it("sends no slot when both cards name the same profile", async () => {
  const page = await open("same-model");
  const calls: unknown[] = [];
  await page.route("**/agents", async (route) => {
    calls.push(route.request().postDataJSON());
    await route.fulfill({
      status: 201,
      json: { agent_id: "new-agent", change_id: "new-change", slot: "A" },
    });
  });
  try {
    await check(page.getByRole("group", { name: "Agent slot" })).toHaveCount(0);
    await check(page.getByLabel("Model: Muse Spark", { exact: true })).toBeVisible();
    await page.getByRole("textbox", { name: "Task for the new agent" }).fill("One model task");
    await page.getByRole("button", { name: "Start agent", exact: true }).click();
    await check(page.getByRole("textbox", { name: "Task for the new agent" })).toHaveValue("");
    expect(calls).toEqual([{ task: "One model task" }]);
  } finally {
    await page.close();
  }
});

it.each([1920, 1280])(
  "keeps the approved lead, member and direct row density at %s",
  async (width) => {
    const page = await open("leads-two", width, "day", true, false, true);
    try {
      await check(page.locator(".chat-agent-name")).toHaveText([
        "Lead A",
        "A·1",
        "A·2",
        "A·3",
        "Lead B",
        "Agent C",
      ]);
      await check(page.locator(".chat-agents > header")).toContainText("3 running");
      await check(leadRow(page, "A")).toContainText("3 agents · 1 working, 1 needs you");
      await check(leadRow(page, "B")).toContainText("1 plan needs you");
      const sizes = await page.evaluate(() =>
        [
          ".chat-lead-row",
          '.chat-agent-row[data-member="true"]',
          '.chat-agent-row[data-member="false"]',
        ].map((selector) => document.querySelector(selector)?.getBoundingClientRect().height),
      );
      for (const [i, target] of (width === 1920 ? [98, 100, 139] : [88, 62, 115]).entries()) {
        expect(Math.abs((sizes[i] ?? 0) - target)).toBeLessThanOrEqual(4);
      }
      const member = await page
        .locator('.chat-agent-row[data-member="true"]')
        .first()
        .boundingBox();
      expect(member?.y).toBe(width === 1920 ? 260 : 246.390625);
      await leadRow(page, "B").click();
      await check(page.getByRole("textbox", { name: "Message Lead B" })).toBeEditable();
    } finally {
      await page.close();
    }
  },
);
it.each([1920, 1280].flatMap((width) => ["dark", "day"].map((theme) => ({ width, theme }))))(
  "shows the first-task field, observed limits and selected draft at $width in $theme",
  async ({ width, theme }) => {
    const page = await open("lead-new", width, theme, true, false, true);
    try {
      await startForm(page);
      await check(page.locator(".chat-lead-draft")).toBeVisible();
      await check(page.locator(".chat-lead-draft")).toHaveAttribute("data-selected", "true");
      await check(page.getByRole("textbox", { name: "First task" })).toBeFocused();
      await check(page.getByRole("combobox", { name: "Model" })).toHaveCount(0);
      await check(page.locator(".new-lead-limit-cards")).toContainText("2,000 characters a task");
      const explanation = page.locator(".new-lead-limit-heading p");
      await check(explanation).toHaveText("Set before Oversight starts; every lead gets the same.");
      await check(explanation).toBeInViewport();
      const copy = await explanation.boundingBox();
      const heading = await page.locator(".new-lead-limit-heading").boundingBox();
      const cards = await page.locator(".new-lead-limit-cards").boundingBox();
      expect(copy?.width).toBeGreaterThan(300);
      expect(copy?.height).toBeGreaterThanOrEqual(18);
      if (!copy || !heading || !cards) throw new Error("Missing limits layout");
      expect(copy.y).toBeGreaterThanOrEqual(heading.y);
      expect(copy.y + copy.height).toBeLessThanOrEqual(heading.y + heading.height);
      expect(heading.y + heading.height).toBeLessThan(cards.y);
      const field = await page.locator(".new-lead-box textarea").boundingBox();
      expect(field?.width).toBe(width === 1920 ? 734 : 630);
      expect(field?.height).toBe(width === 1920 ? 112 : 76);
    } finally {
      await page.close();
    }
  },
);

it("stops the observed lead once, keeps 202 pending across switches and preserves member work", async () => {
  const page = await open("leads-two", 1280, "dark", true, true);
  const calls: { path: string; body: unknown }[] = [];
  let admit = () => {};
  const delayed = new Promise<void>((resolve) => {
    admit = resolve;
  });
  await page.route("**/leads/*/stop", async (route) => {
    calls.push({
      path: new URL(route.request().url()).pathname,
      body: route.request().postDataJSON(),
    });
    await delayed;
    await route.fulfill({ status: 202 });
  });
  try {
    await leadRow(page, "A").click();
    const draft = page.getByRole("textbox", { name: "Message Lead A", exact: true });
    await draft.fill("Keep this draft");
    await page
      .getByRole("button", { name: "Stop lead", exact: true })
      .evaluate((button: HTMLButtonElement) => {
        button.click();
        button.click();
      });
    await check(page.getByRole("status").filter({ hasText: "Stopping lead…" })).toBeVisible();
    expect(calls).toEqual([{ path: "/leads/agent-1/stop", body: { revision: 1 } }]);
    await check(page.locator(".conversation-header")).toContainText("Idle");
    await check(page.getByRole("button", { name: "Send message to lead" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    const preserved = await page.evaluate(() => window.chatLeadsTest.work());
    await leadRow(page, "B").click();
    await check(page.getByRole("button", { name: "Stop lead", exact: true })).toHaveAttribute(
      "aria-disabled",
      "false",
    );
    await publishLead(page, "agent-1", { revision: 2, state: "working" });
    admit();
    await leadRow(page, "A").click();
    await check(page.getByRole("button", { name: "Stop lead", exact: true })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    await check(draft).toHaveValue("Keep this draft");
    await check(page.locator(".conversation-header")).toContainText("Working");
    await leadRow(page, "B").focus();
    await publishLead(page, "agent-1", {
      revision: 3,
      state: "stopped",
      detail: "Stopped by the person.",
    });
    await check(leadRow(page, "B")).toBeFocused();
    await check(leadRow(page, "A")).toHaveAttribute("aria-pressed", "true");
    await check(page.locator(".conversation-composer")).toHaveText(
      "Stopped by the person.Start a new lead to continue.",
    );
    await check(page.locator(".lead-header")).not.toContainText("Stopped by the person.");
    await check(page.locator(".conversation-composer :is(button, textarea)")).toHaveCount(0);
    expect(await page.evaluate(() => window.chatLeadsTest.work())).toEqual(preserved);
    expect(calls).toHaveLength(1);
  } finally {
    admit();
    await page.close();
  }
});
it("does not retry a rejected Stop and uses the newly rendered revision only on another act", async () => {
  const page = await open("leads-two", 1280, "day", true, true);
  const calls: unknown[] = [];
  await page.route("**/leads/*/stop", async (route) => {
    calls.push(route.request().postDataJSON());
    await route.fulfill({
      status: 409,
      json: {
        error: { code: "wrong_state", message: "The lead changed. Check its state and try again." },
      },
    });
  });
  try {
    await leadRow(page, "A").click();
    await page
      .getByRole("textbox", { name: "Message Lead A", exact: true })
      .fill("Keep on refusal");
    await page.getByRole("button", { name: "Stop lead", exact: true }).click();
    await check(page.locator(".conversation-composer").getByRole("alert")).toContainText(
      "The lead changed.",
    );
    await publishLead(page, "agent-1", { revision: 8, state: "working" });
    expect(calls).toEqual([{ revision: 1 }]);
    await check(page.getByRole("textbox", { name: "Message Lead A", exact: true })).toHaveValue(
      "Keep on refusal",
    );
    await check(page.getByRole("button", { name: "Stop lead", exact: true })).toHaveAttribute(
      "aria-disabled",
      "false",
    );
    await page.getByRole("button", { name: "Stop lead", exact: true }).click();
    await check(page.locator(".conversation-composer").getByRole("alert")).toBeVisible();
    expect(calls).toEqual([{ revision: 1 }, { revision: 8 }]);
  } finally {
    await page.close();
  }
});
it.each([1280, 640, 390])("guards cap entry points and an open draft at %i", async (width) => {
  const page = await open("lead-cap", width, "dark", true, false, true);
  const starts: unknown[] = [];
  await page.route("**/leads", async (route) => {
    starts.push(route.request().postDataJSON());
    await route.fulfill({ status: 201, json: { agent_id: "unexpected" } });
  });
  try {
    const action = page
      .locator(".chat-start-actions")
      .getByRole("button", { name: "New lead · 2 of 2", exact: true });
    await check(action).toHaveAttribute("aria-disabled", "true");
    expect(
      await action.evaluate((button) => {
        const bounds = button.getBoundingClientRect();
        const range = document.createRange();
        range.selectNodeContents(button);
        return [...range.getClientRects()].every(
          (r) =>
            r.left >= bounds.left &&
            r.right <= bounds.right &&
            r.top >= bounds.top &&
            r.bottom <= bounds.bottom,
        );
      }),
    ).toBe(true);
    if (width === 390) {
      await check(leadRow(page, "B").locator(".chat-lead-team")).toHaveText("1 plan needs you");
      await check(leadRow(page, "B").locator(".chat-lead-team")).toBeVisible();
    }
    if (process.env.CHAT_LEADS_SHOTS === "1" && width === 390) {
      await check(page.locator(".lead-header")).toBeVisible();
      await capture(page, "lead-cap", width, "dark");
    }
    await action.evaluate((button: HTMLButtonElement) => {
      button.click();
    });
    await check(page.getByRole("textbox", { name: "First task" })).toHaveCount(0);
    await newTask(page).click();
    await check(page.locator(".chat-lead-option button")).toHaveText("New lead · 2 of 2");
    await check(page.locator(".chat-lead-option button")).toHaveAttribute("aria-disabled", "true");
    await publishLead(page, "agent-2", { revision: 2, state: "stopped" });
    await startForm(page);
    await page.getByRole("textbox", { name: "First task" }).fill("Keep capped draft");
    await publishLead(page, "agent-1", { agent_id: "lead-new", revision: 1, state: "starting" });
    await check(
      page
        .getByRole("status")
        .filter({ hasText: "2 of 2 leads active. A lead must end before another can start." }),
    ).toBeVisible();
    await check(page.getByRole("button", { name: "Start lead", exact: true })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    const cappedStart = page.getByRole("button", { name: "Start lead", exact: true });
    await cappedStart.hover();
    await check(cappedStart).toHaveCSS("color", await themeColor(page, "faint"));
    await check(cappedStart).toHaveCSS("background-color", await themeColor(page, "raised"));
    await check(cappedStart).toHaveCSS("cursor", "not-allowed");
    await check(page.getByRole("textbox", { name: "First task" })).toHaveValue("Keep capped draft");
    await page
      .getByRole("button", { name: "Start lead", exact: true })
      .evaluate((button: HTMLButtonElement) => {
        button.click();
      });
    expect(starts).toEqual([]);
  } finally {
    await page.close();
  }
});

it.each(["dark", "day"])(
  "hardens the 390 lead composer and terminal surface in %s",
  async (theme) => {
    const page = await open("leads-two", 390, theme, true, false, true);
    try {
      await check(page.getByRole("textbox", { name: "Message Lead A", exact: true })).toBeVisible();
      await check(leadRow(page, "A").locator(".chat-lead-team")).toBeVisible();
      await check(leadRow(page, "A").locator(".chat-lead-team")).toContainText("3 agents");
      await check(leadRow(page, "B").locator(".chat-lead-team")).toHaveText("1 plan needs you");
      await check(leadRow(page, "B").locator(".chat-lead-team")).toBeVisible();
      await page.locator(".conversation-composer").scrollIntoViewIfNeeded();
      const stopIcon = page.getByRole("button", { name: "Stop lead", exact: true }).locator("svg");
      await check(stopIcon).toHaveClass(/lucide-square/);
      await check(stopIcon).toHaveAttribute("aria-hidden", "true");
      await check(stopIcon).toBeVisible();
      await check(stopIcon).toHaveCSS("width", "16px");
      await check(stopIcon).toHaveCSS("height", "16px");
      const measured = await page.evaluate(() => ({
        sizes: [
          ".chat-lead-row",
          ".conversation-composer textarea",
          ".lead-stop",
          'button[aria-label="Send message to lead"]',
          ".composer-actions .chat-merge-chip",
        ].map((s) => {
          const r = document.querySelector(s)?.getBoundingClientRect();
          return r ? [r.width, r.height] : null;
        }),
        overflow: [
          ...document.querySelectorAll(".chat-shell, .conversation-panel, .conversation-composer"),
        ].some((el) => el.scrollWidth > el.clientWidth),
      }));
      expect(measured).toEqual({
        sizes: [
          [374, 59],
          [362, 44],
          [114, 44],
          [67, 44],
          [189, 44],
        ],
        overflow: false,
      });
      await check(newLead(page)).toHaveCSS("height", "44px");
      if (process.env.CHAT_LEADS_SHOTS === "1")
        await capture(page, "lead-working-phone", 390, theme);
      await publishLead(page, "agent-1", {
        revision: 2,
        state: "halted",
        detail: "Reached the limit of 50 turns.",
      });
      await check(page.locator(".conversation-composer")).toHaveText(
        "Reached the limit of 50 turns.Start a new lead to continue.",
      );
      await check(page.locator(".lead-header")).not.toContainText("Reached the limit of 50 turns.");
      await check(page.locator(".conversation-composer :is(button, textarea)")).toHaveCount(0);
    } finally {
      await page.close();
    }
  },
);

async function assertLifecycleScene(page: Page, name: string) {
  const six = name === "leads-six";
  await check(leadRow(page, six ? "D" : "A")).toHaveAttribute("aria-pressed", "true");
  await check(page.locator(".lead-header h2")).toHaveText(
    six
      ? "Lowercase customer emails everywhere and reject bad addresses in every setter."
      : "Add input checks to the shop: reject negative prices and quantities, and zero or negative reserve quantities.",
  );
  const states: Record<string, string> = six
    ? { A: "Stopped", B: "Working", C: "Working", D: "Halted", E: "Idle", F: "Idle" }
    : { A: "Stopped", B: "Working" };
  for (const [letter, state] of Object.entries(states))
    await check(leadRow(page, letter).locator(".chat-agent-pill")).toHaveText(state);
  if (six) {
    await check(leadRow(page, "F").locator(".chat-lead-team")).toHaveText("1 plan needs you");
    await check(leadRow(page, "F").locator(".chat-lead-team")).toBeVisible();
  }
  await check(page.locator(".chat-agents > header")).toContainText("3 running");
}

it.each(["lead-stopped", "leads-six"])(
  "verifies the selected lead and row states in %s",
  async (name) => {
    const page = await open(name, 390, "dark", true, false, true);
    try {
      await assertLifecycleScene(page, name);
      const rows = await page.locator(".chat-lead-row").evaluateAll((rows) =>
        rows.map((row) => {
          const r = row.getBoundingClientRect();
          return [r.width, r.height];
        }),
      );
      expect(rows).toEqual(Array.from({ length: name === "leads-six" ? 6 : 2 }, () => [374, 59]));
    } finally {
      await page.close();
    }
  },
);

const captureStates = [
  "chat-start",
  "task-direct",
  "leads-two",
  "lead-switch",
  "lead-new",
  "lead-stopped",
  "leads-six",
];
for (const name of captureStates)
  for (const width of name === "lead-stopped" || name === "leads-six"
    ? [1920, 1280, 390]
    : [1920, 1280])
    for (const theme of ["dark", "day"]) {
      it.skipIf(process.env.CHAT_LEADS_SHOTS !== "1")(
        `captures ${name}-${String(width)}-${theme}`,
        async () => {
          const page = await open(name, width, theme, true, false, true);
          try {
            if (name === "lead-new") {
              await startForm(page);
              await page
                .getByRole("textbox", { name: "First task" })
                .fill(
                  "Give premium customers free shipping: change shipping_cost and cover it in tests/test_pricing.py.",
                );
              await page
                .getByRole("heading", { name: "What should the new lead work on?" })
                .click();
              const start = page.getByRole("button", { name: "Start lead", exact: true });
              await check(start).toHaveCSS("height", "40px");
              await check(start).toHaveCSS("background-color", await themeColor(page, "text"));
            }
            if (name === "task-direct") {
              await check(page.getByRole("textbox", { name: "Message Agent C" })).toBeVisible();
              await check(page.getByLabel("Agent C conversation", { exact: true })).toBeVisible();
              await check(page.locator(".conversation-entries")).toContainText(
                "Round prices to two decimals in Item.total",
              );
            }
            if (name === "leads-two" || name === "lead-switch")
              await check(
                page.getByRole("textbox", {
                  name: `Message Lead ${name === "lead-switch" ? "B" : "A"}`,
                }),
              ).toBeVisible();
            if (name === "lead-stopped" || name === "leads-six") {
              await check(
                page.getByRole("region", { name: "Lead conversation", exact: true }),
              ).toBeVisible();
              await check(page.locator(".conversation-composer")).toContainText(
                "Start a new lead to continue.",
              );
              await check(
                page.locator(".conversation-entries [role=listitem]").last(),
              ).toBeVisible();
              await assertLifecycleScene(page, name);
            }
            if (width === 390)
              await page.locator(".conversation-composer").scrollIntoViewIfNeeded();
            await capture(page, name, width, theme);
          } finally {
            await page.close();
          }
        },
      );
    }
for (const theme of ["dark", "day"])
  it.skipIf(process.env.CHAT_LEADS_SHOTS !== "1")(`captures start-phone-390-${theme}`, async () => {
    const page = await open("chat-start", 390, theme, true, false, true);
    try {
      await capture(page, "start-phone", 390, theme);
    } finally {
      await page.close();
    }
  });

const shots = fileURLToPath(new URL("../../../.shots/chat-leads/", import.meta.url));
async function capture(page: Page, name: string, width: number, theme: string) {
  await mkdir(shots, { recursive: true });
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
  const measured = await page.evaluate(() =>
    Object.fromEntries(
      [
        ".chat-agents",
        ".chat-start-actions button",
        ".task-box-field",
        "[data-slot-card]",
        ".task-box-actions > button",
        ".chat-merge-chip",
        ".chat-lead-option button",
        ".chat-lead-row",
        '.chat-agent-row[data-member="false"]',
        '.chat-agent-row[data-member="true"]',
        ".chat-empty h1",
        ".chat-start-explanation",
        ".chat-lead-option",
        ".new-lead-box",
        ".new-lead-box textarea",
        ".new-lead-limits",
        ".conversation-composer",
        ".conversation-composer textarea",
        ".lead-stop",
        '.composer-actions button[aria-label="Send message to lead"]',
        ".conversation-entries",
        ".conversation-header h2",
        ".shell-top-bar",
      ].map((selector) => {
        const el = document.querySelector(selector),
          r = el?.getBoundingClientRect();
        return [
          selector,
          el && r
            ? {
                x: r.x,
                y: r.y,
                width: r.width,
                height: r.height,
                fontSize: getComputedStyle(el).fontSize,
              }
            : null,
        ];
      }),
    ),
  );
  const basename = `${name}-${String(width)}-${theme}`;
  const screenshot = await page.screenshot({
    path: `${shots}${basename}.png`,
    animations: "disabled",
  });
  if (["lead-stopped", "leads-six"].includes(name) && width !== 390)
    await compare(page, screenshot, basename, name, width, theme, measured);
  await writeFile(`${shots}${basename}.json`, JSON.stringify(measured, null, 2));
  await writeFile(
    `${shots}events-${name}.json`,
    JSON.stringify(
      await page.evaluate(
        (name) =>
          window.chatLeadsTest.prefix(
            name === "start-phone"
              ? "chat-start"
              : name === "lead-working-phone"
                ? "leads-two"
                : name,
            true,
          ),
        name,
      ),
    ),
  );
}

async function openViews(width = 1280, theme = "dark", replay = false, live = true) {
  const page = await open("chat-start", width, theme, live);
  await page.evaluate(({ replay, live }) => window.chatLeadsTest.mountViews(replay, live), {
    replay,
    live,
  });
  await check(page.getByRole("article", { name: "Run moment" })).toBeVisible();
  return page;
}
async function captureView(page: Page, name: string, width: number, theme: string) {
  if (process.env.CHAT_LEADS_SHOTS !== "1") return;
  const path = `${shots}views/synthetic-${name}-${String(width)}-${theme}.png`;
  await mkdir(`${shots}views`, { recursive: true });
  await page.screenshot({ path, animations: "disabled" });
}
it("keeps the originating card and later reply, focus and Back through exact replay history", async () => {
  const page = await openViews();
  const requests: string[] = [];
  await page.route("**/leads/**", (route) => {
    requests.push(route.request().method());
    return route.fulfill({ status: 202 });
  });
  await page.evaluate(() => window.chatLeadsTest.mountViews(true, true));
  const moment = page.getByRole("article", { name: "Run moment" });
  const action = moment.getByRole("button", { name: "View event 6" });
  const before = await page.evaluate(() => window.chatLeadsTest.viewReplaySnapshot());
  await action.click();
  await check(page.getByRole("status").filter({ hasText: "Viewing event 6" })).toBeVisible();
  await check(action).toBeFocused();
  await check(moment).toBeVisible();
  await check(
    page.getByText("Later reply: all original cards remain in Chat.", { exact: true }),
  ).toBeVisible();
  expect(await page.evaluate(() => window.chatLeadsTest.viewReplaySnapshot().seq)).toBe(6);
  await check(page.getByRole("textbox", { name: "Message to lead" })).toHaveCount(0);
  await check(page.getByRole("button", { name: "Stop lead", exact: true })).toHaveCount(0);
  await check(newLead(page)).toHaveAttribute("aria-disabled", "true");
  await check(newTask(page)).toBeDisabled();
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await check(page.getByText("Viewing event 6", { exact: false })).toHaveCount(0);
  const returned = await page.evaluate(() => window.chatLeadsTest.viewReplaySnapshot());
  expect(returned.seq).toBe(before.seq);
  expect(returned.snapshot?.position).toBe(before.snapshot?.position);
  expect(returned.snapshot?.status).toBe("paused");
  await check(page.getByRole("article")).toHaveCount(4);
  expect(requests).toEqual([]);
  await page.close();
});
it("shows live moment facts without rewind and never moves focus on arriving views", async () => {
  const page = await openViews();
  const moment = page.getByRole("article", { name: "Run moment" });
  await check(moment.getByText("change.proposed", { exact: false })).toBeVisible();
  await check(moment.getByText("Rewind available in replay.", { exact: true })).toBeVisible();
  await check(moment.getByRole("button")).toHaveCount(0);
  const input = page.locator(".conversation-composer textarea");
  await input.focus();
  await page.evaluate(() => {
    window.chatLeadsTest.deliver([
      {
        type: "lead.view",
        seq: 0,
        ts: "2026-11-03T00:01:00Z",
        payload: {
          agent_id: "agent-9",
          through_seq: 6,
          caption: "Synthetic arrival",
          view: { kind: "moment", event_seq: 6 },
        },
      },
    ]);
  });
  await check(input).toBeFocused();
  await check(page.getByRole("article")).toHaveCount(5);
  await page.close();
});
it.each([390, 1280])(
  "keeps mixed-script cards readable at %i and in a 200 percent zoom-equivalent viewport",
  async (width) => {
    const page = await openViews(width);
    const caption = `مراجعة 漢字 👩🏽‍💻 ${"LongReference".repeat(9)} <b>speech</b>`;
    try {
      await page.evaluate((caption) => {
        window.chatLeadsTest.deliver([
          {
            type: "lead.view",
            seq: 0,
            ts: "2026-11-03T00:01:00Z",
            payload: {
              agent_id: "agent-9",
              through_seq: 6,
              caption,
              view: { kind: "moment", event_seq: 6 },
            },
          },
        ]);
      }, caption);
      const speech = page.getByText(caption, { exact: true });
      await speech.scrollIntoViewIfNeeded();
      await check(speech).toBeVisible();
      await check(speech).toHaveCSS("direction", "rtl");
      await check(speech.locator("b")).toHaveCount(0);
      // Half the CSS viewport models browser zoom reflow, including media queries.
      // CSS zoom alone leaves desktop breakpoints active. Physical devices remain untested.
      if (width === 1280) await page.setViewportSize({ width: 640, height: 400 });
      expect(
        await page.locator(".lead-view-card").evaluateAll((cards) =>
          cards
            .filter((card) => card.scrollWidth > card.clientWidth)
            .map((card) => ({
              title: card.getAttribute("aria-label"),
              width: card.clientWidth,
              scrollWidth: card.scrollWidth,
            })),
        ),
      ).toEqual([]);
      await page.evaluate(() => window.chatLeadsTest.mountViews(true, true));
      const behavior = page.getByRole("article", { name: "Measured behavior" });
      const samples = behavior.getByText("Samples (1)", { exact: true });
      await samples.click();
      await check(behavior.getByText("ValueError", { exact: true })).toBeVisible();
      if (width === 390) {
        expect((await samples.boundingBox())?.height).toBeGreaterThanOrEqual(44);
        await check(behavior.locator("h3")).toHaveCSS("font-weight", "700");
      }
      await page.getByRole("button", { name: "View event 6", exact: true }).click();
      const back = page.getByRole("button", { name: "Back", exact: true });
      await check(back).toBeVisible();
      expect((await back.boundingBox())?.width).toBeGreaterThanOrEqual(44);
      expect((await back.boundingBox())?.height).toBeGreaterThanOrEqual(width === 390 ? 44 : 34);
      await page.emulateMedia({ forcedColors: "active" });
      await page.keyboard.press("Tab");
      await back.focus();
      await check(back).toHaveCSS("outline-style", "solid");
      await back.press("Enter");
      await check(back).toHaveCount(0);
      await check(page.getByRole("article", { name: "Run moment" })).toBeVisible();
    } finally {
      await page.close();
    }
  },
);
it("opens the merged exact recorded file and keeps its diff read-only", async () => {
  const page = await openViews();
  const file = page.getByRole("article", { name: "Changed files" }).getByRole("button").last();
  await file.click();
  await check(page.getByRole("tab", { name: /Live diff/ })).toBeVisible();
  await check(page.getByText("# synthetic after", { exact: false }).first()).toBeVisible();
  const editors = page.locator('.monaco-editor [role="textbox"]');
  await check(editors.first()).toHaveAttribute("aria-autocomplete", "none");
  await page.close();
});
it("frames a cold graph from highlight and clears reference lighting on navigation", async () => {
  const page = await openViews();
  await check(page.getByLabel("Graph stage")).toHaveCount(0);
  await page.getByRole("article", { name: "Graph references" }).getByRole("button").click();
  const stage = page.getByLabel("Graph stage");
  await check(stage).toBeVisible();
  await page.waitForFunction(() => {
    const el = document.querySelector<
      HTMLElement & {
        oversightGraph?: { animating(): boolean; nodeCenters(selector?: string): unknown[] };
      }
    >('[aria-label="Repository graph"]');
    return el?.oversightGraph && !el.oversightGraph.animating();
  });
  const dimmedCount = () =>
    page.evaluate(() => {
      const graph = document.querySelector<
        HTMLElement & { oversightGraph?: { appearance(selector: string): unknown[] } }
      >('[aria-label="Repository graph"]')?.oversightGraph;
      return graph?.appearance("node.dimmed").length;
    });
  expect(await dimmedCount()).toBe(1);
  await page.getByRole("button", { name: "Chat", exact: true }).click();
  await check(page.getByRole("article", { name: "Graph references" })).toBeVisible();
  await page.getByRole("button", { name: "Oversight", exact: true }).click();
  await expect.poll(dimmedCount).toBe(0);
  await page.close();
});
for (const theme of ["dark", "day"])
  for (const width of [1280, 390]) {
    it(`fits synthetic reference cards and history at ${String(width)} in ${theme}`, async () => {
      const page = await openViews(width, theme);
      try {
        await check(page.getByRole("article")).toHaveCount(4);
        for (const [title, name] of [
          ["Graph references", "highlight"],
          ["Changed files", "files-eight-long-paths"],
          ["Measured behavior", "behavior"],
          ["Run moment", "live-moment"],
        ] as const) {
          const card = page.getByRole("article", { name: title });
          await card.scrollIntoViewIfNeeded();
          if (name === "behavior") await card.getByText("Samples (1)", { exact: true }).click();
          expect(await card.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
          expect(
            await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
          ).toBe(true);
          if (name === "files-eight-long-paths") {
            await check(card.getByRole("button")).toHaveCount(8);
            await check(
              card.getByRole("button").filter({ hasText: "· Agent A · approved" }),
            ).toHaveCount(8);
          }
          if (name === "files-eight-long-paths" || name === "live-moment") {
            const agent = card.locator(".lead-view-agent").first();
            await check(agent).toHaveText("Agent A");
            await check(agent).toHaveCSS("color", await themeColor(page, "agent-a-text"));
            await check(card.getByText("Agent agent-1", { exact: true })).toHaveCount(0);
            await check(card.getByText("change-1 · approved", { exact: true })).toHaveCount(0);
          }
          await captureView(page, name, width, theme);
        }
        await page.evaluate(() => window.chatLeadsTest.mountViews(true, true));
        await page.getByRole("button", { name: "View event 6", exact: true }).click();
        await check(page.getByRole("button", { name: "Back", exact: true })).toBeVisible();
        await captureView(page, "historical-banner", width, theme);
        await page.getByRole("button", { name: "Back", exact: true }).click();
        await check(page.getByRole("article", { name: "Run moment" })).toBeVisible();
        await captureView(page, "returned-chat", width, theme);
        if (process.env.CHAT_LEADS_SHOTS === "1") {
          await page.evaluate(() => {
            window.chatLeadsTest.mountFullFilesCapture();
          });
          await check(page.getByRole("article").getByRole("button")).toHaveCount(8);
          await page.screenshot({
            path: `${shots}views/synthetic-files-eight-long-paths-full-${String(width)}-${theme}.png`,
            fullPage: true,
            animations: "disabled",
          });
        }
      } finally {
        await page.close();
      }
    });
  }

it("frames references on first mount without a panel and keeps the camera when an ID disappears", async () => {
  const page = await openViews();
  await page.evaluate(() => {
    window.chatLeadsTest.mountReferenceGraph();
  });
  const probe = () =>
    page.evaluate(() => {
      const host = document.querySelector<
        HTMLElement & {
          oversightGraph?: {
            animating(): boolean;
            renderedBox(id: string): { x1: number; x2: number; y1: number; y2: number } | null;
            zoom(): number;
            pan(): { x: number; y: number };
          };
        }
      >('[aria-label="Repository graph"]');
      const graph = host?.oversightGraph;
      return graph
        ? {
            animating: graph.animating(),
            zoom: graph.zoom(),
            pan: graph.pan(),
            target: graph.renderedBox("n1"),
            width: host.clientWidth,
            height: host.clientHeight,
          }
        : null;
    });
  await expect.poll(probe).toMatchObject({ animating: false });
  const before = await probe();
  expect(before?.height).toBeGreaterThan(0);
  expect(before?.target).not.toBeNull();
  expect(before?.target?.x1).toBeGreaterThanOrEqual(0);
  expect(before?.target?.x2).toBeLessThanOrEqual(before?.width ?? 0);
  await page.evaluate(() => {
    window.chatLeadsTest.deliver([
      {
        seq: 0,
        ts: "2026-11-03T00:01:00Z",
        type: "graph.delta",
        payload: {
          from_version: "g1",
          to_version: "g3",
          cause: "rescan",
          commit: "base",
          nodes_added: [],
          nodes_changed: [],
          nodes_removed: ["n2"],
          edges_added: [],
          edges_changed: [],
          edges_removed: [],
          parse_errors: [],
        },
      },
    ]);
  });
  const after = await probe();
  expect(after?.zoom).toBe(before?.zoom);
  expect(after?.pan).toEqual(before?.pan);
  await page.close();
});

it("clears historical Chat on ordinary replay controls and source replacement", async () => {
  const page = await openViews(1280, "dark", true);
  const moment = () => page.getByRole("button", { name: "View event 6", exact: true });
  await moment().click();
  await page.getByRole("slider", { name: "Position in the recording" }).press("End");
  await check(page.getByRole("button", { name: "Back", exact: true })).toHaveCount(0);
  await check(page.getByRole("article")).toHaveCount(4);
  await moment().click();
  await page.getByRole("button", { name: "Restart", exact: true }).click();
  await check(page.getByRole("button", { name: "Back", exact: true })).toHaveCount(0);
  await page.evaluate(() => window.chatLeadsTest.mountViews(true, true));
  await moment().click();
  await page.evaluate(() => window.chatLeadsTest.mountViews(false, true));
  await check(page.getByRole("button", { name: "Back", exact: true })).toHaveCount(0);
  await check(page.getByText("Rewind available in replay.", { exact: true })).toBeVisible();
  await page.close();
});
