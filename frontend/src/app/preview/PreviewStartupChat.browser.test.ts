import { expect as check } from "@playwright/test";
import { afterAll, beforeAll, expect, it } from "vitest";
import {
  capture,
  chatHostileFixture,
  chatSizes,
  StartupChatBrowser,
  type Session,
} from "./previewStartupChat.browser.support";
import { startupFixture } from "./previewStartup.browser.support";

const support = new StartupChatBrowser();
beforeAll(() => support.start(), 60_000);
afterAll(() => support.close());
const invalidMessage = "The preview startup report is invalid, so reviewed-app previews stay off.";

async function safe(session: Session) {
  const { page } = session;
  await check(
    page.locator(
      "#preview-injected, .preview-startup script, .preview-startup img, .preview-startup a, .preview-startup [onerror], .preview-startup [onclick]",
    ),
  ).toHaveCount(0);
  expect(
    await page.evaluate(() => Reflect.get(window, "previewInjected") as unknown),
  ).toBeUndefined();
  expect(session.pageErrors).toEqual([]);
  expect(session.unexpectedRequests).toEqual([]);
  expect(session.dialogs).toEqual([]);
  expect(session.navigations).toEqual([page.url()]);
  const duplicates = await page.evaluate(() => {
    const ids = [...document.querySelectorAll("[id]")].map((node) => node.id);
    return ids.filter((id, index) => ids.indexOf(id) !== index);
  });
  expect(duplicates).toEqual([]);
}

async function disclosure(session: Session) {
  const { page, scenario } = session;
  const composer = page.locator(".conversation-composer");
  const panel = composer.getByRole("region", { name: "Preview startup", exact: true });
  if (scenario === "missing") {
    await check(page.locator(".preview-startup")).toHaveCount(0);
    await check(composer.locator('[class*="max-h-"]')).toHaveCount(0);
    await check(page.getByText("Not enabled on this server.", { exact: true })).toHaveCount(0);
  } else if (scenario === "invalid" || scenario === "hostile-invalid") {
    await check(panel.locator("p")).toHaveText(invalidMessage);
    await check(page.getByText(invalidMessage, { exact: true })).toHaveCount(1);
  } else {
    const report = scenario === "valid" ? startupFixture : chatHostileFixture;
    await check(panel).toBeVisible();
    await check(panel.locator("dt")).toHaveCount(10);
    const fields: [string, string][] = [
      ["Version", "1"],
      ["Working directory", report.cwd],
      ["Selection reason", report.reason],
      ["Limits", report.limits],
      ["Platform residual", report.platform_residual],
      ["Build log", report.build_log],
      ["Enable hint", report.enable_hint],
      ["Agents", report.agents_label],
    ];
    for (const [label, value] of fields)
      expect(
        await panel
          .locator(".preview-startup-row")
          .filter({ has: page.getByText(label, { exact: true }) })
          .locator("dd")
          .textContent(),
      ).toBe(value);
    const commands = panel.locator("ol > li");
    expect(await commands.count()).toBe(report.argv.length);
    for (const [index, command] of report.argv.entries())
      expect(await commands.nth(index).locator("code").allTextContents()).toEqual(command);
    expect(await panel.locator("ul > li").allTextContents()).toEqual(report.dependencies);
  }
  await check(composer.locator("button").filter({ hasText: "Merge mode:" })).toBeVisible();
  await check(composer.getByRole("button", { name: /^Send/ })).toBeVisible();
  await check(panel.locator("button, a, details, [autofocus]")).toHaveCount(0);
}

