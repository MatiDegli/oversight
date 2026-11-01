import { fileURLToPath } from "node:url";
import { expect } from "@playwright/test";
import type { Browser } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/lead-actions-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><div id="root"></div><script type="module">import "/src/theme/index.css";import "/src/app/leadActions.browser.fixture.tsx";</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

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
      await page.route("**/", (r) => r.fulfill({ contentType: "text/html", body: html }));
      await page.route("**/events?*", (r) =>
        r.fulfill({ contentType: "text/event-stream", body: ": connected\n\n" }),
      );
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
        page.evaluate(() => {
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
      await cart.getByRole("button", { name: "Reject plan" }).click();
      const comment = cart.getByLabel("Comment, saved with the plan (optional)");
      await comment.fill("Do this after the order work.");
      await comment.press("Control+Enter");
      expect(requests).toHaveLength(0);
      await cart.getByRole("button", { name: "Keep plan" }).click();
      expect(requests).toHaveLength(0);
      await cart.getByRole("button", { name: "Reject plan" }).click();
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
      const queued = inbox.getByRole("region", { name: "Queued plans" });
      await expect(chip).toHaveAccessibleName("Decisions: 2 pending. Open decisions inbox");
      await expect(queued.locator("li")).toHaveCount(4);
      await expect(queued).toContainText("Needs 2 free slots; 2 of 2 slots are held.");
      await page.evaluate(() => {
        window.leadActions.update(
          { revision: 2, queued_task_id: "queue-orders" },
          { revision: 8, state: "blocked", detail: "Waiting for the spending brake to clear" },
        );
      });
      await expect(queued).toContainText("Waiting for the spending brake to clear");
      await queued.getByRole("button", { name: "Take out of the queue" }).click();
      await expect(queued.getByRole("status")).toContainText("queue cancellation evidence");
      expect(requests.at(-1)).toEqual({
        path: "/queue/queue-orders/cancel",
        body: { revision: 8 },
      });
      expect(requests).toHaveLength(4);
      await page.evaluate(() => {
        window.leadActions.emit([
          {
            seq: 0,
            ts: "",
            type: "queue.updated",
            payload: { ...window.leadActions.queueItem, revision: 9, state: "cancelled" },
          },
        ]);
      });
      await expect(chip).toHaveAccessibleName("Decisions: 2 pending. Open decisions inbox");
      await page.evaluate(() => {
        window.leadActions.update({ revision: 3, queued_task_id: null });
      });
      await expect(chip).toHaveAccessibleName("Decisions: 3 pending. Open decisions inbox");
      await expect(orders.getByRole("button", { name: "Queue plan" })).toBeEnabled();
      await page.evaluate(() => {
        window.leadActions.update(
          { revision: 4, queued_task_id: "queue-orders" },
          { revision: 10, state: "starting", agent_ids: ["agent-1", "agent-2"] },
        );
      });
      await expect(queued).toContainText("Starting");
      await expect(queued.getByRole("button")).toHaveCount(0);
      await page.evaluate(() => {
        window.leadActions.update(
          { revision: 5, state: "failed", detail: "The dispatch could not start" },
          { revision: 11, state: "failed", detail: "The dispatch could not start" },
        );
      });
      const failed = inbox.getByRole("region", { name: "Ended without running" });
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
      await page.evaluate(() => {
        window.leadActions.readonly();
      });
      await expect(page.getByText(/^Replay of a hand-written fixture:/).first()).toBeVisible();
      await expect(
        page.getByRole("button", { name: /Queue plan|Reject plan|Take out/ }),
      ).toHaveCount(0);
      expect(errors).toEqual([]);
    } finally {
      await page.close();
    }
  },
  60_000,
);
