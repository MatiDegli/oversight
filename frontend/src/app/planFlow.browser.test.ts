import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
// eslint-disable-next-line no-restricted-imports -- Read-only production graph probe for event-arrival invariants.
import type { GraphProbeHost } from "../graph/probe";
import type * as fixture from "./planFlow.browser.fixture";
import type { FlowLayout, FlowScene } from "./planFlow.browser.fixture";
import { writeCaptureMatrix } from "./planFlowEvidence.testSupport";

declare global {
  interface Window {
    planFlowTest: typeof fixture;
  }
}
let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/plan-flow/vite-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><head><meta charset="utf-8"></head><body><script type="module">import "/src/theme/index.css";import * as fixture from "/src/app/planFlow.browser.fixture.tsx";window.planFlowTest=fixture;fixture.mountPlanFlow();</script></body></html>',
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
  scene: FlowScene,
  width = 1280,
  theme = "dark",
  layout: FlowLayout = "Chat",
  readonly = false,
) {
  const page = await browser.newPage({
    viewport: { width, height: width === 390 ? 844 : 1000 },
    reducedMotion: "reduce",
  });
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.goto(url);
  await page.waitForFunction(() => Boolean(window.planFlowTest));
  await page.evaluate(
    ({ scene, layout, readonly, theme }) => {
      document.documentElement.dataset.theme = theme;
      window.planFlowTest.mountPlanFlow(scene, layout, readonly);
    },
    { scene, layout, readonly, theme },
  );
  await check(page.locator(".plan-flow").first()).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  await page.addStyleTag({
    content: "*, *::before, *::after { animation: none !important; transition: none !important; }",
  });
  return page;
}

it.each(["Chat", "Oversight"] as const)(
  "renders sequence-placed plan entries in the %s lead thread without moving focus",
  async (layout) => {
    const page = await open("proposed", 1280, "dark", layout);
    try {
      const card = page.getByRole("article", { name: "Plan plan-checkout", exact: true });
      await check(card).toBeVisible();
      await check(
        card.getByRole("heading", { name: "Input checks across the checkout flow", exact: true }),
      ).toBeVisible();
      await check(card.getByText("Plan", { exact: true })).toHaveCount(1);
      await check(card.getByText("plan-checkout", { exact: true })).toHaveCount(0);
      await check(page.locator(".conversation-entries")).toContainText("I drafted a focused plan.");
      await check(card.locator(".flow-wave")).toHaveCount(2);
      await check(card.getByRole("button", { name: "Queue plan", exact: true })).toBeEnabled();
      const message = page.getByRole("textbox", { name: "Message Lead A" });
      await message.focus();
      const graphStage = page.locator(".graph-stage");
      const camera = async () =>
        layout === "Chat"
          ? null
          : page.locator("[aria-label='Repository graph']").evaluate((node: GraphProbeHost) => ({
              pan: node.oversightGraph?.pan(),
              zoom: node.oversightGraph?.zoom(),
            }));
      if (layout === "Oversight")
        await page.waitForFunction(() =>
          Boolean(
            document.querySelector<GraphProbeHost>("[aria-label='Repository graph']")
              ?.oversightGraph,
          ),
        );
      const beforeCamera = await camera();
      const graph = (await graphStage.count()) ? await graphStage.boundingBox() : null;
      await page.evaluate(() => {
        window.planFlowTest.dispatchPlanFlow();
      });
      await check(message).toBeFocused();
      await check(card.getByRole("button", { name: "Queue plan", exact: true })).toHaveCount(0);
      await check(card.locator(".plan-explanation")).toHaveCount(0);
      await check(card.locator(".plan-count")).toHaveText("4 subtasks · 0 merged");
      expect((await graphStage.count()) ? await graphStage.boundingBox() : null).toEqual(graph);
      expect(await camera()).toEqual(beforeCamera);
    } finally {
      await page.close();
    }
  },
);

