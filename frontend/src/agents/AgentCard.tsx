import { agentSlotStyle } from "../theme";
import { agentLook as look } from "../theme";
import type { AgentStatus, SuiteResult } from "@contracts";
import { Check, CircleCheck, CircleX, Eye, FileDiff, X } from "lucide-react";
import { useLayoutEffect, useRef } from "react";
import type { ReactNode } from "react";

import { costText } from "../state";
import type { AgentCardDetail } from "./selectors";
import "./agentCard.css";
import { ageWords } from "./age";
import { StopControl } from "./StopControl";
import { PauseControl, secondaryButtonClass } from "./PauseControl";

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

const focusRing =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";

/** Keep connecting words in the text face and numeric readings in Mono. */
export function DataWords({ text }: { text: string }) {
  return (
    <span>
      {text.split(/((?:<?US\$)?[+−-]?\d+(?:,\d{3})*(?:\.\d+)?)/g).map((part, index) =>
        index % 2 === 1 ? (
          <span key={index} className="font-mono">
            {part}
          </span>
        ) : (
          part
        ),
      )}
    </span>
  );
}

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

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-(length:--caption-size) text-muted">{label}</dt>
      <dd className="mt-0.5 flex items-center gap-1.5 text-(length:--data-size)">{children}</dd>
    </div>
  );
}

/** What the agent finished with: tests, change, steps and cost, each from its own event. */
function Summary({ data }: { data: AgentCardDetail }) {
  const { agent, step, maxSteps, proposal, cost } = data;
  const tests = proposal ? testsLine(proposal.tests) : undefined;
  return (
    <dl aria-label={`Agent ${agent.slot} summary`} className="grid grid-cols-2 gap-x-4 gap-y-3">
      {proposal && tests && (
        <>
          <Fact label="Tests">
            {tests.passed ? (
              <CircleCheck aria-hidden="true" className="size-4 shrink-0 text-passed" />
            ) : (
              <CircleX aria-hidden="true" className="size-4 shrink-0 text-failed-text" />
            )}
            <span className={tests.passed ? "text-passed" : "text-failed-text"}>
              <DataWords text={tests.text} />
            </span>
          </Fact>
          <Fact label="Change">
            <span>
              <span
                className={`font-mono ${proposal.lines_added > 0 ? "font-bold text-passed" : "text-muted"}`}
              >
                +{proposal.lines_added}
              </span>{" "}
              <span
                className={`font-mono ${proposal.lines_removed > 0 ? "font-bold text-failed-text" : "text-muted"}`}
              >
                −{proposal.lines_removed}
              </span>{" "}
              in <DataWords text={String(proposal.files.length)} />{" "}
              {proposal.files.length === 1 ? "file" : "files"}
            </span>
          </Fact>
        </>
      )}
      <Fact label="Steps">
        <DataWords text={`${String(Math.min(step, maxSteps))} of ${String(maxSteps)}`} />
      </Fact>
      {cost !== undefined && (
        <Fact label="Cost">
          <DataWords text={cost === null ? "Unknown" : costText(cost)} />
        </Fact>
      )}
    </dl>
  );
}

export interface AgentCardProps {
  data: AgentCardDetail;
  onOpenDiff?: (changeId: string) => void;
  onReview?: (changeId: string) => void;
  onStop?: (agentId: string) => Promise<unknown>;
  onPause?: (agentId: string) => Promise<unknown>;
  onResume?: (agentId: string) => Promise<unknown>;
}

export function AgentCard({
  data,
  onOpenDiff,
  onReview,
  onStop,
  onPause,
  onResume,
}: AgentCardProps) {
  const { agent, readyForReview } = data;
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
      data-agent-origin={agent.slot}
      style={agentSlotStyle(agent.slot)}
      className={`agent-card min-w-0 shrink-0 rounded-card border bg-card p-3.5 text-text ${border} ${focusRing}`}
    >
      <CardHeader data={data} />
      <p title={agent.task} className="mt-3 truncate text-(length:--body-size)">
        {agent.task}
      </p>
      <div className="agent-card-body">
        {readyForReview ? <Summary data={data} /> : <RunningBody data={data} />}
      </div>
      <CardActions
        data={data}
        onOpenDiff={onOpenDiff}
        onReview={onReview}
        onStop={onStop}
        onPause={onPause}
        onResume={onResume}
      />
    </article>
  );
}

