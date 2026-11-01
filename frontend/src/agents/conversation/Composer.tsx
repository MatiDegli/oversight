import { Pause, Play, TriangleAlert } from "lucide-react";
import { useId, useRef, useState, type ReactNode } from "react";
import { commandFailure } from "../../transport";
import { StopControl } from "../StopControl";
import type { ConversationHeader } from "./selectConversation";
import { focusRing } from "./rows";

export interface ConversationControls {
  actionAccessory?: ReactNode;
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
  actionAccessory,
}: { header: ConversationHeader } & ConversationControls) {
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [controlPending, setControlPending] = useState(false);
  const sending = useRef(false);
  const [failure, setFailure] = useState<string | null>(null);
  const captionId = useId();
  const active = ["working", "revising", "paused"].includes(header.state);
  const replay = !onSend && !onPause && !onResume && !onStop;
  async function send() {
    if (!onSend || !active || !text.trim() || sending.current) return;
    sending.current = true;
    setPending(true);
    setFailure(null);
    try {
      await onSend(text);
      setText("");
    } catch (error) {
      setFailure(commandFailure("Send", error));
    } finally {
      sending.current = false;
      setPending(false);
    }
  }
  async function control() {
    const callback = header.state === "paused" ? onResume : onPause;
    if (!callback || controlPending || !active) return;
    setControlPending(true);
    setFailure(null);
    try {
      await callback();
    } catch (error) {
      setFailure(commandFailure(header.state === "paused" ? "Resume" : "Pause", error));
    } finally {
      setControlPending(false);
    }
  }
  if (replay) {
    const caption = "Recorded run: messages, pause and stop are off.";
    const replayClass =
      "conversation-composer shrink-0 border-t border-divider px-[22px] py-[14px] text-[14px] text-muted";
    return actionAccessory ? (
      <div className={replayClass}>
        <p>{caption}</p>
        <div className="composer-actions flex">{actionAccessory}</div>
      </div>
    ) : (
      <p className={replayClass}>{caption}</p>
    );
  }
  const paused = header.state === "paused";
  return (
    <div className="conversation-composer shrink-0 border-t border-divider p-[14px]">
      {onSend && (
        <textarea
          aria-label={`Message Agent ${header.slot}`}
          aria-describedby={!active ? captionId : undefined}
          placeholder={`Message Agent ${header.slot}. It reads it at its next step.`}
          aria-disabled={!active}
          readOnly={pending || !active}
          value={text}
          onChange={(event) => {
            setText(event.target.value);
          }}
          className={`block h-16 w-full resize-none rounded-[10px] border border-control-edge bg-field px-3 py-[10px] text-[15px] text-text placeholder:text-faint aria-disabled:text-faint ${focusRing}`}
        />
      )}
      {!active && (
        <p id={captionId} className="mt-[6px] text-[14px] text-muted">
          Agent {header.slot} is not running.
        </p>
      )}
      <div className="composer-actions mt-[10px] flex flex-wrap items-start gap-2">
        {active && (paused ? onResume : onPause) && (
          <button
            type="button"
            className={button}
            aria-disabled={controlPending}
            onClick={() => {
              void control();
            }}
          >
            {paused ? (
              <Play aria-hidden="true" className="size-4" />
            ) : (
              <Pause aria-hidden="true" className="size-4" />
            )}
            {paused ? "Resume" : "Pause"}
          </button>
        )}
        {active && onStop && <StopControl agentId={header.agentId} onStop={onStop} />}
        {actionAccessory}
        {onSend && (
          <button
            type="button"
            className={`${button} ${actionAccessory ? "" : "ml-auto"} bg-raised`}
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
