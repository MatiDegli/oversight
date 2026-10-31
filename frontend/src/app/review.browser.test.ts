import { expect as browserExpect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { expect, it } from "vitest";

import type {} from "./review.browser.fixture";
import type { FieldProbe, GraphProbeHost } from "../graph";
import { assertReadableFrame } from "../../shots/framing";
import { panel, withPage } from "./review.browser.support";

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
    const outside = [...document.querySelectorAll('[aria-label="Possibly affected"] li')]
      .filter((row) => row.textContent.includes("Out of view"))
      .map((row) => row.querySelector("[title]")?.getAttribute("title") ?? "");
    return {
      representative: view.representative(wanted),
      representedOutOfView: [...new Set(outside.map((id) => view.representative(id)))],
      zoom: view.zoom(),
      labelSizes: {
        name: Number.parseFloat(
          getComputedStyle(document.documentElement).getPropertyValue("--node-name-size"),
        ),
        state: Number.parseFloat(
          getComputedStyle(document.documentElement).getPropertyValue("--node-word-size"),
        ),
      },
      leftInset: (() => {
        const card = document.querySelector('[aria-label="Possibly affected"]');
        return card ? card.getBoundingClientRect().right - element.getBoundingClientRect().left : 0;
      })(),
      card: (() => {
        const box = document
          .querySelector('[aria-label="Possibly affected"]')
          ?.parentElement?.getBoundingClientRect();
        const graph = element.getBoundingClientRect();
        return box
          ? {
              x1: box.left - graph.left,
              y1: box.top - graph.top,
              x2: box.right - graph.left,
              y2: box.bottom - graph.top,
            }
          : undefined;
      })(),
      outOfView: [...document.querySelectorAll('[aria-label="Possibly affected"] li')]
        .filter((row) => row.textContent.includes("Out of view"))
        .map((row) => row.querySelector("[title]")?.getAttribute("title") ?? ""),
      pan: view.pan(),
      animating: view.animating(),
      size: { width: element.clientWidth, height: element.clientHeight },
      node: view.nodeCenters().find((center) => center.id === view.representative(wanted)),
      area: (() => {
        const graph = element.getBoundingClientRect();
        const clear = document.querySelector("[data-graph-clear-area]")?.getBoundingClientRect();
        if (!clear) throw new Error("Missing graph clear area");
        const card = document
          .querySelector('[aria-label="Possibly affected"]')
          ?.getBoundingClientRect();
        return {
          x1: (card ? card.right - graph.left : 0) + 40,
          y1: 40,
          x2: element.clientWidth - 40,
          y2: Math.min(element.clientHeight - 40, clear.bottom - graph.top),
        };
      })(),
      focus: view.nodeBounds().filter((node) => node.id === view.representative(wanted)),
      cone: view.nodeBounds("node.cone"),
      graph: view.nodeBounds(),
      modules: view.nodeBounds(".module"),
    };
  }, id);
}

it("the queue counter reframes each proposal while review stays open and keeps keyboard focus", async () => {
  await withPage(false, async (page) => {
    const target = await page.evaluate(() => window.reviewTest.addSecondProposal());
    const positions = (await probe(page, ":childless")).positions;
    const queue = page.getByRole("button", { name: /^Decisions:/ });
    await queue.click();
    await panel(page).locator(".line-insert").first().waitFor();
    const firstId = await page.evaluate(() => window.reviewTest.changed);
    await browserExpect.poll(async () => (await camera(page, firstId)).animating).toBe(false);
    const first = await camera(page, firstId);
    await queue.click();
    await browserExpect(panel(page).getByText("Second proposal", { exact: true })).toBeVisible();
    await browserExpect.poll(async () => (await camera(page, target)).animating).toBe(false);
    const second = await camera(page, target);
    expect(second.pan).not.toEqual(first.pan);
    assertReadableFrame({ ...second, outOfView: second.representedOutOfView });
    expect((await probe(page, ":childless")).positions).toEqual(positions);
    await browserExpect(queue).toBeFocused();
    await queue.click();
    await browserExpect(panel(page).getByText("Second proposal", { exact: true })).toHaveCount(0);
  });
}, 30_000);