it("shares pending attempts and observed dispatch between mounted plan copies", async () => {
  const page = await open("proposed", 1280, "dark", "copies");
  try {
    const queue = page.getByRole("button", { name: "Queue plan", exact: true });
    await check(queue).toHaveCount(2);
    await queue.first().click();
    for (const button of await queue.all()) await check(button).toBeDisabled();
    for (const button of await page.getByRole("button", { name: "Reject plan", exact: true }).all())
      await check(button).toBeDisabled();
    expect(await page.evaluate(() => window.planFlowTest.sent)).toEqual([
      { action: "queue", revision: 1, comment: "" },
    ]);
    await page.evaluate(() => {
      window.planFlowTest.dispatchPlanFlow();
    });
    await check(page.getByRole("button", { name: "Queue plan", exact: true })).toHaveCount(0);
    await check(page.getByRole("button", { name: "Reject plan", exact: true })).toHaveCount(0);
    await check(page.getByRole("heading", { name: "Running plans", exact: true })).toBeVisible();
    await check(page.getByRole("heading", { name: "You can keep watching" })).toHaveCount(0);
    await check(page.getByRole("button", { name: "Decisions 0", exact: true })).toBeVisible();
  } finally {
    await page.close();
  }
});

it("retains rejection comments, Keep plan and queue cancellation behavior", async () => {
  const page = await open("proposed");
  try {
    const card = page.locator(".thread-plan-card");
    await card.getByRole("button", { name: "Reject plan", exact: true }).click();
    const comment = card.getByRole("textbox", { name: "Comment, saved with the plan (optional)" });
    await check(comment).toBeFocused();
    await comment.fill("Keep the existing checkout boundary");
    await card.getByRole("button", { name: "Keep plan", exact: true }).click();
    await card.getByRole("button", { name: "Reject plan", exact: true }).click();
    await check(comment).toHaveValue("Keep the existing checkout boundary");
    await card.getByRole("button", { name: "Reject plan", exact: true }).click();
    expect(await page.evaluate(() => window.planFlowTest.sent[0])).toMatchObject({
      action: "reject",
      comment: "Keep the existing checkout boundary",
    });
    await page.evaluate(() => {
      window.planFlowTest.mountPlanFlow();
    });
    await page.evaluate(() => {
      window.planFlowTest.queuePlanFlow();
    });
    await card.getByRole("button", { name: "Take out of the queue" }).click();
    expect(await page.evaluate(() => window.planFlowTest.sent[0]?.action)).toBe("cancel");
    await page.evaluate(() => {
      window.planFlowTest.queuePlanFlow(true);
    });
    await check(card.getByRole("button", { name: "Queue plan", exact: true })).toBeEnabled();
  } finally {
    await page.close();
  }
});

it.each(["Chat", "Oversight"] as const)(
  "suppresses mutation controls in replay/history in %s",
  async (layout) => {
    const page = await open("proposed", 1280, "dark", layout, true);
    try {
      await check(page.locator(".plan-controls")).toHaveCount(0);
      await check(page.locator(".thread-plan-card")).toContainText(
        "Replay of a synthetic history prefix",
      );
      expect(await page.evaluate(() => window.planFlowTest.sent)).toEqual([]);
    } finally {
      await page.close();
    }
  },
);

it("keeps running plans outside decision counts and exposes failed combined evidence and navigation", async () => {
  const page = await open("inbox", 1280, "dark", "Oversight");
  try {
    const group = page.getByRole("region", { name: "Running plans", exact: true });
    await check(group).toBeVisible();
    await check(page.getByRole("button", { name: "Decisions 0", exact: true })).toBeVisible();
    await check(page.locator(".inbox-empty")).toHaveCount(0);
    await page.evaluate(() => {
      window.planFlowTest.mountPlanFlow("failed");
    });
    const failed = page.locator('[data-flow-index="2"]');
    await check(failed).toContainText("31 passed, 2 failed");
    await check(failed.locator(".flow-word")).toHaveText("Not merged");
    await check(failed.getByRole("img")).toHaveAccessibleName(
      /Combined tests.*failed: 31 passed, 2 failed/,
    );
    await failed.getByRole("button", { name: "Review change" }).click();
    await check(page.getByLabel("Review panel", { exact: true })).toBeVisible();
  } finally {
    await page.close();
  }
});

