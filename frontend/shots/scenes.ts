// The scenes and themes `make shots` captures. This is the file to edit when a front lands: flip a
// scene's `enabled` to true and give it a `prepare` step if the page must be put in that state.
// A disabled entry prints "skipped" with its `needs` text and never fails the run.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

import type { ServerEvent } from "@contracts";
import type { Page } from "@playwright/test";
import type { GraphProbeHost } from "../src/graph";

import { assertPanelFrame } from "./framing.ts";

export interface Scene {
  name: string;
  enabled: boolean;
  /** Use a derived cut or a hand-written fixture instead of the served proposal log. */
  log?: "editing" | "one-slot" | "handwritten-two-slots-braked.jsonl";
  edgeShapes?: readonly ("bezier" | "taxi")[];
  /** What enables a disabled scene. Printed next to "skipped". */
  needs?: string;
  /** Puts the page in this scene's state, after the shell is ready. Nothing to do when omitted. */
  prepare?: (page: Page) => Promise<void>;
}

export interface Theme {
  name: string;
  enabled: boolean;
  /** What enables a disabled theme. Printed next to "skipped". */
  needs?: string;
}

/**
 * Clicks the center of the first node with a pending change, as the person would, waits for the
 * left panel (`panel`, its aria-label) to show the diff's inserted lines and for the camera to
 * settle, then checks the change framing (with the cone only in review) against watch zoom and
 * the 12px label floor.
 * Cytoscape draws on a canvas, so the node's place on screen comes from the graph element's own
 * probe (src/graph/probe.ts), which the app exposes for this. A proposed change opens review; one
 * still being edited opens the live diff.
 */
async function clickChangedNode(page: Page, panel: string): Promise<void> {
  const graph = '[aria-label="Repository graph"]';
  const handle = await page.waitForFunction((selector) => {
    const host = document.querySelector<GraphProbeHost>(selector);
    const node = host?.oversightGraph?.nodeCenters("node.pending")[0];
    if (!host || !node) return null;
    const box = host.getBoundingClientRect();
    return { id: node.id, x: box.left + node.x, y: box.top + node.y };
  }, graph);
  const { id, x, y } = (await handle.jsonValue()) as { id: string; x: number; y: number };
  const zoomBefore = await page.evaluate(
    (selector) => document.querySelector<GraphProbeHost>(selector)?.oversightGraph?.zoom(),
    graph,
  );
  await page.mouse.click(x, y);
  await page.locator(`[aria-label="${panel}"] .line-insert`).first().waitFor();
  await page.waitForFunction((selector) => {
    const host = document.querySelector<GraphProbeHost>(selector);
    return host?.oversightGraph?.animating() === false;
  }, graph);
  // Full lit bounds include labels and badges; the graph's position between panel and rail is its
  // viewport. Distant cone nodes may be clipped once further shrinking would make labels unreadable.
  const framed = await page.evaluate(
    ({ selector, nodeId }) => {
      const host = document.querySelector<GraphProbeHost>(selector);
      const view = host?.oversightGraph;
      return host && view
        ? {
            zoom: view.zoom(),
            leftInset: (() => {
              const card = document.querySelector('[aria-label="Possibly affected"]');
              return card
                ? card.getBoundingClientRect().right - host.getBoundingClientRect().left
                : 0;
            })(),
            card: (() => {
              const box = document
                .querySelector('[aria-label="Possibly affected"]')
                ?.parentElement?.getBoundingClientRect();
              const graph = host.getBoundingClientRect();
              return box
                ? {
                    x1: box.left - graph.left,
                    y1: box.top - graph.top,
                    x2: box.right - graph.left,
                    y2: box.bottom - graph.top,
                  }
                : undefined;
            })(),
            outOfView: [
              ...new Set(
                [...document.querySelectorAll('[aria-label="Possibly affected"] li')]
                  .filter((row) => row.textContent.includes("Out of view"))
                  .map((row) =>
                    view.representative(row.querySelector("[title]")?.getAttribute("title") ?? ""),
                  ),
              ),
            ],
            pan: view.pan(),
            size: { width: host.clientWidth, height: host.clientHeight },
            focus: view.nodeBounds().filter((node) => node.id === nodeId),
            cone: view.nodeBounds("node.cone"),
          }
        : null;
    },
    { selector: graph, nodeId: id },
  );
  if (!framed?.focus.length) throw new Error(`the clicked node ${id} is not on the graph`);
  if (zoomBefore === undefined) throw new Error("the watch zoom is unavailable");
  if (panel === "Review panel") {
    assert(Math.abs(framed.zoom - 0.8) < 1e-6, "the demo cone must stay at the 12px floor");
  }
  assertPanelFrame(zoomBefore, framed, panel === "Review panel" ? "review" : "live-diff");
}

