import { fileURLToPath } from "node:url";

import { chromium, expect as browserExpect } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";

import type {} from "./review.browser.fixture";
import type { GraphProbeHost } from "../graph";

let server: ViteDevServer | undefined;
let browser: Browser | undefined;
let url: string;
let html: string;
beforeAll(async () => {
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("../../../.shots/review-vite-cache", import.meta.url)),
    server: {
      host: "127.0.0.1",
      port: 0,
      strictPort: false,
      fs: { allow: [fileURLToPath(new URL("../../../", import.meta.url))] },
    },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import { reviewTest } from "/src/app/review.browser.fixture.tsx"; window.reviewTest = reviewTest;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await chromium.launch();
}, 60_000);
afterAll(async () => {
  await browser?.close();
  await server?.close();
});

async function withPage(recorded: boolean, test: (page: Page) => Promise<void>) {
  if (!browser) throw new Error("Browser was not started");
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  page.setDefaultTimeout(15_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error" && !message.location().url.endsWith("/@vite/client"))
      errors.push(message.text());
  });
  try {
    // Only the frame is mounted: no transport, so nothing reaches a server.
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    await page.waitForFunction(() => "reviewTest" in window);
    await page.evaluate((flag) => {
      window.reviewTest.mount(flag);
    }, recorded);
    await page.waitForFunction(
      () =>
        (document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.nodeCenters().length ?? 0) > 0,
    );
    await test(page);
    expect(errors).toEqual([]);
  } finally {
    await page.close();
  }
}

/** The graph's read-only probe: which nodes match a selector, and every node's layout position. */
function probe(page: Page, selector: string) {
  return page.evaluate((wanted) => {
    const graph = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
    const view = graph?.oversightGraph;
    if (!view) throw new Error("The graph is not mounted");
    return { ids: view.nodeCenters(wanted).map(({ id }) => id), positions: view.positions() };
  }, selector);
}

/** The camera as the graph reports it: zoom, whether it is easing, and where a node sits. */
function camera(page: Page, id: string) {
  return page.evaluate((wanted) => {
    const element = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
    const view = element?.oversightGraph;
    if (!element || !view) throw new Error("The graph is not mounted");
    return {
      zoom: view.zoom(),
      animating: view.animating(),
      size: { width: element.clientWidth, height: element.clientHeight },
      node: view.nodeCenters().find((center) => center.id === wanted),
    };
  }, id);
}

const panel = (page: Page) => page.getByRole("complementary", { name: "Review panel" });

it("opens review only from the person's click, dims all but the change, and closes", async () => {
  await withPage(false, async (page) => {
    const changed = await page.evaluate(() => window.reviewTest.changed);
    await browserExpect(page.getByRole("button", { name: "Review change" })).toBeVisible();
    await browserExpect(panel(page)).toHaveCount(0);
    expect((await probe(page, "node.dimmed")).ids).toEqual([]);

    await page.getByRole("button", { name: "Review change" }).click();
    await panel(page).locator(".line-insert").first().waitFor();
    await browserExpect
      .poll(async () => (await probe(page, "node.dimmed")).ids.length)
      .toBeGreaterThan(10);
    expect((await probe(page, "node.dimmed")).ids).not.toContain(changed);
    // Cytoscape's selectors have no :not(), so the lit leaves are the leaves minus the dimmed.
    const dimmed = new Set((await probe(page, "node.dimmed")).ids);
    const leaves = (await probe(page, ":childless")).ids;
    expect(leaves.filter((id) => !dimmed.has(id))).toEqual([changed]);

    await page.getByRole("button", { name: "Close review" }).click();
    await browserExpect(panel(page)).toHaveCount(0);
    await browserExpect.poll(async () => (await probe(page, "node.dimmed")).ids).toEqual([]);
  });
}, 30_000);

it("frames the change at the zoom of the watch fit, centered between the panel and the rail", async () => {
  await withPage(false, async (page) => {
    const changed = await page.evaluate(() => window.reviewTest.changed);
    const watch = await camera(page, changed);
    await page.getByRole("button", { name: "Review change" }).click();
    await panel(page).locator(".line-insert").first().waitFor();
    await browserExpect.poll(async () => (await camera(page, changed)).animating).toBe(false);
    const review = await camera(page, changed);
    // The labels stay at the size they had in watch, 15 px at a zoom of 1.
    expect(review.zoom).toBeCloseTo(watch.zoom, 6);
    expect(review.node?.x).toBeCloseTo(review.size.width / 2, 0);
    expect(review.node?.y).toBeCloseTo(review.size.height / 2, 0);

    await page.getByRole("button", { name: "Close review" }).click();
    await browserExpect.poll(async () => (await camera(page, changed)).animating).toBe(false);
    expect((await camera(page, changed)).zoom).toBeCloseTo(watch.zoom, 2);
  });
}, 30_000);

