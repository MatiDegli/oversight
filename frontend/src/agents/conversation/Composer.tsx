import { TriangleAlert } from "lucide-react";
import { useId, useState } from "react";
import { useStore } from "zustand";
import { stateStore } from "../../state";
import { commandFailure } from "../../transport";
import { PauseControl } from "../PauseControl";
import { StopControl } from "../StopControl";
import { beginSend, clearSentDraft, draftKey, draftStore, finishSend, setDraft } from "./drafts";
import type { ConversationHeader } from "./selectConversation";
import { focusRing } from "./rows";

export interface ConversationControls {
  onSend?: (text: string) => Promise<unknown>;
  onPause?: () => Promise<unknown>;
  onResume?: () => Promise<unknown>;
  onStop?: (agentId: string) => Promise<unknown>;
}
const button = `inline-flex h-(--button-secondary-height) items-center justify-center gap-2 rounded-control border border-border px-3 text-[14px] font-semibold not-aria-disabled:hover:bg-raised aria-disabled:cursor-not-allowed aria-disabled:text-faint ${focusRing}`;

/** Commands never focus another element; pending drafts remain readable and retain their text. */
export function Composer({
  header,
  onSend,
  onPause,
  onResume,
  onStop,
}: { header: ConversationHeader } & ConversationControls) {
  const runId = useStore(stateStore, ({ state }) => state.run?.run_id ?? null);
  const key = draftKey(runId, header.agentId);
  const text = useStore(draftStore, (drafts) => drafts[key]?.text ?? "");
  const pending = useStore(draftStore, (drafts) => drafts[key]?.pending ?? false);
  const [failure, setFailure] = useState<string | null>(null);
  const captionId = useId();
  const active = ["working", "revising", "paused"].includes(header.state);
  const replay = !onSend && !onPause && !onResume && !onStop;
  async function send() {
    if (!onSend || !active || !text.trim() || !beginSend(key)) return;
    setFailure(null);
    try {
      await onSend(text);
      clearSentDraft(key, text);
    } catch (error) {
      setFailure(commandFailure("Send", error));
    } finally {
      finishSend(key);
    }
  }
  if (replay)
    return (
      <p className="shrink-0 border-t border-divider px-[22px] py-[14px] text-[14px] text-muted">
        Recorded run: messages, pause and stop are off.
      </p>
    );
  const paused = header.state === "paused";
  return (
    <div className="shrink-0 border-t border-divider p-[14px]">
      {onSend && (
        <textarea
          aria-label={`Message Agent ${header.slot}`}
          aria-describedby={!active ? captionId : undefined}
          placeholder={`Message Agent ${header.slot}. It reads it at its next step.`}
          aria-disabled={!active}
          readOnly={pending || !active}
          value={text}
          onChange={(event) => {
            setDraft(key, event.target.value);
          }}
          className={`block h-16 w-full resize-none rounded-[10px] border border-control-edge bg-field px-3 py-[10px] text-[15px] text-text placeholder:text-faint aria-disabled:text-faint ${focusRing}`}
        />
      )}
      {!active && (
        <p id={captionId} className="mt-[6px] text-[14px] text-muted">
          Agent {header.slot} is not running.
        </p>
      )}
      <div className="mt-[10px] flex flex-wrap items-start gap-2">
        {active && (paused ? onResume : onPause) && (
          <PauseControl
            agentId={header.agentId}
            state={header.state}
            onPause={() => {
              setFailure(null);
              return onPause?.() ?? Promise.resolve();
            }}
            onResume={() => {
              setFailure(null);
              return onResume?.() ?? Promise.resolve();
            }}
          />
        )}
        {active && onStop && <StopControl agentId={header.agentId} onStop={onStop} />}
        {onSend && (
          <button
            type="button"
            className={`${button} ml-auto bg-raised`}
            aria-disabled={!active || pending || !text.trim()}
            onClick={() => {
              void send();
            }}
          >
            Send
          </button>
        )}
      </div>
      {failure && (
        <p
          role="alert"
          title={failure}
          className="mt-[6px] flex min-w-0 gap-2 text-[14px] text-muted"
        >
          <TriangleAlert aria-hidden="true" className="size-4 shrink-0" />
          <span className="truncate">{failure}</span>
        </p>
      )}
    </div>
  );
}
