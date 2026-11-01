import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
let server: ViteDevServer, browser: Browser, html: string, url: string;
let replayServer: ViteDevServer, replayHtml: string, replayUrl: string;
beforeAll(async () => {
  for (const mode of ["development", "replay"]) {
    const instance = await createServer({
      root: fileURLToPath(new URL("../../", import.meta.url)),
      mode,
      cacheDir: fileURLToPath(
        new URL(`../../../.shots/lead-actions-${mode}-cache`, import.meta.url),
      ),
      plugins: [
        {
          name: "lead-actions-replay-session",
          enforce: "pre",
          resolveId(source, importer) {
            if (
              mode === "replay" &&
              source === "./session" &&
              importer?.endsWith("/src/app/App.tsx")
            )
              return "\0lead-actions-replay-session";
            return null;
          },
          load(id) {
            if (id === "\0lead-actions-replay-session")
              return "export function startAppSession() { return () => {}; }";
            return null;
          },
        },
      ],
      server: { host: "127.0.0.1", port: 0, strictPort: false },
    });
    const document = await instance.transformIndexHtml(
      "/",
      '<!doctype html><html><body><div id="root"></div><script type="module">import "/src/theme/index.css";import "/src/app/leadActions.browser.fixture.tsx";</script></body></html>',
    );
    await instance.listen();
    if (mode === "replay") {
      replayServer = instance;
      replayHtml = document;
      replayUrl = instance.resolvedUrls?.local[0] ?? "";
    } else {
      server = instance;
      html = document;
      url = instance.resolvedUrls?.local[0] ?? "";
    }
  }
  browser = await launchBrowser({
    webGL: process.env.LEAD_ACTIONS_SHOTS === "1",
    softwareRendering: true,
  });
}, 60_000);
afterAll(async () => {
  await browser.close();
  await Promise.all([server.close(), replayServer.close()]);
});

async function reachable(page: Page) {
  for (const action of await page.locator(".plan-controls button").all()) {
    await action.scrollIntoViewIfNeeded();
    await expect(action).toBeInViewport();
    const box = await action.boundingBox(),
      scroll = await page.locator(".inbox-entries").boundingBox();
    if (!box || !scroll) throw new Error("Missing action bounds");
    expect(box.y).toBeGreaterThanOrEqual(scroll.y);
    expect(box.y + box.height).toBeLessThanOrEqual(scroll.y + scroll.height);
  }
}
async function capture(page: Page, state: "plans" | "queued", width: number, theme: string) {
  if (process.env.LEAD_ACTIONS_SHOTS !== "1") return;
  await page.evaluate(() => document.fonts.ready);
  // Capture-only scrolling exposes the reference subject; normal access is proved by reachable.
  await page.locator(".inbox-entries").evaluate(
    (scroll, { state, width }) => {
      const subject =
        state === "queued"
          ? scroll.querySelector(".plan-group")
          : [...scroll.querySelectorAll("article")].find((row) =>
              row.textContent.includes("Reject bad quantities in the cart"),
            );
      scroll.scrollTop = subject
        ? subject.getBoundingClientRect().top -
          scroll.getBoundingClientRect().top +
          scroll.scrollTop -
          (width === 1280 ? (state === "queued" ? 0 : 60) : 100)
        : 0;
    },
    { state, width },
  );
  const metrics = await page
    .locator(".plan-content")
    .first()
    .evaluate((content) => {
      const style = (selector: string) => {
        const element = content.querySelector(selector);
        if (!element) throw new Error(`Missing ${selector}`);
        return getComputedStyle(element);
      };
      return {
        focus: document.querySelector(".focus-panel")?.getBoundingClientRect().width,
        gutter: getComputedStyle(content.closest("article") as HTMLElement).paddingInline,
        circle: style(".plan-circle").width,
        subtaskGap: style("li").columnGap,
        actionHeight: style("button").height,
        actionGap: style(".plan-controls").columnGap,
        taskSize: style("li p").fontSize,
        taskLine: style("li p").lineHeight,
      };
    });
  console.log("Lead actions measurements", { state, width, theme, ...metrics });
  expect(metrics).toEqual({
    focus: width === 1920 ? 880 : 608,
    gutter: "22px",
    circle: "20px",
    subtaskGap: "12px",
    actionHeight: "34px",
    actionGap: "8px",
    taskSize: "14px",
    taskLine: "19.6px",
  });
  const directory = fileURLToPath(new URL("../../../.shots/lead-actions/", import.meta.url));
  await mkdir(directory, { recursive: true });
  await page.screenshot({ path: `${directory}${state}-${String(width)}-${theme}.png` });
}

