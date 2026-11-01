import type { ChangeResolved } from "@contracts";
// eslint-disable-next-line no-restricted-imports -- C5 reuses the timeline timestamp leaf without a graph/editor barrel.
import { EventTime } from "../../agents/conversation/rows";
// eslint-disable-next-line no-restricted-imports -- The immutable wording helper is a pure leaf.
import { mergedRuleWords } from "../../review/evidenceWords";
import { ChevronDown, Eye, GitMerge } from "lucide-react";
import type { State } from "../../state";
import { selectMergeModeLine } from "./statusSelectors";
import "./chatMerge.css";

/** Navigation only: command capability remains owned by the shared Settings panel. */
export function ChatMergeChip({
  state,
  onOpenSettings,
}: {
  state: State;
  onOpenSettings?: () => void;
}) {
  const content = (
    <>
      <GitMerge aria-hidden="true" size={15} />
      <span>{selectMergeModeLine(state) ?? "Merge mode: unknown"}</span>
      {onOpenSettings && <ChevronDown aria-hidden="true" size={14} />}
    </>
  );
  return (
    <span className="chat-merge-accessory">
      {onOpenSettings ? (
        <button type="button" className="chat-merge-chip" onClick={onOpenSettings}>
          {content}
        </button>
      ) : (
        <span className="chat-merge-chip">{content}</span>
      )}
    </span>
  );
}

export function isRulesMerge(resolution: ChangeResolved) {
  return (
    resolution.decision === "approved" &&
    resolution.merge === "merged" &&
    resolution.decided_by === "rules"
  );
}

/** Render at the retained resolution, never from the current policy or agent label. */
export function ChatMergeReceipt({
  resolution,
  ts,
  onReview,
}: {
  resolution: ChangeResolved;
  ts: string;
  onReview?: () => void;
}) {
  const words = mergedRuleWords(resolution);
  const mode = resolution.assessment?.mode;
  return (
    <div>
      <article
        className="chat-merge-receipt"
        aria-label="Merged by your rules"
        data-change-id={resolution.change_id}
      >
        <span className="chat-merge-state">
          <GitMerge aria-hidden="true" size={12} />
        </span>
        <div className="chat-merge-content">
          <div className="chat-merge-title">
            <strong>Merged by your rules</strong>
            {mode && (
              <span>Merge mode: {mode === "ask_me" ? "Ask me" : "Merge alone when safe"}</span>
            )}
          </div>
          {words.line && <p className="chat-merge-reasons">{words.line}</p>}
          <div className="chat-merge-links">
            {onReview ? (
              <button type="button" onClick={onReview}>
                <Eye aria-hidden="true" size={14} />
                Open its review
              </button>
            ) : (
              <span>Its review is unavailable.</span>
            )}
            {resolution.merge_commit && (
              <span className="chat-merge-commit">
                Merged into main{" "}
                <span
                  title={resolution.merge_commit}
                  aria-label={`Commit ${resolution.merge_commit}`}
                >
                  {resolution.merge_commit.slice(0, 7)}
                </span>
              </span>
            )}
          </div>
        </div>
        <EventTime ts={ts} />
      </article>
    </div>
  );
}
