import { Undo2, X } from "lucide-react";
import type { State } from "../state";
import { revertView, type RevertAttempt } from "./revert";

export function RevertControl({
  state,
  changeId,
  attempt,
  onRevert,
}: {
  state: State;
  changeId: string;
  attempt?: RevertAttempt;
  onRevert?: (changeId: string) => void;
}) {
  const view = revertView(state, changeId, attempt);
  if (view.kind === "none") return null;
  const line = "basis-full min-w-0 text-sm text-muted [overflow-wrap:anywhere]";
  return (
    <>
      {onRevert === undefined ? (
        <p className="text-sm text-muted">Revert is not available in a replay.</p>
      ) : (
        view.kind !== "reverted" && (
          <button
            type="button"
            aria-disabled={view.kind === "pending"}
            onClick={(event) => {
              if (event.detail !== 0 && view.kind !== "pending") onRevert(changeId);
            }}
            className="flex items-center gap-2 rounded-[10px] border border-control-edge px-3.5 py-2.5 text-sm text-text hover:bg-raised aria-disabled:cursor-default aria-disabled:text-faint aria-disabled:hover:bg-transparent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text"
          >
            <Undo2 aria-hidden="true" className="size-4" />
            Revert
          </button>
        )
      )}
      {view.kind === "pending" && (
        <p className={line} role="status">
          Reverting: waiting for the result.
        </p>
      )}
      {view.kind === "refused" && (
        <p className={`${line} flex items-start gap-2`} role="alert">
          <X aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          {view.message}
        </p>
      )}
      {view.kind === "reverted" && (
        <p className={line}>
          Reverted in main <code>{view.revert_commit.slice(0, 7)}</code>
        </p>
      )}
      {view.kind === "needs_you" && <p className={line}>{view.reason}</p>}
    </>
  );
}
