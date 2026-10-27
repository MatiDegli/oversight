import type { AgentStatus, StartAgent } from "@contracts";
import { Network, X } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { AgentCard, slotUsage, TaskBox } from "../agents";
import type { TaskBoxProps } from "../agents";
import { FileView, LiveDiff } from "../editor";
import type { FileAccess } from "../editor";
import { GraphView } from "../graph";
import type { Framing, GraphApi } from "../graph";
import { ImpactConeCard, ReviewPanel } from "../review";
import type { DecisionAction, DecisionAttempt } from "../review";
import { selectAgentCard, selectAgents, selectLiveDiff } from "../state";
import type { State } from "../state";
import { GraphHeading, Legend, ZoomControls } from "./GraphOverlays";
import { TopBar } from "./TopBar";
import { fieldInput } from "./fieldScope";
import { graphScope } from "./graphScope";
import { changeNodeIds, panelOpen } from "./view";
import type { View } from "./view";
import { useFunctionTask } from "./useFunctionTask";

type AgentState = AgentStatus["state"];

/** The card's words for a state; the agents rail uses the one-word caption. */
const stateWords: Record<AgentState, [label: string, caption: string]> = {
  starting: ["Starting", "Starting"],
  working: ["Working", "Working"],
  ready_for_review: ["Ready for review", "Ready"],
  revising: ["Revising", "Revising"],
  halted: ["Halted", "Halted"],
  stopped: ["Stopped", "Stopped"],
  done: ["Done", "Done"],
  paused: ["Paused", "Paused"],
};

const agentLook = {
  A: {
    tint: "bg-agent-a-tint text-agent-a-text",
    border: "border-agent-a",
    fill: "bg-agent-a",
  },
  B: {
    tint: "bg-agent-b-tint text-agent-b-text",
    border: "border-agent-b",
    fill: "bg-agent-b",
  },
} as const;

const focusRing =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";

export interface FrameProps {
  state: State;
  view: View;
  files?: FileAccess;
  onNodeTap: (nodeId: string) => void;
  /** Present only for a change the card can open; a card never shows a button that does nothing. */
  canOpenChange: (changeId: string) => boolean;
  onOpenChange: (changeId: string) => void;
  onClosePanel: () => void;
  /** Absent in a recorded run, which has no backend: the agents panel then offers no new agent. */
  onStartAgent?: (body: StartAgent) => Promise<unknown>;
  onStopAgent?: (agentId: string) => Promise<unknown>;
  /** True for a proposed change: its card offers Review change instead of the live diff. */
  canReview?: (changeId: string) => boolean;
  onReview?: (changeId: string) => void;
  /** The queue counter's click: review of the next proposed change. */
  onOpenQueue?: () => void;
  /** The person's last decision per change. */
  attempts?: Readonly<Record<string, DecisionAttempt>>;
  /** Absent in a recorded run, which sends no commands: review then shows the recorded decision. */
  onDecide?: (changeId: string, action: DecisionAction, comment: string) => void;
  /** What the replay plays, from its source: review and the new-agent box word themselves with it. */
  replayOf?: string;
  /** Overlay lines on the graph, such as the replay label and a transport failure. */
  notice?: ReactNode;
}

function LeftRail({ watching, onWatch }: { watching: boolean; onWatch: () => void }) {
  return (
    <nav
      aria-label="Views"
      className="flex w-(--left-rail-width) shrink-0 flex-col items-center gap-2 border-r border-divider bg-shell py-2.5"
    >
      <button
        type="button"
        aria-label="Watch"
        aria-current={watching ? "page" : undefined}
        title="Watch: the graph and the agents"
        onClick={onWatch}
        className={`grid size-10 place-items-center rounded-control ${focusRing} ${
          watching ? "bg-raised text-text" : "text-muted hover:bg-raised hover:text-text"
        }`}
      >
        <Network aria-hidden="true" className="size-5" />
      </button>
    </nav>
  );
}

