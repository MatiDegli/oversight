import { expect as check } from "@playwright/test";
import { expect, it } from "vitest";
import { chatSizes } from "./previewChat.browser.support";
import {
  previewLifecycleSupport,
  ownershipPresent,
  type LifecyclePage,
} from "./previewLifecycle.browser.support";

const support = previewLifecycleSupport();
const matrix = chatSizes.flatMap((viewport) =>
  ["dark", "day"].map((theme) => ({ viewport, theme })),
);
const option = (session: LifecyclePage) =>
  session.page.getByRole("button", { name: "Preview app", exact: true });
async function frame(session: LifecyclePage) {
  await check(
    session.page.frameLocator("iframe").getByRole("heading", { name: "Candidate shop" }),
  ).toBeVisible();
  await check(session.page.locator("iframe")).toHaveAttribute(
    "sandbox",
    "allow-scripts allow-same-origin allow-forms",
  );
  await check(session.page.locator("iframe")).toHaveAttribute("referrerpolicy", "no-referrer");
}
async function evidence(session: LifecyclePage, scene: string) {
  const manifest = await support.capture(session, scene);
  expect(manifest.pageErrors).toEqual([]);
  expect(Object.values(manifest.overflow).every((value) => value === 0)).toBe(true);
}
async function arrivalGuard(session: LifecyclePage) {
  const { page } = session;
  const draft = page.locator(".conversation-composer textarea");
  await draft.fill("Keep this lifecycle draft");
  await draft.focus();
  const before = await support.geometry(page);
  support.arrive();
  await check(page.locator("#root")).toHaveAttribute("data-seq", String(support.sequence()));
  await check(draft).toBeFocused();
  await check(draft).toHaveValue("Keep this lifecycle draft");
  expect(await support.geometry(page)).toEqual(before);
  await check(page.locator(".chat-preview-slot, iframe")).toHaveCount(0);
}

it.each(matrix)(
  "proves shared-authority lifecycle at $viewport.width × $viewport.height in $theme",
  async ({ viewport, theme }) => {
    support.reset();
    const session = await support.open(viewport, theme);
    const { page } = session;
    try {
      await arrivalGuard(session);
      await option(session).click();
      await support.ready();
      await frame(session);
      expect(support.count("start")).toBe(1);
      const id = support.previewId();
      expect(await ownershipPresent(page)).toBe(true);
      support.loseUnloadStop();
      await page.reload();
      await support.lead(page);
      await check.poll(() => support.count("renew")).toBeGreaterThanOrEqual(1);
      await check(page.locator(".chat-preview-slot, iframe")).toHaveCount(0);
      expect(support.previewId()).toBe(id);
      expect(support.count("start")).toBe(1);
      expect(support.stops()).toMatchObject([{ authenticated: true, delivered: false }]);
      expect(support.unloadDispatches()).toMatchObject([{ keepalive: true, aborted: false }]);
      await option(session).click();
      await frame(session);
      await evidence(session, "recovered-owner");
      const renewed = support.count("renew");
      await page.clock.fastForward(10_001);
      await check.poll(() => support.count("renew")).toBeGreaterThanOrEqual(renewed + 1);
      expect(support.previewId()).toBe(id);

      const observer = await support.observer(session);
      try {
        expect(await ownershipPresent(observer.page)).toBe(false);
        const calls = { start: support.count("start"), stop: support.count("stop") };
        await arrivalGuard(observer);
        await option(observer).click();
        await check(
          observer.page.getByText("Preview in use in another tab", { exact: true }),
        ).toBeVisible();
        await check(observer.page.locator("iframe")).toHaveCount(0);
        await evidence(observer, "observer-refusal");
        await observer.page.clock.fastForward(20_001);
        expect(support.sourceMutations(observer.page)).toEqual([]);
        expect({ start: support.count("start"), stop: support.count("stop") }).toEqual(calls);
        await observer.page.locator(".preview-panel button").click();
        support.arrive();
        await check(observer.page.locator(".chat-preview-slot, iframe")).toHaveCount(0);
        await option(observer).click();
        await check(
          observer.page.getByText("Preview in use in another tab", { exact: true }),
        ).toBeVisible();
        expect(support.sourceMutations(observer.page)).toEqual([]);
        expect({ start: support.count("start"), stop: support.count("stop") }).toEqual(calls);
        await observer.page.close();
        expect({ start: support.count("start"), stop: support.count("stop") }).toEqual(calls);
        expect(support.state()).toBe("ready");
      } finally {
        if (!observer.page.isClosed()) await observer.page.close();
      }
      await page.locator(".preview-panel button").click();
      await check(page.locator(".chat-preview-slot, iframe")).toHaveCount(0);
      expect(support.stops()).toMatchObject([
        { authenticated: true, delivered: false },
        { authenticated: true, delivered: true },
      ]);
      expect(support.state()).toBe("stopped");
      expect(await ownershipPresent(page)).toBe(false);

      // A separate preview exercises actual delivered pagehide stop and terminal recovery.
      await option(session).click();
      await support.ready();
      await frame(session);
      expect(support.count("start")).toBe(2);
      const stoppedId = support.previewId();
      await page.reload();
      await support.lead(page);
      await check.poll(() => support.state()).toBe("stopped");
      await check.poll(() => ownershipPresent(page)).toBe(false);
      await option(session).click();
      await check(page.locator("iframe")).toHaveCount(0);
      await evidence(session, "stopped-after-reload");
      await page.locator(".preview-panel button").click();
      const mutations = support.mutationCount();
      await page.clock.fastForward(30_001);
      await option(session).click();
      await check(page.locator("iframe")).toHaveCount(0);
      await page.locator(".preview-panel button").click();
      support.arrive();
      await option(session).click();
      expect(support.previewId()).toBe(stoppedId);
      expect(support.count("start")).toBe(2);
      expect(support.mutationCount()).toBe(mutations);
      expect(support.unloadDispatches()).toMatchObject([
        { keepalive: true, aborted: false },
        { keepalive: true, aborted: false },
      ]);
      expect(support.invalidRequests()).toEqual([]);
      expect(session.pageErrors).toEqual([]);
    } catch (error) {
      await support.diagnostics(session);
      throw error;
    } finally {
      await session.context.close();
    }
  },
  60_000,
);