for (const width of [1280, 390])
  for (const theme of ["dark", "day"]) {
    it(`keeps assignment explicit before agent status at ${String(width)} in ${theme}`, async () => {
      const page = await open("running", width, theme);
      try {
        await page.evaluate(() => {
          window.planFlowTest.assignBeforeStatus();
        });
        const assigned = page.locator('[data-flow-index="2"]');
        await check(assigned).toContainText("Agent assigned · label not known");
        await check(assigned).not.toContainText("No agent assigned");
        await check(assigned.locator(".flow-rail")).toHaveAttribute(
          "aria-label",
          /Agent works: Agent assigned · status not known;/,
        );
        await check(page.locator('[data-flow-index="3"]')).toContainText("No agent assigned");
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        );
        if (process.env.PLAN_FLOW_SHOTS === "1")
          await capture(page, `assigned-${String(width)}-${theme}`, "running", width);
      } finally {
        await page.close();
      }
    });
    it(`wraps long text and eight subtasks without overflow at ${String(width)} in ${theme}`, async () => {
      const page = await open("large", width, theme);
      try {
        await check(page.locator(".flow-row")).toHaveCount(8);
        await check(page.locator(".flow-wave")).toHaveCount(3);
        await check(page.locator(".flow-step-key")).toContainText("Agent works");
        await check(page.locator(".flow-step-key")).toContainText("Combined tests, if main moved");
        const marks = page.locator(".flow-rail");
        for (const rail of await marks.all()) {
          await check(rail).toHaveAttribute(
            "aria-label",
            /Agent works:.*Proposal tests:.*Approval:.*Combined tests.*Merged:/,
          );
          await check(rail.locator(".flow-pip")).toHaveCount(5);
        }
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        );
        expect(
          await page.locator(".plan-flow").evaluate((node) => node.scrollWidth <= node.clientWidth),
        ).toBe(true);
        if (width === 390)
          for (const button of await page.locator(".plan-controls button").all()) {
            const bounds = await button.boundingBox();
            expect(bounds?.height).toBeGreaterThanOrEqual(44);
            expect(bounds?.width).toBeGreaterThanOrEqual(44);
          }
        else
          await check(page.getByRole("button", { name: "Queue plan", exact: true })).toHaveCSS(
            "height",
            "34px",
          );
        await page.evaluate(() => {
          window.planFlowTest.mountPlanFlow("long");
        });
        await check(page.locator(".flow-task").first()).toContainText("数量");
        expect(
          await page
            .locator(".thread-plan-card")
            .evaluate((node) => node.scrollWidth <= node.clientWidth),
        ).toBe(true);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        );
      } finally {
        await page.close();
      }
    });
  }

