import type { GraphProbeHost } from "../../graph";
import { readFile } from "node:fs/promises";
import { expect as check } from "@playwright/test";
import { expect, it } from "vitest";
import { previewChatSupport, chatSizes, chatDirectory } from "./previewChat.browser.support";
import { captureCheckpoint } from "./previewPanel.browser.support";
const support = previewChatSupport();
const matrix = chatSizes.flatMap((viewport) =>
  ["dark", "day"].map((theme) => ({ viewport, theme })),
);
it.each(["dark", "day"])("keeps Preview app visually primary in %s", async (theme) => {
  const session = await support.session(chatSizes[0], theme);
  try {
    const appearance = await session.page.locator(".lead-preview-actions").evaluate((node) => {
      const primary = node.querySelector(".lead-preview-primary");
      const secondary = node.querySelector(".lead-preview-secondary");
      if (!primary || !secondary) throw new Error("Missing preview actions");
      const token = (name: string) => {
        const probe = document.createElement("span");
        probe.style.color = `var(--${name})`;
        node.append(probe);
        const color = getComputedStyle(probe).color;
        probe.remove();
        return color;
      };
      return {
        primaryFill: getComputedStyle(primary).backgroundColor,
        primaryText: getComputedStyle(primary).color,
        secondaryFill: getComputedStyle(secondary).backgroundColor,
        secondaryText: getComputedStyle(secondary).color,
        secondaryBorder: getComputedStyle(secondary).borderStyle,
        text: token("text"),
        page: token("page"),
      };
    });
    expect(appearance.primaryFill).toBe(appearance.text);
    expect(appearance.primaryText).toBe(appearance.page);
    expect(appearance.secondaryFill).toBe("rgba(0, 0, 0, 0)");
    expect(appearance.secondaryText).toBe(appearance.text);
    expect(appearance.secondaryBorder).toBe("solid");
  } finally {
    await session.close();
  }
});
it("records the capture HEAD and working-tree state beside the Chat PNG", async () => {
  const session = await support.session();
  try {
    const checkpoint = await captureCheckpoint();
    const manifest = await support.capture(session, "option");
    expect(manifest).toMatchObject(checkpoint);
    expect(manifest.checkpoint).toMatch(/^[a-f0-9]{40}$/);
    const saved: unknown = JSON.parse(
      await readFile(`${chatDirectory}fixture/option-1920-dark.json`, "utf8"),
    );
    expect(saved).toEqual(manifest);
    expect(typeof (saved as Record<string, unknown>).workingTree).toBe("boolean");
  } finally {
    await session.close();
  }
});
it.each(matrix)(
  "proves App option/starting/shop/refusal/replay at $viewport.width in $theme",
  async ({ viewport, theme }) => {
    const session = await support.session(viewport, theme);
    const { page } = session;
    try {
      const option = page.getByRole("button", { name: "Preview app", exact: true });
      const review = page.getByRole("button", { name: "Review change", exact: true });
      await check(option).toBeEnabled();
      await check(review).toBeEnabled();
      await check(page.locator("iframe")).toHaveCount(0);
      const reply = page
        .locator("[role=listitem]")
        .filter({ hasText: "The coupon change is ready to try." });
      expect(await reply.count()).toBe(1);
      expect(
        await page
          .locator(".lead-preview-option")
          .evaluate((node) => node.closest("[role=listitem]")?.previousElementSibling?.textContent),
      ).toContain("The coupon change is ready to try.");
      await support.capture(session, "option");
      const composer = page.locator(".conversation-composer textarea");
      if (await composer.count()) await composer.fill("Keep this draft");
      await option.focus();
      const rail = await page.locator(".chat-agents").boundingBox();
      const original = await page.locator(".conversation-panel").boundingBox();
      const beforeOpenRenders = await page.evaluate(() => ({ ...window.previewChatRenders }));
      await option.press("Enter");
      await check(page.locator(".preview-panel-phase")).toContainText("App starting");
      await support.capture(session, "starting");
      const openedRenders = await page.evaluate(() => window.previewChatRenders);
      for (const key of ["shell", "conversation", "graph"])
        expect(openedRenders[key]).toBe(beforeOpenRenders[key]);
      const optionBox = await page.locator(".lead-preview-option").boundingBox();
      const timelineBox = await page.locator(".conversation-timeline").boundingBox();
      if (viewport.width >= 768 && optionBox && timelineBox)
        expect(optionBox.y + optionBox.height).toBeLessThanOrEqual(
          timelineBox.y + timelineBox.height,
        );
      const before = await page.locator(".conversation-composer").boundingBox();
      const replyBefore = await reply.boundingBox();
      const counters = await page.evaluate(() => ({ ...window.previewChatRenders }));
      const focus = await page.evaluate(() => document.activeElement?.outerHTML);
      await page.waitForTimeout(1100); // One real elapsed tick, bounded to this header.
      session.publish("ready");
      await check(
        page.frameLocator("iframe").getByRole("heading", { name: "Candidate shop" }),
      ).toBeVisible();
      await check(page.locator("iframe")).toHaveAttribute(
        "sandbox",
        "allow-scripts allow-same-origin allow-forms",
      );
      await check(page.locator("iframe")).toHaveAttribute("referrerpolicy", "no-referrer");
      expect(await page.locator(".conversation-composer").boundingBox()).toEqual(before);
      expect(await reply.boundingBox()).toEqual(replyBefore);
      expect(await page.evaluate(() => document.activeElement?.outerHTML)).toEqual(focus);
      const after = await page.evaluate(() => window.previewChatRenders);
      expect(after.shell).toBe(counters.shell);
      expect(after.conversation).toBe(counters.conversation);
      expect(after.graph).toBe(counters.graph);
      expect(after.phase).toBeGreaterThan(counters.phase ?? 0);
      const manifest = await support.capture(session, "ready");
      expect(manifest.timeToReadyMs).toBeGreaterThan(0);
      await check(page.locator(".preview-panel button")).toBeVisible();
      await page.locator(".preview-panel button").click();
      await check(page.locator(".chat-preview-slot")).toHaveCount(0);
      const closedRenders = await page.evaluate(() => window.previewChatRenders);
      for (const key of ["shell", "conversation", "graph"])
        expect(closedRenders[key]).toBe(after[key]);
      expect(await page.locator(".chat-agents").boundingBox()).toEqual(rail);
      expect(await page.locator(".conversation-panel").boundingBox()).toEqual(original);
      if (await composer.count()) await check(composer).toHaveValue("Keep this draft");
      await check(page.locator(".chat-work")).toHaveCount(1);
      await option.click();
      session.publish("failed", "isolation_unavailable");
      await check(page.getByText("Preview refused", { exact: true })).toBeVisible();
      await support.capture(session, "refused");
      await page.locator(".preview-panel button").click();
    } finally {
      await session.close();
    }
    const replay = await support.session(viewport, theme, "replay=true");
    try {
      await check(
        replay.page.getByRole("button", { name: "Preview app", exact: true }),
      ).toBeDisabled();
      await check(
        replay.page.getByRole("button", { name: "Review change", exact: true }),
      ).toBeDisabled();
      await check(replay.page.locator("iframe")).toHaveCount(0);
      await support.capture(replay, "replay");
      expect(replay.calls.start).toBe(0);
    } finally {
      await replay.close();
    }
  },
  40_000,
);
it.each(["expired", "observer", "uncertain", "replay"])(
  "captures the explicitly inert %s panel fixture",
  async (state) => {
    const session = await support.session(chatSizes[2], "dark", `panelFixture=${state}`);
    try {
      await support.capture(session, `panel-fixture-${state}`);
      await check(session.page.locator("iframe")).toHaveCount(0);
    } finally {
      await session.close();
    }
  },
);
it("Review change targets the resolved proposal without sending any decision", async () => {
  const session = await support.session();
  try {
    await session.page.getByRole("button", { name: "Review change", exact: true }).press("Space");
    await check(session.page.getByRole("complementary", { name: "Review panel" })).toBeVisible();
    expect(session.calls.start).toBe(0);
    expect(session.requests).toEqual([]);
  } finally {
    await session.close();
  }
});

