import type { AgentStatus, SuiteResult } from "@contracts";
import { Check, CircleCheck, CircleX, Eye, FileDiff, X } from "lucide-react";
import { useLayoutEffect, useRef } from "react";
import type { ReactNode } from "react";

import { formatUsd } from "../state";
import type { AgentCardData } from "../state";
import { ageWords } from "./age";
import { StopControl } from "./StopControl";

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

const look = {
  A: {
    tint: "bg-agent-a-tint text-agent-a-text",
    border: "border-agent-a",
    fill: "bg-agent-a",
    track: "bg-agent-a",
  },
  B: {
    tint: "bg-agent-b-tint text-agent-b-text",
    border: "border-agent-b",
    fill: "bg-agent-b",
    track: "bg-agent-b",
  },
} as const;

const focusRing =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";

/** The tests as one line for the summary: "24 of 24 pass", or why there is no count. */
function testsLine(tests: SuiteResult): { passed: boolean; text: string } {
  switch (tests.outcome) {
    case "passed":
    case "failed": {
      const ran = tests.passed + tests.failed;
      const skipped = tests.skipped > 0 ? `, ${String(tests.skipped)} skipped` : "";
      return {
        passed: tests.outcome === "passed",
        text: `${String(tests.passed)} of ${String(ran)} pass${skipped}`,
      };
    }
    case "error":
      return { passed: false, text: "Could not run" };
    case "timeout":
      return { passed: false, text: "Timed out" };
  }
}

/** A cost under half a cent would read as zero at two places, and zero is not what it is. */
function costText(cost: number): string {
  return cost > 0 && cost < 0.005 ? "<US$0.01" : formatUsd(cost);
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-(length:--caption-size) text-muted">{label}</dt>
      <dd className="mt-0.5 flex items-center gap-1.5 font-mono text-(length:--data-size)">
        {children}
      </dd>
    </div>
  );
}

/** What the agent finished with: tests, change, steps and cost, each from its own event. */
function Summary({ data }: { data: AgentCardData }) {
  const { agent, step, proposal, cost } = data;
  const tests = proposal ? testsLine(proposal.tests) : undefined;
  return (
    <dl
      aria-label={`Agent ${agent.slot} summary`}
      className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3"
    >
      {proposal && tests && (
        <>
          <Fact label="Tests">
            {tests.passed ? (
              <CircleCheck aria-hidden="true" className="size-4 shrink-0 text-passed" />
            ) : (
              <CircleX aria-hidden="true" className="size-4 shrink-0 text-failed-text" />
            )}
            <span className={tests.passed ? "text-passed" : "text-failed-text"}>{tests.text}</span>
          </Fact>
          <Fact label="Change">
            <span>
              <span className={proposal.lines_added > 0 ? "font-bold text-passed" : "text-muted"}>
                +{proposal.lines_added}
              </span>{" "}
              <span
                className={proposal.lines_removed > 0 ? "font-bold text-failed-text" : "text-muted"}
              >
                −{proposal.lines_removed}
              </span>{" "}
              in {proposal.files.length} {proposal.files.length === 1 ? "file" : "files"}
            </span>
          </Fact>
        </>
      )}
      <Fact label="Steps">
        {Math.min(step, agent.max_steps)} of {agent.max_steps}
      </Fact>
      {cost !== undefined && <Fact label="Cost">{cost === null ? "Unknown" : costText(cost)}</Fact>}
    </dl>
  );
}

export interface AgentCardProps {
  data: AgentCardData;
  onOpenDiff?: (changeId: string) => void;
  onReview?: (changeId: string) => void;
  onStop?: (agentId: string) => Promise<unknown>;
}