function AgentsPanel({
  state,
  canOpenChange,
  onOpenChange,
  onStartAgent,
  onStopAgent,
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
      className="flex w-(--agents-panel-width) shrink-0 flex-col border-l border-divider bg-panel"
    >
      <div className="flex items-baseline gap-2.5 px-3.5 pt-3.5 pb-2.5">
        <h2 className="text-(length:--title-size) font-bold">Agents</h2>
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

function AgentsRail({ state }: { state: State }) {
  return (
    <aside
      aria-label="Agents"
      className="flex w-(--agents-rail-width) shrink-0 flex-col items-center gap-3.5 border-l border-divider bg-shell py-3.5"
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

function LeftPanel({ state, view, onClose }: { state: State; view: View; onClose: () => void }) {
  const target = view.diff;
  const data = target ? selectLiveDiff(state, target.changeId, target.path) : undefined;
  const agent = data ? state.agents[data.agentId] : undefined;
  return (
    <aside
      aria-label="Live diff panel"
      className="flex w-[min(var(--review-panel-width),46vw)] shrink-0 flex-col border-r border-divider bg-page"
    >
      <div className="flex items-center gap-2.5 border-b border-page-border px-4 py-2.5">
        {agent && (
          <>
            <span
              aria-hidden="true"
              className={`size-3 shrink-0 rounded-label ${agentLook[agent.slot].fill}`}
            />
            <span className="shrink-0 font-bold">Agent {agent.slot}</span>
            <span
              className={`shrink-0 rounded-full px-2 py-1 text-sm ${agentLook[agent.slot].tint}`}
            >
              {stateWords[agent.state][0]}
            </span>
            <p title={agent.task} className="min-w-0 truncate text-sm text-muted">
              {agent.task}
            </p>
          </>
        )}
        <button
          type="button"
          aria-label="Close live diff"
          title="Close (Escape)"
          onClick={onClose}
          className={`ml-auto grid size-(--button-small-height) shrink-0 place-items-center rounded-control text-muted hover:bg-raised hover:text-text ${focusRing}`}
        >
          <X aria-hidden="true" className="size-4" />
        </button>
      </div>
      <div className="min-h-0 flex-1">
        <LiveDiff data={data} />
      </div>
    </aside>
  );
}

/**
 * One screen: top bar, view rail, the graph in the center and a side panel that follows what the
 * person opened. The graph keeps its place in the tree, so opening the left panel resizes it and
 * moves only the camera, never a node.
 */
export function Frame(props: FrameProps) {
  const { state, view, onNodeTap, canOpenChange, onOpenChange, onClosePanel, notice } = props;
  const open = panelOpen(view);
  const functionTask = useFunctionTask(state, view, props.onStartAgent !== undefined);
  // The cone and the change stay lit until the decision; events never open review themselves.
  const { cone, liveCone, litIds } = useMemo(() => graphScope(state, view), [state, view]);
  // The dot field's light follows the same state and the same view, so it never needs an event.
  const field = useMemo(() => fieldInput(state, view, liveCone), [state, view, liveCone]);
  // When the panel opens, the camera centers the nodes of the change it shows, review or live diff.
  const shown = view.review ?? view.diff?.changeId ?? null;
  const shownChange = shown === null ? undefined : state.changes[shown];
  const frameIds = useMemo(() => (shownChange ? changeNodeIds(shownChange) : null), [shownChange]);
  const frameKey = view.review === null ? shown : `review:${view.review}`;
  const [framing, setFraming] = useState<{ key: string | null; outOfView: ReadonlySet<string> }>();
  const coneCardRef = useRef<HTMLDivElement>(null);
  const startAreaRef = useRef<HTMLDivElement>(null);
  const graphApi = useRef<GraphApi | null>(null);
  const onFramed = useCallback(
    (result: Framing | null) => {
      setFraming({ key: frameKey, outOfView: result?.outOfView ?? new Set() });
    },
    [frameKey],
  );
  return (
    <div className="flex h-full flex-col bg-background text-text">
      <TopBar state={state} onOpenQueue={props.onOpenQueue} />
      <div className="flex min-h-0 flex-1">
        <LeftRail watching={!open} onWatch={onClosePanel} />
        {view.review !== null ? (
          <ReviewPanel
            key={view.review}
            state={state}
            changeId={view.review}
            attempt={props.attempts?.[view.review]}
            files={props.files}
            onDecide={props.onDecide}
            replayOf={props.replayOf}
            onClose={onClosePanel}
          />
        ) : view.diff ? (
          <LeftPanel state={state} view={view} onClose={onClosePanel} />
        ) : view.file && props.files ? (
          <FileView
            target={view.file}
            startAgentAction={functionTask.action}
            files={props.files}
            commit={state.graph?.commit ?? ""}
            onClose={onClosePanel}
          />
        ) : null}
        <main className="relative min-w-0 flex-1 bg-ground">
          <GraphView
            selectedId={view.selected}
            onNodeTap={onNodeTap}
            panelOpen={open}
            litIds={litIds}
            frameIds={frameIds}
            cone={cone}
            liveCone={liveCone}
            frameKey={frameKey}
            onFramed={onFramed}
            coneCardRef={coneCardRef}
            apiRef={graphApi}
            startAreaRef={startAreaRef}
            field={field}
          />
          {/* Overlays sit 24px in from the graph's edges: the heading (in watch) or the cone card (in
              review) top left, the legend bottom left, the zoom controls bottom right. */}
          <div className="pointer-events-none absolute inset-(--spacing-overlay) flex flex-col gap-(--spacing-md)">
            <div className="flex min-h-0 flex-col items-start gap-(--spacing-md)">
              {cone ? (
                <div
                  ref={coneCardRef}
                  className="pointer-events-auto min-h-0 w-full max-w-(--cone-card-width) overflow-auto"
                >
                  <ImpactConeCard
                    cone={cone}
                    outOfView={framing?.key === frameKey ? framing.outOfView : undefined}
                    onReveal={(id) => graphApi.current?.reveal(id)}
                    nodes={[
                      ...(state.graph?.nodes ?? []),
                      ...(shownChange?.graph.nodes_added ?? []),
                    ]}
                  />
                </div>
              ) : (
                // The narrower graph beside the editor panel is framed on its own nodes, so a
                // heading there would sit on top of them: it is a watch-mode overlay.
                !open && (
                  <div className="pointer-events-auto">
                    <GraphHeading state={state} />
                  </div>
                )
              )}
              {notice && <div className="pointer-events-auto text-muted">{notice}</div>}
            </div>
            {/* The start camera uses the space between overlays, measured after fonts load. */}
            <div ref={startAreaRef} className="min-h-0 flex-1" />
            <div className="flex items-end justify-between gap-(--spacing-lg)">
              <div className="pointer-events-auto min-w-0">
                <Legend state={state} />
              </div>
              <div className="pointer-events-auto shrink-0">
                <ZoomControls api={graphApi} />
              </div>
            </div>
          </div>
        </main>
        {open && !functionTask.expanded ? (
          <AgentsRail state={state} />
        ) : (
          <AgentsPanel
            inputRef={functionTask.inputRef}
            state={state}
            canOpenChange={canOpenChange}
            onOpenChange={onOpenChange}
            onStartAgent={props.onStartAgent}
            onStopAgent={props.onStopAgent}
            canReview={props.canReview}
            onReview={props.onReview}
            replayOf={props.replayOf}
          />
        )}
      </div>
    </div>
  );
}
