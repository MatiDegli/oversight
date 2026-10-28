import { expect as browserExpect } from "@playwright/test";
import { expect, it } from "vitest";
import type { ServerEvent } from "@contracts";
import log from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { withPage, filePanel } from "./fileView.browser.support";

const first = JSON.parse(log.split("\n")[0] ?? "{}") as Extract<
  ServerEvent,
  { type: "run.started" }
>;
const response = {
  run_id: first.payload.run_id,
  agent_id: null,
  graph_version: null,
  directories: ["shop", "tests"],
  files: [
    { path: "shop/__init__.py", hash: null },
    { path: "shop/cart.py", hash: null },
  ],
  truncated: false,
};
const tree = (page: Parameters<Parameters<typeof withPage>[0]>[0]) =>
  page.getByRole("tree", { name: "Repository files" });

it("Files rail and Ctrl B preserve folding; arrows and Enter open a file tab", async () => {
  await withPage(async (page) => {
    await page.route("**/tree?*", (route) => route.fulfill({ json: response }));
    await page.getByRole("button", { name: "Files", exact: true }).click();
    await browserExpect(
      tree(page).getByRole("treeitem", { name: "shop", exact: true }),
    ).toBeVisible();
    await browserExpect(page.getByLabel("Files sidebar")).toHaveCSS("width", "280px");
    const shop = tree(page).getByRole("treeitem", { name: "shop", exact: true });
    await shop.click();
    await browserExpect(shop).toHaveAttribute("aria-expanded", "false");
    await page.keyboard.press("Control+b");
    await browserExpect(tree(page)).toHaveCount(0);
    await page.keyboard.press("Control+b");
    await browserExpect(shop).toHaveAttribute("aria-expanded", "false");
    await tree(page).focus();
    await page.keyboard.press("Home");
    await page.keyboard.press("ArrowRight");
    await browserExpect(shop).toHaveAttribute("aria-expanded", "true");
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowDown");
    await browserExpect(
      tree(page).getByRole("treeitem", { name: "cart.py", exact: true }),
    ).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("Enter");
    await browserExpect(page.getByRole("tab", { name: "cart.py", exact: true })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await browserExpect(
      filePanel(page).getByRole("heading", { name: "shop/cart.py" }),
    ).toBeVisible();
    await browserExpect(tree(page)).toBeFocused();
  });
});

it("a file opens in a background tab while the review comment keeps focus and text", async () => {
  await withPage(async (page) => {
    await page.route("**/tree?*", (route) =>
      route.fulfill({
        status: 501,
        json: { error: { code: "not_implemented", message: "Replay" } },
      }),
    );
    await page.getByRole("button", { name: "Files", exact: true }).click();
    await browserExpect(
      tree(page).getByRole("treeitem", { name: "cart.py", exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: /change ready for review/ }).click();
    const review = page.getByRole("complementary", { name: "Review panel", exact: true });
    const comment = review.getByRole("textbox", { name: "Comment (required for Request changes)" });
    await comment.fill("Keep this comment and its focus.");
    await tree(page).getByRole("treeitem", { name: "pricing.py", exact: true }).click();
    await browserExpect(comment).toBeFocused();
    await browserExpect(comment).toHaveValue("Keep this comment and its focus.");
    await browserExpect(page.getByRole("tab", { name: "pricing.py", exact: true })).toHaveAttribute(
      "aria-selected",
      "false",
    );
  });
});

it("graph.delta and a merged resolution refresh the listing without unfolding folders", async () => {
  await withPage(async (page) => {
    let requests = 0;
    await page.route("**/tree?*", async (route) => {
      requests++;
      await route.fulfill({
        json: {
          ...response,
          files: [
            ...response.files,
            ...(requests > 1 ? [{ path: "tests/new.py", hash: null }] : []),
            ...(requests > 2 ? [{ path: "merged.py", hash: null }] : []),
          ],
        },
      });
    });
    await page.getByRole("button", { name: "Files", exact: true }).click();
    await browserExpect.poll(() => requests).toBe(1);
    const shop = tree(page).getByRole("treeitem", { name: "shop", exact: true });
    await shop.click();
    await browserExpect(shop).toHaveAttribute("aria-expanded", "false");
    await page.evaluate(() => {
      window.fileViewTest.refreshFiles("graph.delta");
    });
    await browserExpect.poll(() => requests).toBe(2);
    await browserExpect(
      tree(page).getByRole("treeitem", { name: "new.py", exact: true }),
    ).toBeVisible();
    await browserExpect(shop).toHaveAttribute("aria-expanded", "false");
    await page.evaluate(() => {
      window.fileViewTest.refreshFiles("change.resolved");
    });
    await browserExpect.poll(() => requests).toBe(3);
    await browserExpect(
      tree(page).getByRole("treeitem", { name: "merged.py", exact: true }),
    ).toBeVisible();
    await browserExpect(shop).toHaveAttribute("aria-expanded", "false");
    expect(requests).toBe(3);
  });
});
