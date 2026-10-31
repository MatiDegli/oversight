import { agentSlotStyle } from "../../theme";
import { Maximize2, Minimize2, X } from "lucide-react";
import { useLayoutEffect, useRef, type ReactNode } from "react";
import { FileView } from "../../editor";
import { ConversationPanel, selectConversation } from "../../agents";
import { ReviewPanel, DecisionInbox } from "../../review";
import type { FrameProps } from "../Frame";
import { LivePanel } from "./LivePanel";
import { focusRing } from "./shared";

export function FocusPanel(
  props: FrameProps & {
    startAgentAction: ReactNode;
    practiceOnly?: boolean;
    maximized: boolean;
    onMaximize: () => void;
  },
) {
  const { view, state, onClosePanel, onTabAction } = props;
  const tabs = view.tabs ?? [];
  const agentId = tabs.find((tab) => tab.id === view.activeTab)?.conversation;
  const tablist = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    tablist.current
      ?.querySelector('[aria-selected="true"]')
      ?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [view.activeTab]);
  if (props.practiceOnly)
    return (
      <section className="focus-panel" aria-label="Focus panel">
        {props.practice?.content}
      </section>
    );
  const Icon = props.maximized ? Minimize2 : Maximize2;
  return (
    <section className="focus-panel" aria-label="Focus panel">
      <div className="focus-tabs">
        <div
          ref={tablist}
          role="tablist"
          aria-label="Open files and changes"
          className="flex min-w-0 flex-1 overflow-x-auto"
        >
          {tabs.map((tab) => {
            const changeId = tab.review ?? tab.diff?.changeId;
            const change = changeId ? state.changes[changeId] : undefined;
            const slot = tab.conversation
              ? state.agents[tab.conversation]?.slot
              : change
                ? state.agents[change.agent_id]?.slot
                : undefined;
            const title =
              tab.id === "inbox"
                ? "Decisions"
                : tab.id === "practice"
                  ? "Two-change practice"
                  : tab.conversation
                    ? slot
                      ? `Agent ${slot} · conversation`
                      : "Conversation"
                    : tab.review
                      ? `Review · ${tab.review}`
                      : tab.diff
                        ? `Live diff · ${tab.diff.path.split("/").at(-1) ?? tab.diff.path}`
                        : (tab.file?.path.split("/").at(-1) ?? "File");
            return (
              <div
                key={tab.id}
                className={`focus-tab ${view.activeTab === tab.id ? "is-active" : ""}`}
                data-slot={slot ?? "file"}
                style={agentSlotStyle(slot)}
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={view.activeTab === tab.id}
                  aria-controls="focus-content"
                  onClick={() => onTabAction?.({ type: "activate", id: tab.id })}
                  className={`min-w-0 truncate px-3 py-2 ${focusRing}`}
                >
                  {slot && <span className="mr-2 font-bold">{slot}</span>}
                  {title}
                  {tab.review && (
                    <span className="ml-2 text-faint">
                      {change?.status.replaceAll("_", " ") ?? "Unavailable"}
                    </span>
                  )}
                </button>
                <button
                  type="button"
                  aria-label={`Close tab ${title}`}
                  onClick={() => onTabAction?.({ type: "close", id: tab.id })}
                  className={`mr-1 p-1 text-muted ${focusRing}`}
                >
                  <X aria-hidden="true" className="size-3.5" />
                </button>
              </div>
            );
          })}
        </div>
        <button
          type="button"
          aria-label={props.maximized ? "Show graph" : "Maximize focus panel"}
          title={props.maximized ? "Show graph" : "Maximize focus panel"}
          onClick={props.onMaximize}
          className={`px-3 text-muted ${focusRing}`}
        >
          <Icon aria-hidden="true" className="size-4" />
        </button>
      </div>
      <div id="focus-content" role="tabpanel" className="focus-body">
        {view.activeTab === "inbox" ? (
          <DecisionInbox state={state} onReview={props.onReview} />
        ) : view.activeTab === "practice" && props.practice?.active ? (
          props.practice.content
        ) : agentId ? (
          <ConversationFocus {...props} agentId={agentId} />
        ) : view.review !== null ? (
          <ReviewPanel
            key={view.review}
            state={state}
            changeId={view.review}
            attempt={props.attempts?.[view.review]}
            files={props.files?.readOnly ? undefined : props.files}
            onDecide={props.onDecide}
            replayOf={props.replayOf}
            onClose={onClosePanel}
          />
        ) : view.diff ? (
          <LivePanel state={state} view={view} onClose={onClosePanel} />
        ) : view.file && props.files ? (
          <FileView
            target={view.file}
            startAgentAction={props.startAgentAction}
            files={props.files}
            commit={state.graph?.commit ?? ""}
            onClose={onClosePanel}
          />
        ) : null}
      </div>
    </section>
  );
}

function ConversationFocus({
  state,
  agentId,
  onMessageAgent,
  onPauseAgent,
  onResumeAgent,
  onStopAgent,
  onOpenChange,
  canOpenChange,
}: FrameProps & { agentId: string }) {
  const conversation = selectConversation(state, agentId, state.messages[agentId] ?? []);
  if (!conversation)
    return <p className="px-3 py-2 text-muted">This agent is not in the current run.</p>;
  return (
    <ConversationPanel
      conversation={conversation}
      onSend={onMessageAgent ? (text) => onMessageAgent(agentId, text) : undefined}
      onPause={onPauseAgent ? () => onPauseAgent(agentId) : undefined}
      onResume={onResumeAgent ? () => onResumeAgent(agentId) : undefined}
      onStop={onStopAgent}
      onOpenDiff={canOpenChange(conversation.header.changeId) ? onOpenChange : undefined}
    />
  );
}
