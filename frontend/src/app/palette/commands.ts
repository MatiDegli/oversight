import type { CommandId, PaletteCommand } from "./search";

export interface CommandActions {
  toggleSidebar: () => void;
  toggleDock: () => void;
  closeTab?: () => void;
  toggleMaximize?: () => void;
  rescan?: () => void;
  focusTask?: () => void;
}

/** Explicit allowlist: review decisions and agent lifecycle actions never enter search. */
export function paletteCommands(actions: CommandActions, maximized: boolean): PaletteCommand[] {
  const command = (
    id: CommandId,
    label: string,
    run: (() => void) | undefined,
    unavailable: string,
  ): PaletteCommand => ({
    id,
    label,
    run: run ?? (() => undefined),
    unavailable: run ? undefined : unavailable,
  });
  return [
    { id: "sidebar", label: "Toggle sidebar", run: actions.toggleSidebar },
    { id: "dock", label: "Toggle bottom dock", run: actions.toggleDock },
    command("close", "Close focus tab", actions.closeTab, "Open a focus tab first"),
    command(
      "maximize",
      maximized ? "Restore graph" : "Maximize focus panel",
      actions.toggleMaximize,
      "Open a focus tab first",
    ),
    command("rescan", "Rescan graph", actions.rescan, "Unavailable in replay"),
    command("task", "Focus task box", actions.focusTask, "No task box available"),
  ];
}
