import type { AgentStatus } from "@contracts";
import { Pause, Play, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { commandFailure } from "../transport";

export const secondaryButtonClass =
  "inline-flex h-(--button-secondary-height) items-center gap-2 rounded-control border border-control-edge px-3 text-(length:--label-size) font-semibold text-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";

export function PauseControl({
  agentId,
  state,
  onPause,
  onResume,
}: {
  agentId: string;
  state: AgentStatus["state"];
  onPause: (agentId: string) => Promise<unknown>;
  onResume: (agentId: string) => Promise<unknown>;
}) {
  const [request, setRequest] = useState({
    agentId,
    state,
    pending: false,
    requested: false,
    failure: null as string | null,
  });
  if (request.agentId !== agentId || request.state !== state) {
    setRequest({ agentId, state, pending: false, requested: false, failure: null });
  }
  if (state !== "working" && state !== "revising" && state !== "paused") return null;
  const resume = state === "paused";
  const action = resume ? "Resume" : "Pause";
  const Icon = resume ? Play : Pause;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <button
        type="button"
        className={`${secondaryButtonClass} aria-disabled:cursor-not-allowed aria-disabled:text-faint self-start`}
        aria-disabled={request.pending || request.requested}
        onClick={() => {
          if (request.pending || request.requested) return;
          const pending = { ...request, pending: true, failure: null };
          setRequest(pending);
          void (resume ? onResume : onPause)(agentId).then(
            () => {
              setRequest((current) =>
                current === pending ? { ...current, pending: false, requested: true } : current,
              );
            },
            (error: unknown) => {
              setRequest((current) =>
                current === pending
                  ? { ...current, pending: false, failure: commandFailure(action, error) }
                  : current,
              );
            },
          );
        }}
      >
        <Icon aria-hidden="true" className="size-4" />
        {request.requested && !resume ? "Pausing after this step" : action}
      </button>
      {request.failure && (
        <p role="alert" className="flex gap-2 text-sm text-muted">
          <TriangleAlert aria-hidden="true" className="size-4 shrink-0" />
          {request.failure}
        </p>
      )}
    </div>
  );
}