export const scenes: readonly Scene[] = [
  // Watch mode: the graph and the agents panel, captured once the replay has put a card in it.
  {
    name: "watch",
    enabled: true,
    prepare: async (page) => {
      await page.locator('article[aria-label^="Agent "]').first().waitFor({ state: "visible" });
    },
  },
  // The fit button: the whole graph in view, as the person gets it by asking. Watch starts at 100%.
  {
    name: "watch-fit",
    enabled: true,
    prepare: async (page) => {
      await page.locator('article[aria-label^="Agent "]').first().waitFor({ state: "visible" });
      await page.getByRole("button", { name: "Fit the graph to the view" }).click();
      await page.waitForFunction(() => {
        const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
        return host?.oversightGraph?.animating() === false;
      });
    },
  },
  // Review: the proposed change's node opens it, with Monaco's inline diff on the left and the graph
  // dimmed but for the change and its cone. It needs a log that ends while the change is proposed: `make shots`
  // serves handwritten-level1-proposed.jsonl, the hand-written log cut after seq 20.
  {
    name: "review",
    enabled: true,
    prepare: async (page) => {
      // The replay API has no worktrees. Read only the diff already present in this fixture.
      const text = await readFile(
        new URL("../../fixtures/events/handwritten-level1-proposed.jsonl", import.meta.url),
        "utf8",
      );
      const updates = text
        .trim()
        .split("\n")
        .map((line) => JSON.parse(line) as ServerEvent)
        .filter((event) => event.type === "change.updated");
      await page.route("**/files?*", (route) => {
        const path = new URL(route.request().url()).searchParams.get("path");
        const update = updates.findLast((event) => event.payload.file.path === path);
        if (!update) return route.abort();
        const content = update.payload.file.modified ?? "";
        return route.fulfill({
          json: {
            path,
            content,
            agent_id: update.payload.agent_id,
            hash: createHash("sha256").update(content).digest("hex"),
          },
        });
      });
      await clickChangedNode(page, "Review panel");
      await page
        .getByLabel("Editable review file")
        .getByText("Edits go to the agent’s worktree")
        .waitFor();
      await page.waitForFunction(
        () =>
          document
            .querySelector('[aria-label="Editable review file"]')
            ?.getAttribute("data-state") === "clean",
      );
    },
  },
  {
    name: "live-diff",
    enabled: true,
    log: "editing",
    prepare: (page) => clickChangedNode(page, "Live diff panel"),
  },
  {
    name: "task-one-slot",
    enabled: true,
    log: "one-slot",
    prepare: async (page) => {
      await page.getByLabel("Task for the new agent").fill("Cap the combined discount at 30%");
      assert.equal(await page.getByRole("group", { name: "Agent slot" }).count(), 0);
    },
  },
  {
    name: "task-two-slots-braked",
    enabled: true,
    log: "handwritten-two-slots-braked.jsonl",
    prepare: async (page) => {
      await page
        .getByLabel("Task for the new agent")
        .fill("Add a second task with Muse Spark Open");
      const a = page.getByRole("button", { name: "A Muse Spark", exact: true });
      const b = page.getByRole("button", { name: "B Muse Spark Open", exact: true });
      assert.equal(await a.getAttribute("aria-disabled"), "false");
      assert.equal(await b.getAttribute("aria-pressed"), "true");
    },
  },
  {
    name: "file-view",
    enabled: true,
    // The replay API has no file contents. This scene reads the demo template through a GET
    // double; it is a capture of the file view, not a saved edit reconstructed from the log.
    prepare: async (page) => {
      const path = "shop/cart.py";
      // The uncut start camera may leave Cart wholly outside the view. Reveal it as a person
      // would before opening the file; a screenshot must not click an offscreen class row.
      await page.getByRole("button", { name: "Fit the graph to the view" }).click();
      await page.waitForFunction(
        () =>
          document
            .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
            ?.oversightGraph?.animating() === false,
      );
      const content = await readFile(new URL(`../../demo-repo/${path}`, import.meta.url), "utf8");
      await page.route("**/files?*", (route) =>
        route.fulfill({
          json: {
            path,
            content,
            hash: createHash("sha256").update(content).digest("hex"),
            agent_id: null,
          },
        }),
      );
      const folded = await page.evaluate(() => {
        const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
        const row = host?.oversightGraph
          ?.nodeCenters(".folded")
          .find(({ id }) => id === "shop.cart:Cart");
        const box = host?.getBoundingClientRect();
        return row && box ? { x: box.left + row.x, y: box.top + row.y } : null;
      });
      if (folded) {
        await page.mouse.click(folded.x, folded.y);
        await page.waitForFunction(
          () =>
            document
              .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
              ?.oversightGraph?.animating() === false,
        );
      }
      const point = await page.evaluate(() => {
        const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
        const node = host?.oversightGraph
          ?.nodeCenters()
          .find(({ id }) => id === "shop.cart:Cart.add");
        if (!host || !node) throw new Error("The untouched cart node is missing");
        const box = host.getBoundingClientRect();
        return { x: box.left + node.x, y: box.top + node.y };
      });
      await page.mouse.click(point.x, point.y);
      const panel = page.getByRole("complementary", { name: "File view" });
      await panel.locator(".monaco-editor .view-lines").waitFor();
      await page.waitForFunction(
        () =>
          document.querySelector('[aria-label="File view"]')?.getAttribute("data-state") ===
          "clean",
      );
      await page.waitForFunction(
        () =>
          document
            .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
            ?.oversightGraph?.animating() === false,
      );
    },
  },
];

export const themes: readonly Theme[] = [
  { name: "dark", enabled: true },
  {
    name: "light",
    enabled: false,
    // Enabled by: day mode, which does not exist yet. The capture already sets data-theme on the
    // root element before the screenshot, so only the theme's styles are missing.
    needs: "day mode (not built yet)",
  },
];
