import type { editor } from "monaco-editor";
import { useEffect, useEffectEvent, useRef, useState } from "react";

import type { LiveDiffData } from "../state";
import { FileDocument } from "./fileDocument";
import type { FileAccess, FileState } from "./fileDocument";
import { FileFeedback } from "./FileView";
import { EditorDiff } from "./LiveDiff";

const control =
  "h-(--button-small-height) rounded-control border border-control-edge px-3 text-sm disabled:cursor-not-allowed disabled:text-faint focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";

/** Only review owns an editable modified model. FileDocument preserves hashes, drafts and EOLs. */
export function ReviewEditor({
  data,
  files,
  editable,
  revision,
  onBlocked,
}: {
  data: LiveDiffData;
  files: FileAccess;
  editable: boolean;
  revision: number;
  onBlocked: (blocked: "loading" | "edits" | null) => void;
}) {
  const document = useRef<FileDocument | null>(null);
  const [state, setState] = useState<FileState>({
    phase: "loading",
    dirty: false,
    loaded: false,
    message: null,
    retryLoad: false,
    canSave: false,
  });
  const report = useEffectEvent((next: FileState) => {
    onBlocked(next.dirty || next.phase === "saving" ? "edits" : !next.loaded ? "loading" : null);
  });
  const mount = (diff: editor.IStandaloneDiffEditor) => {
    const model = diff.getModifiedEditor().getModel();
    if (!model) return;
    const opened = new FileDocument(
      { path: data.path, agentId: data.agentId, startLine: 1, endLine: 1 },
      model,
      files,
      setState,
      (text) => {
        // An edit rebuilds folded ranges without flushing the diff view or detaching its models.
        model.pushEditOperations([], [{ range: model.getFullModelRange(), text }], () => null);
      },
      true,
    );
    document.current = opened;
    opened.observeCommit(JSON.stringify([revision, data.modified]));
    void opened.load();
  };
  useEffect(() => {
    report(state);
  }, [state]);
  useEffect(
    () => () => {
      document.current?.dispose();
    },
    [],
  );
  useEffect(() => {
    document.current?.observeCommit(JSON.stringify([revision, data.modified]));
  }, [revision, data.modified]);
  const saving = state.phase === "saving";
  return (
    <div
      className="flex h-full min-h-0 flex-col"
      data-state={state.phase}
      aria-label="Editable review file"
      onKeyDown={(event) => {
        if (
          (event.ctrlKey || event.metaKey) &&
          event.key.toLowerCase() === "s" &&
          !event.nativeEvent.isComposing
        ) {
          event.preventDefault();
          if (editable && state.canSave) document.current?.save();
        }
      }}
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-page-border px-4.5 py-1.5 text-sm text-muted">
        <span>
          {editable ? "Edits go to the agent’s worktree" : "Read-only while the agent is not ready"}
        </span>
        {state.dirty && <span>Unsaved edits</span>}
        <button
          type="button"
          disabled={!editable || !state.canSave}
          className={`${control} ml-auto text-text`}
          onClick={() => {
            document.current?.save();
          }}
        >
          Save review edits
        </button>
        {state.dirty && (
          <button
            type="button"
            disabled={saving}
            className={`${control} text-failed-text`}
            onClick={() => {
              document.current?.discard();
            }}
          >
            Discard edits
          </button>
        )}
      </div>
      <FileFeedback
        state={state}
        fontError={false}
        onReload={() => {
          void document.current?.load(true);
        }}
      />
      <div className="min-h-0 flex-1">
        <EditorDiff
          data={data}
          onMount={mount}
          managedModified
          readOnly={!editable || !state.loaded || saving}
        />
      </div>
    </div>
  );
}
