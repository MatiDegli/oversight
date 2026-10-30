import { ChangeCard } from "./ChangeCard";
import { selectChangeCard } from "./inboxSelectors";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

import { EditorDiff, fileKey, ReviewEditor } from "../editor";
import type { FileAccess } from "../editor";
import { selectLiveDiff } from "../state";
import type { State } from "../state";
import { CrossReviewNote } from "./CrossReviewNote";
import { decisionView } from "./decisions";
import type { DecisionAction, DecisionAttempt } from "./decisions";
import { ReviewHeader } from "./ReviewHeader";
import { FactsSummary } from "./FactsSummary";
import { ReviewFacts } from "./ReviewFacts";
import { factsForRevision } from "./factsForRevision";

import { ReviewDecisionBar } from "./ReviewDecisionBar";

const focusRing =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";
export interface ReviewPanelProps {
  state: State;
  files?: FileAccess;
  changeId: string;
  /** The person's last decision on this change, kept by the app across panel closes. */
  attempt?: DecisionAttempt;
  /** Absent in a replay: the replay build has no backend and sends no commands. */
  onDecide?: (changeId: string, action: DecisionAction, comment: string) => void;
  /** What a replay plays, such as "hand-written demo fixture"; absent in a live run. */
  replayOf?: string;
  onClose: () => void;
}

/**
 * Review of one proposed change: what it is, its diff file by file, and the decisions. Opened only
 * by the person; events update it in place and never open or close it.
 */
function LegacyReviewPanel({
  state,
  files: fileAccess,
  changeId,
  attempt,
  onDecide,
  replayOf,
  onClose,
}: ReviewPanelProps) {
  const change = state.changes[changeId];
  const proposal = change?.proposal ?? null;
  const facts = proposal === null ? undefined : factsForRevision(state, proposal);
  const hasContext = proposal !== null;
  const agent = change ? state.agents[change.agent_id] : undefined;
  const files = proposal?.files.map((file) => file.path) ?? Object.keys(change?.files ?? {});
  const [editing, setEditing] = useState<{
    path: string;
    blocked: "loading" | "edits" | null;
  } | null>(null);
  const decision = change
    ? decisionView(change, state.errors, attempt, onDecide === undefined)
    : undefined;
  const editable =
    agent?.state === "ready_for_review" &&
    change?.status === "ready_for_review" &&
    decision?.kind === "open";
  const [chosen, setChosen] = useState<string | null>(null);
  const openPath = chosen !== null && files.includes(chosen) ? chosen : (files[0] ?? null);
  // A deferred editor cannot report readiness until its chunk and file have loaded.
  const editingBlocked =
    fileAccess !== undefined &&
    openPath !== null &&
    selectLiveDiff(state, changeId, openPath) !== undefined &&
    (editing?.path !== openPath || editing.blocked);
  const stats = new Map(proposal?.files.map((file) => [file.path, file]));
  const context = (
    <>
      <FactsSummary facts={facts} />
      {change && (!attempt || attempt.revision !== proposal?.revision) && (
        <CrossReviewNote change={change} />
      )}
      {proposal && <ReviewFacts state={state} proposal={proposal} scrollable={false} />}
    </>
  );
  const report = (
    <p className="border-b border-page-border px-4.5 py-3.5 text-(length:--body-size)">
      {proposal?.summary ?? "The agent left no note."}
    </p>
  );
  return (
    <aside
      aria-label="Review panel"
      className="flex w-[min(var(--review-panel-width),46vw)] shrink-0 flex-col border-r border-divider bg-page"
    >
      <ReviewHeader state={state} changeId={changeId} onClose={onClose} />
      {hasContext ? (
        <section aria-label="Review context" className="min-h-0 overflow-y-auto">
          {context}
          {report}
        </section>
      ) : (
        context
      )}
      <div className={`flex flex-1 flex-col ${hasContext ? "min-h-min" : "min-h-0"}`}>
        {!hasContext && report}
        {files.map((path) => {
          const open = path === openPath;
          const stat = stats.get(path);
          const data = open ? selectLiveDiff(state, changeId, path) : undefined;
          return (
            <section
              key={path}
              aria-label={`Diff of ${path}`}
              className={`flex flex-col border-b border-page-border ${open ? (hasContext ? "min-h-min flex-1" : "min-h-0 flex-1") : ""}`}
            >
              <button
                type="button"
                aria-expanded={open}
                onClick={() => {
                  setChosen(path);
                }}
                className={`flex h-10 shrink-0 items-center gap-2.5 px-4.5 text-left ${focusRing}`}
              >
                {open ? (
                  <ChevronDown aria-hidden="true" className="size-4 text-muted" />
                ) : (
                  <ChevronRight aria-hidden="true" className="size-4 text-muted" />
                )}
                <span className="min-w-0 truncate font-mono text-sm">{path}</span>
                {stat && (
                  <span className="font-mono text-sm">
                    <span className="text-added">+{stat.lines_added}</span>{" "}
                    <span className="text-removed">−{stat.lines_removed}</span>
                  </span>
                )}
                <span className="ml-auto text-sm text-muted">
                  {fileAccess && editable ? "Editable · Inline diff" : "Read-only · Inline diff"}
                </span>
              </button>
              {open && (
                <div className="min-h-48 flex-1">
                  {data ? (
                    fileAccess ? (
                      <ReviewEditor
                        key={path}
                        data={data}
                        files={fileAccess}
                        editable={editable}
                        revision={proposal?.revision ?? 0}
                        onBlocked={(blocked) => {
                          const hasDraft = Object.keys(change?.files ?? {}).some(
                            (name) =>
                              fileAccess.drafts.has(fileKey(name, data.agentId)) ||
                              fileAccess.inFlightSaves.has(fileKey(name, data.agentId)),
                          );
                          setEditing({ path, blocked: hasDraft ? "edits" : blocked });
                        }}
                      />
                    ) : (
                      <EditorDiff data={data} />
                    )
                  ) : (
                    <p className="px-4.5 py-3.5 text-sm text-muted">
                      No contents were recorded for this file.
                    </p>
                  )}
                </div>
              )}
            </section>
          );
        })}
      </div>
      {change && (
        <ReviewDecisionBar
          changeId={changeId}
          view={decisionView(change, state.errors, attempt, onDecide === undefined)}
          onDecide={onDecide}
          replayOf={replayOf}
          blocked={
            editingBlocked ? (editing?.path === openPath ? editing.blocked : "loading") : null
          }
        />
      )}
    </aside>
  );
}

export function ReviewPanel(props: ReviewPanelProps) {
  return selectChangeCard(props.state, props.changeId) ? (
    <ChangeCard {...props} reviewPanel />
  ) : (
    <LegacyReviewPanel {...props} />
  );
}
