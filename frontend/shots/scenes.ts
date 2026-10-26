// The scenes and themes `make shots` captures. This is the file to edit when a front lands: flip a
// scene's `enabled` to true and give it a `prepare` step if the page must be put in that state.
// A disabled entry prints "skipped" with its `needs` text and never fails the run.
import type { Page } from "@playwright/test";

export interface Scene {
  name: string;
  enabled: boolean;
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
 * left panel's diff to show the inserted lines and for the camera to settle, then fails unless that
 * node is still in view. Cytoscape draws on a canvas, so the node's place on screen comes from the
 * graph element's own probe (src/graph/probe.ts), which the app exposes for this.
 */
async function clickChangedNode(page: Page): Promise<void> {
  type Probe = {
    nodeCenters(selector?: string): { id: string; x: number; y: number }[];
    animating(): boolean;
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
  await page.mouse.click(x, y);
  await page.locator('[aria-label="Live diff panel"] .line-insert').first().waitFor();
  await page.waitForFunction((selector) => {
    const host = document.querySelector<HTMLElement & { oversightGraph?: Probe }>(selector);
    return host?.oversightGraph?.animating() === false;
  }, graph);
  const inView = await page.evaluate(
    ({ selector, nodeId }) => {
      const host = document.querySelector<HTMLElement & { oversightGraph?: Probe }>(selector);
      const node = host?.oversightGraph?.nodeCenters().find((center) => center.id === nodeId);
      return (
        host !== null &&
        node !== undefined &&
        node.x >= 0 &&
        node.y >= 0 &&
        node.x <= host.clientWidth &&
        node.y <= host.clientHeight
      );
    },
    { selector: graph, nodeId: id },
  );
  if (!inView) throw new Error(`the clicked node ${id} is out of the graph's view`);
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
  {
    name: "review",
    enabled: false,
    // Enabled by: the review panel (Monaco's inline diff on the left, the graph with the impact
    // cone on the right) and a recorded run that proposes a change. Then `prepare` clicks the
    // "Ready for review" pill, and the scene waits on the review panel being mounted.
    needs: "the review panel and the graph view (not on main yet)",
  },
  {
    name: "live-diff",
    // Ready: `prepare` clicks a changed node and waits for Monaco's inline diff. It needs a log that
    // ends while the change is pending; the default log approves it at seq 21, so no node is changed
    // when the replay ends. Run it with LOG set to a log cut before the decision.
    enabled: false,
    needs: "a log that ends with a pending change (handwritten-level1 approves it at seq 21)",
    prepare: clickChangedNode,
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
