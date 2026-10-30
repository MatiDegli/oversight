import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium, expect, type Browser, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, it } from "vitest";
let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/inbox-stage2-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<html><body><script type="module">import "/src/theme/index.css"; import {mountInboxCommands,reviewTest} from "/src/app/review.browser.fixture.tsx";window.inboxCommands={mountInboxCommands,reviewTest};mountInboxCommands();</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await chromium.launch();
}, 60000);
afterAll(async () => {
  await browser.close();
  await server.close();
});
const mount = (page: Page, recorded = false, two = false) =>
  page.evaluate(
    ([recorded, two]) => {
      (
        window as unknown as {
          inboxCommands: { mountInboxCommands: (r: boolean, t: boolean) => void };
        }
      ).inboxCommands.mountInboxCommands(!!recorded, !!two);
    },
    [recorded, two],
  );
const inbox = (page: Page) => page.getByRole("button", { name: /^Decisions: 3/ }).click();
it.each(["dark", "day"])(
  "sends decisions and revokes in %s, retaining event-owned rows",
  async (theme) => {
    const page = await browser.newPage({
      viewport: { width: 1920, height: 1080 },
      reducedMotion: "reduce",
    });
    await page.route("**/", (r) => r.fulfill({ contentType: "text/html", body: html }));
    const posts: { path: string; body: unknown }[] = [];
    let refused = false;
    await page.route(/\/(changes|grants)\//, async (r) => {
      posts.push({ path: new URL(r.request().url()).pathname, body: r.request().postDataJSON() });
      await r.fulfill(
        refused
          ? {
              status: 409,
              json: {
                error: {
                  code: "invalid_request",
                  message: "Grant scope does not cover this change's measured possible callers",
                },
              },
            }
          : { status: 202 },
      );
    });
    const out = fileURLToPath(new URL("../../../.shots/inbox-stage2/", import.meta.url));
    const shot = async (name: string) => {
      if (process.env.INBOX_SHOTS !== "1") return;
      await mkdir(out, { recursive: true });
      await page.evaluate(() => document.fonts.ready);
      await page.waitForFunction(() => {
        const h = document.querySelector<
          HTMLElement & { oversightGraph?: { animating: () => boolean } }
        >('[aria-label="Repository graph"]');
        return h?.oversightGraph && !h.oversightGraph.animating();
      });
      await page.screenshot({ path: `${out}/${name}-${theme}.png` });
    };
    try {
      await page.goto(url);
      await page.evaluate((t) => {
        document.documentElement.dataset.theme = t;
      }, theme);
      await inbox(page);
      await page.getByRole("button", { name: "Standing grants 1", exact: true }).click();
      await expect(page.getByRole("button", { name: "Revoke", exact: true })).toBeEnabled();
      await expect(page.getByText(/Preview/)).toHaveCount(0);
      await shot("01-grants");
      await page.getByRole("button", { name: "Revoke", exact: true }).click();
      await expect(page.getByRole("button", { name: "Revoking…" })).toBeDisabled();
      await expect(page.locator(".grant-row")).toHaveCount(1);
      expect(posts.at(-1)).toEqual({ path: "/grants/grant-pricing/revoke", body: null });
      await page.evaluate(() => {
        const api = (
          window as unknown as { inboxCommands: { reviewTest: { emit: (e: unknown) => void } } }
        ).inboxCommands.reviewTest;
        api.emit({
          type: "grant.revoked",
          payload: { grant_id: "grant-pricing" },
          ts: "2026-10-29T18:08:00.000Z",
        });
      });
      await expect(page.locator(".grant-row")).toHaveCount(0);
      await mount(page);
      await inbox(page);
      await page.locator(".inbox-row").nth(1).locator("summary").first().click();
      let card = page.getByRole("complementary", { name: "Change card" });
      await expect(
        card.getByRole("heading", { name: /Was this behavior change asked for/ }),
      ).toBeVisible();
      await card.getByRole("button", { name: "Yes, keep it" }).first().click();
      await page
        .locator(".inbox-row")
        .nth(1)
        .getByRole("button", { name: "Review change" })
        .click();
      card = page.getByRole("complementary", { name: "Change card" });
      await expect(card.getByText("1 of 2 answered")).toBeVisible();
      await card.getByRole("button", { name: "Yes, keep it" }).click();
      const count = posts.length;
      await card.getByRole("button", { name: "Approve and merge" }).focus();
      await page.keyboard.press("Enter");
      await page.keyboard.press("Control+Enter");
      expect(posts).toHaveLength(count);
      await card.getByRole("button", { name: "Approve and merge" }).click();
      await expect(card.getByText("Approving: waiting for the merge result.")).toBeVisible();
      await expect(card.getByRole("button", { name: "Reject", exact: true })).toBeDisabled();
      await shot("02-approve-pending");
      expect(posts.at(-1)?.body).toMatchObject({
        answers: [
          { answer: "yes", answered_by: "person" },
          { answer: "yes", answered_by: "person" },
        ],
      });
      await inbox(page);
      await page.locator(".inbox-row").nth(1).locator("summary").first().click();
      await expect(page.getByText("Approving: waiting for the merge result.")).toBeVisible();
      await mount(page);
      refused = true;
      await inbox(page);
      await page.getByText("Don't ask again", { exact: false }).click();
      await expect(page.getByRole("radio", { name: /This module shop.pricing/ })).toBeEnabled();
      await page.getByRole("radio", { name: /This module/ }).check();
      await shot("04-module-name");
      await page.getByRole("button", { name: "Approve", exact: true }).click();
      await expect(page.getByRole("alert")).toHaveText(
        "Grant scope does not cover this change's measured possible callers",
      );
      await shot("03-refused-grant");
      expect(posts.at(-1)?.body).toMatchObject({
        grant: {
          reason: "possible_callers",
          scope: { kind: "module", value: "shop.pricing", expires: "run" },
        },
      });
      await expect(
        page.getByRole("button", { name: "Standing grants 1", exact: true }),
      ).toBeVisible();
      await page.getByRole("button", { name: "Standing grants 1", exact: true }).click();
      await page.getByRole("button", { name: "Revoke", exact: true }).click();
      await expect(page.getByRole("alert")).toHaveText(
        "Grant scope does not cover this change's measured possible callers",
      );
      await expect(page.locator(".grant-row")).toHaveCount(1);
      await expect(page.getByRole("button", { name: "Revoke", exact: true })).toBeEnabled();
      await mount(page, false, true);
      await inbox(page);
      await page.getByText("Don't ask again", { exact: false }).click();
      await expect(page.getByRole("radio", { name: /This module/ })).toBeDisabled();
      await expect(page.getByText("Module scope requires exactly one module's file")).toBeVisible();
      await page.getByRole("radio", { name: "This run", exact: true }).check();
      await page.getByRole("button", { name: "Approve", exact: true }).click();
      expect(posts.at(-1)?.body).toMatchObject({
        grant: { scope: { kind: "run", value: "run-20261029-180000", expires: "run" } },
      });
      await mount(page, true);
      await inbox(page);
      await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeDisabled();
      await page.getByRole("button", { name: "Standing grants 1", exact: true }).click();
      await expect(page.getByRole("button", { name: "Revoke", exact: true })).toBeDisabled();
      await expect(
        page.getByText(
          "Replay: nothing here sends a decision. The decision in the log shows when it arrives.",
        ),
      ).toBeVisible();
    } finally {
      await page.close();
    }
  },
  60000,
);
