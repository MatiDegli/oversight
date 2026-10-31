import { LockKeyhole, X } from "lucide-react";
import type { ReactNode } from "react";
import type { FileViewProps } from "../../editor";
import { languageForPath } from "../../editor";
import { agentLook, agentSlotStyle } from "../../theme";
import { costText, selectDecisionInbox, selectActiveGrants } from "../../state";
import type { State } from "../../state";
import { agentStatusLabels as labels } from "../../agents";
import type { ConversationHeader } from "../../agents";
import { ReviewHeader, selectInboxSummary } from "../../review";
import type { Attempt } from "../challenge/attempt";
import { usePhonePractice } from "../challenge/usePhonePractice";

export function ReviewFrame({
  state,
  changeId,
  onClose,
  children,
}: {
  state: State;
  changeId: string;
  onClose: () => void;
  children?: ReactNode;
}) {
  const change = state.changes[changeId];
  return (
    <aside
      aria-label="Review panel"
      className="review-panel flex w-[min(var(--review-panel-width),46vw)] min-h-0 min-w-0 shrink-0 flex-col bg-page"
      style={agentSlotStyle(change ? state.agents[change.agent_id]?.slot : undefined)}
    >
      <ReviewHeader
        state={state}
        changeId={changeId}
        status={
          change?.resolution?.decided_by === "rules" && change.resolution.merge === "merged"
            ? "Merged"
            : undefined
        }
        onClose={onClose}
      />
      {children}
    </aside>
  );
}
export function InboxFrame({ state, children }: { state: State; children?: ReactNode }) {
  const entries = selectDecisionInbox(state),
    grants = selectActiveGrants(state);
  const { agents, steps, minutes } = selectInboxSummary(state);
  return (
    <section aria-label="Decisions inbox" className="decisions-inbox">
      <nav aria-label="Inbox views" className="inbox-views">
        <button type="button" aria-pressed={true} disabled>
          Decisions <span>{entries.length}</span>
        </button>
        <button type="button" aria-pressed={false} disabled>
          Standing grants <span>{grants.length}</span>
        </button>
      </nav>
      <header className="inbox-heading">
        <span className="inbox-count">{entries.length}</span>
        <div>
          <h2>{entries.length ? "Decisions waiting for you" : "No decisions waiting"}</h2>
          <p className="decision-muted">
            {agents} {agents === 1 ? "agent" : "agents"} took {steps}{" "}
            {steps === 1 ? "step" : "steps"}
            {minutes === null ? "" : ` in ${String(minutes)} min`}.{" "}
            {entries.length
              ? `These ${String(entries.length)} need a person.`
              : "Nothing needs your decision now."}
          </p>
        </div>
      </header>
      {children}
    </section>
  );
}
const chip =
  "max-w-full rounded-control border border-border px-[10px] py-1 text-[14px] wrap-anywhere";

function ConversationHeaderFrame({ header }: { header: ConversationHeader }) {
  return (
    <header className="shrink-0 border-b border-divider bg-panel px-[18px] py-4">
      <div className="flex items-start gap-[10px]">
        <span
          className={`flex size-(--letter-badge-size) shrink-0 items-center justify-center rounded-label font-bold ${agentLook[header.slot].swatch}`}
        >
          {header.slot}
        </span>
        <h2 className="min-w-0 text-[20px] leading-[1.3] font-bold wrap-anywhere">{header.task}</h2>
        <span className="shrink-0 rounded-full bg-raised px-[10px] py-[3px] text-[13px] font-semibold text-muted">
          {labels[header.state]}
        </span>
      </div>
      <div className="mt-[14px] flex flex-wrap gap-2">
        <span className={chip}>
          {header.modelLabel}
          {header.reasoning !== null ? ` · reasoning ${header.reasoning}` : ""}
        </span>
        <span className={chip}>
          Step {header.step} of {header.maxSteps}
        </span>
        {header.cost !== undefined && (
          <span className={`${chip} ${header.cost === null ? "" : "font-mono"}`}>
            {header.cost === null ? "Cost unknown" : costText(header.cost)}
          </span>
        )}
        {header.round > 1 && <span className={chip}>Round {header.round}</span>}
        {header.zonePaths[0] && (
          <span className={chip}>
            Zone: {header.zonePaths[0]}
            {header.zonePaths.length > 1 ? ` and ${String(header.zonePaths.length - 1)} more` : ""}
          </span>
        )}
      </div>
    </header>
  );
}

