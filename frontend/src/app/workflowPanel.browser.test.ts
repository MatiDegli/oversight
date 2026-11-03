import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
// eslint-disable-next-line no-restricted-imports -- Read-only graph ownership probe.
import type { GraphProbeHost } from "../graph/probe";
import type * as fixture from "./workflowPanel.browser.fixture";
import type { WorkflowOptions } from "./workflowPanel.browser.fixture";

declare global {
  interface Window {
    workflowTest: typeof fixture;
  }
}
let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/workflow-panel/vite-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><head><meta charset="utf-8"></head><body><script type="module">import "/src/theme/index.css";import * as fixture from "/src/app/workflowPanel.browser.fixture.tsx";window.workflowTest=fixture;await fixture.mountWorkflow();</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

async function open(options: WorkflowOptions = {}, width = 1280, theme = "dark") {
  const page = await browser.newPage({
    viewport: { width, height: width === 390 ? 844 : 900 },
    reducedMotion: "reduce",
  });
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.goto(url);
  await page.waitForFunction(() => Boolean(window.workflowTest));
  await page.evaluate(
    async ({ options, theme }) => {
      document.documentElement.dataset.theme = theme;
      await window.workflowTest.mountWorkflow(options);
    },
    { options, theme },
  );
  if (width === 390 && options.layout === "Oversight")
    await check(page.locator(".narrow-notice")).toBeVisible();
  else
    await check(
      page.getByRole("button", { name: "Open workflow", exact: true }).first(),
    ).toBeVisible();
  return page;
}
const panel = (page: Page) => page.getByLabel("Workflow", { exact: true });
const steps = (page: Page) =>
  panel(page).getByRole("list", { name: "Workflow steps" }).getByRole("button");

it.each([
  { layout: "Chat" },
  { layout: "Oversight" },
  { layout: "Oversight", inbox: true },
] as const)("opens explicitly from %j and restores the originating launcher", async (options) => {
  const page = await open(options);
  try {
    const graphRequests: string[] = [];
    page.on("request", (request) => {
      if (/\/src\/(graph\/|app\/shell\/OversightStage)/u.test(request.url()))
        graphRequests.push(request.url());
    });
    await check(panel(page)).toHaveCount(0);
    const launcher = page.getByRole("button", { name: "Open workflow", exact: true }).first();
    await launcher.scrollIntoViewIfNeeded();
    const origin = options.inbox
      ? page.locator(".inbox-entries")
      : page.locator(".conversation-timeline");
    const retained = await origin.elementHandle();
    const scroll = await origin.evaluate((node) => node.scrollTop);
    await launcher.click();
    await check(panel(page)).toBeVisible();
    await check(panel(page).getByRole("button", { name: "Close", exact: true })).toBeFocused();
    await check(steps(page)).toHaveCount(4);
    await check(steps(page).first()).toHaveAccessibleName(
      "1 Reject negative prices in Item Merged",
    );
    if (options.layout === "Chat") {
      expect(graphRequests).toEqual([]);
      await check(page.getByLabel("Repository graph", { exact: true })).toHaveCount(0);
      await check(page.locator(".conversation-panel")).toBeVisible();
    } else {
      await check(page.getByLabel("Repository graph", { exact: true })).toBeVisible();
      const graph = await page.locator(".graph-stage").boundingBox();
      const workflow = await panel(page).boundingBox();
      expect(graph && workflow && graph.x >= workflow.x + workflow.width).toBe(true);
      expect(graph?.width).toBeGreaterThanOrEqual(560);
    }
    await panel(page).getByRole("button", { name: "Close", exact: true }).click();
    await check(launcher).toBeFocused();
    expect(await origin.evaluate((node) => node.scrollTop)).toBe(scroll);
    expect(await retained.evaluate((node) => node.isConnected)).toBe(true);
  } finally {
    await page.close();
  }
});