it("a later matching reply moves the same option DOM without moving its keyboard focus", async () => {
  const session = await support.session(chatSizes[1], "dark", "replyLate=true");
  try {
    const button = session.page.getByRole("button", { name: "Preview app", exact: true });
    await check(
      session.page.getByText("Preview suggested; no matching lead reply is available."),
    ).toBeVisible();
    await button.focus();
    session.reply();
    await check(
      session.page.getByText("Preview suggested; no matching lead reply is available."),
    ).toHaveCount(0);
    await check(button).toBeFocused();
    expect(session.calls.start).toBe(0);
  } finally {
    await session.close();
  }
});
it("edits invalidate the suggestion and arrivals cannot open the slot or steal focus", async () => {
  const session = await support.session();
  try {
    const composer = session.page.locator(".conversation-composer textarea");
    await composer.focus();
    session.invalidate();
    await check(session.page.getByRole("button", { name: "Preview app", exact: true })).toHaveCount(
      0,
    );
    await check(composer).toBeFocused();
    await check(session.page.locator(".chat-preview-slot")).toHaveCount(0);
    expect(session.calls.start).toBe(0);
  } finally {
    await session.close();
  }
});
it("resize and explicit navigation retain one cold-to-warm graph and cancel the live frame", async () => {
  const session = await support.session();
  const { page } = session;
  try {
    await check(page.getByRole("img", { name: "Repository graph" })).toHaveCount(0);
    await page.getByRole("button", { name: "Preview app", exact: true }).click();
    session.publish("ready");
    await check(page.locator("iframe")).toBeVisible();
    const close = page.locator(".preview-panel button");
    await close.focus();
    await page.setViewportSize({ width: 1280, height: 800 });
    await check(close).toBeFocused();
    await page.getByRole("button", { name: "Oversight", exact: true }).click();
    await check(page.locator("iframe")).toHaveCount(0);
    await check(page.getByRole("img", { name: "Repository graph" })).toHaveCount(1);
    await page.waitForFunction(() =>
      Boolean(
        document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]')?.oversightGraph,
      ),
    );
    const graph = await page.getByRole("img", { name: "Repository graph" }).elementHandle();
    await page.getByRole("button", { name: "Chat", exact: true }).click();
    await check(page.locator(".chat-preview-slot")).toHaveCount(0);
    await page.getByRole("button", { name: "Oversight", exact: true }).click();
    await check(page.getByRole("img", { name: "Repository graph" })).toHaveCount(1);
    expect(
      await graph.evaluate(
        (node) => node === document.querySelector('[aria-label="Repository graph"]'),
      ),
    ).toBe(true);
    expect(session.calls.stop).toBe(1);
    await page.getByRole("button", { name: "Chat", exact: true }).click();
    const warmed = await page.evaluate(() => ({ ...window.previewChatRenders }));
    await page.getByRole("button", { name: "Preview app", exact: true }).click();
    session.publish("ready");
    await check(page.locator("iframe")).toBeVisible();
    const previewed = await page.evaluate(() => window.previewChatRenders);
    for (const key of ["shell", "conversation", "graph"]) expect(previewed[key]).toBe(warmed[key]);
    await page.locator(".preview-panel button").click();
    await check(page.locator("iframe")).toHaveCount(0);
    const exposed = await page.evaluate(() => ({
      html: document.body.innerHTML,
      url: location.href,
      storage: [Object.keys(localStorage), Object.keys(sessionStorage)],
    }));
    expect(JSON.stringify(exposed)).not.toMatch(/lease_token|F{43}/);
  } finally {
    await session.close();
  }
});
