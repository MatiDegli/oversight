// The scenes and themes `make shots` captures. This is the file to edit when a front lands: flip a
// scene's `enabled` to true and give it a `prepare` step if the page must be put in that state.
// A disabled entry prints "skipped" with its `needs` text and never fails the run.
import type { Page } from "@playwright/test";

export interface Scene {
  name: string;
  enabled: boolean;
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
 * settle, then fails unless that node is centered in the graph area at the zoom it had before.
 * Cytoscape draws on a canvas, so the node's place on screen comes from the graph element's own
 * probe (src/graph/probe.ts), which the app exposes for this. A proposed change opens review; one
 * still being edited opens the live diff.
 */
async function clickChangedNode(page: Page, panel: string): Promise<void> {
  type Probe = {
    nodeCenters(selector?: string): { id: string; x: number; y: number }[];
    animating(): boolean;
    zoom(): number;
  };
  const graph = '[aria-label="Repository graph"]';
  const handle = await page.waitForFunction((selector) => {
    const host = document.querySelector<HTMLElement & { oversightGraph?: Probe }>(selector);
    const node = host?.oversightGraph?.nodeCenters("node.pending")[0];
    if (!host || !node) return null;
    const box = host.getBoundingClientRect();
    return { id: node.id, x: box.left + node.x, y: box.top + node.y };
  }, graph);
  const { id, x, y } = (await handle.jsonValue()) as { id: string; x: number; y: number };
  const zoomBefore = await page.evaluate(
    (selector) =>
      document
        .querySelector<HTMLElement & { oversightGraph?: Probe }>(selector)
        ?.oversightGraph?.zoom(),
    graph,
  );
  await page.mouse.click(x, y);
  await page.locator(`[aria-label="${panel}"] .line-insert`).first().waitFor();
  await page.waitForFunction((selector) => {
    const host = document.querySelector<HTMLElement & { oversightGraph?: Probe }>(selector);
    return host?.oversightGraph?.animating() === false;
  }, graph);
  // The labels keep the size they had in watch, and the change sits in the middle of the graph area
  // between the panel and the rail (the log's change is one node, so it always fits).
  const framed = await page.evaluate(
    ({ selector, nodeId }) => {
      const host = document.querySelector<HTMLElement & { oversightGraph?: Probe }>(selector);
      const node = host?.oversightGraph?.nodeCenters().find((center) => center.id === nodeId);
      return host && node
        ? {
            zoom: host.oversightGraph?.zoom(),
            dx: node.x - host.clientWidth / 2,
            dy: node.y - host.clientHeight / 2,
          }
        : null;
    },
    { selector: graph, nodeId: id },
  );
  if (!framed) throw new Error(`the clicked node ${id} is not on the graph`);
  if (zoomBefore === undefined || Math.abs((framed.zoom ?? 0) - zoomBefore) > 1e-6)
    throw new Error(`the zoom moved from ${String(zoomBefore)} to ${String(framed.zoom)}`);
  if (Math.abs(framed.dx) > 2 || Math.abs(framed.dy) > 2)
    throw new Error(
      `the clicked node ${id} is not centered: off by ${String(framed.dx)}, ${String(framed.dy)}`,
    );
}

export const scenes: readonly Scene[] = [
  // Watch mode: the graph and the agents panel, captured once the replay has put a card in it.
  {
    name: "watch",
    enabled: true,
    edgeShapes: ["bezier", "taxi"],
    prepare: async (page) => {
      await page.locator('article[aria-label^="Agent "]').first().waitFor({ state: "visible" });
    },
  },
  // Review: the proposed change's node opens it, with Monaco's inline diff on the left and the graph
  // dimmed but for the change. It needs a log that ends while the change is proposed: `make shots`
  // serves handwritten-level1-proposed.jsonl, the hand-written log cut after seq 20.
  {
    name: "review",
    enabled: true,
    prepare: (page) => clickChangedNode(page, "Review panel"),
  },
  {
    name: "live-diff",
    // Ready: `prepare` clicks a changed node and waits for Monaco's inline diff. It needs a log that
    // ends while the change is being edited: once proposed (seq 19) the node opens review instead.
    // Run it with LOG set to a log cut between seq 12 and 18.
    enabled: false,
    needs: "a log that ends while the change is being edited, before change.proposed at seq 19",
    prepare: (page) => clickChangedNode(page, "Live diff panel"),
  },
  {
    name: "file-view",
    enabled: false,
    // Enabled by: the editor panel's file view. Then `prepare` clicks a node with no pending
    // change and waits for the file's text to show.
    needs: "the editor panel's file view (not on main yet)",
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
