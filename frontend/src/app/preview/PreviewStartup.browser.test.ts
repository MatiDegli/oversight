import { expect as check } from "@playwright/test";
import { afterAll, beforeAll, expect, it } from "vitest";
import {
  captureStartup,
  fixtureTexts,
  hostileFixture,
  hostileText,
  largeFixture,
  multilingualFixture,
  StartupBrowser,
  startupFixture,
  type StartupSession,
} from "./previewStartup.browser.support";

const support = new StartupBrowser();
beforeAll(() => support.start(), 60_000);
afterAll(() => support.close());
const invalidMessage = "The preview startup report is invalid, so reviewed-app previews stay off.";

async function safe(session: StartupSession) {
  const { page } = session;
  await check(page.locator("#preview-injected")).toHaveCount(0);
  await check(
    page.locator(
      ".preview-startup script, .preview-startup img, .preview-startup a, .preview-startup iframe, .preview-startup [onerror], .preview-startup [onclick]",
    ),
  ).toHaveCount(0);
  expect(
    await page.evaluate(() => Reflect.get(window, "previewInjected") as unknown),
  ).toBeUndefined();
  expect(session.pageErrors).toEqual([]);
  expect(session.dialogs).toEqual([]);
  expect(session.unexpectedRequests).toEqual([]);
  expect(session.navigations).toEqual([page.url()]);
}

it.each(["valid", "large", "missing", "invalid", "oversized", "hostile"] as const)(
  "reads the initial %s fixture index through real Settings navigation",
  async (scenario) => {
    const session = await support.open(scenario);
    const { page } = session;
    try {
      const panel = page.getByRole("region", { name: "Preview startup", exact: true });
      await check(page.getByRole("heading", { name: "Merge mode", exact: true })).toHaveCount(1);
      await check(page.getByRole("heading", { name: "Preview startup", exact: true })).toHaveCount(
        1,
      );
      await check(panel.locator("button, a, details, [autofocus]")).toHaveCount(0);
      expect(await panel.evaluate((element) => element.parentElement?.scrollTop)).toBe(0);
      const text = fixtureTexts[scenario];
      expect(await page.locator("#oversight-preview-startup").count()).toBe(
        text === undefined ? 0 : 1,
      );
      if (text !== undefined)
        expect(await page.locator("#oversight-preview-startup").textContent()).toBe(text);
      if (scenario === "valid" || scenario === "large" || scenario === "hostile") {
        const bytes = new TextEncoder().encode(text).byteLength;
        expect(bytes).toBeLessThanOrEqual(4096);
        if (scenario === "large") expect(bytes).toBeGreaterThan(1024);
        const report =
          scenario === "valid"
            ? startupFixture
            : scenario === "large"
              ? largeFixture
              : hostileFixture;
        const values: [string, string][] = [
          ["Version", "1"],
          ["Working directory", report.cwd],
          ["Selection reason", report.reason],
          ["Limits", report.limits],
          ["Platform residual", report.platform_residual],
          ["Build log", report.build_log],
          ["Enable hint", report.enable_hint],
          ["Agents", report.agents_label],
        ];
        for (const [label, value] of values) {
          const row = panel
            .locator(".preview-startup-row")
            .filter({ has: page.getByText(label, { exact: true }) });
          expect(await row.locator("dd").textContent()).toBe(value);
        }
        const commands = panel.locator("ol > li");
        expect(await commands.count()).toBe(report.argv.length);
        for (const [index, command] of report.argv.entries())
          expect(await commands.nth(index).locator("code").allTextContents()).toEqual(command);
        expect(await panel.locator("ul > li").allTextContents()).toEqual(report.dependencies);
        if (scenario === "hostile") expect(await panel.textContent()).toContain(hostileText);
      } else {
        await check(panel.locator("p")).toHaveText(
          scenario === "missing" ? "Not enabled on this server." : invalidMessage,
        );
        await check(panel.locator("dl")).toHaveCount(0);
      }
      await safe(session);
    } finally {
      await page.close();
    }
  },
);

it("keeps the startup report frozen across Settings close, document edits and reopen", async () => {
  const session = await support.open("valid");
  const { page } = session;
  try {
    await page.getByRole("button", { name: "Close tab Settings", exact: true }).click();
    await page.locator("#oversight-preview-startup").evaluate((element) => {
      element.textContent = "{}";
    });
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await check(page.getByRole("region", { name: "Preview startup", exact: true })).toContainText(
      startupFixture.reason,
    );
    await safe(session);
  } finally {
    await page.close();
  }
});

it.each([
  [1280, 800],
  [390, 844],
])("keeps mixed-script values reachable at 200%% magnification at %i×%i", async (width, height) => {
  const session = await support.open("multilingual");
  const { page } = session;
  try {
    expect(new TextEncoder().encode(fixtureTexts.multilingual).byteLength).toBeLessThanOrEqual(
      4096,
    );
    await page.getByRole("button", { name: "Maximize focus panel", exact: true }).click();
    await page.setViewportSize({ width, height });
    const panel = page.getByRole("region", { name: "Preview startup", exact: true });
    await panel.evaluate((element) => {
      // Magnify fixed-pixel theme type too; changing the root font size would leave it unchanged.
      element.style.zoom = "2";
    });
    expect(await panel.locator("code.preview-startup-token").allTextContents()).toEqual(
      multilingualFixture.argv[0],
    );
    const values = panel.locator(".preview-startup-row > dd");
    for (const index of [2, 3, 5, 6, 7, 8, 9])
      expect(await values.nth(index).textContent()).toBe(multilingualFixture.cwd);
    expect(await panel.locator("ul > li").allTextContents()).toEqual(
      multilingualFixture.dependencies,
    );
    const geometry = await panel.evaluate((element) => {
      const frame = element.parentElement;
      const end = element.querySelector(".preview-startup-row:last-child dd");
      if (!frame || !end) throw new Error("Missing startup disclosure or scroll frame");
      frame.scrollTop = frame.scrollHeight;
      return {
        overflow: Math.max(
          frame.scrollWidth - frame.clientWidth,
          element.scrollWidth - element.clientWidth,
          ...[...element.querySelectorAll("dd, code, li")].map(
            (node) => node.scrollWidth - node.clientWidth,
          ),
        ),
        scrolled: frame.scrollTop,
        end: end.getBoundingClientRect().bottom,
        bottom: frame.getBoundingClientRect().bottom,
      };
    });
    expect(geometry.overflow).toBeLessThanOrEqual(1);
    expect(geometry.scrolled).toBeGreaterThan(0);
    expect(geometry.end).toBeLessThanOrEqual(geometry.bottom);
    await safe(session);
  } finally {
    await page.close();
  }
});

for (const scenario of ["valid", "missing", "invalid"] as const)
  for (const [width, height] of [
    [1920, 1080],
    [1280, 800],
    [390, 844],
  ] as const)
    for (const theme of ["dark", "day"])
      it(`captures ${scenario} fixture at ${String(width)}×${String(height)} ${theme}`, async () => {
        const session = await support.open(scenario);
        try {
          const overflow = await captureStartup(session, scenario, { width, height }, theme);
          expect(overflow.page).toBeLessThanOrEqual(1);
          expect(overflow.panel).toBeLessThanOrEqual(1);
          expect(overflow.values).toBeLessThanOrEqual(1);
          if (width === 390 && scenario === "valid") {
            const longArgument = session.page.locator(".preview-startup-token").last();
            expect(
              await longArgument.evaluate((element) => element.getBoundingClientRect().height),
            ).toBeGreaterThan(40);
          }
          await safe(session);
        } finally {
          await session.page.close();
        }
      });
