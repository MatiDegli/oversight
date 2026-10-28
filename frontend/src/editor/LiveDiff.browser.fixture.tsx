// Browser-only test harness; never imported by the application.
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";

import { stateStore } from "../state";
import type { LiveDiffData, State } from "../state";
import { getActiveTheme } from "../theme";
import { LiveDiff } from "./LiveDiff";
import { ReviewEditor } from "./ReviewEditor";
import { App, createFileSession } from "../app";
import { monaco } from "./monaco";
import { editorTokens } from "./theme";

const host = document.createElement("div");
host.style.height = "900px";
document.body.replaceChildren(host);
const root = createRoot(host);
let current: monaco.editor.IStandaloneDiffEditor | undefined;
let mounts = 0;
const reveals: { line: number; immediate: boolean }[] = [];
const fontLoad = document.fonts.load.bind(document.fonts);
const pendingFonts: (() => void)[] = [];

// Canvases (the overview rulers) blend their fills, so the colors are recorded as Monaco sets them.
const canvasColors = new Set<string>();
for (const property of ["fillStyle", "strokeStyle"] as const) {
  const native = Object.getOwnPropertyDescriptor(CanvasRenderingContext2D.prototype, property);
  Object.defineProperty(CanvasRenderingContext2D.prototype, property, {
    ...native,
    set(this: CanvasRenderingContext2D, value: unknown) {
      if (typeof value === "string") canvasColors.add(value);
      native?.set?.call(this, value);
    },
  });
}

const palette = getActiveTheme();
/** The colors Monaco receives, as rgb hex; a tint counts as its token's color. */
const allowed = new Set(
  editorTokens.map((name) => {
    const value = palette[name];
    return (typeof value === "string" ? value : value.color).toLowerCase();
  }),
);
const hex = (parts: number[]) =>
  `#${parts
    .slice(0, 3)
    .map((n) => Math.round(n).toString(16).padStart(2, "0"))
    .join("")}`;
/** A CSS color as rgb hex, or null when it paints nothing. */
const rgbHex = (value: string) => {
  if (value.startsWith("#"))
    return value.length === 9 && value.endsWith("00") ? null : value.slice(0, 7).toLowerCase();
  const parts = value.match(/[\d.]+/g)?.map(Number);
  return !parts || (parts.length === 4 && parts[3] === 0) ? null : hex(parts);
};
/** Off-screen parts, such as the closed find widget above the editor, are not painted. */
const onScreen = (element: Element) => {
  const box = element.getBoundingClientRect();
  const frame = element.closest(".monaco-diff-editor")?.getBoundingClientRect() ?? {
    top: 0,
    left: 0,
    bottom: innerHeight,
    right: innerWidth,
  };
  return (
    box.width > 0 &&
    box.height > 0 &&
    box.bottom > frame.top &&
    box.top < frame.bottom &&
    box.right > frame.left &&
    box.left < frame.right
  );
};
const editorSurface =
  ".monaco-diff-editor, .monaco-diff-editor *, .context-view, .context-view *, .monaco-hover, .monaco-hover *";

export const panelTest = {
  renderApp(state: State) {
    host.style.height = "100vh";
    flushSync(() => {
      stateStore.setState({ state });
      root.render(<App />);
    });
  },
  setState(state: State) {
    flushSync(() => {
      stateStore.setState({ state });
    });
  },
  render(data: LiveDiffData | undefined) {
    flushSync(() => {
      root.render(
        <LiveDiff
          data={data}
          onMount={(editor) => {
            current = editor;
            mounts += 1;
            const modified = editor.getModifiedEditor();
            const reveal = modified.revealLineNearTop.bind(modified);
            modified.revealLineNearTop = (line, scrollType) => {
              reveals.push({ line, immediate: scrollType === monaco.editor.ScrollType.Immediate });
              reveal(line, scrollType);
            };
          }}
        />,
      );
    });
  },
  renderReview(data: LiveDiffData) {
    const files = createFileSession({
      getFile: () =>
        Promise.resolve({
          path: data.path,
          agent_id: data.agentId,
          content: data.modified,
          hash: "test-hash",
        }),
      saveFile: () => Promise.reject(new Error("This test does not save")),
    });
    flushSync(() => {
      root.render(
        <ReviewEditor data={data} files={files} editable revision={1} onBlocked={() => {}} />,
      );
    });
  },
  resize(width: number, height: number) {
    host.style.width = `${String(width)}px`;
    host.style.height = `${String(height)}px`;
  },
  reveals() {
    return reveals;
  },
  gateFonts(fail = false) {
    document.fonts.load = (font) =>
      fail
        ? Promise.reject(new Error("Test font failure"))
        : new Promise<FontFace[]>((resolve, reject) => {
            pendingFonts.push(() => {
              void fontLoad(font).then(resolve, reject);
            });
          });
  },
  releaseFont() {
    pendingFonts.shift()?.();
  },
  pendingFonts() {
    return pendingFonts.length;
  },
  current() {
    return current ?? monaco.editor.getDiffEditors()[0];
  },
  mounts() {
    return mounts;
  },
  models() {
    return monaco.editor.getModels().length;
  },
  /** Every painted color on Monaco's DOM that is not one of the editor tokens. */
  unknownColors() {
    const unknown = new Set<string>();
    for (const element of document.querySelectorAll(editorSurface)) {
      if (!onScreen(element)) continue;
      const style = getComputedStyle(element);
      if (style.visibility === "hidden" || style.opacity === "0") continue;
      const painted = ["color", "background-color"];
      for (const side of ["top", "right", "bottom", "left"])
        if (style.getPropertyValue(`border-${side}-style`) !== "none")
          painted.push(`border-${side}-color`);
      if (style.outlineStyle !== "none") painted.push("outline-color");
      for (const property of painted) {
        const value = style.getPropertyValue(property);
        const color = rgbHex(value);
        if (color && !allowed.has(color)) unknown.add(`${element.className}: ${property}=${value}`);
      }
    }
    return [...unknown];
  },
  canvasColorCount() {
    return canvasColors.size;
  },
  /** Every color set on a canvas (the overview rulers) that is not one of the editor tokens. */
  unknownCanvasColors() {
    return [...canvasColors].filter((value) => {
      const color = rgbHex(value);
      return color !== null && !allowed.has(color);
    });
  },
  palette,
  monaco,
};

declare global {
  interface Window {
    panelTest: typeof panelTest;
  }
}
