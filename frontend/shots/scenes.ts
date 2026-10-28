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
  log?: "editing" | "proposed" | "one-slot" | "handwritten-two-slots-braked.jsonl";
  edgeShapes?: readonly ("bezier" | "taxi")[];
  /** What enables a disabled scene. Printed next to "skipped". */
  needs?: string;
  /** Puts the page in this scene's state, after the shell is ready. Nothing to do when omitted. */
  prepare?: (page: Page, context: { jsonl: string; resume?: () => Promise<void> }) => Promise<void>;
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
 * settle, then checks the change and cone framing with legible labels and state words.
 * Cytoscape draws on a canvas, so the node's place on screen comes from the graph element's own
 * probe (src/graph/probe.ts), which the app exposes for this. A proposed change opens review; one
 * still being edited opens the live diff.
 */
async function clickChangedNode(page: Page, panel: string): Promise<string> {
  const graph = '[aria-label="Repository graph"]';
  const handle = await page.waitForFunction((selector) => {
    const host = document.querySelector<GraphProbeHost>(selector);
    const node = host?.oversightGraph?.nodeCenters("node.pending")[0];
    if (!host || !node) return null;
    const slot = ["A", "B"].find((slot) =>
      host.oversightGraph?.nodeBounds(`node.pending-${slot}`).some((bound) => bound.id === node.id),
    );
    if (!slot) return null;
    const box = host.getBoundingClientRect();
    return { id: node.id, x: box.left + node.x, y: box.top + node.y, slot };
  }, graph);
  const { id, x, y, slot } = (await handle.jsonValue()) as {
    id: string;
    x: number;
    y: number;
    slot: string;
  };
  const pending = `node.pending-${slot}`;
  await page.mouse.click(x, y);
  await page.locator(`[aria-label="${panel}"] .line-insert`).first().waitFor();
  await page.waitForFunction((selector) => {
    const host = document.querySelector<GraphProbeHost>(selector);
    return host?.oversightGraph?.animating() === false;
  }, graph);
  // Full lit bounds include labels and badges; the graph's position between panel and rail is its
  // viewport. Distant cone nodes use the out-of-view rows when the full cone cannot fit legibly.
  const framed = await page.evaluate(
    ({ selector, pending }) => {
      const host = document.querySelector<GraphProbeHost>(selector);
      const view = host?.oversightGraph;
      return host && view
        ? {
            zoom: view.zoom(),
            labelSizes: {
              name: Number.parseFloat(
                getComputedStyle(document.documentElement).getPropertyValue("--node-name-size"),
              ),
              state: Number.parseFloat(
                getComputedStyle(document.documentElement).getPropertyValue("--node-word-size"),
              ),
            },
            area: (() => {
              const clear = document
                .querySelector("[data-graph-clear-area]")
                ?.getBoundingClientRect();
              if (!clear) throw new Error("Missing graph clear area");
              const graph = host.getBoundingClientRect();
              const card = document
                .querySelector('[aria-label="Possibly affected"]')
                ?.getBoundingClientRect();
              return {
                x1: (card ? card.right - graph.left : 0) + 40,
                y1: 40,
                x2: host.clientWidth - 40,
                y2: Math.min(host.clientHeight - 40, clear.bottom - graph.top),
              };
            })(),
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
            // The panel frames its agent's change; the other agent stays pending on the graph.
            focus: view.nodeBounds(pending),
            cone: view.nodeBounds("node.cone"),
            graph: view.nodeBounds(),
            modules: view.nodeBounds(".module"),
          }
        : null;
    },
    { selector: graph, pending },
  );
  if (!framed?.focus.some((node) => node.id === id))
    throw new Error(`the clicked node ${id} is not on the graph`);
  assertPanelFrame(framed, panel === "Review panel" ? "review" : "live-diff");
  return pending;
}

async function openFileView(page: Page): Promise<void> {
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
  for (const foldedId of ["shop.cart", "shop.cart:Cart"]) {
    const folded = await page.evaluate((wanted) => {
      const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
      const row = host?.oversightGraph?.nodeCenters(".folded").find(({ id }) => id === wanted);
      const box = host?.getBoundingClientRect();
      return row && box ? { x: box.left + row.x, y: box.top + row.y } : null;
    }, foldedId);
    if (folded) {
      await page.mouse.click(folded.x, folded.y);
      await page.waitForFunction(
        () =>
          document
            .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
            ?.oversightGraph?.animating() === false,
      );
    }
  }
  const point = await page.evaluate(() => {
    const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
    const node = host?.oversightGraph?.nodeCenters().find(({ id }) => id === "shop.cart:Cart.add");
    if (!host || !node) throw new Error("The untouched cart node is missing");
    const box = host.getBoundingClientRect();
    return { x: box.left + node.x, y: box.top + node.y };
  });
  await page.mouse.click(point.x, point.y);
  const panel = page.getByRole("complementary", { name: "File view" });
  await panel.locator(".monaco-editor .view-lines").waitFor();
  await page.waitForFunction(
    () =>
      document.querySelector('[aria-label="File view"]')?.getAttribute("data-state") === "clean",
  );
  await page.waitForFunction(
    () =>
      document
        .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
        ?.oversightGraph?.animating() === false,
  );
}