it("traverses Close, all numbered steps and eligible detail actions with native keys", async () => {
  const page = await open();
  try {
    const launcher = page.getByRole("button", { name: "Open workflow", exact: true });
    await launcher.click();
    const close = panel(page).getByRole("button", { name: "Close", exact: true });
    await close.press("Tab");
    await check(steps(page).nth(0)).toBeFocused();
    await page.keyboard.press("Enter");
    await check(steps(page).nth(0)).toHaveAttribute("aria-pressed", "true");
    await page.keyboard.press("Tab");
    await page.keyboard.press("Space");
    await check(steps(page).nth(1)).toBeFocused();
    await check(steps(page).nth(1)).toHaveAttribute("aria-pressed", "true");
    const detail = panel(page).getByRole("region", { name: "Step detail" });
    await check(
      detail.getByRole("heading", { name: "Reject negative quantities in Cart" }),
    ).toBeVisible();
    await check(detail).toContainText("Team A");
    await check(detail).toContainText("flow-2");
    for (const index of [2, 3]) {
      await page.keyboard.press("Tab");
      await check(steps(page).nth(index)).toBeFocused();
    }
    await page.keyboard.press("Tab");
    const review = detail.getByRole("button", { name: "Review change" });
    await check(review).toBeFocused();
    await page.keyboard.press("Shift+Tab");
    await check(steps(page).nth(3)).toBeFocused();
    await page.keyboard.press("Escape");
    await check(panel(page)).toHaveCount(0);
    await check(launcher).toBeFocused();
    await launcher.click();
    await steps(page).nth(1).click();
    await review.click();
    await check(panel(page)).toHaveCount(0);
    await check(page.getByLabel("Review panel", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => window.workflowTest.navigation)).toEqual(["change-flow-2"]);
  } finally {
    await page.close();
  }
});

it("never opens, replaces, scrolls or focuses on arrival; missing selection falls back to Close", async () => {
  const page = await open({ scene: "large" });
  try {
    const launcher = page.getByRole("button", { name: "Open workflow", exact: true });
    await launcher.focus();
    await page.evaluate(() => {
      window.workflowTest.arrive();
    });
    await check(panel(page)).toHaveCount(0);
    await check(launcher).toBeFocused();
    await launcher.click();
    await steps(page).nth(3).click();
    await steps(page).nth(3).focus();
    await panel(page)
      .locator(".workflow-body")
      .evaluate((node) => {
        node.scrollTop = 80;
      });
    const position = await panel(page)
      .locator(".workflow-body")
      .evaluate((node) => node.scrollTop);
    const selected = await steps(page).nth(3).elementHandle();
    await page.evaluate(() => {
      window.workflowTest.arrive();
    });
    await check(steps(page)).toHaveCount(8);
    await check(steps(page).nth(3)).toHaveAttribute("aria-pressed", "true");
    await check(steps(page).nth(3)).toBeFocused();
    expect(await selected.evaluate((node) => node.isConnected)).toBe(true);
    expect(
      await panel(page)
        .locator(".workflow-body")
        .evaluate((node) => node.scrollTop),
    ).toBe(position);
    await page.evaluate(() => {
      window.workflowTest.removeSelected();
    });
    await check(steps(page)).toHaveCount(2);
    await check(panel(page).getByText("Select a step to see its evidence.")).toBeVisible();
    await check(panel(page).getByRole("button", { name: "Close", exact: true })).toBeFocused();
    await check(panel(page).getByRole("button", { pressed: true })).toHaveCount(0);
  } finally {
    await page.close();
  }
});

it("keeps the graph instance, camera and docks while workflow evidence refreshes", async () => {
  const page = await open({ layout: "Oversight" });
  try {
    await page.waitForFunction(() =>
      Boolean(
        document.querySelector<GraphProbeHost>("[aria-label='Repository graph']")?.oversightGraph,
      ),
    );
    const graph = await page
      .getByLabel("Repository graph", { exact: true })
      .evaluateHandle((node: GraphProbeHost) => node.oversightGraph);
    await page.getByRole("button", { name: "Open workflow", exact: true }).click();
    await page.getByRole("button", { name: "Toggle bottom dock", exact: true }).click();
    const camera = await graph.evaluate((cy) => ({ pan: cy?.pan(), zoom: cy?.zoom() }));
    await page.evaluate(() => {
      window.workflowTest.arrive();
    });
    expect(await graph.evaluate((cy) => ({ pan: cy?.pan(), zoom: cy?.zoom() }))).toEqual(camera);
    expect(
      await graph.evaluate(
        (cy) =>
          cy ===
          document.querySelector<GraphProbeHost>("[aria-label='Repository graph']")?.oversightGraph,
      ),
    ).toBe(true);
    await check(page.getByLabel("Bottom dock", { exact: true })).toBeVisible();
  } finally {
    await page.close();
  }
});

