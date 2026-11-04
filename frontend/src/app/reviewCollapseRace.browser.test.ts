import { expect as browserExpect } from "@playwright/test";
import { expect, it } from "vitest";

import { panel, withPage } from "./review.browser.support";

declare global {
  interface Window {
    reviewRace: { armed: boolean; held: boolean; release: () => void };
  }
}

it("discards a collapsed review before the pending dirty diff returns", async () => {
  await withPage(false, async (page) => {
    await page.evaluate(() => {
      // Hold the actual worker reply, after it has computed EOF mappings for the longer draft.
      // This controls response order without relying on CPU speed or diff debounce timing.
      const gate = { armed: false, held: false, release: () => {} };
      window.reviewRace = gate;
      const NativeWorker = window.Worker;
      window.Worker = class extends NativeWorker {
        private pending: string | undefined;
        constructor(url: string | URL, options?: WorkerOptions) {
          super(url, options);
          this.addEventListener("message", (event: MessageEvent<{ seq?: string }>) => {
            if (!this.pending || event.data.seq !== this.pending) return;
            this.pending = undefined;
            event.stopImmediatePropagation();
            gate.held = true;
            gate.release = () =>
              this.dispatchEvent(new MessageEvent("message", { data: event.data }));
          });
        }
        override postMessage(
          message: { method?: string; req?: string },
          options?: Transferable[] | StructuredSerializeOptions,
        ) {
          if (gate.armed && message.method === "$computeDiff") {
            gate.armed = false;
            this.pending = message.req;
          }
          if (Array.isArray(options)) super.postMessage(message, options);
          else super.postMessage(message, options);
        }
      };
    });
    await page.getByRole("button", { name: "Review change" }).click();
    const review = panel(page);
    const file = review.getByLabel("Editable review file");
    await browserExpect(file).toHaveAttribute("data-state", "clean", { timeout: 15_000 });
    await review.locator(".line-insert").first().waitFor();
    await review.getByRole("textbox", { name: /Comment/ }).fill("Keep my comment");
    await page.evaluate(() => {
      window.reviewRace.armed = true;
    });
    await review.locator(".editor.modified :is(.native-edit-context, textarea.inputarea)").focus();
    await page.keyboard.press("Control+End");
    await page.keyboard.insertText("\n# pending draft\n");
    await page.waitForFunction(() => window.reviewRace.held);
    const toggle = review.getByRole("button", { name: /shop\/pricing.py/ });
    await toggle.click();
    await browserExpect(review.getByRole("button", { name: "Approve and merge" })).toBeDisabled();
    await toggle.click();
    await review.getByRole("button", { name: "Discard edits", exact: true }).click();
    await page.evaluate(() => {
      window.reviewRace.release();
    });
    await browserExpect(file).toHaveAttribute("data-state", "clean");
    await browserExpect(review.getByRole("button", { name: "Approve and merge" })).toBeEnabled();
    await browserExpect(review.getByRole("textbox", { name: /Comment/ })).toHaveValue(
      "Keep my comment",
    );
    await browserExpect(
      review.locator('.modified .diff-hidden-lines [title="Show Unchanged Region"]').first(),
    ).toBeVisible();
    expect(await page.evaluate(() => window.reviewTest.decisions)).toEqual([]);
  });
}, 30_000);
