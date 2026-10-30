import { ChangeCard } from "./ChangeCard";
import { selectChangeCard } from "./inboxSelectors";
import type { ChangeResolved } from "@contracts";
import { Check, ChevronDown, ChevronRight, GitMerge, MessageSquare, X } from "lucide-react";
import { useState } from "react";

import { EditorDiff, fileKey, ReviewEditor } from "../editor";
import type { FileAccess } from "../editor";
import { selectLiveDiff } from "../state";
import type { State } from "../state";
import { CrossReviewNote } from "./CrossReviewNote";
import { decisionView } from "./decisions";
import type { DecisionAction, DecisionAttempt, DecisionView } from "./decisions";
import { ReviewHeader } from "./ReviewHeader";
import { FactsSummary } from "./FactsSummary";
import { ReviewFacts } from "./ReviewFacts";
import { factsForRevision } from "./factsForRevision";

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

/** The recorded or returned decision, in words, with an icon so color is never alone. */
function Resolution({ resolution }: { resolution: ChangeResolved }) {
  let ok = false;
  let text: string;
  if (resolution.decision === "approved" && resolution.merge === "merged") {
    ok = true;
    text = "Approved and merged into main";
  } else if (resolution.decision === "approved" && resolution.conflict?.kind === "tests") {
    text = "Approved, not merged: the tests failed on the combined result with main";
  } else if (resolution.decision === "approved") {
    const paths = resolution.conflict?.paths.join(", ");
    text = `Approved, not merged: the merge conflicts with main${paths ? ` in ${paths}` : ""}`;
  } else if (resolution.decision === "rejected") {
    text =
      resolution.reason === "stopped"
        ? "The agent was stopped and its change discarded"
        : "Rejected: the change was discarded";
  } else {
    text = "Changes requested: the comment went back to the agent";
  }
  return (
    <div className="flex flex-col gap-1.5">
      <p className="flex items-center gap-2">
        {ok ? (
          <Check aria-hidden="true" className="size-4 shrink-0 text-passed" />
        ) : resolution.decision === "changes_requested" ? (
          <MessageSquare aria-hidden="true" className="size-4 shrink-0 text-muted" />
        ) : (
          <X aria-hidden="true" className="size-4 shrink-0 text-failed-text" />
        )}
        <span>{text}</span>
        {resolution.merge_commit && (
          <span className="font-mono text-muted" title={resolution.merge_commit}>
            {resolution.merge_commit.slice(0, 7)}
          </span>
        )}
      </p>
      {resolution.comment && <p className="text-sm text-muted">Comment: {resolution.comment}</p>}
    </div>
  );
}

const primary = `inline-flex h-(--button-primary-height) items-center gap-2 rounded-control bg-text px-4 text-(length:--label-size) font-semibold text-page hover:bg-text-hover disabled:cursor-not-allowed disabled:bg-raised disabled:text-faint ${focusRing}`;
const destructive = `inline-flex h-(--button-destructive-height) items-center gap-2 rounded-control px-4 text-(length:--label-size) font-semibold text-failed-text hover:bg-raised disabled:cursor-not-allowed disabled:text-faint disabled:hover:bg-transparent ${focusRing}`;

