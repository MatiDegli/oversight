import { Clock, Gauge, RefreshCw, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import type { BudgetMark } from "../../state";
import type { Conversation } from "./selectConversation";
import { budgetWords, causeWords, timeWords } from "./progressWords";

function Numbers({ text }: { text: string }) {
  return text.split(/(US\$[\d,.]+|\d+(?:\.\d+)?%?)/).map((part, i) =>
    /\d/.test(part) ? (
      <span key={i} className="font-mono">
        {part}
      </span>
    ) : (
      part
    ),
  );
}
function Notice({ icon: Icon, children }: { icon: LucideIcon; children: ReactNode }) {
  return (
    <div className="@container">
      <div className="my-2 mr-[22px] ml-[56px] flex min-h-[44px] items-center gap-3 rounded-[10px] border border-divider bg-inset px-[14px] py-[6px] text-[14px] text-muted @max-[400px]:flex-wrap [&>p]:@max-[400px]:basis-[calc(100%_-_28px)]">
        <Icon aria-hidden="true" className="size-4 shrink-0" />
        {children}
      </div>
    </div>
  );
}
function Meter({ value, ticks }: { value: number; ticks: number[] }) {
  return (
    <span
      aria-hidden="true"
      className="relative ml-auto h-2 w-[150px] shrink-0 rounded-full bg-divider"
    >
      <span
        className="absolute inset-y-0 left-0 rounded-full bg-text"
        style={{ width: `${String(Math.min(1, Math.max(0, value)) * 100)}%` }}
      />
      {ticks.map((tick, i) => (
        <span
          key={i}
          className="absolute top-[-4px] h-4 w-px bg-muted"
          style={{ left: `${String(tick * 100)}%` }}
        />
      ))}
    </span>
  );
}
export function BudgetNotice({
  mark,
  progress,
}: {
  mark: BudgetMark;
  progress: Conversation["progress"];
}) {
  if (!progress.budget) return null;
  const words = budgetWords(mark, progress.budget, progress.spendUnknown);
  return (
    <Notice icon={Gauge}>
      <p className="min-w-0 flex-1 wrap-anywhere">
        <strong className="font-semibold text-text">
          <Numbers text={words.lead} />
        </strong>{" "}
        · <Numbers text={words.detail} />
      </p>
      {!progress.spendUnknown && mark.budget_usd > 0 && (
        <Meter
          value={mark.spent_usd / mark.budget_usd}
          ticks={[...progress.budget.warn_at, progress.budget.block_at]}
        />
      )}
    </Notice>
  );
}
export function Notices({ conversation: { header, progress } }: { conversation: Conversation }) {
  const { wait, openRound, limits } = progress;
  const time =
    openRound && limits > 0 && openRound.elapsedMs >= limits * 800
      ? timeWords(openRound.elapsedMs, limits)
      : null;
  return (
    <>
      {wait && (header.state === "working" || header.state === "revising") && (
        <div role="listitem">
          <Notice icon={RefreshCw}>
            <p className="min-w-0 flex-1 wrap-anywhere">
              <strong className="font-semibold text-text">Waiting for model</strong> (retry{" "}
              <span className="font-mono">
                {wait.attempt + 1}/{wait.max_attempts}
              </span>
              )
            </p>
            <span className="text-[13px] text-faint">
              {causeWords[wait.cause]}
              {wait.status !== null && (
                <>
                  {" "}
                  · <span className="font-mono">{wait.status}</span>
                </>
              )}
            </span>
            <span aria-hidden="true" className="flex shrink-0 gap-1">
              {Array.from({ length: wait.max_attempts }, (_, i) => (
                <span
                  key={i}
                  className={`h-[6px] w-4 rounded-[2px] ${i <= wait.attempt ? "bg-text" : "bg-divider"}`}
                />
              ))}
            </span>
          </Notice>
        </div>
      )}
      {time && openRound && (
        <div role="listitem">
          <Notice icon={Clock}>
            <p className="min-w-0 flex-1 wrap-anywhere">
              <strong className="font-mono font-semibold text-text">{time.used}</strong> of{" "}
              <strong className="font-mono font-semibold text-text">{time.bound}</strong> · time
              limit for this round
            </p>
            <Meter value={openRound.elapsedMs / (limits * 1000)} ticks={[1]} />
          </Notice>
        </div>
      )}
    </>
  );
}
