import { expect as browserExpect } from "@playwright/test";
import { expect, it } from "vitest";

import type { GraphProbeHost } from "../graph";
import {
  append,
  clickGraphNode,
  clickUntouched,
  filePanel,
  input,
  original,
  withPage,
} from "./fileView.browser.support";

it("opens the file at its span without focus or node movement; Ctrl+S saves once; stale_file offers Reload", async () => {
  await withPage(async (page, calls, serverFile) => {
    const positions = await page.evaluate(() =>
      document
        .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
        ?.oversightGraph?.positions(),
    );
    await clickUntouched(page);
    await browserExpect(
      filePanel(page).getByRole("heading", { name: "shop/cart.py" }),
    ).toBeVisible();
    await browserExpect(filePanel(page).locator(".file-span").first()).toBeVisible();
    expect(
      await page.evaluate(() => document.activeElement?.closest(".monaco-editor") !== null),
    ).toBe(false);
    expect(
      await page.evaluate(() =>
        document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.positions(),
      ),
    ).toEqual(positions);
    await append(page, "saved from the file view");
    await page.keyboard.press("Control+s");
    await browserExpect(filePanel(page)).toHaveAttribute("data-state", "clean");
    expect(calls).toHaveLength(1);
    expect(calls[0]).toEqual({
      path: "shop/cart.py",
      content: original + "\n# saved from the file view",
      base_hash: "h0",
    });
    await append(page, "keep this draft");
    serverFile.content = original + "\n# moved on main";
    serverFile.hash = "moved";
    await filePanel(page).getByRole("button", { name: "Save", exact: true }).click();
    await browserExpect(filePanel(page).getByRole("alert")).toContainText(
      "Your edits are still here.",
    );
    await browserExpect(
      filePanel(page).getByRole("button", { name: "Save", exact: true }),
    ).toBeDisabled();
    await filePanel(page).getByRole("button", { name: "Reload", exact: true }).click();
    await browserExpect(filePanel(page)).toHaveAttribute("data-state", "clean");
    await browserExpect(filePanel(page).getByRole("alert")).toHaveCount(0);
    await browserExpect(filePanel(page).locator(".file-span").first()).toBeVisible();
    await append(page, "after reload");
    await page.keyboard.press("Meta+s");
    await browserExpect(filePanel(page)).toHaveAttribute("data-state", "clean");
    expect(calls.at(-1)?.base_hash).toBe("moved");
    expect(calls.at(-1)?.content).toContain("# moved on main");
    expect(calls.at(-1)?.content).not.toContain("# keep this draft");
  });
}, 30_000);

it("an untouched cone node only selects: review, comment and cone lighting survive", async () => {
  await withPage(async (page, calls) => {
    await page.getByRole("button", { name: /change ready for review/ }).click();
    const review = page.getByRole("complementary", { name: "Review panel" });
    await review.locator(".line-insert").first().waitFor();
    const comment = review.getByRole("textbox", { name: "Comment (required for Request changes)" });
    await comment.fill("Keep this request for changes while I inspect the cone.");
    await page.waitForFunction(
      () =>
        document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.animating() === false,
    );
    const before = await page.evaluate(() => {
      const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
      const graph = host?.oversightGraph;
      if (!host || !graph) throw new Error("Graph missing");
      const { width, height } = host.getBoundingClientRect();
      const cone = graph.nodeCenters("node.cone:not(.dimmed)");
      const visible = cone.find(
        ({ x, y }) => x > 20 && x < width - 20 && y > 20 && y < height - 20,
      );
      if (!visible) throw new Error("Visible untouched cone node missing");
      return {
        clicked: visible.id,
        cone: cone.map(({ id }) => id).sort(),
        dimmed: graph
          .nodeCenters("node.dimmed")
          .map(({ id }) => id)
          .sort(),
      };
    });
    expect(before.dimmed.length).toBeGreaterThan(0);
    await clickGraphNode(page, before.clicked);
    await browserExpect(review).toBeVisible();
    await browserExpect(comment).toHaveValue(
      "Keep this request for changes while I inspect the cone.",
    );
    await browserExpect(filePanel(page)).toHaveCount(0);
    const after = await page.evaluate(() => {
      const graph = document.querySelector<GraphProbeHost>(
        '[aria-label="Repository graph"]',
      )?.oversightGraph;
      return {
        cone: graph
          ?.nodeCenters("node.cone:not(.dimmed)")
          .map(({ id }) => id)
          .sort(),
        dimmed: graph
          ?.nodeCenters("node.dimmed")
          .map(({ id }) => id)
          .sort(),
        selected: graph?.nodeCenters("node:selected").map(({ id }) => id),
      };
    });
    expect(after.cone).toEqual(before.cone);
    expect(after.dimmed).toEqual(before.dimmed);
    expect(after.selected).toContain(before.clicked);
    expect(calls).toEqual([]);
  });
}, 30_000);

