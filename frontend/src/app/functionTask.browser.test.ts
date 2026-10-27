import type { StartAgent } from "@contracts";
import { expect as browserExpect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { expect, it } from "vitest";

import type { GraphProbeHost } from "../graph";
import { append, clickUntouched, filePanel, withPage } from "./fileView.browser.support";

async function graphSelection(page: Page) {
  return page.evaluate(() => {
    const graph = document.querySelector<GraphProbeHost>(
      '[aria-label="Repository graph"]',
    )?.oversightGraph;
    return {
      positions: graph?.positions(),
      selected: graph?.nodeCenters("node:selected").map(({ id }) => id),
    };
  });
}

it(
  "prefills from a method, focuses at the end, and sends StartAgent only after Start",
  { timeout: 30_000 },
  async () => {
    await withPage(async (page) => {
      const starts: StartAgent[] = [];
      await page.route("**/agents", async (route) => {
        starts.push(route.request().postDataJSON() as StartAgent);
        await route.fulfill({
          status: 201,
          json: { agent_id: "agent-2", change_id: "change-2", slot: "B" },
        });
      });
      await clickUntouched(page);
      await append(page, "Keep this local edit");
      const editor = await filePanel(page).locator(".monaco-editor").elementHandle();
      const graphBefore = await graphSelection(page);
      const action = filePanel(page).getByRole("button", { name: "Start an agent here" });
      await action.click();
      const box = page.getByLabel("Task for the new agent");
      const prefix = "In shop/cart.py, Cart.add: ";
      await browserExpect(box).toHaveValue(prefix);
      await browserExpect(box).toBeFocused();
      expect(
        await box.evaluate((element: HTMLTextAreaElement) => [
          element.selectionStart,
          element.selectionEnd,
        ]),
      ).toEqual([prefix.length, prefix.length]);
      await browserExpect(filePanel(page)).toHaveAttribute("data-state", "dirty");
      expect(await editor.evaluate((element) => element.isConnected)).toBe(true);
      expect(await graphSelection(page)).toEqual(graphBefore);
      await browserExpect(
        page.getByRole("button", { name: "B Muse Spark Open", exact: true }),
      ).toHaveAttribute("aria-pressed", "true");
      expect(starts).toEqual([]);
      await page.keyboard.insertText("Validate the quantity.");
      await page.getByRole("button", { name: "Start agent", exact: true }).click();
      await browserExpect(box).toHaveValue("");
      expect(starts).toEqual([{ task: `${prefix}Validate the quantity.`, slot: "B" }]);
      await browserExpect(filePanel(page)).toHaveAttribute("data-state", "dirty");
      // Repeat while the box is already mounted: focus must use the new draft's length.
      await action.click();
      await browserExpect(box).toHaveValue(prefix);
      await browserExpect(box).toBeFocused();
      expect(await box.evaluate((element: HTMLTextAreaElement) => element.selectionStart)).toBe(
        prefix.length,
      );
      // A later mount cannot replay the explicit focus request.
      await page.getByRole("button", { name: "Close file" }).click();
      await clickUntouched(page, "dirty");
      await browserExpect(box).not.toBeFocused();
    });
  },
);

it("disables the file action with the task box's reason when no slot is free", async () => {
  await withPage(async (page) => {
    let starts = 0;
    await page.route("**/agents", (route) => {
      starts += 1;
      return route.abort();
    });
    await clickUntouched(page);
    await page.evaluate(() => {
      window.fileViewTest.holdSlots();
    });
    const action = filePanel(page).getByRole("button", { name: "Start an agent here" });
    await browserExpect(action).toHaveAttribute("aria-disabled", "true");
    await browserExpect(action).toHaveAccessibleDescription("Agent A is waiting for your review.");
    await action.click({ force: true });
    await browserExpect(page.getByLabel("Task for the new agent")).toHaveCount(0);
    await browserExpect(filePanel(page)).toHaveAttribute("data-state", "clean");
    expect(starts).toBe(0);
  });
});

it.each(["Keep my existing task", " "])(
  "keeps an occupied task box (%j) while folded",
  async (draft) => {
    await withPage(async (page) => {
      const box = page.getByLabel("Task for the new agent");
      await box.fill(draft);
      await clickUntouched(page);
      const action = filePanel(page).getByRole("button", { name: "Start an agent here" });
      await browserExpect(action).toHaveAttribute("aria-disabled", "true");
      await browserExpect(action).toHaveAccessibleDescription(
        "Task box is in use. Finish or clear the current task first.",
      );
      await action.click({ force: true });
      await browserExpect(box).toHaveCount(0);
      await page.getByRole("button", { name: "Close file" }).click();
      await browserExpect(box).toHaveValue(draft);
      await browserExpect(box).not.toBeFocused();
    });
  },
);
