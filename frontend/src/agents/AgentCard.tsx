import type { AgentStatus } from "@contracts";
import { Check, FileDiff, X } from "lucide-react";

import type { AgentCardData } from "../state";

const labels: Record<AgentStatus["state"], string> = {
  starting: "Starting",
  working: "Working",
  ready_for_review: "Ready for review",
  revising: "Revising",
  halted: "Halted",
  stopped: "Stopped",
  done: "Done",
  paused: "Paused",
};

export interface AgentCardProps {
  data: AgentCardData;
  onOpenDiff?: (changeId: string) => void;
  onReview?: (changeId: string) => void;
}

export function AgentCard({ data, onOpenDiff, onReview }: AgentCardProps) {
  const { agent, steps, step, readyForReview } = data;
  const accent = agent.slot === "A" ? "text-agent-a" : "text-agent-b";
  const border = readyForReview
    ? agent.slot === "A"
      ? "border-agent-a"
      : "border-agent-b"
    : "border-border";
  const buttonClass =
    "inline-flex h-10 items-center gap-2 rounded-[7px] border border-border px-3 text-sm text-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";

  return (
    <article
      aria-label={`Agent ${agent.slot}`}
      className={`min-w-0 rounded-[10px] border bg-panel p-4 text-text ${border}`}
    >
      <header className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-bold">
          Agent <span className={accent}>{agent.slot}</span>
        </h2>
        <span className="text-sm text-muted">{agent.model}</span>
        <span role="status" className={`ml-auto rounded-[7px] border px-2 py-1 text-sm ${accent}`}>
          {labels[agent.state]}
        </span>
      </header>
      <p title={agent.task} className="mt-3 truncate text-[15px]">
        {agent.task}
      </p>
      <p
        title={agent.detail ?? steps[0]?.summary}
        className="mt-2 line-clamp-2 h-10 break-words text-sm text-muted"
      >
        {agent.detail ?? steps[0]?.summary ?? "Waiting for the first tool call."}
      </p>
      <div
        role="progressbar"
        aria-label={`Agent ${agent.slot} steps`}
        aria-valuenow={Math.min(step, agent.max_steps)}
        aria-valuemax={agent.max_steps}
        className="mt-2 h-1 overflow-hidden rounded-full bg-border"
      >
        <div
          className={`h-full ${agent.slot === "A" ? "bg-agent-a" : "bg-agent-b"}`}
          style={{ width: `${String(Math.min(100, (step / agent.max_steps) * 100))}%` }}
        />
      </div>
      <p className="mt-2 text-sm text-muted">
        Step <span className="font-mono text-text">{step}</span> of{" "}
        <span className="font-mono text-text">{agent.max_steps}</span>
      </p>
      <ol
        aria-label={`Agent ${agent.slot} steps, newest first`}
        className="mt-3 h-60 overflow-y-auto overscroll-contain [overflow-anchor:none] [scrollbar-gutter:stable] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text"
        tabIndex={0}
      >
        {steps.map((entry) => (
          <li key={entry.step} className="border-t border-border py-2 text-sm">
            <div className="flex items-center gap-2">
              {entry.result === "ok" ? (
                <Check aria-hidden="true" className="size-4 shrink-0 text-passed" />
              ) : (
                <X aria-hidden="true" className="size-4 shrink-0 text-failed" />
              )}
              <span className="font-mono text-muted">Step {entry.step}</span>
              <span className="min-w-0 break-all font-mono">{entry.tool}</span>
              <span className="ml-auto shrink-0 font-mono text-muted">{entry.duration_ms} ms</span>
            </div>
            <p className="mt-1 break-words">{entry.summary}</p>
            <p className={entry.result === "ok" ? "text-passed" : "text-failed"}>
              {entry.result === "ok" ? "Ok" : "Error"}
              {entry.error ? `: ${entry.error}` : ""}
            </p>
          </li>
        ))}
        {steps.length === 0 && <li className="py-2 text-sm text-muted">No steps yet.</li>}
      </ol>
      <footer className="mt-3 flex min-h-10 flex-wrap gap-2">
        {onOpenDiff && (
          <button
            type="button"
            className={buttonClass}
            onClick={() => {
              onOpenDiff(agent.change_id);
            }}
          >
            <FileDiff aria-hidden="true" className="size-4" /> Live diff
          </button>
        )}
        {readyForReview && onReview && (
          <button
            type="button"
            className={buttonClass}
            onClick={() => {
              onReview(agent.change_id);
            }}
          >
            Review change
          </button>
        )}
      </footer>
    </article>
  );
}
