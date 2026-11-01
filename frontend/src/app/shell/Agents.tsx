import { AgentCard, selectAgentCard, slotUsage, TaskBox } from "../../agents";
import type { TaskBoxProps } from "../../agents";
import { selectAgents } from "../../state";
import type { State } from "../../state";
import type { FrameProps } from "../Frame";
import { agentLook, stateWords } from "./shared";
export function AgentsPanel({
  state,
  canOpenChange,
  onOpenChange,
  onStartAgent,
  onStopAgent,
  onOpenConversation,
  onPauseAgent,
  onResumeAgent,
  canReview,
  onReview,
  replayOf,
  inputRef,
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
  | "canReview"
  | "onReview"
  | "replayOf"
> &
  Pick<TaskBoxProps, "inputRef">) {
  const cards = selectAgents(state).flatMap((agent) => {
    const data = selectAgentCard(state, agent.agent_id);
    return data ? [data] : [];
  });
  const slots = slotUsage(state);
  return (
    <aside
      aria-label="Agents"
      className="agents-expanded flex w-(--agents-panel-width) shrink-0 flex-col border-l border-divider bg-panel"
    >
      <div className="flex items-baseline gap-2.5 px-3.5 pt-3.5 pb-2.5">
        <h2 className="group-label font-bold">Agents</h2>
        {slots && (
          <span className="text-sm text-muted">
            {slots.held} of {slots.total} {slots.total === 1 ? "slot" : "slots"}
          </span>
        )}
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto px-3.5 pb-3.5">
        {cards.map((data) => {
          const review = canReview?.(data.agent.change_id) === true;
          return (
            <AgentCard
              key={data.agent.agent_id}
              data={data}
              onStop={onStopAgent}
              onConversation={onOpenConversation}
              onPause={onPauseAgent}
              onResume={onResumeAgent}
              onOpenDiff={!review && canOpenChange(data.agent.change_id) ? onOpenChange : undefined}
              onReview={review ? onReview : undefined}
            />
          );
        })}
        {cards.length === 0 && (
          <p className="text-sm text-muted">
            No agents yet. Each agent gets a card here when it starts.
          </p>
        )}
      </div>
      {/* Pinned to the bottom, as in the sketch: a card appearing above never moves it, or the
          person's focus in it. */}
      <div className="shrink-0 border-t border-divider p-3.5">
        <TaskBox state={state} onStart={onStartAgent} replayOf={replayOf} inputRef={inputRef} />
      </div>
    </aside>
  );
}

export function AgentsRail({ state }: { state: State }) {
  return (
    <aside
      aria-label="Agents"
      className="agents-rail flex w-(--agents-rail-width) shrink-0 flex-col items-center gap-3.5 border-l border-divider bg-shell py-3.5"
    >
      {selectAgents(state).map((agent) => {
        const look = agentLook[agent.slot];
        const [label, caption] = stateWords[agent.state];
        const lamp =
          agent.state === "ready_for_review"
            ? look.fill
            : `bg-shell ${agent.state === "working" ? "animate-pulse [animation-duration:var(--status-lamp-duration)] motion-reduce:animate-none" : ""}`;
        return (
          <div
            key={agent.agent_id}
            role="group"
            aria-label={`Agent ${agent.slot}: ${label}`}
            className="flex flex-col items-center gap-1.5"
          >
            <span
              data-agent-origin={agent.slot}
              className={`relative grid size-(--letter-avatar-size) place-items-center rounded-card border-(length:--agent-border-width) text-(length:--letter-avatar-font-size) font-bold ${look.border} ${look.tint}`}
            >
              {agent.slot}
              <span
                aria-hidden="true"
                className={`absolute -top-1 -right-1 size-(--status-lamp-size) rounded-full border-(length:--agent-border-width) ${look.border} ${lamp}`}
              />
            </span>
            <span aria-hidden="true" className="text-(length:--caption-size) text-muted">
              {caption}
            </span>
          </div>
        );
      })}
    </aside>
  );
}