/** Recent events stay scrollable without moving this or neighbouring cards. */
function RunningBody({ data }: { data: AgentCardDetail }) {
  const { agent, step, maxSteps, cost, activity } = data;
  const current = Math.min(step, maxSteps);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <p title={activity} className="truncate text-sm text-muted">
        {activity}
      </p>
      {agent.detail && agent.state !== "working" && agent.state !== "revising" && (
        <p className="text-sm text-muted">{agent.detail}</p>
      )}
      <div
        role="progressbar"
        aria-label={`Agent ${agent.slot} steps`}
        aria-valuenow={current}
        aria-valuemax={maxSteps}
        className="mt-3 h-1 shrink-0 overflow-hidden rounded-full bg-raised"
      >
        <div
          className={`h-full ${look[agent.slot].fill}`}
          style={{ width: `${String(maxSteps > 0 ? Math.min(100, (step / maxSteps) * 100) : 0)}%` }}
        />
      </div>
      <p className="mt-2 flex items-baseline justify-between gap-2 text-sm text-muted">
        <span>
          <DataWords text={`Step ${String(current)} of ${String(maxSteps)}`} />
        </span>
        {cost !== undefined && (
          <span title="Cost so far" className="text-(length:--data-size)">
            <DataWords text={cost === null ? "Cost unknown" : costText(cost)} />
          </span>
        )}
      </p>
      <StepHistory data={data} />
    </div>
  );
}

export function StepHistory({ data }: { data: AgentCardDetail }) {
  const { agent, steps, stepTimes, now } = data;
  return (
    <ol
      aria-label={`Agent ${agent.slot} steps, newest first`}
      tabIndex={0}
      className={`mt-2 min-h-0 flex-1 overflow-y-auto overscroll-contain [overflow-anchor:none] [scrollbar-gutter:stable] ${focusRing}`}
    >
      {steps.map((entry) => (
        <li
          key={entry.step}
          title={`Step ${String(entry.step)} · ${entry.tool} · ${String(entry.duration_ms)} ms`}
          className="border-t border-card-border py-2 text-sm"
        >
          <div className="flex items-center gap-2">
            {entry.result === "ok" ? (
              <Check aria-hidden="true" className="size-4 shrink-0 text-passed" />
            ) : (
              <X aria-hidden="true" className="size-4 shrink-0 text-failed-text" />
            )}
            <span className="sr-only">
              Step {entry.step} {entry.tool}:{" "}
            </span>
            <p className="min-w-0 flex-1 truncate" title={entry.summary}>
              {entry.summary}
            </p>
            <span className="shrink-0 text-(length:--caption-size) text-muted">
              {ageWords(stepTimes[entry.step], now)}
            </span>
          </div>
          <p className={entry.result === "ok" ? "sr-only text-passed" : "text-failed-text"}>
            {entry.result === "ok" ? "Ok" : "Error"}
            {entry.error ? `: ${entry.error}` : ""}
            <span className="font-mono text-muted"> · {entry.duration_ms} ms</span>
          </p>
        </li>
      ))}
      {steps.length === 0 && <li className="py-2 text-sm text-muted">No steps yet.</li>}
    </ol>
  );
}

function CardHeader({ data }: { data: AgentCardDetail }) {
  const { agent, modelLabel, readyForReview } = data;
  const own = look[agent.slot];
  const lamp = readyForReview
    ? own.fill
    : `bg-card ${agent.state === "working" ? "animate-pulse [animation-duration:var(--status-lamp-duration)] motion-reduce:animate-none" : ""}`;
  return (
    <header className="flex h-7 items-center gap-2">
      <span
        aria-hidden="true"
        className={`size-(--status-lamp-size) shrink-0 rounded-full border-(length:--agent-border-width) ${own.border} ${lamp}`}
      />
      <h3 className="shrink-0 text-(length:--title-size) font-bold">Agent {agent.slot}</h3>
      <span title={modelLabel} className="min-w-0 truncate text-sm text-muted">
        {modelLabel}
      </span>
      <span
        role={readyForReview ? "status" : undefined}
        className={`ml-auto rounded-full shrink-0 p-(--status-pill-padding) text-(length:--label-size) font-semibold ${own.tint}`}
      >
        {agent.state === "working" ? data.activityLabel : labels[agent.state]}
      </span>
    </header>
  );
}

function CardActions({ data, onOpenDiff, onReview, onStop, onPause, onResume }: AgentCardProps) {
  const { agent, readyForReview } = data;
  const own = look[agent.slot];
  const reviewClass = `inline-flex h-(--button-primary-height) items-center gap-2 rounded-control px-4 text-(length:--label-size) font-semibold text-on-agent hover:brightness-110 ${own.fill} ${focusRing}`;
  return (
    <footer className="mt-3 flex min-h-10 flex-wrap gap-2">
      {!readyForReview && onOpenDiff && (
        <button
          type="button"
          className={secondaryButtonClass}
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
      {onPause && onResume && (
        <PauseControl
          agentId={agent.agent_id}
          state={agent.state}
          onPause={onPause}
          onResume={onResume}
        />
      )}
      {onStop &&
        ["working", "revising", "ready_for_review", "halted", "paused"].includes(agent.state) && (
          <div className="w-full">
            <StopControl agentId={agent.agent_id} onStop={onStop} />
          </div>
        )}
    </footer>
  );
}
