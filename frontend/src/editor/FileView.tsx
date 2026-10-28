import { LockKeyhole, TriangleAlert, X } from "lucide-react";
import type { editor, IDisposable } from "monaco-editor";
import { useEffect, useEffectEvent, useRef, useState } from "react";
import type { ReactNode } from "react";

import { getActiveTheme } from "../theme";
import { FileDocument } from "./fileDocument";
import type { FileAccess, FileLocation, FileState } from "./fileDocument";
import { loadEditorFonts } from "./fonts";
import { languageForPath } from "./language";
import { diffEditorOptions, modelOptions } from "./LiveDiff";
import { monaco } from "./monaco";
import { createEditorTheme } from "./theme";

export interface FileViewProps {
  target: FileLocation;
  files: FileAccess;
  commit: string;
  onClose: () => void;
  startAgentAction?: ReactNode;
}

const control =
  "rounded-control px-3 h-(--button-small-height) text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text disabled:opacity-50";

/** Attach first: disposing a model still attached to Monaco is an editor lifecycle error. */
export function showFile(
  code: Pick<editor.IStandaloneCodeEditor, "setModel" | "saveViewState" | "restoreViewState">,
  old: FileDocument | null,
  target: FileLocation,
  files: FileAccess,
  changed: (state: FileState) => void,
  decorate: () => void = () => undefined,
) {
  const model = monaco.editor.createModel("", languageForPath(target.path));
  model.updateOptions(modelOptions);
  code.setModel(model);
  old?.dispose();
  old?.model.dispose();
  return new FileDocument(target, model, files, changed, (text) => {
    const view = code.saveViewState();
    model.setValue(text);
    decorate();
    if (view) code.restoreViewState(view);
  });
}