it("a review and Escape preserve drafts; Ctrl+S in review sends nothing and never approves", async () => {
  await withPage(async (page, calls) => {
    await clickUntouched(page);
    await append(page, "draft survives review and Escape");
    await page.getByRole("button", { name: /change ready for review/ }).click();
    const review = page.getByRole("complementary", { name: "Review panel" });
    await review.locator(".line-insert").first().waitFor();
    await browserExpect(filePanel(page)).toHaveCount(0);
    await review
      .getByRole("textbox", { name: "Comment (required for Request changes)" })
      .fill("Keep typing");
    await page.keyboard.press("Control+s");
    await page.keyboard.press("Control+Enter");
    expect(calls).toEqual([]);
    await browserExpect(review.getByRole("button", { name: "Approve and merge" })).toBeEnabled();
    await page.getByRole("button", { name: "Close review" }).click();
    await clickUntouched(page, "dirty");
    await browserExpect(filePanel(page)).toHaveAttribute("data-state", "dirty");
    await page.getByRole("button", { name: "Close file" }).focus();
    await page.keyboard.press("Escape");
    await browserExpect(filePanel(page)).toHaveCount(0);
    await clickUntouched(page, "dirty");
    await browserExpect(filePanel(page)).toHaveAttribute("data-state", "dirty");
    await filePanel(page).getByRole("button", { name: "Save", exact: true }).click();
    await browserExpect(filePanel(page)).toHaveAttribute("data-state", "clean");
    expect(calls).toHaveLength(1);
    expect(calls[0]?.content).toContain("# draft survives review and Escape");
  });
}, 30_000);

it("a new graph commit refreshes a clean file through App without taking focus", async () => {
  await withPage(async (page, calls, serverFile) => {
    await clickUntouched(page);
    await input(page).focus();
    serverFile.content = original + "\n# changed on main";
    serverFile.hash = "external-main";
    const refresh = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/files" && response.request().method() === "GET",
    );
    await page.evaluate(() => {
      window.fileViewTest.commit("external-commit");
    });
    await refresh;
    await browserExpect(filePanel(page)).toHaveAttribute("data-state", "clean");
    await browserExpect(filePanel(page).locator(".file-span").first()).toBeVisible();
    await browserExpect(input(page)).toBeFocused();
    await append(page, "edited after refresh");
    await page.keyboard.press("Control+s");
    await browserExpect(filePanel(page)).toHaveAttribute("data-state", "clean");
    expect(calls).toHaveLength(1);
    expect(calls[0]?.base_hash).toBe("external-main");
    expect(calls[0]?.content).toContain("# changed on main");
    expect(calls[0]?.content).toContain("# edited after refresh");
  });
}, 30_000);

it("a held refetch preserves the editor bounds and scroll, then restores the span", async () => {
  await withPage(async (page, _calls, serverFile) => {
    serverFile.content = original + "\n".repeat(100);
    await clickUntouched(page);
    const editor = filePanel(page).locator(".monaco-editor");
    await browserExpect(filePanel(page).locator(".file-span").first()).toBeVisible();
    const initialTop = await filePanel(page)
      .locator(".file-span")
      .first()
      .evaluate((element) => element.getBoundingClientRect().top);
    await editor.hover();
    await page.mouse.wheel(0, 84);
    await browserExpect
      .poll(async () =>
        filePanel(page)
          .locator(".file-span")
          .first()
          .evaluate((element) => element.getBoundingClientRect().top),
      )
      .toBeLessThan(initialTop - 20);
    const box = await editor.boundingBox();
    const spanTop = await filePanel(page)
      .locator(".file-span")
      .first()
      .evaluate((element) => element.getBoundingClientRect().top);
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    let requested!: () => void;
    const started = new Promise<void>((resolve) => {
      requested = resolve;
    });
    await page.route("**/files?*", async (route) => {
      requested();
      await held;
      await route.fulfill({
        json: {
          path: "shop/cart.py",
          content: serverFile.content + "# refreshed",
          hash: "h2",
          agent_id: null,
        },
      });
    });
    try {
      await page.evaluate(() => {
        window.fileViewTest.commit("held-refresh");
      });
      await started;
      await browserExpect(filePanel(page)).toHaveAttribute("data-state", "loading");
      await browserExpect(filePanel(page).getByRole("status")).toHaveText("");
      expect(await editor.boundingBox()).toEqual(box);
    } finally {
      release();
    }
    await browserExpect(filePanel(page)).toHaveAttribute("data-state", "clean");
    await browserExpect(filePanel(page).locator(".file-span").first()).toBeVisible();
    expect(await editor.boundingBox()).toEqual(box);
    expect(
      await filePanel(page)
        .locator(".file-span")
        .first()
        .evaluate((element) => element.getBoundingClientRect().top),
    ).toBeCloseTo(spanTop, 0);
  });
}, 30_000);