export function ConversationFrame({
  header,
  children,
}: {
  header: ConversationHeader;
  children?: ReactNode;
}) {
  return (
    <section
      aria-label={`Agent ${header.slot} conversation`}
      className="flex h-full min-h-0 min-w-0 flex-col bg-page text-text"
    >
      <ConversationHeaderFrame header={header} />
      {children}
    </section>
  );
}
export function PracticeFrame({
  attempt,
  onExit,
  children,
}: {
  attempt: Attempt;
  onExit: () => void;
  children?: ReactNode;
}) {
  const phone = usePhonePractice(true);
  if (phone)
    return (
      <section className="phone-practice" aria-label="Two-change practice">
        <header className="pp-chrome">
          <div className="pp-topbar">
            <span>Oversight</span>
            <button onClick={onExit}>
              Leave practice
              <X size={14} />
            </button>
          </div>
          <div className="pp-status" aria-label="Status line" role="status">
            Replay of a real recorded run · Oct 27, 2026
          </div>
        </header>
        {children}
      </section>
    );
  return (
    <section
      aria-label="Two-change practice"
      className="practice-panel h-full overflow-auto p-6 text-text"
      style={{ width: "min(100%, calc(70ch + 3rem))" }}
    >
      <div className="mx-auto flex max-w-[70ch] flex-col gap-5">
        <h1 className="text-xl font-semibold">
          {attempt.revealed
            ? "Recorded findings"
            : attempt.started
              ? `Decision ${String(attempt.index + 1)} of 2`
              : "Would you approve these changes?"}
        </h1>
        {children}
      </div>
    </section>
  );
}

export function FileFrame({ target, files, onClose, startAgentAction }: FileViewProps) {
  const control =
    "rounded-control px-3 h-(--button-small-height) text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text disabled:opacity-50";
  return (
    <aside
      aria-label="File view"
      className="flex w-[min(var(--review-panel-width),46vw)] shrink-0 flex-col border-r border-divider bg-page text-text"
    >
      <header className="flex flex-wrap items-center gap-2.5 border-b border-page-border px-4 py-2.5">
        <h2 className="min-w-0 break-all text-base font-bold">{target.path}</h2>
        {languageForPath(target.path) !== "python" && (
          <span className="text-sm text-muted">No impact analysis</span>
        )}

        {files.readOnly || target.agentId !== null ? (
          <span className="inline-flex items-center gap-2 text-sm text-muted">
            <LockKeyhole aria-hidden="true" className="size-4" />
            {files.readOnly
              ? "Read-only · Recorded file"
              : `Read-only · Agent ${target.agentId ?? ""}`}
          </span>
        ) : (
          <>
            <button
              type="button"
              className={`${control} border border-outline text-text enabled:hover:bg-raised`}
              disabled
            >
              Save
            </button>
          </>
        )}
        {startAgentAction}
        <button
          type="button"
          aria-label="Close file"
          title="Close (Escape)"
          onClick={onClose}
          className={`${control} ml-auto text-muted hover:bg-raised hover:text-text`}
        >
          <X aria-hidden="true" className="size-4" />
        </button>
      </header>
      <p
        role="status"
        className="h-(--caption-line-height) shrink-0 px-4 text-(length:--caption-size) leading-(--caption-line-height) text-muted"
      />
      <div className="min-h-0 flex-1" />
    </aside>
  );
}