it("disables WebGL and leaves the field hidden on the product's plain ground", async () => {
  await withPage(false, async (page) => {
    // No context shim: the shared launcher's Chromium flag must select the product fallback.
    const fieldCanvas = page
      .getByRole("img", { name: "Repository graph", exact: true })
      .locator("..")
      .locator(":scope > canvas");
    await browserExpect(fieldCanvas).toHaveCount(1);
    const field = await fieldCanvas.evaluate((element) => {
      const canvas = element as HTMLCanvasElement & { oversightField?: FieldProbe };
      return {
        webgl2: document.createElement("canvas").getContext("webgl2") === null,
        hidden: canvas.hidden,
        state: canvas.oversightField?.state(),
        draws: canvas.oversightField?.draws(),
      };
    });
    expect(field).toEqual({ webgl2: true, hidden: true, state: "plain", draws: 0 });
  });
});

it("opens review only from the person's click, keeps the change and cone lit, dims the rest, and closes", async () => {
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
    // Compound frames dim too, while each lit child keeps full opacity independently.
    const dimmed = new Set((await probe(page, "node.dimmed")).ids);
    const nodes = (await probe(page, "node")).ids;
    const lit = await page.evaluate(() => {
      const probe = document.querySelector<GraphProbeHost>(
        '[aria-label="Repository graph"]',
      )?.oversightGraph;
      return [...new Set(window.reviewTest.lit.map((id) => probe?.representative(id) ?? id))];
    });
    expect(nodes.filter((id) => !dimmed.has(id)).sort()).toEqual([...lit].sort());
    expect((await probe(page, "node.cone")).ids.sort()).toEqual(
      lit.filter((id) => id !== changed).sort(),
    );

    await page.getByRole("button", { name: "Close review" }).click();
    await browserExpect(panel(page)).toHaveCount(0);
    await browserExpect.poll(async () => (await probe(page, "node.dimmed")).ids).toEqual([]);
  });
}, 30_000);

it("frames the cone with legible labels, clears the card, and marks only clipped boxes", async () => {
  await withPage(false, async (page) => {
    const changed = await page.evaluate(() => window.reviewTest.changed);
    const watch = await camera(page, changed);
    const positions = (await probe(page, ":childless")).positions;
    await page.getByRole("button", { name: "Review change" }).click();
    await panel(page).locator(".line-insert").first().waitFor();
    await browserExpect.poll(async () => (await camera(page, changed)).animating).toBe(false);
    const review = await camera(page, changed);
    expect(review.leftInset).toBeCloseTo(304);
    expect(
      Math.min(review.labelSizes.name, review.labelSizes.state) * review.zoom,
    ).toBeGreaterThanOrEqual(11 - 1e-6);
    expect(review.zoom).toBeLessThanOrEqual(1);
    assertReadableFrame({ ...review, outOfView: review.representedOutOfView });
    // All excluded nodes remain lit and listed; the camera never removes cone entries.
    for (const id of review.outOfView) {
      expect((await probe(page, "node.cone:not(.dimmed)")).ids).toContain(
        (await camera(page, id)).representative,
      );
      await browserExpect(
        page
          .getByRole("region", { name: "Possibly affected", exact: true })
          .getByTitle(id)
          .getByText("Out of view"),
      ).toBeVisible();
    }
    expect((await probe(page, ":childless")).positions).toEqual(positions);

    await page.getByRole("button", { name: "Close review" }).click();
    await browserExpect.poll(async () => (await camera(page, changed)).animating).toBe(false);
    const restored = await camera(page, changed);
    expect(restored.zoom).toBeCloseTo(watch.zoom, 10);
    expect(restored.pan.x).toBeCloseTo(watch.pan.x, 10);
    expect(restored.pan.y).toBeCloseTo(watch.pan.y, 10);
  });
}, 30_000);

