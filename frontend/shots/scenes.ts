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
 * Clicks the center of the first node with a pending change, as the person would, and waits for the
 * left panel's diff to show the inserted lines. Cytoscape draws on a canvas, so the node's place on
 * screen comes from the instance Cytoscape registers on its container.
 */
async function clickChangedNode(page: Page): Promise<void> {
  const point = await page.waitForFunction(() => {
    type Graph = { $: (selector: string) => GraphNodes };
    type GraphNodes = { empty: () => boolean; renderedPosition: () => { x: number; y: number } };
    const host = document.querySelector<HTMLElement & { _cyreg?: { cy?: Graph } }>(
      '[aria-label="Repository graph"]',
    );
    const node = host?._cyreg?.cy?.$("node.pending");
    if (!host || !node || node.empty()) return null;
    const box = host.getBoundingClientRect();
    const { x, y } = node.renderedPosition();
    return { x: box.left + x, y: box.top + y };
  });
  const { x, y } = (await point.jsonValue()) as { x: number; y: number };
  await page.mouse.click(x, y);
  await page.locator('[aria-label="Live diff panel"] .line-insert').first().waitFor();
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
