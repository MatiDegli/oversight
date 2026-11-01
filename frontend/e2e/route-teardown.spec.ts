import { test } from "@playwright/test";

import { removeRoutes } from "./level2-support";

test.afterEach(async ({ page }) => removeRoutes(page));

for (const read of ["after close", "in flight"] as const) {
  test(`a route locator read ${read} does not fail an ended test`, async ({ page }) => {
    const closed = new Promise<void>((resolve) => {
      page.once("close", () => {
        resolve();
      });
    });
    let entered!: () => void;
    const pending = new Promise<void>((resolve) => {
      entered = resolve;
    });
    await page.route("**/files?teardown", async () => {
      // A missing root keeps the in-flight read pending until teardown ends the test.
      const seq = read === "in flight" ? page.locator("#root").getAttribute("data-seq") : null;
      entered();
      await closed;
      await (seq ?? page.locator("#root").getAttribute("data-seq"));
    });
    await page.evaluate(() => {
      void fetch("http://127.0.0.1/files?teardown").catch(() => {});
    });
    // Return only once the callback is waiting for the fixture to close the page.
    await pending;
  });
}
