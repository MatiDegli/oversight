import type { SuiteResult } from "@contracts";
import {
  Check,
  CircleHelp,
  Coins,
  FileCode,
  GitBranch,
  GitCommitHorizontal,
  ListChecks,
  ScanEye,
  TestTube,
  X,
} from "lucide-react";
import type { ReactNode } from "react";

import { DataWords, selectAgentCard } from "../agents";
import { liveImpact, possibleCallers } from "../graph";
import { costText } from "../state";
import type { State } from "../state";
import { impactSummary, possibleCallersText } from "./ImpactCone";

const chip =
  "inline-flex min-h-(--chip-height) min-w-0 items-center gap-1.5 rounded-control border border-page-border bg-page-raised px-2.5 py-1 text-(length:--data-size)";
const icon = "size-4 shrink-0 text-muted";

function Fact({
  label,
  symbol,
  children,
  review = false,
}: {
  review?: boolean;
  label: string;
  symbol: ReactNode;
  children: ReactNode;
}) {
  return (
    <li
      aria-label={label}
      className={review ? `${chip} h-[30px] rounded-[7px] py-0 text-sm font-semibold` : chip}
    >
      {symbol}
      <span className="min-w-0 break-words">{children}</span>
    </li>
  );
}

function TestsFact({
  tests,
  review = false,
}: {
  tests: SuiteResult | undefined;
  review?: boolean;
}) {
  const text = !tests
    ? "Tests unknown"
    : tests.outcome === "passed"
      ? `${String(tests.passed)} passed`
      : tests.outcome === "failed"
        ? `${String(tests.failed)} failed, ${String(tests.passed)} passed`
        : tests.outcome === "error"
          ? "Tests could not run"
          : "Tests timed out";
  return (
    <Fact
      review={review}
      label="Tests"
      symbol={
        !tests ? (
          <TestTube aria-hidden="true" className={icon} />
        ) : tests.outcome === "passed" ? (
          <Check aria-hidden="true" className="size-4 shrink-0 text-passed" />
        ) : (
          <X aria-hidden="true" className="size-4 shrink-0 text-failed-text" />
        )
      }
    >
      <DataWords text={text} />
    </Fact>
  );
}

/** The live preview shares the graph's bounded cone; review keeps the proposal's evidence. */
export function ChangeFactsRow({
  state,
  changeId,
  live = false,
}: {
  state: State;
  changeId: string;
  live?: boolean;
}) {
  const change = state.changes[changeId];
  if (!change) return null;
  const proposal = live && ["editing", "revising"].includes(change.status) ? null : change.proposal;
  const cone = live ? liveImpact(state, change) : proposal?.impact;
  const possible = cone ? (possibleCallers(state, change, cone) ?? 0) : 0;
  const fileCount = live ? change.files_changed : (proposal?.files.length ?? change.files_changed);
  const card = selectAgentCard(state, change.agent_id);
  const base = proposal?.base_commit || card?.agent.base_commit;
  const lastTests = Object.values(state.testExecutions).findLast(
    (execution) =>
      execution.change_id === changeId &&
      execution.agent_id === change.agent_id &&
      execution.phase === "agent",
  );
  if (!live)
    return (
      <ul aria-label="Facts" className="flex flex-wrap items-center gap-2 text-text">
        <Fact review label="Files" symbol={<FileCode aria-hidden="true" className={icon} />}>
          <span className="font-mono">{fileCount}</span> {fileCount === 1 ? "file" : "files"}
        </Fact>
        <Fact review label="Lines" symbol={<ListChecks aria-hidden="true" className={icon} />}>
          <span className="font-mono text-added">
            +{proposal?.lines_added ?? change.lines_added}
          </span>{" "}
          <span className="font-mono text-removed">
            −{proposal?.lines_removed ?? change.lines_removed}
          </span>
        </Fact>
        <TestsFact review tests={proposal?.tests ?? lastTests?.suite} />
        <Fact review label="Impact cone" symbol={<ScanEye aria-hidden="true" className={icon} />}>
          <DataWords
            text={
              !cone
                ? "Possibly affected: unknown"
                : cone.entries.length === 0
                  ? "No exact callers"
                  : `${String(cone.entries.length)} possibly affected · up to distance ${String(Math.max(...cone.entries.map((entry) => entry.distance)))}`
            }
          />
        </Fact>
      </ul>
    );
  return (
    <ul aria-label="Facts" className="flex flex-wrap items-center gap-2 text-text">
      <Fact label="Files" symbol={<FileCode aria-hidden="true" className={icon} />}>
        <span className="font-mono">{fileCount}</span> {fileCount === 1 ? "file" : "files"}
      </Fact>
      <Fact label="Lines" symbol={<ListChecks aria-hidden="true" className={icon} />}>
        <span className="font-mono text-added">+{change.lines_added}</span>{" "}
        <span className="font-mono text-removed">−{change.lines_removed}</span>
      </Fact>
      <TestsFact tests={proposal?.tests ?? lastTests?.suite} />
      <Fact
        label="Base commit"
        symbol={<GitCommitHorizontal aria-hidden="true" className={icon} />}
      >
        Base{" "}
        {base ? (
          <code title={base} className="font-mono">
            {base.slice(0, 7)}
          </code>
        ) : (
          "Unknown"
        )}
      </Fact>
      <Fact label="Impact cone" symbol={<ScanEye aria-hidden="true" className={icon} />}>
        {cone ? <DataWords text={impactSummary(cone, possible)} /> : "Possibly affected: unknown"}
      </Fact>
      <Fact label="Model and cost" symbol={<Coins aria-hidden="true" className={icon} />}>
        {card?.modelLabel ?? "Model unknown"} · <DataWords text={costText(card?.cost)} />
      </Fact>
      <Fact label="Worktree" symbol={<GitBranch aria-hidden="true" className={icon} />}>
        Worktree{" "}
        <code title={`.runtime/worktrees/${change.agent_id}`} className="font-mono">
          {change.agent_id}
        </code>
      </Fact>
      <Fact label="Steps" symbol={<ListChecks aria-hidden="true" className={icon} />}>
        {card ? (
          <DataWords text={`Step ${String(card.step)} of ${String(card.maxSteps)}`} />
        ) : (
          "Steps unknown"
        )}
      </Fact>
      {cone && cone.entries.length > 0 && possible > 0 && (
        <Fact label="Possible callers" symbol={<CircleHelp aria-hidden="true" className={icon} />}>
          <DataWords text={`+${possibleCallersText(possible)}`} />
        </Fact>
      )}
      {cone && cone.omitted_by_depth > 0 && (
        <Fact label="Cone depth bound" symbol={<CircleHelp aria-hidden="true" className={icon} />}>
          <DataWords text={`+${String(cone.omitted_by_depth)} omitted by depth`} />
        </Fact>
      )}
      {cone && cone.omitted_by_limit > 0 && (
        <Fact label="Cone node bound" symbol={<CircleHelp aria-hidden="true" className={icon} />}>
          <DataWords text={`+${String(cone.omitted_by_limit)} omitted by the node limit`} />
        </Fact>
      )}
    </ul>
  );
}
