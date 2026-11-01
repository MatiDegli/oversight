import { ChevronDown, GitMerge } from "lucide-react";
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
