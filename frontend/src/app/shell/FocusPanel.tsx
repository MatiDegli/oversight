import { agentSlotStyle } from "../../theme";
import { Maximize2, Minimize2, Settings, Timer, X } from "lucide-react";
import { lazy, useLayoutEffect, useRef, type ReactNode } from "react";
import { FileView } from "../../editor";
import { selectConversation, loadConversationPanel } from "../../agents";
import { InboxFrame, ConversationFrame, FileFrame, SettingsFrame } from "./PanelFallbacks";
import { LazyPanel } from "./LazyPanel";

import { loadDecisionInbox } from "../../review";
import type { FrameProps } from "../Frame";
import { UsageMount } from "../usage/UsageMount";
import { LivePanel } from "./LivePanel";
import { ChatMergeChip, ChatMergeReceipt, isRulesMerge, resolvedReviewAction } from "./ChatMerge";
import { focusRing } from "./shared";
import { ChatReview } from "./ChatReview";
import { ChatChangeCard } from "./ChatChangeCard";
import { selectChatChange } from "../chatReviewSelectors";

const SettingsPanel = lazy(() =>
  import("./SettingsPanel").then((m) => ({ default: m.SettingsPanel })),
);
const DecisionInbox = lazy(() => loadDecisionInbox().then((m) => ({ default: m.DecisionInbox })));
const ConversationPanel = lazy(() =>
  loadConversationPanel().then((m) => ({
    default: m.ConversationPanel,
  })),
);

export function FocusPanel(
  props: FrameProps & {
    startAgentAction: ReactNode;
    chat?: boolean;
    onGraph?: (changeId: string) => void;
    practiceOnly?: boolean;
    maximized: boolean;
    onMaximize: () => void;
  },
) {
  const { view, state, onClosePanel, onTabAction } = props;
  const tabs = view.tabs ?? [];
  const agentId = tabs.find((tab) => tab.id === view.activeTab)?.conversation;
  const owner = view.review ? state.changes[view.review]?.proposal?.agent_id : agentId;
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
      <div className="focus-tabs" hidden={props.chat && Boolean(owner)}>
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
                : tab.id === "usage"
                  ? "Usage"
                  : tab.id === "settings"
                    ? "Settings"
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
                  {tab.id === "settings" && (
                    <Settings aria-hidden="true" className="mr-2 inline size-3.5" />
                  )}
                  {tab.id === "usage" && (
                    <Timer aria-hidden="true" className="mr-2 inline size-3.5" />
                  )}
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
        {view.activeTab === "usage" ? (
          <UsageMount
            key={state.run?.run_id ?? "empty"}
            state={state}
            recorded={Boolean(props.replayOf)}
          />
        ) : view.activeTab === "settings" ? (
          <LazyPanel key="settings" frame={<SettingsFrame />}>
            <SettingsPanel
              state={state}
              attempt={props.mergeModeAttempt}
              onSetMergeMode={props.onSetMergeMode}
            />
          </LazyPanel>
        ) : view.activeTab === "inbox" ? (
          <LazyPanel key="inbox" frame={<InboxFrame state={state} />}>
            <DecisionInbox state={state} onReview={props.onReview} />
          </LazyPanel>
        ) : view.activeTab === "practice" && props.practice?.active ? (
          props.practice.content
        ) : owner || view.review !== null ? (
          <ChatReview
            {...props}
            agentId={owner}
            conversation={owner ? <ConversationFocus {...props} agentId={owner} /> : null}
          />
        ) : view.diff ? (
          <LivePanel state={state} view={view} onClose={onClosePanel} />
        ) : view.file && props.files ? (
          <FileView
            loadingFallback={
              <FileFrame
                target={view.file}
                files={props.files}
                commit={state.graph?.commit ?? ""}
                onClose={onClosePanel}
                startAgentAction={props.startAgentAction}
              />
            }
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

/** The same adapter and mounted conversation serve both shell layouts. */
function ConversationFocus({
  state,
  agentId,
  onMessageAgent,
  onPauseAgent,
  onResumeAgent,
  onStopAgent,
  onOpenChange,
  canOpenChange,
  chat,
  onOpenSettings,
  onTabAction,
  view,
  canReview,
  onReview,
  onGraph,
}: FrameProps & { agentId: string; chat?: boolean; onGraph?: (id: string) => void }) {
  const conversation = selectConversation(state, agentId, state.messages[agentId] ?? []);
  if (!conversation)
    return <p className="px-3 py-2 text-muted">This agent is not in the current run.</p>;
  return (
    <LazyPanel key={agentId} frame={<ConversationFrame header={conversation.header} />}>
      <ConversationPanel
        conversation={conversation}
        renderProposed={
          chat
            ? (entry) => {
                const changeId = conversation.header.changeId;
                if (!selectChatChange(state, changeId, entry.seq)) return null;
                return (
                  <ChatChangeCard
                    state={state}
                    changeId={changeId}
                    seq={entry.seq}
                    reviewing={view.review === changeId}
                    onReview={
                      canReview?.(changeId) && onReview
                        ? () => {
                            onReview(changeId);
                          }
                        : undefined
                    }
                    onGraph={
                      onGraph
                        ? () => {
                            onGraph(changeId);
                          }
                        : undefined
                    }
                  />
                );
              }
            : undefined
        }
        renderResolution={
          chat
            ? (entry) => {
                if (!isRulesMerge(entry.resolution)) return null;
                const action = resolvedReviewAction(state, view, entry);
                return (
                  <ChatMergeReceipt
                    resolution={entry.resolution}
                    ts={entry.ts}
                    onReview={
                      action && onTabAction
                        ? () => {
                            onTabAction(action);
                          }
                        : undefined
                    }
                  />
                );
              }
            : undefined
        }
        actionAccessory={
          chat ? <ChatMergeChip state={state} onOpenSettings={onOpenSettings} /> : undefined
        }
        onSend={onMessageAgent ? (text) => onMessageAgent(agentId, text) : undefined}
        onPause={onPauseAgent ? () => onPauseAgent(agentId) : undefined}
        onResume={onResumeAgent ? () => onResumeAgent(agentId) : undefined}
        onStop={onStopAgent}
        onOpenDiff={canOpenChange(conversation.header.changeId) ? onOpenChange : undefined}
      />
    </LazyPanel>
  );
}