function Decisions({
  changeId,
  view,
  onDecide,
  replayOf,
  blocked = null,
}: {
  changeId: string;
  view: DecisionView;
  onDecide?: ReviewPanelProps["onDecide"];
  replayOf?: string;
  blocked?: "loading" | "edits" | null;
}) {
  const [comment, setComment] = useState("");
  // Set when Approve was activated without a pointer, so the person is told why nothing happened.
  const [keyboardTried, setKeyboardTried] = useState(false);
  if (view.kind === "decided") {
    return (
      <section aria-label="Decision" className="shrink-0 border-t border-page-border p-4.5">
        <Resolution resolution={view.resolution} />
      </section>
    );
  }
  const recorded = view.kind === "recorded";
  // What the log is comes from its source: only a real recording may call itself one.
  const replay = replayOf === undefined ? "Replay" : `Replay of a ${replayOf}`;
  if (recorded && view.resolution) {
    return (
      <section
        aria-label="Decision"
        className="flex flex-col gap-2.5 border-t border-page-border p-4.5"
      >
        <p className="text-sm text-muted">{`${replay}: this is the decision the log holds.`}</p>
        <Resolution resolution={view.resolution} />
      </section>
    );
  }
  const pending = view.kind === "pending";
  const revising = view.kind === "revising";
  const approvable = view.kind === "open" && onDecide !== undefined && !blocked;
  const requestable =
    (view.kind === "open" || view.kind === "conflict") && onDecide !== undefined && !blocked;
  const rejectable = (view.kind === "open" || view.kind === "conflict") && onDecide !== undefined;
  const failure = view.kind === "open" || view.kind === "conflict" ? view.failure : null;
  // No key in this section makes a decision. Approve answers only a pointer click: Enter and Space
  // on the focused button arrive as a click with detail 0, which AGENTS.md forbids it to act on.
  // Reject stays keyboard-operable, and Escape in the comment box only leaves the box.
  return (
    <section
      aria-label="Decision"
      className="flex shrink-0 flex-col gap-2.5 border-t border-page-border p-4.5"
    >
      {view.kind === "conflict" && <Resolution resolution={view.resolution} />}
      {recorded ? (
        <p className="text-sm text-muted">
          {`${replay}: nothing here sends a decision. The decision in the log shows when it arrives.`}
        </p>
      ) : (
        <label className="flex flex-col gap-1.5 text-sm text-muted">
          Comment (required for Request changes)
          <textarea
            value={comment}
            rows={3}
            readOnly={pending || revising}
            onChange={(event) => {
              setComment(event.target.value);
            }}
            onKeyDown={(event) => {
              // The app's Escape closes review, which would drop the comment; here it only leaves.
              if (event.key === "Escape" && !event.nativeEvent.isComposing) {
                event.preventDefault();
                event.currentTarget.blur();
              }
            }}
            placeholder="What should the agent change?"
            className={`resize-none rounded-card border border-control-edge bg-field p-(--input-padding) text-(length:--body-size) text-text placeholder:text-faint read-only:text-muted ${focusRing}`}
          />
        </label>
      )}
      <div className="flex flex-wrap items-center gap-2.5">
        <button
          type="button"
          disabled={!approvable}
          onClick={(event) => {
            // 0 for Enter, Space and a script's click; a mouse, pen or touch click is 1 or more.
            if (event.detail === 0) {
              setKeyboardTried(true);
              return;
            }
            setKeyboardTried(false);
            onDecide?.(changeId, "approve", comment);
          }}
          className={primary}
        >
          <GitMerge aria-hidden="true" className="size-4" /> Approve and merge
        </button>
        <button
          type="button"
          disabled={!requestable || comment.trim() === ""}
          onClick={() => {
            onDecide?.(changeId, "request-changes", comment);
          }}
          className={`inline-flex h-(--button-secondary-height) items-center gap-2 rounded-control border border-control-edge px-3 text-(length:--label-size) font-semibold disabled:cursor-not-allowed disabled:text-faint ${focusRing}`}
        >
          <MessageSquare aria-hidden="true" className="size-4" /> Request changes
        </button>
        <button
          type="button"
          disabled={!rejectable}
          onClick={() => {
            setKeyboardTried(false);
            onDecide?.(changeId, "reject", comment);
          }}
          className={`ml-auto ${destructive}`}
        >
          <X aria-hidden="true" className="size-4" /> Reject
        </button>
      </div>
      <p className="text-sm text-muted">
        Request changes keeps the worktree and sends your comment back.
      </p>
      {revising &&
        (view.resolution ? (
          <Resolution resolution={view.resolution} />
        ) : (
          <p role="status">The agent is revising. Decisions reopen when it proposes again.</p>
        ))}
      {blocked === "edits" && (
        <p role="status" className="text-sm text-muted">
          Save or discard your review edits before approving or requesting changes.
        </p>
      )}
      {/* Always mounted, so what appears under the buttons is announced where it appears. */}
      <div role="status" className="text-sm text-muted">
        {pending ? (
          <p>
            {view.action === "request-changes"
              ? "Requesting changes: waiting for the agent."
              : view.action === "approve"
                ? "Approving: waiting for the merge result."
                : "Rejecting: waiting for the server."}
          </p>
        ) : (
          keyboardTried && approvable && <p>Approve with a click: it merges into main</p>
        )}
      </div>
      {!recorded && view.kind !== "conflict" && (
        <p className="text-sm text-muted">
          If main moved since the agent started, the tests run again on the combined result before
          the merge.
        </p>
      )}
      {failure !== null && (
        <p role="alert" className="flex items-start gap-2 text-sm text-text">
          <X aria-hidden="true" className="mt-0.5 size-4 shrink-0" /> {failure}
        </p>
      )}
    </section>
  );
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
        <Decisions
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
    <ChangeCard {...props} />
  ) : (
    <LegacyReviewPanel {...props} />
  );
}
