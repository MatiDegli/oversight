import { LockKeyhole, X } from "lucide-react";
import type { FileViewProps } from "./FileView";
import type { FileState } from "./fileDocument";
import { languageForPath } from "./language";

export const fileControl =
  "rounded-control px-3 h-(--button-small-height) text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text disabled:opacity-50";

export function FileViewHeader({
  target,
  files,
  onClose,
  startAgentAction,
  inert,
  state = { dirty: false, canSave: false, phase: "loading" },
  onSave,
  onDiscard,
}: Pick<FileViewProps, "target" | "files" | "onClose" | "startAgentAction"> & {
  inert?: boolean;
  state?: Pick<FileState, "dirty" | "canSave" | "phase">;
  onSave?: () => void;
  onDiscard?: () => void;
}) {
  const control = fileControl;
  const saving = state.phase === "saving";
  return (
    <header
      inert={inert}
      className="flex flex-wrap items-center gap-2.5 border-b border-page-border px-4 py-2.5"
    >
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
              onSave?.();
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
                onDiscard?.();
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
  );
}
