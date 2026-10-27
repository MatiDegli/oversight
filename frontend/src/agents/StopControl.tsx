import { Square, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { commandFailure } from "../transport";

const control =
  "inline-flex h-(--button-secondary-height) items-center gap-2 rounded-control px-3 text-(length:--label-size) font-semibold hover:bg-raised aria-disabled:cursor-not-allowed aria-disabled:text-faint focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";

/** Inline confirmation: opening it never moves focus, scrolls, or changes the screen. */
export function StopControl({
  agentId,
  onStop,
}: {
  agentId: string;
  onStop: (agentId: string) => Promise<unknown>;
}) {
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  return (
    <div className="flex w-full flex-col gap-2">
      <button
        type="button"
        className={`${control} self-start text-failed-text`}
        aria-expanded={confirming}
        aria-disabled={pending}
        onClick={() => {
          if (!pending) setConfirming(!confirming);
        }}
      >
        <Square aria-hidden="true" className="size-4" /> Stop and discard
      </button>
      {confirming && (
        <div className="flex flex-col gap-2 text-sm text-muted">
          <p>This stops the agent and discards its worktree and all pending edits.</p>
          <div className="flex gap-2">
            <button
              type="button"
              aria-disabled={pending}
              className={`${control} border border-control-edge text-failed-text`}
              onClick={() => {
                if (pending) return;
                setPending(true);
                setFailure(null);
                void onStop(agentId).catch((error: unknown) => {
                  setPending(false);
                  setFailure(commandFailure("Stop and discard", error));
                });
              }}
            >
              Confirm stop and discard
            </button>
            <button
              type="button"
              aria-disabled={pending}
              className={`${control} text-text`}
              onClick={() => {
                if (!pending) setConfirming(false);
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
      {pending && (
        <p role="status" className="text-sm text-muted">
          Stopping: waiting for the discarded change.
        </p>
      )}
      {failure && (
        <p role="alert" className="flex gap-2 text-sm text-muted">
          <TriangleAlert aria-hidden="true" className="size-4 shrink-0" />
          {failure}
        </p>
      )}
    </div>
  );
}
