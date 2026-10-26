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

export const scenes: readonly Scene[] = [
  // Watch mode: the graph and the agents panel. Today main renders only the shell.
  { name: "watch", enabled: true },
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
    enabled: false,
    // Enabled by: the editor panel showing a read-only diff while an agent works. Then `prepare`
    // clicks an edited node on the graph and waits for the DiffEditor to hold both versions.
    needs: "the editor panel and the graph view (not on main yet)",
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
