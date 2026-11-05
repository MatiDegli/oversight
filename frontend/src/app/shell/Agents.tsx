import {
  compareAgentPresentation,
  presentationFor,
  selectPresentation,
} from "../../teams/presentation";
import { presentationLook } from "../../teams/badge";
import { AgentCard, selectAgentCard, TaskBox } from "../../agents";
import type { TaskBoxProps } from "../../agents";
import { selectAgentActivity, selectAgents } from "../../state";
import type { State } from "../../state";
import type { FrameProps } from "../Frame";
import { noAgentsYet, stateWords } from "./shared";
export function AgentsPanel({
  state,
  canOpenChange,
  onOpenChange,
  onStartAgent,
  onQueueAgent,
  onCancelQueued,
  onOpenSettings,
  onStopAgent,
  onOpenConversation,
  onPauseAgent,
  onResumeAgent,
  canReview,
  onReview,
  replayOf,
  inputRef,
  mergeLabels,
}: Pick<
  FrameProps,
  | "state"
  | "canOpenChange"
  | "onOpenChange"
  | "onStopAgent"
  | "onOpenConversation"
  | "onPauseAgent"
  | "onResumeAgent"
  | "onStartAgent"
  | "onQueueAgent"
  | "onCancelQueued"
  | "onOpenSettings"
  | "canReview"
  | "onReview"
  | "replayOf"
> &
  Pick<TaskBoxProps, "inputRef"> & { mergeLabels?: ReadonlyMap<string, "Merging" | "Merged"> }) {
  const cards = selectAgents(state)
    .flatMap((agent) => {
      const data = selectAgentCard(state, agent.agent_id);
      return data ? [data] : [];
    })
    .sort((a, b) => compareAgentPresentation(a.presentation, b.presentation));
  return (
    <aside
      aria-label="Agents"
      className="agents-expanded flex w-(--agents-panel-width) shrink-0 flex-col border-l border-divider bg-panel"
    >
      <div className="flex items-baseline gap-2.5 px-3.5 pt-3.5 pb-2.5">
        <h2 className="group-label font-bold">Agents</h2>
        {state.run && <span className="text-sm text-muted">{selectAgentActivity(state).text}</span>}
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto px-3.5 pb-3.5">
        {cards.map((data) => {
          const review = canReview?.(data.agent.change_id) === true;
          return (
            <AgentCard
              key={data.agent.agent_id}
              data={data}
              mergeLabel={mergeLabels?.get(data.agent.change_id)}
              onStop={onStopAgent}
              onConversation={onOpenConversation}
              onPause={onPauseAgent}
              onResume={onResumeAgent}
              onOpenDiff={!review && canOpenChange(data.agent.change_id) ? onOpenChange : undefined}
              onReview={review ? onReview : undefined}
            />
          );
        })}
        {cards.length === 0 && <p className="text-sm text-muted">{noAgentsYet}</p>}
      </div>
      {/* Pinned to the bottom, as in the sketch: a card appearing above never moves it, or the
          person's focus in it. */}
      <div className="shrink-0 border-t border-divider p-3.5">
        <TaskBox
          onOpenSettings={onOpenSettings}
          state={state}
          onStart={onStartAgent}
          onQueue={onQueueAgent}
          onCancelQueued={onCancelQueued}
          replayOf={replayOf}
          inputRef={inputRef}
        />
      </div>
    </aside>
  );
}

export function AgentsRail({ state }: { state: State }) {
  const identities = selectPresentation(state).byAgent;
  const agents = selectAgents(state).sort((a, b) =>
    compareAgentPresentation(identities[a.agent_id], identities[b.agent_id]),
  );
  return (
    <aside
      aria-label="Agents"
      className="agents-rail flex w-(--agents-rail-width) shrink-0 flex-col items-center gap-3.5 border-l border-divider bg-shell py-3.5"
    >
      {agents.map((agent) => {
        const identity = presentationFor(state, agent.agent_id);
        const look = presentationLook(identity);
        const [label, caption] = stateWords[agent.state];
        const lamp =
          agent.state === "ready_for_review"
            ? look.fill
            : `bg-shell ${agent.state === "working" ? "animate-pulse [animation-duration:var(--status-lamp-duration)] motion-reduce:animate-none" : ""}`;
        return (
          <div
            key={agent.agent_id}
            role="group"
            aria-label={`Agent ${identity?.label ?? "unknown"}: ${label}`}
            className="agents-rail-tile flex flex-col items-center gap-1.5"
          >
            <span
              data-agent-origin={agent.agent_id}
              className={`relative grid size-(--letter-avatar-size) place-items-center rounded-card border-(length:--agent-border-width) text-(length:--letter-avatar-font-size) font-bold ${look.border} ${look.tint}`}
            >
              {identity?.planContext?.shortLabel ?? identity?.label ?? "?"}
              <span
                aria-hidden="true"
                className={`agents-rail-lamp absolute top-0 right-0 size-(--status-lamp-size) rounded-full border-(length:--agent-border-width) ${look.border} ${lamp}`}
              />
            </span>
            {identity?.planContext && (
              <span className="text-(length:--caption-size) text-muted">
                {identity.planContext.caption}
              </span>
            )}
            <span aria-hidden="true" className="text-(length:--caption-size) text-muted">
              {caption}
            </span>
          </div>
        );
      })}
    </aside>
  );
}