/** One locally bundled editor; opening and revealing a span never takes keyboard focus. */
export function FileView({ target, files, commit, onClose, startAgentAction }: FileViewProps) {
  const host = useRef<HTMLDivElement>(null);
  const current = useRef<editor.IStandaloneCodeEditor | null>(null);
  const shown = useRef<FileDocument | null>(null);
  const saveAction = useRef<IDisposable | null>(null);
  const span = useRef<editor.IEditorDecorationsCollection | null>(null);
  const [state, setState] = useState<FileState>({
    phase: "loading",
    dirty: false,
    loaded: false,
    message: null,
    retryLoad: false,
    canSave: false,
  });
  const [fontError, setFontError] = useState(false);

  const decorateTarget = useEffectEvent(() => {
    span.current?.set([
      {
        range: new monaco.Range(target.startLine, 1, target.endLine, 1),
        options: { isWholeLine: true, className: "file-span" },
      },
    ]);
  });

  const revealTarget = useEffectEvent(() => {
    current.current?.revealLineInCenter(target.startLine);
    decorateTarget();
  });

  const showTarget = useEffectEvent(() => {
    const code = current.current;
    if (!code) return;
    const old = shown.current;
    if (old?.target.path !== target.path || old.target.agentId !== target.agentId) {
      saveAction.current?.dispose();
      let revealed = false;
      const opened = showFile(
        code,
        old,
        target,
        files,
        (next) => {
          code.updateOptions({
            readOnly: files.readOnly || target.agentId !== null || !next.loaded,
          });
          if (next.loaded && !revealed) {
            revealed = true;
            revealTarget();
          }
          setState(next);
        },
        () => {
          decorateTarget();
        },
      );
      shown.current = opened;
      if (target.agentId === null) {
        saveAction.current = code.addAction({
          id: "oversight-save-file",
          label: "Save file",
          keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS],
          run: () => {
            opened.save();
          },
        });
      } else saveAction.current = null;
      opened.observeCommit(commit);
      void opened.load();
    }
    revealTarget();
  });

  useEffect(() => {
    let active = true;
    const element = host.current;
    if (!element) return;
    void loadEditorFonts(document.fonts).then(
      () => {
        if (!active) return;
        const theme = getActiveTheme();
        const editorTheme = createEditorTheme(theme);
        element.style.setProperty(
          "--file-span",
          editorTheme.colors["editor.rangeHighlightBackground"] ?? "",
        );
        monaco.editor.defineTheme("oversight-file", editorTheme);
        monaco.editor.setTheme("oversight-file");
        const options = diffEditorOptions(theme["code-font"]);
        current.current = monaco.editor.create(element, {
          ...options,
          model: null,
          readOnly: true,
          domReadOnly: false,
          ariaLabel: "File contents",
        });
        span.current = current.current.createDecorationsCollection();
        showTarget();
      },
      () => {
        if (active) setFontError(true);
      },
    );
    return () => {
      active = false;
      shown.current?.dispose();
      saveAction.current?.dispose();
      current.current?.setModel(null);
      current.current?.dispose();
      shown.current?.model.dispose();
      shown.current = null;
      current.current = null;
    };
  }, [files]);

  useEffect(() => {
    showTarget();
  }, [target.path, target.agentId, target.startLine, target.endLine]);
  useEffect(() => {
    shown.current?.observeCommit(commit);
  }, [commit]);

  const saving = state.phase === "saving";
  return (
    <aside
      aria-label="File view"
      data-state={state.phase}
      className="flex w-[min(var(--review-panel-width),46vw)] shrink-0 flex-col border-r border-divider bg-page text-text"
      onKeyDown={(event) => {
        if (
          target.agentId === null &&
          !files.readOnly &&
          !event.defaultPrevented &&
          !event.nativeEvent.isComposing &&
          (event.ctrlKey || event.metaKey) &&
          event.key.toLowerCase() === "s"
        ) {
          event.preventDefault();
          shown.current?.save();
        }
      }}
    >
      <header className="flex flex-wrap items-center gap-2.5 border-b border-page-border px-4 py-2.5">
        <h2 className="min-w-0 break-all text-base font-bold">{target.path}</h2>
        {languageForPath(target.path) !== "python" && (
          <span className="text-sm text-muted">No impact analysis</span>
        )}
        {state.dirty && <span className="text-sm text-muted">Unsaved edits</span>}
        {files.readOnly || target.agentId !== null ? (
          <span className="inline-flex items-center gap-2 text-sm text-muted">
            <LockKeyhole aria-hidden="true" className="size-4" />
            {files.readOnly
              ? "Read-only · Recorded file"
              : `Read-only · Agent ${target.agentId ?? ""}`}
          </span>
        ) : (
          <>
            <button
              type="button"
              className={`${control} border border-outline text-text enabled:hover:bg-raised`}
              disabled={!state.canSave}
              onClick={() => {
                shown.current?.save();
              }}
            >
              Save
            </button>
            {state.dirty && (
              <button
                type="button"
                className={`${control} text-failed-text enabled:hover:bg-raised`}
                disabled={saving}
                onClick={() => {
                  shown.current?.discard();
                }}
              >
                Discard edits
              </button>
            )}
          </>
        )}
        {startAgentAction}
        <button
          type="button"
          aria-label="Close file"
          title="Close (Escape)"
          onClick={onClose}
          className={`${control} ml-auto text-muted hover:bg-raised hover:text-text`}
        >
          <X aria-hidden="true" className="size-4" />
        </button>
      </header>
      <FileFeedback
        state={state}
        fontError={fontError}
        onReload={() => {
          void shown.current?.load(true);
        }}
      />
      <div ref={host} className="min-h-0 flex-1" />
    </aside>
  );
}

/** A fixed caption line keeps the editor bounds stable while loading or saving. */
export function FileFeedback({
  state,
  fontError,
  onReload,
}: {
  state: FileState;
  fontError: boolean;
  onReload: () => void;
}) {
  const saving = state.phase === "saving";
  return (
    <>
      {(fontError || state.message) && (
        <div
          role="alert"
          className="flex items-start gap-2 border-b border-page-border p-4 text-sm text-muted"
        >
          <TriangleAlert aria-hidden="true" className="size-4 shrink-0" />
          <div>
            <p>
              {fontError
                ? "Editor fonts could not load. Reload the page to try again."
                : state.message}
            </p>
            {!fontError && (
              <>
                <p>
                  {state.phase === "stale"
                    ? "Reload to use the server version and discard these edits."
                    : state.retryLoad
                      ? state.dirty
                        ? "Try Reload again. Reloading discards these edits."
                        : "Try loading the file again."
                      : "Your edits are still here. Try Save again."}
                </p>
                {(state.phase === "stale" || state.retryLoad) && (
                  <button
                    type="button"
                    className={`${control} mt-2 border border-outline text-text hover:bg-raised`}
                    onClick={() => {
                      onReload();
                    }}
                  >
                    Reload
                  </button>
                )}
              </>
            )}
          </div>
        </div>
      )}
      <p
        role="status"
        className="h-(--caption-line-height) shrink-0 px-4 text-(length:--caption-size) leading-(--caption-line-height) text-muted"
      >
        {!fontError &&
          (saving
            ? "Saving…"
            : state.phase === "loading" && !state.loaded
              ? "Loading file…"
              : null)}
      </p>
    </>
  );
}