it.each([
  [1920, 1080, "dark"],
  [1920, 1080, "day"],
  [1280, 800, "dark"],
  [1280, 800, "day"],
] as const)(
  "event-owned plan actions at %s × %s, %s",
  async (width, height, theme) => {
    const page = await browser.newPage({ viewport: { width, height }, reducedMotion: "reduce" });
    const requests: { path: string; body: unknown }[] = [],
      loaded: string[] = [],
      errors: string[] = [];
    let refuse = true,
      hold = false,
      network = false;
    let release: (() => void) | undefined;
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("request", (r) => loaded.push(r.url()));
    try {
      await page.route("**/", (r) =>
        r.fulfill({
          contentType: "text/html",
          body: html.replace("<html>", `<html data-theme="${theme}">`),
        }),
      );
      // Hold the real SSE request idle; only fixture evidence advances this run.
      await page.route("**/events?*", () => undefined);
      await page.route(/\/(plans|queue)\/[^/]+\/(start|reject|cancel)$/, async (route) => {
        const path = new URL(route.request().url()).pathname;
        requests.push({ path, body: route.request().postDataJSON() });
        if (hold) {
          await new Promise<void>((resolve) => {
            release = resolve;
          });
          return route.fulfill({
            status: 409,
            json: { error: { code: "wrong_state", message: "Old run refusal" } },
          });
        }
        if (network) return route.abort();
        return path.endsWith("reject") && refuse
          ? route.fulfill({
              status: 409,
              json: { error: { code: "wrong_state", message: "The plan revision differs" } },
            })
          : route.fulfill({ status: 202 });
      });
      await page.goto(url);
      const chip = page.getByRole("button", { name: /^Decisions:/ });
      await expect(chip).toHaveAccessibleName("Decisions: 2 pending. Open decisions inbox");
      await page.evaluate((t) => {
        document.documentElement.dataset.theme = t;
      }, theme);
      await chip.click();
      await expect(page.locator(".inbox-heading h2")).toHaveCSS("font-size", "20px");
      await expect(page.locator(".inbox-heading h2")).toHaveCSS("font-weight", "700");
      await expect(page.locator(".inbox-heading h2")).toHaveCSS("line-height", "26px");
      expect(loaded.filter((u) => /\/(PlanActions|planActions)\./.test(u))).toEqual([]);
      await page.getByRole("button", { name: "Close tab Decisions" }).click();
      await chip.focus();
      await page.evaluate(() => {
        window.leadActions.propose();
      });
      await expect(chip).toHaveAccessibleName("Decisions: 4 pending. Open decisions inbox");
      await expect(chip).toBeFocused();
      await expect(page.getByRole("region", { name: "Decisions inbox" })).toHaveCount(0);
      expect(loaded.filter((u) => /\/(PlanActions|planActions)\./.test(u))).toEqual([]);
      await chip.click();
      const inbox = page.getByRole("region", { name: "Decisions inbox" });
      const cart = inbox.locator("article").filter({
        has: page.getByRole("heading", {
          name: "Reject bad quantities in the cart",
          exact: true,
        }),
      });
      const orders = inbox.locator("article").filter({
        has: page.getByRole("heading", { name: "Roll back failed orders", exact: true }),
      });
      await expect(cart.locator(".plan-piece li")).toHaveCount(2);
      await expect(orders.getByRole("button", { name: "Queue plan" })).toBeEnabled();
      await expect(inbox.locator("article").first()).toContainText("Inventory.reserve");
      expect(loaded.some((u) => u.includes("/PlanActions.tsx"))).toBe(true);
      const camera = () =>
        page.evaluate(async () => {
          // Let panel layout effects and ResizeObserver finish before reading the camera.
          await new Promise<void>((resolve) => {
            requestAnimationFrame(() => {
              requestAnimationFrame(() => {
                resolve();
              });
            });
          });
          const host = document.querySelector('[aria-label="Repository graph"]') as HTMLElement & {
            oversightGraph: { pan(): unknown; zoom(): number; positions(): unknown };
          };
          return {
            pan: host.oversightGraph.pan(),
            zoom: host.oversightGraph.zoom(),
            positions: host.oversightGraph.positions(),
          };
        });
      const unchanged = await camera();
      let closedCamera = unchanged;
      const closeForEvent = async () => {
        await page.getByRole("button", { name: "Close tab Decisions" }).click();
        await chip.focus();
        await expect(inbox).toHaveCount(0);
        closedCamera = await camera();
      };
      const checkClosedAndReopen = async () => {
        await expect(inbox).toHaveCount(0);
        await expect(chip).toBeFocused();
        expect(await camera()).toEqual(closedCamera);
        await chip.click();
      };
      await reachable(page);
      await cart.getByRole("button", { name: "Reject plan" }).focus();
      await page.keyboard.press("Enter");
      const comment = cart.getByLabel("Comment, saved with the plan (optional)");
      await expect(comment).toBeFocused();
      await comment.fill("Do this after the order work.");
      await comment.press("Control+Enter");
      await reachable(page);
      await capture(page, "plans", width, theme);
      expect(requests).toHaveLength(0);
      await page.keyboard.press("Tab");
      await page.keyboard.press("Tab");
      await expect(cart.getByRole("button", { name: "Keep plan" })).toBeFocused();
      await page.keyboard.press("Enter");
      await expect(cart.getByRole("button", { name: "Reject plan" })).toBeFocused();
      expect(requests).toHaveLength(0);
      await page.keyboard.press("Space");
      await expect(comment).toBeFocused();
      await expect(comment).toHaveValue("Do this after the order work.");
      await cart.getByRole("button", { name: "Reject plan" }).click();
      await expect(cart.getByRole("alert")).toHaveText("The plan revision differs");
      await expect(comment).toHaveValue("Do this after the order work.");
      expect(requests[0]?.body).toEqual({ revision: 1, comment: "Do this after the order work." });
      refuse = false;
      await cart
        .getByRole("button", { name: "Reject plan" })
        .evaluate((button: HTMLButtonElement) => {
          button.click();
          button.click();
        });
      await expect(cart.getByRole("status")).toContainText("plan rejection evidence");
      expect(requests).toHaveLength(2);
      await expect(chip).toHaveAccessibleName("Decisions: 4 pending. Open decisions inbox");
      await page.evaluate(() => {
        window.leadActions.emit([
          {
            seq: 0,
            ts: "",
            type: "plan.updated",
            payload: {
              ...window.leadActions.cartPlan,
              revision: 2,
              state: "rejected",
              subtasks:
                window.leadActions.cartPlan.subtasks?.map((s) => ({ ...s, state: "cancelled" })) ??
                null,
            },
          },
        ]);
      });
      await expect(chip).toHaveAccessibleName("Decisions: 3 pending. Open decisions inbox");
      await orders
        .getByRole("button", { name: "Queue plan" })
        .evaluate((button: HTMLButtonElement) => {
          button.click();
          button.click();
        });
      await expect(orders.getByRole("status")).toContainText("queue evidence");
      expect(requests.at(-1)).toEqual({ path: "/plans/plan-orders/start", body: { revision: 1 } });
      await expect(chip).toHaveAccessibleName("Decisions: 3 pending. Open decisions inbox");
      await closeForEvent();
      await page.evaluate(() => {
        const a = window.leadActions;
        a.update(
          {
            revision: 2,
            queued_task_id: a.queueItem.queued_task_id,
            subtasks: [...(a.orderPlan.subtasks ?? []), a.fourthTask],
          },
          {},
        );
      });
      const queued = inbox.getByRole("region", { name: "Queued, waiting for slots" });
      await expect(chip).toHaveAccessibleName("Decisions: 2 pending. Open decisions inbox");
      await checkClosedAndReopen();
      await expect(queued.locator("li")).toHaveCount(4);
      const groupHeading = queued.getByRole("heading", { name: "Queued, waiting for slots" });
      await expect(groupHeading).toHaveText("Queued, waiting for slots");
      await expect(groupHeading).toHaveCSS("font-size", "11.5px");
      await expect(groupHeading).toHaveCSS("font-weight", "600");
      await expect(groupHeading).toHaveCSS("letter-spacing", "0.92px");
      await expect(groupHeading).toHaveCSS("text-transform", "uppercase");
      const waitingLine = queued
        .locator(".plan-explanation")
        .filter({ hasText: "Waits for 2 of the 2 running agents to finish." });
      await expect(waitingLine).toHaveText("Waits for 2 of the 2 running agents to finish.");
      await expect(waitingLine.locator('svg.lucide-clock[aria-hidden="true"]')).toHaveCount(1);
      await reachable(page);
      await capture(page, "queued", width, theme);
      await page.evaluate(() => {
        window.leadActions.update(
          { revision: 2, queued_task_id: "queue-orders" },
          { revision: 8, state: "blocked", detail: "Waiting for the spending brake to clear" },
        );
      });
      await expect(queued).toContainText("Waiting for the spending brake to clear");
      await page.evaluate(() => {
        window.leadActions.emit([
          {
            seq: 0,
            ts: "",
            type: "queue.updated",
            payload: {
              ...window.leadActions.queueItem,
              revision: 9,
              state: "blocked",
              detail: null,
            },
          },
        ]);
      });
      await expect(queued).toContainText("Blocked");
      await expect(queued).not.toContainText("Waits for");
      await queued.getByRole("button", { name: "Take out of the queue" }).click();
      await expect(queued.getByRole("status")).toContainText("queue cancellation evidence");
      expect(requests.at(-1)).toEqual({
        path: "/queue/queue-orders/cancel",
        body: { revision: 9 },
      });
      expect(requests).toHaveLength(4);
      await closeForEvent();
      await page.evaluate(() => {
        window.leadActions.emit([
          {
            seq: 0,
            ts: "",
            type: "queue.updated",
            payload: { ...window.leadActions.queueItem, revision: 10, state: "cancelled" },
          },
        ]);
      });
      await expect(chip).toHaveAccessibleName("Decisions: 2 pending. Open decisions inbox");
      await checkClosedAndReopen();
      await page.evaluate(() => {
        window.leadActions.update({ revision: 3, queued_task_id: null });
      });
      await expect(chip).toHaveAccessibleName("Decisions: 3 pending. Open decisions inbox");
      await expect(orders.getByRole("button", { name: "Queue plan" })).toBeEnabled();
      await page.evaluate(() => {
        window.leadActions.update(
          { revision: 4, state: "starting", queued_task_id: "queue-orders" },
          { revision: 11, state: "starting", agent_ids: ["agent-1", "agent-2"] },
        );
      });
      await expect(queued).toContainText("Starting");
      await expect(queued.getByRole("button")).toHaveCount(0);
      await expect(queued.locator(".plan-piece li")).toHaveCount(3);
      await expect(chip).toHaveAccessibleName("Decisions: 2 pending. Open decisions inbox");
      await closeForEvent();
      await page.evaluate(() => {
        window.leadActions.update(
          { revision: 5, state: "failed", detail: "The dispatch could not start" },
          { revision: 12, state: "failed", detail: "The dispatch could not start" },
        );
      });
      await checkClosedAndReopen();
      const failed = inbox.getByRole("region", { name: "Ended without running" });
      const failedHeading = failed.getByRole("heading", { name: "Ended without running" });
      await expect(failedHeading).toHaveCSS("font-size", "11.5px");
      await expect(failedHeading).toHaveCSS("font-weight", "600");
      await expect(failedHeading).toHaveCSS("letter-spacing", "0.92px");
      await expect(failedHeading).toHaveCSS("text-transform", "uppercase");
      await expect(failed.locator("blockquote")).toHaveText("The dispatch could not start");
      await expect(failed.getByRole("button")).toHaveCount(0);
      expect(await camera()).toEqual(unchanged);
      await page.evaluate(() => {
        window.leadActions.restart();
      });
      await expect(orders.getByRole("button", { name: "Queue plan" })).toBeEnabled();
      hold = true;
      await orders.getByRole("button", { name: "Queue plan" }).click();
      await expect.poll(() => !!release).toBe(true);
      await page.evaluate(() => {
        window.leadActions.restart();
      });
      const oldResponse = page.waitForResponse((r) => r.status() === 409);
      hold = false;
      release?.();
      await oldResponse;
      await expect(orders.getByRole("button", { name: "Queue plan" })).toBeEnabled();
      await expect(orders.getByRole("alert")).toHaveCount(0);
      network = true;
      await orders.getByRole("button", { name: "Queue plan" }).click();
      await expect(orders.getByRole("alert")).toContainText("Queue plan did not reach the server");
      await page.evaluate(() => {
        window.leadActions.restart(1);
      });
      await expect(orders.getByRole("button", { name: "Queue plan" })).toBeDisabled();
      await expect(orders).toContainText("The first wave exceeds the 1 offered slots.");
      expect(errors).toEqual([]);
    } finally {
      await page.close();
    }
  },
  60_000,
);

