import { LockKeyhole, X } from "lucide-react";
import type { ReactNode } from "react";
import type { FileViewProps } from "../../editor";
import { languageForPath } from "../../editor";
import { agentSlotStyle } from "../../theme";
import { selectDecisionInbox, selectActiveGrants } from "../../state";
import type { State } from "../../state";
import { ConversationPanelHeader } from "../../agents";
import type { ConversationHeader } from "../../agents";
import { ReviewHeader, selectInboxSummary, selectChangeCard } from "../../review";
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
        inert
        state={state}
        changeId={changeId}
        status={selectChangeCard(state, changeId)?.kind === "merged" ? "Merged" : undefined}
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
      <ConversationPanelHeader header={header} />
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
      className="practice-panel h-full w-[min(100%,calc(70ch+3rem))] overflow-auto p-6 text-text"
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
      <header
        inert
        className="flex flex-wrap items-center gap-2.5 border-b border-page-border px-4 py-2.5"
      >
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