it("never approves from the keyboard in the comment box; the click waits for change.resolved", async () => {
  await withPage(false, async (page) => {
    await page.getByRole("button", { name: /change ready for review/ }).click();
    const approve = panel(page).getByRole("button", { name: "Approve and merge" });
    const reject = panel(page).getByRole("button", { name: "Reject" });
    const comment = panel(page).getByRole("textbox", { name: "Comment (optional)" });
    await comment.click();
    await page.keyboard.type("Looks right");
    for (const key of ["Control+Enter", "Enter", "Meta+Enter", "Shift+Enter", "Alt+Enter"])
      await page.keyboard.press(key);
    expect(await page.evaluate(() => window.reviewTest.decisions.length)).toBe(0);
    await browserExpect(comment).toBeFocused();
    expect(await comment.inputValue()).toMatch(/^Looks right\n/);
    await browserExpect(approve).toBeEnabled();

    const before = await probe(page, "node");
    await approve.click();
    await browserExpect(approve).toBeDisabled();
    await browserExpect(reject).toBeDisabled();
    await browserExpect(panel(page).getByRole("status")).toHaveText(
      "Approving: waiting for the merge result.",
    );
    const sent = await page.evaluate(() => window.reviewTest.decisions);
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ changeId: "change-1", action: "approve" });

    // change.resolved, then graph.delta (cause merge) and agent.status done.
    await page.evaluate(() => {
      window.reviewTest.apply(23);
    });
    await browserExpect(panel(page).getByText("Approved and merged into main")).toBeVisible();
    await browserExpect(approve).toHaveCount(0);
    // The panel shows the outcome while the graph is back to its normal lights.
    await browserExpect.poll(async () => (await probe(page, "node.dimmed")).ids).toEqual([]);
    const after = await probe(page, "node");
    expect(after.positions).toEqual(before.positions);
    expect((await probe(page, "node.pending")).ids).toEqual([]);
  });
}, 30_000);

it("approves only on a pointer click: Tab then Enter or Space says so and sends nothing", async () => {
  await withPage(false, async (page) => {
    await page.getByRole("button", { name: /change ready for review/ }).click();
    const approve = panel(page).getByRole("button", { name: "Approve and merge" });
    const comment = panel(page).getByRole("textbox", { name: "Comment (optional)" });
    const caption = panel(page).getByText("Approve with a click: it merges into main");
    await comment.click();
    await page.keyboard.type("Looks right");
    await page.keyboard.press("Tab");
    await browserExpect(approve).toBeFocused();
    await browserExpect(caption).toHaveCount(0);
    for (const key of ["Enter", "Space"]) {
      await page.keyboard.press(key);
      await browserExpect(caption).toBeVisible();
      expect(await page.evaluate(() => window.reviewTest.decisions.length)).toBe(0);
      await browserExpect(approve).toBeEnabled();
      // Pressing a key does not move focus either.
      await browserExpect(approve).toBeFocused();
    }
    // A script's click has no pointer behind it, so it is refused too.
    await approve.evaluate((button: HTMLButtonElement) => {
      button.click();
    });
    expect(await page.evaluate(() => window.reviewTest.decisions.length)).toBe(0);

    await approve.click();
    await browserExpect(caption).toHaveCount(0);
    const sent = await page.evaluate(() => window.reviewTest.decisions);
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      changeId: "change-1",
      action: "approve",
      comment: "Looks right",
    });
  });
}, 30_000);

it("keeps Reject keyboard-operable", async () => {
  await withPage(false, async (page) => {
    await page.getByRole("button", { name: /change ready for review/ }).click();
    const reject = panel(page).getByRole("button", { name: "Reject" });
    await panel(page).getByRole("textbox", { name: "Comment (optional)" }).click();
    await page.keyboard.press("Tab");
    await page.keyboard.press("Tab");
    await browserExpect(reject).toBeFocused();
    await page.keyboard.press("Enter");
    const sent = await page.evaluate(() => window.reviewTest.decisions);
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ changeId: "change-1", action: "reject" });
  });
}, 30_000);

it("Escape in the comment box only leaves it; Escape elsewhere closes review", async () => {
  await withPage(false, async (page) => {
    await page.getByRole("button", { name: /change ready for review/ }).click();
    const comment = panel(page).getByRole("textbox", { name: "Comment (optional)" });
    await comment.click();
    await page.keyboard.type("Looks right");
    await page.keyboard.press("Escape");
    await browserExpect(comment).not.toBeFocused();
    await browserExpect(panel(page)).toHaveCount(1);
    expect(await comment.inputValue()).toBe("Looks right");
    await page.keyboard.press("Escape");
    await browserExpect(panel(page)).toHaveCount(0);
  });
}, 30_000);

it("in a recorded run offers no decision and shows the recorded one when it arrives", async () => {
  await withPage(true, async (page) => {
    await page.getByRole("button", { name: /change ready for review/ }).click();
    await browserExpect(panel(page).getByText(/Recorded run: nothing here sends/)).toBeVisible();
    await browserExpect(panel(page).getByRole("textbox", { name: /Comment/ })).toHaveCount(0);
    await browserExpect(
      panel(page).getByRole("button", { name: "Approve and merge" }),
    ).toBeDisabled();
    await browserExpect(panel(page).getByRole("button", { name: "Reject" })).toBeDisabled();
    await page.evaluate(() => {
      window.reviewTest.apply(21);
    });
    await browserExpect(panel(page).getByText("Approved and merged into main")).toBeVisible();
    expect(await page.evaluate(() => window.reviewTest.decisions.length)).toBe(0);
  });
}, 30_000);
