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
    await check(page.locator(".chat-agents > header")).toContainText("2 of 2 running");
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
    await check(page.locator(".conversation-header h2")).toHaveText("Lead A");
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
it("sends the shown model through App, retains refusals and never starts from Ctrl+Enter", async () => {
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
    await check(page.getByRole("combobox", { name: "Model" })).toHaveValue("muse-spark");
    await check(page.locator(".new-lead-limits")).toHaveText(
      "Set before Oversight starts; every lead gets the same.",
    );
    await task.fill("Coordinate validation");
    await task.press("Control+Enter");
    expect(calls).toEqual([]);
    await page.getByRole("combobox", { name: "Model" }).selectOption("muse-spark-open");
    await page.getByRole("button", { name: "Start lead", exact: true }).click();
    await check(page.locator(".new-lead").getByRole("alert")).toHaveText("No lead can start now.");
    await check(task).toHaveValue("Coordinate validation");
    refuse = false;
    await page.getByRole("button", { name: "Start lead", exact: true }).click();
    await check(task).toHaveCount(0);
    expect(calls).toEqual(
      Array.from({ length: 2 }, () => ({
        task: "Coordinate validation",
        model: "muse-spark-open",
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
      await check(page.locator(".chat-agents > header")).toContainText("3 of 4 running");
      await check(leadRow(page, "A")).toContainText("3 agents · 1 working, 1 needs you");
      await check(leadRow(page, "B")).toContainText("1 plan needs you");
      const sizes = await page.evaluate(() =>
        [
          ".chat-lead-row",
          '.chat-agent-row[data-member="true"]',
          '.chat-agent-row[data-member="false"]',
        ].map((selector) => document.querySelector(selector)?.getBoundingClientRect().height),
      );
      for (const [i, target] of (width === 1920 ? [136, 107, 139] : [111, 62, 115]).entries()) {
        expect(Math.abs((sizes[i] ?? 0) - target)).toBeLessThanOrEqual(4);
      }
      await leadRow(page, "B").click();
      await check(page.getByRole("textbox", { name: "Message Lead B" })).toBeEditable();
    } finally {
      await page.close();
    }
  },
);
it.each([1920, 1280])(
  "shows the first-task field, observed limits and selected draft at %s",
  async (width) => {
    const page = await open("lead-new", width, "dark", true, false, true);
    try {
      await startForm(page);
      await check(page.locator(".chat-lead-draft")).toBeVisible();
      await check(page.locator(".chat-lead-draft")).toHaveAttribute("data-selected", "true");
      await check(page.getByRole("textbox", { name: "First task" })).toBeFocused();
      const model = page.getByRole("combobox", { name: "Model" });
      await check(model).toHaveValue("muse-spark");
      await check(model.locator("option")).toHaveText([
        "Muse Spark · low",
        "Muse Spark Open · low",
      ]);
      await check(page.locator(".new-lead-limit-cards")).toContainText("2,000 characters a task");
      const field = await page.locator(".new-lead-box textarea").boundingBox();
      expect(field?.width).toBe(width === 1920 ? 734 : 630);
      expect(field?.height).toBe(width === 1920 ? 112 : 76);
    } finally {
      await page.close();
    }
  },
);

const shots = fileURLToPath(new URL("../../../.shots/chat-leads/", import.meta.url));
const captureStates = ["chat-start", "task-direct", "leads-two", "lead-switch", "lead-new"];
for (const name of captureStates)
  for (const width of [1920, 1280])
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
  await page.screenshot({ path: `${shots}${basename}.png`, animations: "disabled" });
  await writeFile(`${shots}${basename}.json`, JSON.stringify(measured, null, 2));
  await writeFile(
    `${shots}events-${name}.json`,
    JSON.stringify(
      await page.evaluate(
        (name) => window.chatLeadsTest.prefix(name === "start-phone" ? "chat-start" : name, true),
        name,
      ),
    ),
  );
}