const shots = fileURLToPath(new URL("../../../.shots/plan-flow/", import.meta.url));
async function measure(page: Page) {
  return page.evaluate(() => {
    const selectors = [
      ".thread-plan-card",
      ".plan-flow",
      ".flow-row",
      ".flow-key",
      ".flow-task",
      ".flow-wave h4",
      ".plan-controls button",
      ".flow-review",
      ".conversation-header",
      ".conversation-timeline",
      '.plan-group[aria-label="Running plans"]',
    ];
    return {
      provenance: "Synthetic scripted event scene, rendered by production components",
      theme: document.documentElement.dataset.theme,
      layout: document.querySelector(".chat-shell") ? "Chat" : "Oversight",
      focusMaximized: Boolean(document.querySelector(".is-maximized")),
      width: innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      selectors: Object.fromEntries(
        selectors.map((selector) => [
          selector,
          [...document.querySelectorAll(selector)].map((node) => {
            const rect = node.getBoundingClientRect(),
              style = getComputedStyle(node);
            return {
              x: rect.x,
              y: rect.y,
              width: rect.width,
              height: rect.height,
              fontSize: style.fontSize,
              fontWeight: style.fontWeight,
              letterSpacing: style.letterSpacing,
              scrollWidth: node.scrollWidth,
              clientWidth: node.clientWidth,
            };
          }),
        ]),
      ),
    };
  });
}
async function capture(page: Page, basename: string, scene: FlowScene, width: number) {
  await page.evaluate(() => document.fonts.ready);
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
  const card = page.locator(".thread-plan-card, .inbox-row:has(.plan-flow)").first();
  // Explicit capture navigation only. Production event updates retain timeline ownership.
  const start = scene === "inbox" ? page.locator('.plan-group[aria-label="Running plans"]') : card;
  await start.evaluate((node) => {
    node.scrollIntoView({ block: "start" });
  });
  const measured = await measure(page);
  expect(measured.documentWidth).toBeLessThanOrEqual(width);
  await mkdir(shots, { recursive: true });
  if (width === 390)
    await page.evaluate(() => {
      window.scrollTo(0, 0);
    });
  if (scene === "inbox")
    await check(page.getByRole("heading", { name: "Running plans", exact: true })).toBeInViewport();
  await page.screenshot({ path: `${shots}${basename}.png`, animations: "disabled" });
  // Real scroll positions expose the bottom of tall cards without changing layout or clipping ancestors.
  await card
    .locator(".flow-row")
    .last()
    .evaluate((node) => {
      node.scrollIntoView({ block: "end" });
    });
  const controls = card.locator(".plan-controls");
  if (await controls.count())
    await controls.evaluate((node) => {
      node.scrollIntoView({ block: "end" });
    });
  await page.screenshot({ path: `${shots}${basename}-end.png`, animations: "disabled" });
  await writeFile(`${shots}${basename}.json`, JSON.stringify({ scene, ...measured }, null, 2));
}
for (const scene of ["proposed", "running", "gates", "failed", "inbox", "large"] as const)
  for (const width of [1280, 390])
    for (const theme of ["dark", "day"])
      it.skipIf(process.env.PLAN_FLOW_SHOTS !== "1")(
        `captures ${scene}-${String(width)}-${theme}`,
        async () => {
          const page = await open(
            scene,
            scene === "inbox" && width === 390 ? 1280 : width,
            theme,
            scene === "inbox" ? "Oversight" : "Chat",
          );
          try {
            if (scene === "inbox") {
              if (width === 390) {
                await page
                  .getByRole("button", { name: "Maximize focus panel", exact: true })
                  .click();
                await page.setViewportSize({ width, height: 844 });
              }
              await check(page.locator(".chat-shell")).toHaveCount(0);
              await check(
                page.getByRole("heading", { name: "Running plans", exact: true }),
              ).toBeVisible();
              await check(
                page.locator('.plan-group[aria-label="Running plans"] .plan-flow'),
              ).toBeVisible();
            }
            await capture(page, `${scene}-${String(width)}-${theme}`, scene, width);
          } finally {
            await page.close();
          }
        },
      );
for (const theme of ["dark", "day"])
  it.skipIf(process.env.PLAN_FLOW_SHOTS !== "1")(
    `captures dedicated phone Chat in ${theme}`,
    async () => {
      const page = await open("phone", 390, theme);
      try {
        await capture(page, `phone-390-${theme}`, "phone", 390);
      } finally {
        await page.close();
      }
    },
  );

it.skipIf(process.env.PLAN_FLOW_SHOTS !== "1")(
  "stages the complete capture matrix for delivery and re-inspection",
  async () => {
    const matrix = await writeCaptureMatrix(new URL("../../../.shots/plan-flow/", import.meta.url));
    expect(matrix.captures).toHaveLength(26);
  },
);

it("uses one-line rows above six when the built thread has room", async () => {
  const page = await open("large", 1920);
  try {
    const task = await page.locator(".flow-task").first().boundingBox();
    const condition = await page.locator(".flow-condition").first().boundingBox();
    if (!task || !condition) throw new Error("Missing dense row geometry");
    expect(Math.abs(task.y - condition.y)).toBeLessThanOrEqual(2);
    expect(
      await page.locator(".plan-flow").evaluate((node) => node.clientWidth),
    ).toBeGreaterThanOrEqual(680);
  } finally {
    await page.close();
  }
});
