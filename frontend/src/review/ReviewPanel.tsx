import { ReviewBody } from "./ReviewBody";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

import { EditorDiff, fileKey, ReviewEditor } from "../editor";
import type { FileAccess } from "../editor";
import { selectLiveDiff } from "../state";
import type { State } from "../state";
import { decisionView } from "./decisions";
import type { DecisionAction, DecisionAttempt } from "./decisions";
import { ReviewHeader } from "./ReviewHeader";
import { selectReviewBody } from "./reviewBodyEvidence";
import { candidateKey, useAnswers } from "./answers";
import { agentSlotStyle } from "../theme";

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
export function ReviewPanel({
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
  const body = selectReviewBody(state, changeId);
  const candidate = candidateKey(state, changeId);
  const answers = useAnswers((s) => s.drafts[candidate]) ?? {};
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
  const [chosen, setChosen] = useState<string | null>(() =>
    ["a", "unavailable"].includes(body.kind) ? null : (files[0] ?? null),
  );
  const openPath = chosen !== null && files.includes(chosen) ? chosen : null;
  const [opened, setOpened] = useState(() => new Set(chosen ? [chosen] : []));
  // A deferred editor cannot report readiness until its chunk and file have loaded.
  const editingBlocked =
    fileAccess !== undefined &&
    openPath !== null &&
    selectLiveDiff(state, changeId, openPath) !== undefined &&
    (editing?.path !== openPath || editing.blocked);
  const hasDraft =
    change &&
    files.some(
      (path) =>
        fileAccess?.drafts.has(fileKey(path, change.agent_id)) ||
        fileAccess?.inFlightSaves.has(fileKey(path, change.agent_id)),
    );
  const stats = new Map(proposal?.files.map((file) => [file.path, file]));
  return (
    <aside
      aria-label="Review panel"
      className="review-panel flex w-[min(var(--review-panel-width),46vw)] min-h-0 min-w-0 shrink-0 flex-col bg-page"
      style={agentSlotStyle(agent?.slot)}
    >
      <ReviewHeader
        state={state}
        changeId={changeId}
        status={body.kind === "g" ? "Merged" : undefined}
        onClose={onClose}
      />
      <ReviewBody
        state={state}
        changeId={changeId}
        showCrossReview={!attempt || attempt.revision !== proposal?.revision}
        diff={
          <div aria-label="Change files">
            {files.map((path) => {
              const open = path === openPath;
              const stat = stats.get(path);
              const data = selectLiveDiff(state, changeId, path);
              return (
                <section
                  key={path}
                  aria-label={`Diff of ${path}`}
                  className={`flex flex-col border-b border-page-border ${open ? "review-file-open" : ""}`}
                >
                  <button
                    type="button"
                    aria-expanded={open}
                    onClick={() => {
                      setChosen(open ? null : path);
                      if (!open) setOpened((paths) => new Set([...paths, path]));
                    }}
                    className={`review-file-row flex shrink-0 items-center gap-2.5 text-left ${focusRing}`}
                  >
                    {open ? (
                      <ChevronDown aria-hidden="true" className="size-4 text-muted" />
                    ) : (
                      <ChevronRight aria-hidden="true" className="size-4 text-muted" />
                    )}
                    <span className="min-w-0 truncate font-mono text-sm">{path}</span>
                    {stat && (
                      <span className="text-sm font-semibold">
                        <span className="text-added">+{stat.lines_added}</span>{" "}
                        <span className="text-removed">−{stat.lines_removed}</span>
                      </span>
                    )}
                    <span className="ml-auto text-sm text-muted">
                      {!open
                        ? "Show diff"
                        : body.kind === "g"
                          ? "Inline diff"
                          : fileAccess && editable
                            ? "Editable · Inline diff"
                            : "Read-only · Inline diff"}
                    </span>
                  </button>
                  {opened.has(path) && (
                    <div className="review-diff-viewport" hidden={!open}>
                      {data ? (
                        fileAccess && body.kind !== "g" ? (
                          <ReviewEditor
                            key={path}
                            data={data}
                            files={fileAccess}
                            editable={editable}
                            revision={proposal?.revision ?? 0}
                            onMount={(diff) => {
                              diff.updateOptions({ wordWrap: "off", diffWordWrap: "off" });
                            }}
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
                          <EditorDiff
                            data={data}
                            onMount={(diff) => {
                              diff.updateOptions({ wordWrap: "off", diffWordWrap: "off" });
                            }}
                          />
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
        }
      />
      {body.kind === "g" ? (
        <footer className="review-merged">
          Merged into main <code>{change?.resolution?.merge_commit?.slice(0, 7)}</code>
        </footer>
      ) : (
        change && (
          <ReviewDecisionBar
            changeId={changeId}
            view={decisionView(change, state.errors, attempt, onDecide === undefined)}
            onDecide={onDecide}
            replayOf={replayOf}
            candidate={body.facts?.review_cards?.length ? candidate : changeId}
            cards={body.facts?.review_cards ?? []}
            answers={answers}
            unavailable={body.kind === "unavailable"}
            blocked={
              hasDraft
                ? "edits"
                : editingBlocked
                  ? editing?.path === openPath
                    ? editing.blocked
                    : "loading"
                  : null
            }
          />
        )
      )}
    </aside>
  );
}