it("keeps plan pieces readable through App's replay path without mutation requests", async () => {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const sent: string[] = [];
  let releaseChunk = () => {};
  const chunkGate = new Promise<void>((resolve) => {
    releaseChunk = resolve;
  });
  await page.route("**/src/review/PlanActions.tsx*", async (route) => {
    await chunkGate;
    await route.continue();
  });
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: replayHtml }));
  await page.route(/\/(plans|queue)\/[^/]+\/(start|reject|cancel)$/, (route) => {
    sent.push(new URL(route.request().url()).pathname);
    return route.fulfill({ status: 501 });
  });
  try {
    await page.goto(replayUrl);
    await page.getByRole("button", { name: /^Decisions:/ }).click();
    const inbox = page.getByRole("region", { name: "Decisions inbox" });
    await expect(inbox.getByText("Loading plan…")).toHaveCount(2);
    const cart = inbox.locator("article").filter({ hasText: "Reject bad quantities in the cart" });
    const rowLayout = async () => ({
      padding: await cart.evaluate((row) => getComputedStyle(row).padding),
      timeFont: await cart.locator("time").evaluate((time) => getComputedStyle(time).fontFamily),
      title: await cart.locator("h3").evaluate((title) => ({
        size: getComputedStyle(title).fontSize,
        weight: getComputedStyle(title).fontWeight,
      })),
    });
    const eagerLayout = await rowLayout();
    expect(eagerLayout).toEqual({
      padding: "12px 22px",
      timeFont: '"Atkinson Hyperlegible Mono", monospace',
      title: { size: "15px", weight: "600" },
    });
    await page.evaluate(() => {
      const a = window.leadActions;
      a.update({ revision: 2, queued_task_id: a.queueItem.queued_task_id }, {});
    });
    const group = inbox.getByRole("heading", { name: "Queued, waiting for slots" });
    const groupLayout = () =>
      group.evaluate((heading) => ({
        padding: getComputedStyle(heading).padding,
        color: getComputedStyle(heading).color,
      }));
    const eagerGroup = await groupLayout();
    expect(eagerGroup.padding).toBe("12px 22px 0px");
    expect(eagerGroup.color).toBe(
      await inbox
        .locator(".decision-muted")
        .first()
        .evaluate((p) => getComputedStyle(p).color),
    );
    releaseChunk();
    await expect(inbox.locator(".plan-piece li")).toHaveCount(5);
    expect(await rowLayout()).toEqual(eagerLayout);
    expect(await groupLayout()).toEqual(eagerGroup);
    await expect(inbox.getByText(/^Replay of a hand-written demo fixture:/).first()).toBeVisible();
    await expect(
      inbox.getByRole("button", { name: /Queue plan|Reject plan|Take out/ }),
    ).toHaveCount(0);
    await page.evaluate(() => {
      const a = window.leadActions;
      a.update({ revision: 2, queued_task_id: a.queueItem.queued_task_id }, {});
    });
    const queued = inbox.getByRole("region", { name: "Queued, waiting for slots" });
    await expect(queued.locator(".plan-piece li")).toHaveCount(3);
    await expect(queued.getByRole("button")).toHaveCount(0);
    await expect(inbox.locator(".plan-piece li")).toHaveCount(5);
    expect(sent).toEqual([]);
    await page.evaluate(() => {
      window.leadActions.update({
        revision: 3,
        queued_task_id: "queue-orders",
        task: "A long queued plan title that must wrap without running underneath its status pill ".repeat(
          3,
        ),
      });
    });
    const queuedRow = queued.locator("article");
    await expect(queuedRow.locator("h3")).toContainText("A long queued plan title");
    const separated = await queuedRow.evaluate((row) => {
      const title = row.querySelector("h3"),
        pill = row.querySelector(".plan-status");
      if (!title || !pill) throw new Error("Missing queued title or status");
      const range = document.createRange();
      range.selectNodeContents(title);
      return [...range.getClientRects()].every(
        (line) => line.right <= pill.getBoundingClientRect().left,
      );
    });
    expect(separated).toBe(true);
  } finally {
    releaseChunk();
    await page.close();
  }
});