it.each(["valid", "missing", "invalid", "hostile", "hostile-invalid"] as const)(
  "proves %s initial-index startup through coding-agent selection and the lead's production option",
  async (scenario) => {
    const session = await support.open(scenario);
    const { page } = session;
    try {
      expect(session.initialIndex).toContain(session.carrier || '<div id="root"');
      // The selected coding-agent row opens its actual conversation/composer, not the lead.
      await check(page.locator('.chat-agent-row[data-agent-origin="agent-1"]')).toHaveAttribute(
        "aria-pressed",
        "true",
      );
      await disclosure(session);
      await check(page.locator(".chat-preview-slot, iframe")).toHaveCount(0);
      expect(session.requests).toEqual([]);
      await session.lead();
      await disclosure(session);
      const draft = page.getByRole("textbox", { name: /Message Lead/ });
      await draft.fill("Keep this draft after startup and event arrival");
      await draft.focus();
      const geometry = await page.locator(".conversation-composer").boundingBox();
      session.arrive();
      await check(
        page.getByText(
          "The coupon change is ready to try. Check the shop, then review the change.",
          { exact: true },
        ),
      ).toHaveCount(2);
      await check(draft).toBeFocused();
      await check(draft).toHaveValue("Keep this draft after startup and event arrival");
      expect(await page.locator(".conversation-composer").boundingBox()).toEqual(geometry);
      await check(page.locator(".chat-preview-slot, iframe")).toHaveCount(0);
      expect(session.requests).toEqual([]);
      const preview = page.getByRole("button", { name: "Preview app", exact: true });
      const review = page.getByRole("button", { name: "Review change", exact: true });
      await check(review).toBeEnabled();
      if (scenario === "invalid" || scenario === "hostile-invalid") {
        await check(preview).toBeDisabled();
        const description = await preview.getAttribute("aria-describedby");
        expect(description).toBeTruthy();
        expect(await page.locator(`[id="${description ?? "missing"}"]`).textContent()).toContain(
          invalidMessage,
        );
        const box = await preview.boundingBox();
        if (!box) throw new Error("Missing disabled action geometry");
        await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
        await preview.press("Enter");
        await preview.press("Space");
        expect(session.requests).toEqual([]);
        await check(page.locator(".chat-preview-slot, iframe")).toHaveCount(0);
      } else {
        await check(preview).toBeEnabled();
        await preview.click();
        await check(page.locator(".chat-preview-slot")).toBeVisible();
        session.ready();
        await check(
          page.frameLocator("iframe").getByRole("heading", { name: "Fixture candidate shop" }),
        ).toBeVisible();
        expect(session.requests).toHaveLength(1);
        await page.locator(".preview-panel button").click();
        await check(page.locator(".chat-preview-slot, iframe")).toHaveCount(0);
      }
      await safe(session);
      const starts = session.requests.length;
      await review.press("Space");
      await check(
        page.getByRole("complementary", { name: "Review panel", exact: true }),
      ).toBeVisible();
      expect(session.requests).toHaveLength(starts);
      await safe(session);
    } finally {
      await session.close();
    }
  },
);

it("retains the separate Settings copy and keeps startup frozen across navigation", async () => {
  const session = await support.open("valid");
  const { page } = session;
  try {
    await disclosure(session);
    await page.locator("#oversight-preview-startup").evaluate((element) => {
      element.textContent = "{}";
    });
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await check(page.getByRole("heading", { name: "Merge mode", exact: true })).toBeVisible();
    await check(page.getByRole("region", { name: "Preview startup", exact: true })).toContainText(
      startupFixture.reason,
    );
    await safe(session);
    await page.getByRole("button", { name: "Close tab Settings", exact: true }).click();
    await page.locator('.chat-agent-row[data-agent-origin="agent-1"]').click();
    await disclosure(session);
    await safe(session);
  } finally {
    await session.close();
  }
});

it("lets keyboard users read the complete report and continue to composer controls", async () => {
  const session = await support.open("valid", { width: 390, height: 844 });
  const { page } = session;
  try {
    const details = page.getByRole("group", { name: "Conversation details", exact: true });
    await details.focus();
    await details.press("Tab");
    await page.keyboard.press("Shift+Tab");
    await check(details).toBeFocused();
    const outline = await details.evaluate((element) => {
      const style = getComputedStyle(element);
      return { width: style.outlineWidth, color: style.outlineColor, text: style.color };
    });
    expect(outline.width).toBe("2px");
    expect(outline.color).toBe(outline.text);
    await details.press("End");
    await check
      .poll(() =>
        details.evaluate(
          (element) => element.scrollHeight - element.scrollTop - element.clientHeight,
        ),
      )
      .toBeLessThanOrEqual(1);
    await check(details.locator(".preview-startup-row").last()).toBeInViewport();
    await details.press("Tab");
    await check(page.getByRole("textbox", { name: /Message Agent A/ })).toBeFocused();
    await safe(session);
  } finally {
    await session.close();
  }
});

for (const scenario of ["valid", "missing", "invalid"] as const)
  for (const viewport of chatSizes)
    for (const theme of ["dark", "day"])
      it(`captures Chat ${scenario} fixture at ${String(viewport.width)}×${String(viewport.height)} ${theme}`, async () => {
        const session = await support.open(scenario, viewport, theme);
        try {
          // Evidence survives subsequent assertion failures, including page errors/overflow.
          const overflow = await capture(session);
          expect(overflow).toBe(0);
          await disclosure(session);
          await safe(session);
          const composer = session.page.locator(".conversation-composer");
          const send = composer.getByRole("button", { name: /^Send/ });
          const box = await send.boundingBox();
          expect(box).not.toBeNull();
          if (box) expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);
          if (scenario === "valid" && viewport.width === 390) {
            const panel = composer.locator(".preview-startup");
            const reachable = await panel.evaluate((element) => {
              const scroll = element.parentElement?.parentElement;
              const last = element.querySelector(".preview-startup-row:last-child");
              if (!scroll || !last) throw new Error("Missing disclosure scroll frame");
              scroll.scrollTop = scroll.scrollHeight;
              return last.getBoundingClientRect().bottom <= scroll.getBoundingClientRect().bottom;
            });
            expect(reachable).toBe(true);
            expect(
              await composer
                .locator(".preview-startup-token")
                .last()
                .evaluate((element) => element.getBoundingClientRect().height),
            ).toBeGreaterThan(40);
          }
        } finally {
          await session.close();
        }
      });
