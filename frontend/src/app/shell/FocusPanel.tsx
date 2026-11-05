import { presentationFor, presentationStyle } from "../../teams/presentation";
import { agentSlotStyle } from "../../theme";
import { Maximize2, Minimize2, Settings, Timer, X } from "lucide-react";
import { Suspense, lazy, useLayoutEffect, useRef, type ReactNode } from "react";
import { FileView } from "../../editor";
// eslint-disable-next-line no-restricted-imports -- K2a shares the Chat team identity without extending the agents barrel.
import { selectChatLeads } from "../../agents/chatLeadSelectors";
import { selectConversation, loadConversationPanel } from "../../agents";
import { InboxFrame, ConversationFrame, FileFrame, SettingsFrame } from "./PanelFallbacks";
import { LazyPanel } from "./LazyPanel";

import { loadDecisionInbox, loadPlanActions, selectPlanEvidence } from "../../review";
import { slotUsage } from "../../agents";
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

const PlanActions = lazy(loadPlanActions);

type WorkflowNavigation = {
  onOpenWorkflow?: (id: string, launcher: HTMLButtonElement) => void;
  workflowCovered?: boolean;
};

const LeadConversationFocus = lazy(() =>
  loadConversationPanel().then((module) => ({
    default: ({
      state,
      agentId,
      onMessageLead,
      onStopLead,
      chat,
      composerDisclosure,
      onOpenSettings,
      chatState,
      onViewMoment,
      onViewHighlight,
      onViewFile,
      replay,
      onReview,
      onOpenWorkflow,
    }: Pick<
      FrameProps,
      | "state"
      | "onMessageLead"
      | "onStopLead"
      | "onOpenSettings"
      | "chatState"
      | "onViewMoment"
      | "onViewHighlight"
      | "onViewFile"
      | "replay"
      | "onReview"
    > &
      WorkflowNavigation & {
        agentId: string;
        chat?: boolean;
        composerDisclosure?: ReactNode;
      }) => {
      const threadState = chatState ?? state;
      const conversation = module.selectLeadConversation(threadState, agentId);
      const { plans } = selectPlanEvidence(threadState);
      if (!conversation) return null;
      return (
        <module.ConversationPanel
          conversation={conversation}
          renderPlan={(planId) => {
            const plan = plans.get(planId)?.payload;
            if (!plan) return null;
            return (
              <Suspense fallback={<p role="status">Loading plan…</p>}>
                <article className="thread-plan-card" aria-label={`Plan ${plan.plan_id}`}>
                  <h3>{plan.task}</h3>
                  <PlanActions
                    state={threadState}
                    plan={plan}
                    slots={slotUsage(threadState)}
                    onReview={onReview}
                    onOpenWorkflow={onOpenWorkflow}
                  />
                </article>
              </Suspense>
            );
          }}
          renderView={(entry) => (
            <module.LeadViewCard
              state={state}
              event={entry.event}
              header={conversation.header}
              replay={Boolean(replay)}
              actions={{ onMoment: onViewMoment, onHighlight: onViewHighlight, onFile: onViewFile }}
            />
          )}
          actionAccessory={
            chat ? <ChatMergeChip state={state} onOpenSettings={onOpenSettings} /> : undefined
          }
          composerDisclosure={chat ? composerDisclosure : undefined}
          onStopLead={onStopLead}
          onSend={onMessageLead ? (text) => onMessageLead(agentId, text) : undefined}
        />
      );
    },
  })),
);

function LeadConversationFrame({
  lead,
  children = (
    <p role="status" className="px-[18px] py-4 text-muted">
      Loading lead conversation…
    </p>
  ),
}: {
  lead: ReturnType<typeof selectChatLeads>[number];
  children?: ReactNode;
}) {
  return (
    <section
      aria-label={`Lead ${lead.letter} conversation`}
      className="flex h-full min-h-0 w-full min-w-0 flex-col bg-page text-text"
      style={agentSlotStyle(lead.hue)}
    >
      <header className="conversation-header flex items-center gap-3 px-[18px] py-4">
        <span className="chat-lead-ring" aria-hidden="true">
          {lead.letter}
        </span>
        <h2 className="min-w-0 text-[16px] font-semibold wrap-anywhere">{lead.status.task}</h2>
      </header>
      {children}
    </section>
  );
}

