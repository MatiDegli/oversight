import { X } from "lucide-react";
import type { ReactNode } from "react";
import type { FileViewProps } from "../../editor";
import { FileViewHeader } from "../../editor";
import { agentSlotStyle } from "../../theme";
import { selectDecisionInbox, selectActiveGrants } from "../../state";
import type { State } from "../../state";
import { ConversationPanelHeader } from "../../agents";
import type { ConversationHeader } from "../../agents";
import {
  ReviewHeader,
  InboxViews,
  InboxHeading,
  selectInboxSummary,
  selectChangeCard,
} from "../../review";
import type { Attempt } from "../challenge/attempt";
import { usePhonePractice } from "../challenge/usePhonePractice";

export { SettingsFrame } from "./SettingsFrame";

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
      <InboxViews inert count={entries.length} grants={grants.length} />
      <InboxHeading count={entries.length} agents={agents} steps={steps} minutes={minutes} />
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
      className="flex h-full min-h-0 w-full min-w-0 flex-col bg-page text-text"
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
        <header inert className="pp-chrome">
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
  return (
    <aside
      aria-label="File view"
      className="flex w-[min(var(--review-panel-width),46vw)] shrink-0 flex-col border-r border-divider bg-page text-text"
    >
      <FileViewHeader
        inert
        target={target}
        files={files}
        onClose={onClose}
        startAgentAction={startAgentAction}
      />
      <p
        role="status"
        className="h-(--caption-line-height) shrink-0 px-4 text-(length:--caption-size) leading-(--caption-line-height) text-muted"
      >
        Loading editor…
      </p>
      <div className="min-h-0 flex-1" />
    </aside>
  );
}
