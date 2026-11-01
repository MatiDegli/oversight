import { useState, type ReactNode } from "react";
import { ChevronRight, LoaderCircle } from "lucide-react";
import type { Slot } from "@contracts";
import { agentLook } from "../../theme";
import type { ConversationEntry } from "./selectConversation";
import { focusRing, TimelineEntry } from "./rows";
import { elapsed, toolTime } from "./progressWords";

function FoldToggle({
  open,
  ended,
  label,
  aside,
  onToggle,
}: {
  open: boolean;
  ended: boolean;
  label: ReactNode;
  aside: ReactNode;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      aria-expanded={open}
      onClick={() => {
        onToggle();
      }}
      className={`flex min-h-[38px] w-full items-center rounded-[10px] ${ended ? "gap-2" : "gap-3 px-[14px]"} text-left text-[14px] text-muted ${focusRing}`}
    >
      <ChevronRight aria-hidden="true" className={`size-3 shrink-0 ${open ? "rotate-90" : ""}`} />
      <span className="min-w-0 flex-1 wrap-anywhere">{label}</span>
      <span className="shrink-0 font-mono text-[13px]">{aside}</span>
    </button>
  );
}

export function ActivityBlock({
  entry,
  slot,
  changeId,
  onOpenDiff,
  showHeader = true,
}: {
  entry: Extract<ConversationEntry, { kind: "activity" | "round" }>;
  slot: Slot;
  changeId: string;
  onOpenDiff?: (id: string) => void;
  showHeader?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const { round } = entry;
  const earlier = round.steps.slice(0, -1);
  const newest = round.steps.at(-1);
  const ended = entry.kind === "round";

  return (
    <div className="my-[9px] mr-[22px] ml-[56px]">
      {!ended && showHeader && (
        <div className="mb-[9px] flex min-h-6 items-center gap-[10px] text-[14px] text-muted">
          <LoaderCircle
            aria-hidden="true"
            className={`size-[14px] ${agentLook[slot].textClass} ${round.paused ? "" : "animate-spin motion-reduce:animate-none"}`}
          />
          <span>{round.paused ? "Paused" : "Working"}</span>
          <span className="font-mono text-[13px]">{elapsed(round.elapsedMs)}</span>
        </div>
      )}
      <div
        className={
          ended && !open
            ? ""
            : "overflow-hidden rounded-[12px] border border-border bg-card [&>div+div]:border-t [&>div+div]:border-divider"
        }
      >
        {(ended || earlier.length > 0) && (
          <div key="toggle">
            <FoldToggle
              open={open}
              ended={ended}
              onToggle={() => {
                setOpen(!open);
              }}
              label={
                ended ? (
                  <>
                    Round <span className="font-mono">{round.number}</span> · {round.outcome}
                  </>
                ) : (
                  <>
                    <span className="font-mono">{earlier.length}</span> earlier{" "}
                    {earlier.length === 1 ? "call" : "calls"}
                  </>
                )
              }
              aside={
                ended ? (
                  <>
                    {round.steps.length} {round.steps.length === 1 ? "call" : "calls"} ·{" "}
                    {elapsed(round.elapsedMs)}
                  </>
                ) : (
                  <>{toolTime(earlier.reduce((sum, e) => sum + e.step.duration_ms, 0))} in tools</>
                )
              }
            />
          </div>
        )}
        {(ended ? (open ? entry.children : []) : open ? round.steps : newest ? [newest] : []).map(
          (child) => (
            <div key={child.seq}>
              <TimelineEntry entry={child} changeId={changeId} onOpenDiff={onOpenDiff} compact />
            </div>
          ),
        )}
      </div>
    </div>
  );
}