export function FocusPanel(
  props: FrameProps &
    WorkflowNavigation & {
      startAgentAction: ReactNode;
      chat?: boolean;
      composerDisclosure?: ReactNode;
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
    <section
      className="focus-panel"
      aria-label="Focus panel"
      hidden={props.workflowCovered}
      inert={props.workflowCovered}
    >
      <div
        className="focus-tabs"
        hidden={
          props.chat &&
          Boolean(owner) &&
          (!view.review || state.changes[view.review]?.resolution?.merge !== "merged")
        }
      >
        <div
          ref={tablist}
          role="tablist"
          aria-label="Open files and changes"
          className="flex min-w-0 flex-1 overflow-x-auto"
        >
          {tabs.map((tab) => {
            const changeId = tab.review ?? tab.diff?.changeId;
            const change = changeId ? state.changes[changeId] : undefined;
            const identity = presentationFor(state, tab.conversation ?? change?.agent_id);
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
                        ? identity
                          ? `${identity.kind === "lead" && !state.agents[identity.agentId] ? "Lead" : "Agent"} ${identity.label} · conversation`
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
                data-agent-id={identity?.agentId}
                style={presentationStyle(identity)}
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={view.activeTab === tab.id}
                  aria-controls="focus-content"
                  onClick={() => onTabAction?.({ type: "activate", id: tab.id })}
                  className={`min-w-0 truncate px-3 py-2 ${focusRing}`}
                >
                  {identity && <span className="mr-2 font-bold">{identity.label}</span>}
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
            <DecisionInbox
              state={state}
              onReview={props.onReview}
              onOpenWorkflow={props.onOpenWorkflow}
            />
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
          <LivePanel state={state} view={view} onClose={onClosePanel} onReview={props.onReview} />
        ) : view.file && props.files ? (
          <FileView
            loadingFallback={
              <FileFrame
                agentLabel={presentationFor(state, view.file.agentId)?.label}
                target={view.file}
                files={props.files}
                commit={state.graph?.commit ?? ""}
                onClose={onClosePanel}
                startAgentAction={props.startAgentAction}
              />
            }
            agentLabel={presentationFor(state, view.file.agentId)?.label}
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
  chatState,
  onViewMoment,
  onViewHighlight,
  onViewFile,
  replay,
  agentId,
  onMessageAgent,
  onMessageLead,
  onStopLead,
  onPauseAgent,
  onResumeAgent,
  onStopAgent,
  onOpenChange,
  canOpenChange,
  chat,
  composerDisclosure,
  onOpenSettings,
  onTabAction,
  view,
  canReview,
  onReview,
  onGraph,
  onOpenWorkflow,
}: FrameProps &
  WorkflowNavigation & {
    agentId: string;
    chat?: boolean;
    composerDisclosure?: ReactNode;
    onGraph?: (id: string) => void;
  }) {
  const threadState = chatState ?? state;
  const conversation = selectConversation(
    threadState,
    agentId,
    threadState.messages[agentId] ?? [],
  );
  if (!conversation) {
    const lead = selectChatLeads(threadState).find((row) => row.id === agentId);
    if (lead)
      return (
        <LazyPanel key={agentId} frame={<LeadConversationFrame lead={lead} />}>
          <LeadConversationFocus
            state={state}
            chatState={chatState}
            onViewMoment={onViewMoment}
            onViewHighlight={onViewHighlight}
            onViewFile={onViewFile}
            replay={replay}
            onReview={onReview}
            onOpenWorkflow={onOpenWorkflow}
            agentId={agentId}
            onMessageLead={onMessageLead}
            onStopLead={onStopLead}
            chat={chat}
            composerDisclosure={composerDisclosure}
            onOpenSettings={onOpenSettings}
          />
        </LazyPanel>
      );
    return <p className="px-3 py-2 text-muted">This agent is not in the current run.</p>;
  }
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
        composerDisclosure={chat ? composerDisclosure : undefined}
        onSend={onMessageAgent ? (text) => onMessageAgent(agentId, text) : undefined}
        onPause={onPauseAgent ? () => onPauseAgent(agentId) : undefined}
        onResume={onResumeAgent ? () => onResumeAgent(agentId) : undefined}
        onStop={onStopAgent}
        onOpenDiff={canOpenChange(conversation.header.changeId) ? onOpenChange : undefined}
      />
    </LazyPanel>
  );
}
