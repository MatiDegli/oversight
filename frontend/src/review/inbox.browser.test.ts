import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium, expect } from "@playwright/test";
import type { Browser } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, it } from "vitest";
let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/inbox-browser-cache", import.meta.url)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import {mountInbox,openInboxChange} from "/src/app/inbox.browser.fixture.tsx"; window.inboxPreview={mountInbox,openInboxChange};mountInbox();</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await chromium.launch();
}, 60000);
afterAll(async () => {
  await browser.close();
  await server.close();
});

it.each(["dark", "day"])(
  "keeps inbox, answers and grants inside the %s shell without commands",
  async (theme) => {
    const page = await browser.newPage({
      viewport: { width: 1920, height: 1080 },
      reducedMotion: "reduce",
    });
    const errors: string[] = [],
      commands: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("request", (request) => {
      if (request.method() !== "GET") commands.push(request.url());
    });
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    const shots = fileURLToPath(new URL("../../../.shots/inbox-answers/", import.meta.url));
    await mkdir(shots, { recursive: true });
    const capture = async (name: string) => {
      if (process.env.INBOX_SHOTS === "1") {
        await page.evaluate(() => document.fonts.ready);
        await page.waitForFunction(() => {
          const host = document.querySelector<
            HTMLElement & { oversightGraph?: { animating: () => boolean } }
          >('[aria-label="Repository graph"]');
          return host?.oversightGraph && !host.oversightGraph.animating();
        });
        await page.screenshot({ path: `${shots}/${name}-${theme}.png` });
      }
    };
    const mount = async (end?: number) =>
      page.evaluate((n) => {
        (
          window as unknown as { inboxPreview: { mountInbox: (end?: number) => void } }
        ).inboxPreview.mountInbox(n);
      }, end);
    try {
      await page.goto(url);
      await page.evaluate((t) => {
        document.documentElement.dataset.theme = t;
      }, theme);
      await mount();
      await page
        .getByRole("button", { name: "Decisions: 3 pending. Open decisions inbox" })
        .click();
      await expect(page.getByRole("region", { name: "Decisions inbox" })).toBeVisible();
      await expect(page.locator(".graph-stage")).toBeVisible();
      await expect(page.locator(".inbox-row")).toHaveCount(3);
      await expect(page.getByRole("button", { name: "Open Settings" })).toHaveCount(0);
      await expect(
        page.getByRole("button", { name: "Standing grants 1", exact: true }),
      ).toBeVisible();
      await capture("01-inbox");
      await page.getByText("Don't ask again", { exact: false }).first().click();
      await expect(page.getByRole("radio")).toHaveCount(2);
      await expect(page.getByRole("radio", { name: /This module shop.pricing/ })).toBeDisabled();
      await expect(page.getByRole("radio", { name: "This run", exact: true })).toBeDisabled();
      await capture("02-grant-choice");
      await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeDisabled();
      await expect(page.locator(".inbox-row").first().getByRole("status")).toHaveText(
        "Replay: nothing here sends a decision. The decision in the log shows when it arrives.",
      );
      await expect(
        page.getByRole("button", { name: "Decisions: 3 pending. Open decisions inbox" }),
      ).toBeVisible();
      await page.evaluate(() => {
        (
          window as unknown as { inboxPreview: { openInboxChange: (id: string) => void } }
        ).inboxPreview.openInboxChange("change-1");
      });
      await expect(page.getByRole("heading", { name: "Merged by your rules" })).toBeVisible();
      await expect(
        page.getByRole("heading", {
          name: "What was compared",
          exact: true,
        }),
      ).toBeVisible();
      const merged = page.getByRole("complementary", { name: "Review panel" });
      await expect(
        merged.getByRole("region", { name: "Review lead" }).getByText(/Under grant/),
      ).toContainText("grant-pricing");
      await expect(merged.getByText(/Under grant/)).toHaveText(
        "Under grant grant-pricing · Possible callers, not followed · This module · shop.pricing · given 15:03 ART.",
      );
      await expect(merged.getByRole("region", { name: "Decision", exact: true })).toHaveCount(0);
      await expect(merged.locator(".review-merged")).toHaveText("Merged into main merge-p");
      await capture("03-merged-grant");
      await page.getByRole("button", { name: /Decisions: 3/ }).click();
      const inboxRow = page.locator(".inbox-row").nth(1);
      await inboxRow.locator("summary").first().click();
      const card = inboxRow.getByRole("complementary", { name: "Change card" });
      await expect(
        card.getByRole("region", { name: "Compared functions and callers" }),
      ).toBeVisible();
      const comparison = card.getByRole("region", { name: "Compared functions and callers" });
      await expect(
        comparison.getByRole("heading", {
          name: "No other difference in what was compared · 1 function, 24 inputs",
          exact: true,
        }),
      ).toBeVisible();
      await expect(comparison.locator("h3 svg")).toHaveCount(0);
      const unmeasured = comparison
        .locator(".function-squares > span")
        .filter({ hasText: /^\?$/ })
        .first();
      await expect(unmeasured).toHaveAttribute("class", "");
      const colors = await unmeasured.evaluate((element) => {
        const probe = document.createElement("span");
        probe.style.color = "var(--muted)";
        probe.style.borderColor = "var(--control-edge)";
        element.parentElement?.append(probe);
        const actual = getComputedStyle(element),
          expected = getComputedStyle(probe);
        const square = {
          actual: [actual.color, actual.borderTopColor],
          expected: [expected.color, expected.borderTopColor],
        };
        probe.style.color = "var(--text)";
        const heading = element.closest("section")?.querySelector("h3");
        const result = {
          square,
          heading: heading ? getComputedStyle(heading).color : null,
          text: getComputedStyle(probe).color,
        };
        probe.remove();
        return result;
      });
      expect(colors.square.actual).toEqual(colors.square.expected);
      expect(colors.heading).toBe(colors.text);
      await expect(card.getByLabel(/Inventory.reserve: changed/)).toHaveAttribute(
        "aria-label",
        /questions 1, 2/,
      );
      await card.getByRole("button", { name: "Yes, keep it" }).first().click();
      await expect(card.getByText("1 of 2 answered")).toBeVisible();
      await page
        .locator(".inbox-row")
        .nth(1)
        .getByRole("button", { name: "Review change" })
        .click();
      const panel = page.getByRole("complementary", { name: "Review panel" });
      await expect(panel.getByText("1 of 2 answered")).toBeVisible();
      await expect(
        panel.getByRole("region", { name: "Compared functions and callers" }),
      ).toHaveCount(0);
      await expect(
        panel.getByRole("region", { name: "What was compared", exact: true }),
      ).toBeVisible();
      await expect(panel.locator(".review-details > details")).not.toHaveAttribute("open");
      await panel.getByRole("button", { name: "Change answer" }).click();
      await panel.getByRole("button", { name: "Yes, keep it" }).first().click();
      await expect(panel.getByRole("button", { name: "Change answer" })).toBeFocused();
      await expect(
        panel.getByText("Approve opens when every answer is Yes.", { exact: true }),
      ).toBeVisible();
      await expect(panel.getByRole("button", { name: "Approve and merge" })).toBeDisabled();
      await capture("04-choice");
      await panel.getByRole("button", { name: "No, send it back" }).click();
      await expect(
        panel.getByRole("button", { name: "Request changes", exact: true }),
      ).toBeDisabled();
      await expect(
        panel.getByRole("button", { name: "Request changes", exact: true }),
      ).not.toHaveClass(/decision-primary/);
      await expect(panel.getByRole("button", { name: "Approve and merge" })).toBeDisabled();
      await expect(panel.getByRole("textbox")).toHaveValue(/quantity=True|True/);
      await panel.getByRole("button", { name: "Change answer" }).last().click();
      await panel.getByRole("button", { name: "Unsure, decide later" }).click();
      await expect(
        panel.getByText("Approve opens when every answer is Yes.", { exact: true }),
      ).toBeVisible();
      await expect(panel.getByText("2 of 2 answered")).toBeVisible();
      await expect(panel.getByRole("button", { name: "Approve and merge" })).toBeDisabled();
      await panel.getByRole("button", { name: "Change answer" }).last().click();
      await panel.getByRole("button", { name: "Yes, keep it" }).click();
      await expect(panel.getByRole("button", { name: "Approve and merge" })).toBeDisabled();
      await expect(
        panel.getByRole("button", { name: "Request changes", exact: true }),
      ).toBeDisabled();
      await expect(panel.getByRole("button", { name: "Reject", exact: true })).toBeDisabled();
      await expect(panel.getByRole("textbox")).toHaveJSProperty("readOnly", true);
      await panel.getByRole("textbox").focus();
      await page.keyboard.press("Control+Enter");
      expect(commands).toEqual([]);
      await expect(panel.getByText("Approve prepared locally. No command sent.")).toHaveCount(0);
      await expect(panel.getByRole("button", { name: "Approve and merge" })).toBeDisabled();
      await page.getByRole("button", { name: /Decisions: 3/ }).click();
      await page
        .locator(".inbox-row")
        .nth(2)
        .getByRole("button", { name: "Review change" })
        .click();
      await expect(
        page.getByRole("heading", { name: "Needs you because a check was unavailable" }),
      ).toBeVisible();
      await expect(page.getByRole("button", { name: "Reject", exact: true })).toBeDisabled();
      await capture("05-unavailable");
      await page.getByRole("button", { name: /^Decisions:/ }).click();
      await page.getByRole("button", { name: /^Standing grants / }).click();
      await expect(page.getByRole("heading", { name: "Standing grants" })).toBeVisible();
      await expect(page.locator(".grant-row")).toHaveCount(1);
      await expect(page.locator(".grant-row")).toContainText("1 change let through");
      await capture("06-grants");
      await expect(page.getByRole("button", { name: "Revoke", exact: true })).toBeDisabled();
      await expect(page.getByText(/Preview/)).toHaveCount(0);
      await page.getByRole("button", { name: "Revoke", exact: true }).dispatchEvent("click");
      await expect(page.locator(".grant-row")).toHaveCount(1);
      await mount(14);
      await page.getByRole("button", { name: /^Decisions:/ }).click();
      await page.getByRole("button", { name: /^Standing grants / }).click();
      await expect(page.getByRole("heading", { name: "No active grants" })).toBeVisible();
      await capture("07-grants-empty");
      await page
        .getByRole("button", { name: "Decisions: 0 pending. Open decisions inbox" })
        .click();
      await page.getByRole("button", { name: "Decisions 0", exact: true }).click();
      await expect(page.getByRole("heading", { name: "No decisions waiting" })).toBeVisible();
      await expect(
        page.getByRole("button", { name: "Decisions: 0 pending. Open decisions inbox" }),
      ).toHaveText("Nothing needs you");
      await capture("08-inbox-empty");
      expect(commands).toEqual([]);
      expect(errors).toEqual([]);
    } finally {
      await page.close();
    }
  },
  60000,
);
