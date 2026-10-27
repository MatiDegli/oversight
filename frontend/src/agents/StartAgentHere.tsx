import type { GraphNode } from "@contracts";
import { Bot } from "lucide-react";
import { useId } from "react";
import { useStore } from "zustand";

import type { State } from "../state";
import { startBlock, taskBoxStore } from "./taskBox";
import { functionTask } from "./functionTask";

export function StartAgentHere({
  node,
  state,
  onOpen,
}: {
  node: GraphNode;
  state: State;
  onOpen: () => void;
}) {
  const id = useId();
  const draft = useStore(taskBoxStore, (box) => box.draft);
  const pending = useStore(taskBoxStore, (box) => box.pending);
  if (node.kind !== "function" && node.kind !== "method") return null;
  const reason =
    startBlock(state) ??
    (pending
      ? "Starting the agent."
      : draft !== ""
        ? "Task box is in use. Finish or clear the current task first."
        : null);
  return (
    <div className="min-w-0">
      <button
        type="button"
        aria-disabled={reason !== null}
        aria-describedby={reason ? id : undefined}
        className="inline-flex h-(--button-small-height) items-center gap-2 rounded-control px-3 text-sm text-muted not-aria-disabled:hover:bg-raised not-aria-disabled:hover:text-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text aria-disabled:cursor-not-allowed"
        onClick={() => {
          if (reason !== null || !taskBoxStore.getState().prefill(functionTask(node))) return;
          onOpen();
        }}
      >
        <Bot aria-hidden="true" className="size-4 shrink-0" />
        Start an agent here
      </button>
      {reason && (
        <p id={id} role="status" className="text-sm text-muted">
          {reason}
        </p>
      )}
    </div>
  );
}