export function AgentCard({ data, onOpenDiff, onReview, onStop }: AgentCardProps) {
  const { agent, modelLabel, steps, stepTimes, now, step, readyForReview, cost } = data;
  const own = look[agent.slot];
  // Keep the person's focus when what holds it leaves the card. The step list and Live diff go when
  // the change is proposed, and a removed element takes focus to the page; the card is where it
  // was, so it takes it, and only if the focus was inside it. Nothing else here moves focus.
  const card = useRef<HTMLElement>(null);
  const inside = useRef(false);
  useLayoutEffect(() => {
    const element = card.current;
    if (inside.current && element && !element.contains(document.activeElement)) {
      element.focus({ preventScroll: true });
    }
  });
  const border = readyForReview ? own.border : "border-card-border";
  // Filled when the agent needs the person, outlined otherwise; the pill says it in words.
  const lamp = readyForReview
    ? own.fill
    : `bg-card ${agent.state === "working" ? "animate-pulse [animation-duration:var(--status-lamp-duration)] motion-reduce:animate-none" : ""}`;
  const buttonClass = `inline-flex h-(--button-secondary-height) items-center gap-2 rounded-control border border-control-edge px-3 text-(length:--label-size) font-semibold text-text ${focusRing}`;
  const reviewClass = `inline-flex h-(--button-primary-height) items-center gap-2 rounded-control px-4 text-(length:--label-size) font-semibold text-on-agent hover:brightness-110 ${own.fill} ${focusRing}`;

  return (
    <article
      aria-label={`Agent ${agent.slot}`}
      ref={card}
      tabIndex={-1}
      onFocus={() => {
        inside.current = true;
      }}
      onBlur={() => {
        inside.current = false;
      }}
      data-slot={agent.slot}
      className={`agent-card min-w-0 rounded-card border bg-card p-4 text-text ${border} ${focusRing}`}
    >
      <header className="flex flex-wrap items-center gap-2.5">
        <span
          aria-hidden="true"
          className={`size-(--status-lamp-size) shrink-0 rounded-full border-(length:--agent-border-width) ${own.border} ${lamp}`}
        />
        <h3 className="text-(length:--title-size) font-bold">Agent {agent.slot}</h3>
        <span className="text-sm text-muted">{modelLabel}</span>
        <span
          role={readyForReview ? "status" : undefined}
          className={`ml-auto rounded-full p-(--status-pill-padding) text-(length:--label-size) font-semibold ${own.tint}`}
        >
          {labels[agent.state]}
        </span>
      </header>
      <p title={agent.task} className="mt-3 truncate text-(length:--body-size)">
        {agent.task}
      </p>
      {readyForReview ? (
        <Summary data={data} />
      ) : (
        <>
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
            className="mt-2 h-1 overflow-hidden rounded-full bg-raised"
          >
            <div
              className={`h-full ${own.track}`}
              style={{ width: `${String(Math.min(100, (step / agent.max_steps) * 100))}%` }}
            />
          </div>
          <p className="mt-2 flex items-baseline justify-between gap-2 text-sm text-muted">
            <span>
              Step <span className="font-mono text-text">{step}</span> of{" "}
              <span className="font-mono text-text">{agent.max_steps}</span>
            </span>
            {cost !== undefined && (
              <span title="Cost so far" className="font-mono text-(length:--data-size)">
                {cost === null ? "Cost unknown" : costText(cost)}
              </span>
            )}
          </p>
          <ol
            aria-label={`Agent ${agent.slot} steps, newest first`}
            className="mt-3 h-60 overflow-y-auto overscroll-contain [overflow-anchor:none] [scrollbar-gutter:stable] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text"
            tabIndex={0}
          >
            {steps.map((entry) => (
              <li key={entry.step} className="border-t border-card-border py-2 text-sm">
                <div className="flex items-center gap-2">
                  {entry.result === "ok" ? (
                    <Check aria-hidden="true" className="size-4 shrink-0 text-passed" />
                  ) : (
                    <X aria-hidden="true" className="size-4 shrink-0 text-failed-text" />
                  )}
                  <span className="font-mono text-muted">Step {entry.step}</span>
                  <span className="min-w-0 break-all font-mono">{entry.tool}</span>
                  <span className="ml-auto shrink-0 text-(length:--caption-size) text-muted">
                    {ageWords(stepTimes[entry.step], now)}
                  </span>
                </div>
                <p className="mt-1 break-words">{entry.summary}</p>
                <p className={entry.result === "ok" ? "text-passed" : "text-failed-text"}>
                  {entry.result === "ok" ? "Ok" : "Error"}
                  {entry.error ? `: ${entry.error}` : ""}
                  <span className="font-mono text-muted"> · {entry.duration_ms} ms</span>
                </p>
              </li>
            ))}
            {steps.length === 0 && <li className="py-2 text-sm text-muted">No steps yet.</li>}
          </ol>
        </>
      )}
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
            className={reviewClass}
            onClick={() => {
              onReview(agent.change_id);
            }}
          >
            <Eye aria-hidden="true" className="size-4" /> Review change
          </button>
        )}
        {onStop && ["working", "revising", "ready_for_review", "halted"].includes(agent.state) && (
          <StopControl agentId={agent.agent_id} onStop={onStop} />
        )}
      </footer>
    </article>
  );
}