it.each(["dark", "day"])(
  "maximizes phone Chat explicitly and restores reading and focus in %s",
  async (theme) => {
    const page = await open({}, 390, theme);
    try {
      const launcher = page.getByRole("button", { name: "Open workflow", exact: true });
      await launcher.scrollIntoViewIfNeeded();
      const chat = page.locator(".chat-shell");
      const before = await chat.evaluate((node) => node.scrollTop);
      await launcher.click();
      await check(page.getByRole("dialog", { name: "Workflow", exact: true })).toBeVisible();
      await check(panel(page)).toHaveAttribute("aria-modal", "true");
      const back = panel(page).getByRole("button", { name: "Back", exact: true });
      await check(back).toBeFocused();
      const bounds = await panel(page).boundingBox();
      expect(bounds).toEqual({ x: 0, y: 0, width: 390, height: 844 });
      expect(await page.locator(".chat-agents").evaluate((node: HTMLElement) => node.inert)).toBe(
        true,
      );
      expect(await chat.evaluate((node) => node.scrollTop)).toBe(before);
      await back.press("Tab");
      await check(steps(page).first()).toBeFocused();
      await page.keyboard.press("Enter");
      await page.keyboard.press("Tab");
      await page.keyboard.press("Space");
      await check(steps(page).nth(1)).toHaveAttribute("aria-pressed", "true");
      await back.focus();
      await back.press("Shift+Tab");
      await check(panel(page).getByRole("button", { name: "Review change" })).toBeFocused();
      await page.keyboard.press("Tab");
      await check(back).toBeFocused();
      await back.click();
      await check(panel(page)).toHaveCount(0);
      await check(launcher).toBeFocused();
      expect(await chat.evaluate((node) => node.scrollTop)).toBe(before);
      expect(await page.locator(".chat-agents").evaluate((node: HTMLElement) => node.inert)).toBe(
        false,
      );
      await check(page.getByLabel("Repository graph", { exact: true })).toHaveCount(0);
    } finally {
      await page.close();
    }
  },
);

it("phone updates keep selection and focus, and a removed step returns focus to Back", async () => {
  const page = await open({ scene: "large" }, 390);
  try {
    const launcher = page.getByRole("button", { name: "Open workflow", exact: true });
    await launcher.focus();
    await page.evaluate(() => {
      window.workflowTest.arrive();
    });
    await check(panel(page)).toHaveCount(0);
    await check(launcher).toBeFocused();
    await launcher.click();
    await steps(page).nth(3).click();
    const scroll = await panel(page)
      .locator(".workflow-body")
      .evaluate((node) => node.scrollTop);
    await page.evaluate(() => {
      window.workflowTest.arrive();
    });
    await check(steps(page).nth(3)).toBeFocused();
    await check(steps(page).nth(3)).toHaveAttribute("aria-pressed", "true");
    expect(
      await panel(page)
        .locator(".workflow-body")
        .evaluate((node) => node.scrollTop),
    ).toBe(scroll);
    await page.evaluate(() => {
      window.workflowTest.removeSelected();
    });
    await check(panel(page).getByRole("button", { name: "Back", exact: true })).toBeFocused();
    await page.keyboard.press("Escape");
    await check(panel(page)).toHaveCount(0);
    await check(launcher).toBeFocused();
  } finally {
    await page.close();
  }
});

it("keeps Oversight's widen-window notice at 390", async () => {
  const page = await open({ layout: "Oversight" }, 390);
  try {
    await check(page.locator(".narrow-notice")).toContainText("Widen it to at least 1280 px");
    await check(page.locator(".workflow-launch")).toBeHidden();
    await check(panel(page)).toHaveCount(0);
  } finally {
    await page.close();
  }
});

it("wraps full long-task evidence on the phone while keeping the node face compact", async () => {
  const page = await open({ scene: "long" }, 390);
  try {
    await page.getByRole("button", { name: "Open workflow", exact: true }).click();
    const label = await steps(page).first().locator("span").nth(1).innerText();
    expect(Array.from(label).length).toBeLessThanOrEqual(48);
    expect(label.endsWith("…")).toBe(true);
    await steps(page).first().click();
    await check(
      panel(page).getByRole("heading", { name: /Validate the extraordinarily long/ }),
    ).toContainText("acción, 数量, 🛒");
    await check(panel(page).getByRole("button", { name: "Review change" })).toHaveCount(0);
    expect(await panel(page).evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
  } finally {
    await page.close();
  }
});

it("withdraws Review navigation without dropping focus when a decision arrives", async () => {
  const page = await open();
  try {
    await page.getByRole("button", { name: "Open workflow", exact: true }).click();
    await steps(page).nth(1).click();
    const review = panel(page).getByRole("button", { name: "Review change" });
    await review.focus();
    await page.evaluate(() => {
      window.workflowTest.settleReview();
    });
    await check(review).toBeFocused();
    await check(review).toHaveAttribute("aria-disabled", "true");
    await review.press("Enter");
    expect(await page.evaluate(() => window.workflowTest.navigation)).toEqual([]);
    await review.press("Tab");
    await check(review).toHaveCount(0);
    await check(steps(page).nth(1)).toHaveAttribute("aria-pressed", "true");
  } finally {
    await page.close();
  }
});
