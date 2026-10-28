import { expect as check } from "@playwright/test";
import { expect, it } from "vitest";

import { withPage } from "../review.browser.support";

it.each([
  [1920, 1080, false],
  [1440, 900, false],
  [1280, 800, false],
  [1280, 800, true],
] as const)(
  "bounds a 40-entry cone above the legend at %sx%s (dock open: %s)",
  async (width, height, dock) => {
    await withPage(false, async (page) => {
      await page.setViewportSize({ width, height });
      await page.evaluate(() => {
        const state = window.reviewTest.state();
        const proposal = state.changes["change-1"]?.proposal;
        if (!proposal) throw new Error("Missing proposal");
        window.reviewTest.emit({
          seq: 0,
          ts: "2026-10-28T15:00:00Z",
          type: "change.proposed",
          payload: {
            ...proposal,
            impact: {
              ...proposal.impact,
              entries: Array.from({ length: 40 }, (_, index) => ({
                node_id: `shop.orders:possibly_affected_caller_${String(index)}`,
                distance: index < 25 ? 1 : 2,
              })),
              max_nodes: 40,
              omitted_by_depth: 53,
              omitted_by_limit: 170,
            },
          },
        });
      });
      await page.getByRole("button", { name: "Review change" }).click();
      if (dock) await page.keyboard.press("Control+Backquote");
      const card = page.getByRole("region", { name: "Possibly affected", exact: true });
      await check(card.locator("li")).toHaveCount(40);
      const header = card.getByRole("heading", { name: "Possibly affected", exact: true });
      const depth = card.getByText("+53 omitted by depth", { exact: true });
      const limit = card.getByText("+170 omitted by the node limit", { exact: true });
      const before = await Promise.all([
        header.boundingBox(),
        depth.boundingBox(),
        limit.boundingBox(),
      ]);
      const bounds = await card.boundingBox();
      const legend = await page.getByLabel("Legend", { exact: true }).boundingBox();
      const zoom = await page.getByLabel("Zoom", { exact: true }).boundingBox();
      if (!bounds || !legend || !zoom) throw new Error("Missing overlay bounds");
      expect(bounds.y).toBeGreaterThanOrEqual(0);
      expect(bounds.y + bounds.height).toBeLessThanOrEqual(Math.min(legend.y, zoom.y, height));
      const scrolled = await card.evaluate((element) => {
        const scrollers = [...element.querySelectorAll<HTMLElement>("*")].filter(
          (child) => getComputedStyle(child).overflowY === "auto",
        );
        const list = scrollers.find((child) => child.querySelectorAll("li").length === 40);
        if (!list) throw new Error("Missing scrollable function list");
        list.scrollTop = list.scrollHeight;
        return { top: list.scrollTop, max: list.scrollHeight - list.clientHeight };
      });
      expect(scrolled.top).toBeGreaterThan(0);
      expect(scrolled.top).toBe(scrolled.max);
      await check(card.locator("li").last()).toBeInViewport();
      await check(card.locator("li").first()).not.toBeInViewport();
      for (const item of [header, depth, limit]) await check(item).toBeInViewport();
      expect(
        await Promise.all([header.boundingBox(), depth.boundingBox(), limit.boundingBox()]),
      ).toEqual(before);
      // Neither the card nor its wrapper can scroll the title or counts out of sight.
      expect(
        await card.evaluate((element) => {
          const positions = [];
          for (let parent: Element | null = element; parent; parent = parent.parentElement) {
            parent.scrollTop = parent.scrollHeight;
            positions.push(parent.scrollTop === 0);
          }
          return positions.every(Boolean);
        }),
      ).toBe(true);
      expect(await header.boundingBox()).toEqual(before[0]);
      expect(await depth.boundingBox()).toEqual(before[1]);
      expect(await limit.boundingBox()).toEqual(before[2]);
    });
  },
);
