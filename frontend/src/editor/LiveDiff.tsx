import { DiffEditor } from "@monaco-editor/react";
import type { DiffEditorProps, DiffOnMount } from "@monaco-editor/react";
import { LockKeyhole } from "lucide-react";
import { useEffect, useState } from "react";
import type { ReactElement } from "react";

import type { LiveDiffData } from "../state";
import { colors, fontFamilies, stateColors } from "../theme";
import { loadEditorFonts } from "./fonts";
import { monaco } from "./monaco";
import { createEditorTheme } from "./theme";
import type { EditorColors } from "./theme";

const defaultColors: EditorColors = { ...colors, ...stateColors };
const themeName = "oversight-live-diff";
const options = {
  readOnly: true,
  originalEditable: false,
  domReadOnly: true,
  renderSideBySide: false,
  useInlineViewWhenSpaceIsLimited: true,
  automaticLayout: true,
  minimap: { enabled: false },
  scrollBeyondLastLine: false,
  renderIndicators: true,
  fontFamily: fontFamilies.mono,
  fontSize: 15,
  lineHeight: 21,
  accessibilityVerbose: true,
} as const;

export interface LiveDiffProps {
  data: LiveDiffData | undefined;
  palette?: EditorColors;
  /** Optional observer for integration; updates do not remount the editor. */
  onMount?: DiffOnMount;
}

/** Kept mounted by LiveDiff; changing contents only updates the existing models. */
export function InlineDiff({
  data,
  onMount,
}: {
  data: LiveDiffData;
  onMount?: DiffOnMount;
}): ReactElement<DiffEditorProps> {
  const modelRoot = `oversight://changes/${encodeURIComponent(data.changeId)}/${encodeURIComponent(data.path)}`;
  return (
    <DiffEditor
      height="100%"
      language="python"
      theme={themeName}
      original={data.original}
      modified={data.modified}
      originalModelPath={`${modelRoot}/original`}
      modifiedModelPath={`${modelRoot}/modified`}
      options={options}
      onMount={onMount}
      loading={<p className="p-4 text-sm text-muted">Loading diff…</p>}
    />
  );
}

export function LiveDiff({ data, palette = defaultColors, onMount }: LiveDiffProps) {
  const [fonts, setFonts] = useState<"loading" | "ready" | "failed">("loading");

  useEffect(() => {
    let active = true;
    void loadEditorFonts(document.fonts).then(
      () => {
        if (active) setFonts("ready");
      },
      () => {
        if (active) setFonts("failed");
      },
    );
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    monaco.editor.defineTheme(themeName, createEditorTheme(palette));
    monaco.editor.setTheme(themeName);
  }, [palette]);

  return (
    <section
      aria-label="Live diff"
      className="flex h-full min-h-96 min-w-0 flex-col bg-background text-text"
    >
      <header className="flex flex-wrap items-center gap-3 border-b border-border bg-panel p-4">
        <h2 className="min-w-0 break-all text-base font-bold">{data?.path ?? "Live diff"}</h2>
        {data && (
          <p className="font-mono text-sm">
            <span className="text-added">+{data.linesAdded}</span>{" "}
            <span className="text-removed">−{data.linesRemoved}</span>
          </p>
        )}
        <span className="ml-auto inline-flex items-center gap-2 text-sm text-muted">
          <LockKeyhole aria-hidden="true" className="size-4" /> Read-only · Inline diff
        </span>
      </header>
      <p className="border-b border-border p-4 text-sm text-muted">
        {data
          ? "This diff updates with each edit. Decisions open when the agent proposes the change."
          : "Select a changed file to see its live diff."}
      </p>
      <div className="min-h-0 flex-1" hidden={!data}>
        {fonts === "loading" && (
          <p role="status" className="p-4 text-sm text-muted">
            Loading editor fonts…
          </p>
        )}
        {fonts === "failed" && (
          <p role="alert" className="p-4 text-sm text-failed">
            Editor fonts could not load. Reload to try again.
          </p>
        )}
        {fonts === "ready" && data && <InlineDiff data={data} onMount={onMount} />}
      </div>
    </section>
  );
}