it("pans to a node its cone row lists as out of view only when that row is clicked", async () => {
  await withPage(false, async (page) => {
    const changed = await page.evaluate(() => window.reviewTest.changed);
    await page.getByRole("button", { name: "Review change" }).click();
    await panel(page).locator(".line-insert").first().waitFor();
    await browserExpect.poll(async () => (await camera(page, changed)).animating).toBe(false);
    // The opening frame now shows the cone. A deliberate zoom makes a row out of view.
    for (let step = 0; step < 3; step++) {
      await page.getByRole("button", { name: "Zoom in", exact: true }).click();
      await browserExpect.poll(async () => (await camera(page, changed)).animating).toBe(false);
    }
    const review = await camera(page, changed);
    const id = review.outOfView[0];
    if (!id) throw new Error("The demo's review should list a caller out of view");
    const positions = (await probe(page, ":childless")).positions;
    const card = page.getByRole("region", { name: "Possibly affected", exact: true });
    // The caller is off screen or behind the card, and the camera has not gone to it on its own.
    expect(review.outOfView).toContain(id);
    await card.locator(`button[title="${id}"]`).click();
    await browserExpect.poll(async () => (await camera(page, id)).animating).toBe(false);
    const after = await camera(page, id);
    // The same zoom, the node in the middle of what the card leaves free, and no node moved.
    expect(after.zoom).toBeCloseTo(review.zoom, 10);
    expect(after.node?.x).toBeCloseTo((review.leftInset + after.size.width) / 2, 0);
    expect(after.node?.y).toBeCloseTo(after.size.height / 2, 0);
    expect((await probe(page, ":childless")).positions).toEqual(positions);
    // The row no longer says it is out of view, so it is no longer a button.
    await browserExpect(card.locator(`button[title="${id}"]`)).toHaveCount(0);
    expect((await camera(page, id)).outOfView).not.toContain(id);
  });
}, 30_000);

it("never approves from the keyboard in the comment box; the click waits for change.resolved", async () => {
  await withPage(false, async (page) => {
    let releaseEditor!: () => void;
    const loading = new Promise<void>((resolve) => {
      releaseEditor = resolve;
    });
    await page.route("**/src/editor/ReviewEditor.tsx", async (route) => {
      await loading;
      await route.continue();
    });
    await page.getByRole("button", { name: /^Decisions:/ }).click();
    const approve = panel(page).getByRole("button", { name: "Approve and merge" });
    const reject = panel(page).getByRole("button", { name: "Reject" });
    try {
      await browserExpect(
        panel(page)
          .getByRole("status", { name: "", exact: true })
          .filter({ hasText: "Loading editor…" }),
      ).toBeVisible();
      await browserExpect(approve).toBeDisabled();
    } finally {
      releaseEditor();
      await page.unrouteAll({ behavior: "wait" });
    }
    await browserExpect(panel(page).getByLabel("Editable review file")).toHaveAttribute(
      "data-state",
      "clean",
    );
    const comment = panel(page).getByRole("textbox", {
      name: "Comment for the agent",
    });
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
    await browserExpect(
      panel(page).getByRole("region", { name: "Decision", exact: true }).getByRole("status"),
    ).toHaveText("Approving: waiting for the merge result.");
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

it("approves only on a pointer click: Tab then Enter or Space sends nothing and gives no hint", async () => {
  await withPage(false, async (page) => {
    await page.getByRole("button", { name: /^Decisions:/ }).click();
    const approve = panel(page).getByRole("button", { name: "Approve and merge" });
    const comment = panel(page).getByRole("textbox", {
      name: "Comment for the agent",
    });
    const caption = panel(page).getByText("Approve with a click: it merges into main");
    await browserExpect(approve).toBeEnabled();
    await comment.click();
    await page.keyboard.type("Looks right");
    await page.keyboard.press("Tab");
    await browserExpect(approve).toBeFocused();
    await browserExpect(caption).toHaveCount(0);
    for (const key of ["Enter", "Space"]) {
      await page.keyboard.press(key);
      await browserExpect(caption).toHaveCount(0);
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
    await page.getByRole("button", { name: /^Decisions:/ }).click();
    const reject = panel(page).getByRole("button", { name: "Reject" });
    await browserExpect(
      panel(page).getByRole("button", { name: "Approve and merge" }),
    ).toBeEnabled();
    await panel(page).getByRole("textbox", { name: "Comment for the agent" }).click();
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
    await page.getByRole("button", { name: /^Decisions:/ }).click();
    const comment = panel(page).getByRole("textbox", {
      name: "Comment for the agent",
    });
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
    await page.getByRole("button", { name: /^Decisions:/ }).click();
    await browserExpect(
      panel(page).getByText(/Replay of a hand-written demo fixture: nothing here sends/),
    ).toBeVisible();
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
