import type { editor } from "monaco-editor";

import type { LiveDiffData } from "../state";
import { monaco } from "./monaco";

const interactionPauseMs = 5_000;

/** The first changed line between successive versions, rather than the last hunk against main. */
function editedLine(previous: string, next: string): number {
  const before = previous.split(/\r\n|\r|\n/);
  const after = next.split(/\r\n|\r|\n/);
  let index = 0;
  while (index < before.length && index < after.length && before[index] === after[index]) index++;
  return Math.min(index + 1, after.length);
}

/** Follows computed hunks without taking focus or treating Monaco's own scrolling as input. */
export class DiffViewport {
  private previous: { key: string; modified: string } | undefined;
  private pending: { line: number; first: boolean } | undefined;
  private lastInteraction = -Infinity;
  private frame: number | undefined;
  private readonly input = new AbortController();
  private readonly subscription: { dispose: () => void };

  constructor(private readonly diff: editor.IStandaloneDiffEditor) {
    const host = diff.getContainerDomNode();
    const pause = () => {
      this.lastInteraction = performance.now();
    };
    // Wheel/touch and scrollbar drags count even when neither editor has keyboard focus.
    for (const event of ["wheel", "touchmove", "pointerdown", "keydown"]) {
      host.addEventListener(event, pause, {
        capture: true,
        passive: true,
        signal: this.input.signal,
      });
    }
    host.addEventListener(
      "pointermove",
      (event) => {
        if (event.buttons) pause();
      },
      { capture: true, signal: this.input.signal },
    );
    this.subscription = diff.onDidUpdateDiff(() => {
      if (this.frame !== undefined) cancelAnimationFrame(this.frame);
      // The diff event precedes Monaco's folded view-zone layout. Reveal after that layout.
      this.frame = requestAnimationFrame(() => {
        this.frame = undefined;
        this.reveal();
      });
    });
  }

  update(
    data: Pick<LiveDiffData, "changeId" | "path" | "original" | "modified" | "author">,
    replace: () => void,
    follow: boolean,
  ) {
    const key = JSON.stringify([data.changeId, data.path]);
    const previous = this.previous;
    const same = previous?.key === key;
    const view = same ? this.diff.saveViewState() : null;
    if (!previous || !same) {
      this.pending = { line: 1, first: true };
      this.lastInteraction = -Infinity;
    } else if (previous.modified !== data.modified) {
      this.pending =
        follow && data.author === "agent"
          ? { line: editedLine(previous.modified, data.modified), first: false }
          : undefined;
    }
    this.previous = { key, modified: data.modified };
    replace();
    // setValue resets the cursor and viewport. Keep the person's place while the worker diffs.
    if (view) this.diff.restoreViewState(view);
  }

  private reveal() {
    const pending = this.pending;
    const changes = this.diff.getLineChanges();
    if (!pending || !changes) return;
    this.pending = undefined;
    const modified = this.diff.getModifiedEditor();
    if (
      performance.now() - this.lastInteraction < interactionPauseMs ||
      modified.hasWidgetFocus() ||
      this.diff.getOriginalEditor().hasWidgetFocus()
    )
      return;
    // Find the hunk nearest the latest edit; an earlier hunk may have changed after a later one.
    const distance = (change: editor.ILineChange) =>
      Math.max(
        change.modifiedStartLineNumber - pending.line,
        pending.line - (change.modifiedEndLineNumber || change.modifiedStartLineNumber),
        0,
      );
    const hunk = pending.first
      ? changes[0]
      : changes.reduce<editor.ILineChange | undefined>(
          (nearest, change) =>
            !nearest || distance(change) < distance(nearest) ? change : nearest,
          undefined,
        );
    if (!hunk) return;
    const line =
      hunk.modifiedEndLineNumber === 0
        ? hunk.modifiedStartLineNumber + 1
        : hunk.modifiedStartLineNumber;
    modified.revealLineNearTop(
      Math.max(1, Math.min(line, modified.getModel()?.getLineCount() ?? 1)),
      monaco.editor.ScrollType.Immediate,
    );
  }

  dispose() {
    this.input.abort();
    this.subscription.dispose();
    if (this.frame !== undefined) cancelAnimationFrame(this.frame);
  }
}
