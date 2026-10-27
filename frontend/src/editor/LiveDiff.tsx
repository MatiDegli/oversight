import { LockKeyhole } from "lucide-react";
import { useEffect, useEffectEvent, useRef, useState } from "react";
import type { editor } from "monaco-editor";

import type { LiveDiffData } from "../state";
import { getActiveTheme } from "../theme";
import { loadEditorFonts } from "./fonts";
import { DiffViewport } from "./diffViewport";
import { monaco } from "./monaco";
import { createEditorTheme } from "./theme";
import type { EditorColors } from "./theme";

const themeName = "oversight-live-diff";

/** The live diff is read-only, inline, and drops every feature that brings its own palette. */
export function diffEditorOptions(
  fontFamily: string,
): editor.IStandaloneDiffEditorConstructionOptions {
  return {
    readOnly: true,
    originalEditable: false,
    domReadOnly: true,
    renderSideBySide: false,
    useInlineViewWhenSpaceIsLimited: true,
    wordWrap: "on",
    diffWordWrap: "on",
    hideUnchangedRegions: {
      enabled: true,
      contextLineCount: 3,
      minimumLineCount: 3,
      revealLineCount: 10,
    },
    smoothScrolling: false,
    automaticLayout: true,
    minimap: { enabled: false },
    scrollBeyondLastLine: false,
    renderIndicators: true,
    renderMarginRevertIcon: false,
    contextmenu: false,
    links: false,
    selectionHighlight: false,
    occurrencesHighlight: "off",
    stickyScroll: { enabled: false },
    scrollbar: { useShadows: false },
    fontFamily,
    fontSize: 15,
    lineHeight: 21,
    accessibilityVerbose: true,
    bracketPairColorization: { enabled: false },
    guides: {
      bracketPairs: false,
      bracketPairsHorizontal: false,
      highlightActiveBracketPair: false,
    },
  };
}

/** Monaco reads bracket colors from each model; the editor option above never reaches them. */
export const modelOptions: editor.ITextModelUpdateOptions = {
  bracketColorizationOptions: { enabled: false, independentColorPoolPerBracketType: false },
};

type DiffText = Pick<LiveDiffData, "changeId" | "path" | "original" | "modified">;
export interface ShownDiff {
  key: string;
  models: editor.IDiffEditorModel;
}

/**
 * Shows `next` in the one diff editor. A new selection gets new models, attached before the old
 * ones are disposed: Monaco reports a model disposed while attached as a bug. An edit to the shown
 * selection updates its models in place.
 */
export function showDiff(
  diffEditor: Pick<editor.IStandaloneDiffEditor, "setModel">,
  shown: ShownDiff | undefined,
  next: DiffText,
  createModel: (text: string) => editor.ITextModel,
  managedModified = false,
): ShownDiff {
  const key = JSON.stringify([next.changeId, next.path]);
  if (shown?.key === key) {
    const { original, modified } = shown.models;
    if (original.getValue() !== next.original) original.setValue(next.original);
    if (!managedModified && modified.getValue() !== next.modified) modified.setValue(next.modified);
    return shown;
  }
  const models = { original: createModel(next.original), modified: createModel(next.modified) };
  diffEditor.setModel(models);
  shown?.models.original.dispose();
  shown?.models.modified.dispose();
  return { key, models };
}

function createPythonModel(text: string): editor.ITextModel {
  const model = monaco.editor.createModel(text, "python");
  model.updateOptions(modelOptions);
  return model;
}

/** Owns one diff editor, created once and never per event or per selection. */
export function InlineDiff({
  data,
  onMount,
  readOnly = true,
  managedModified = false,
  follow = false,
}: {
  data: LiveDiffData;
  readOnly?: boolean;
  managedModified?: boolean;
  follow?: boolean;
  onMount?: (diffEditor: editor.IStandaloneDiffEditor) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const diffEditor = useRef<editor.IStandaloneDiffEditor | null>(null);
  const shown = useRef<ShownDiff | undefined>(undefined);
  const viewport = useRef<DiffViewport | undefined>(undefined);
  const { changeId, path, original, modified, author } = data;
  const reportMount = useEffectEvent((created: editor.IStandaloneDiffEditor) => {
    viewport.current = new DiffViewport(created);
    viewport.current.update(
      { changeId, path, original, modified, author },
      () => {
        shown.current = showDiff(created, undefined, data, createPythonModel);
      },
      follow,
    );
    onMount?.(created);
  });

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const created = monaco.editor.createDiffEditor(
      element,
      diffEditorOptions(getActiveTheme()["code-font"]),
    );
    diffEditor.current = created;
    reportMount(created);
    return () => {
      const models = shown.current?.models;
      viewport.current?.dispose();
      viewport.current = undefined;
      created.setModel(null);
      created.dispose();
      models?.original.dispose();
      models?.modified.dispose();
      diffEditor.current = null;
      shown.current = undefined;
    };
  }, []);

  useEffect(() => {
    const current = diffEditor.current;
    if (!current) return;
    viewport.current?.update(
      { changeId, path, original, modified, author },
      () => {
        shown.current = showDiff(
          current,
          shown.current,
          { changeId, path, original, modified },
          createPythonModel,
          managedModified,
        );
      },
      follow,
    );
  }, [changeId, path, original, modified, author, managedModified, follow]);

  useEffect(() => {
    diffEditor.current?.updateOptions({ readOnly, domReadOnly: readOnly });
  }, [readOnly]);

  return <div ref={host} className="h-full" />;
}

export interface LiveDiffProps {
  data: LiveDiffData | undefined;
  palette?: EditorColors;
  readOnly?: boolean;
  managedModified?: boolean;
  /** Live updates follow the agent unless the person is using the diff. Review only opens at a hunk. */
  follow?: boolean;
  /** Called once, when the diff editor is created; selections and edits reuse it. */
  onMount?: (diffEditor: editor.IStandaloneDiffEditor) => void;
}

/**
 * The read-only inline diff once the editor fonts load, with the editor theme from the active
 * tokens. The live diff and review both show their diffs through it.
 */
export function EditorDiff({
  data,
  palette = getActiveTheme(),
  onMount,
  readOnly = true,
  managedModified,
  follow,
}: LiveDiffProps) {
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
    <>
      {fonts === "loading" && (
        <p role="status" className="p-4 text-sm text-muted">
          Loading editor fonts…
        </p>
      )}
      {fonts === "failed" && (
        <p role="alert" className="p-4 text-sm text-muted">
          Editor fonts could not load. Reload to try again.
        </p>
      )}
      {fonts === "ready" && data && (
        <InlineDiff
          data={data}
          onMount={onMount}
          readOnly={readOnly}
          managedModified={managedModified}
          follow={follow}
        />
      )}
    </>
  );
}

export function LiveDiff({ data, palette, onMount }: LiveDiffProps) {
  return (
    <section
      aria-label="Live diff"
      className="flex h-full min-h-96 min-w-0 flex-col bg-page text-text"
    >
      <header className="flex flex-wrap items-center gap-3 border-b border-page-border bg-page p-4">
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
      <p className="border-b border-page-border p-4 text-sm text-muted">
        {data
          ? "This diff updates with each edit. Decisions open when the agent proposes the change."
          : "Select a changed file to see its live diff."}
      </p>
      <div className="min-h-0 flex-1" hidden={!data}>
        <EditorDiff data={data} palette={palette} onMount={onMount} follow />
      </div>
    </section>
  );
}