export const scenes: readonly Scene[] = [
  // Watch mode: the graph and the agents panel, captured once the replay has put a card in it.
  {
    name: "watch",
    enabled: true,
    log: "proposed",
    prepare: async (page) => {
      await page.locator('article[aria-label^="Agent "]').first().waitFor({ state: "visible" });
    },
  },
  // The fit button: the whole graph in view, as the person gets it by asking. Watch starts at 100%.
  {
    name: "watch-fit",
    enabled: true,
    log: "proposed",
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
  // dimmed but for the change and its cone. Cut before the first decision to retain proposals.
  {
    name: "review",
    enabled: true,
    log: "proposed",
    prepare: async (page, { jsonl }) => {
      // The replay API has no worktrees. Read only the diff present in this scene's log.
      const updates = jsonl
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
    prepare: async (page) => {
      await clickChangedNode(page, "Live diff panel");
    },
  },
  {
    name: "focus",
    enabled: true,
    log: "editing",
    prepare: async (page, { resume }) => {
      const pendingSelector = await clickChangedNode(page, "Live diff panel");
      await openFileView(page);
      if (!resume) throw new Error("Focus scene needs the rest of the proposal stream");
      await resume();
      await page.getByRole("button", { name: /changes? ready for review/ }).click();
      await page.getByRole("complementary", { name: "Review panel", exact: true }).waitFor();
      await page.getByRole("tab", { name: /Live diff ·/ }).click();
      await page.getByRole("button", { name: "Files", exact: true }).click();
      await page
        .getByRole("tree", { name: "Repository files" })
        .getByRole("treeitem", { name: "shop", exact: true })
        .click();
      await page
        .getByRole("tree", { name: "Repository files" })
        .getByRole("treeitem", { name: "pricing.py", exact: true })
        .waitFor();
      await page.getByRole("button", { name: "Toggle bottom dock", exact: true }).click();
      assert.equal(
        await page
          .getByRole("tablist", { name: "Open files and changes" })
          .getByRole("tab")
          .count(),
        3,
      );
      await page.locator('[aria-label="Live diff panel"] .line-insert').first().waitFor();
      await page.waitForFunction(
        () =>
          document
            .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
            ?.oversightGraph?.animating() === false,
      );
      const framed = await page.evaluate((selector) => {
        const host = document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]');
        const pending = host?.oversightGraph?.nodeCenters(selector) ?? [];
        return (
          !!host &&
          pending.length > 0 &&
          pending.every(
            (node) =>
              node.x > 0 && node.x < host.clientWidth && node.y > 0 && node.y < host.clientHeight,
          )
        );
      }, pendingSelector);
      assert.equal(
        framed,
        true,
        "The active live diff stays framed after sidebar and dock toggles",
      );
    },
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
    prepare: openFileView,
  },
  {
    name: "file-view-task",
    enabled: true,
    prepare: async (page) => {
      await openFileView(page);
      await page.getByRole("button", { name: "Start an agent here" }).click();
      const box = page.getByLabel("Task for the new agent");
      assert.equal(await box.inputValue(), "In shop/cart.py, Cart.add: ");
      assert.equal(await box.evaluate((element) => document.activeElement === element), true);
    },
  },
  {
    name: "palette-open",
    enabled: true,
    log: "proposed",
    prepare: async (page) => {
      await page
        .getByRole("button", { name: "Find a function, file or command", exact: true })
        .click();
      const dialog = page.getByRole("dialog", { name: "Find a function, file or command" });
      await dialog.getByRole("combobox").fill("cart");
      await dialog.getByRole("group", { name: "Files", exact: true }).waitFor();
      assert.equal(
        await dialog
          .getByRole("combobox")
          .evaluate((element) => document.activeElement === element),
        true,
      );
    },
  },
  {
    name: "dock-open",
    enabled: true,
    prepare: async (page) => {
      await page.getByRole("button", { name: "Toggle bottom dock", exact: true }).click();
      await page.getByRole("tabpanel", { name: "Tests", exact: true }).waitFor();
      assert.ok(await page.getByRole("list", { name: "Test runs", exact: true }).isVisible());
      assert.equal(
        (await page.getByLabel("Bottom dock", { exact: true }).boundingBox())?.height,
        280,
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
