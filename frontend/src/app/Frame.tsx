import type { AgentStatus } from "@contracts";
import { GitBranch, Network, X } from "lucide-react";
import type { ReactNode } from "react";

import { AgentCard } from "../agents";
import { LiveDiff } from "../editor";
import { GraphView } from "../graph";
import { selectAgentCard, selectAgents, selectLiveDiff } from "../state";
import type { State } from "../state";
import { readyCount } from "./view";
import type { View } from "./view";

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
  onNodeTap: (nodeId: string) => void;
  /** Present only for a change the card can open; a card never shows a button that does nothing. */
  canOpenChange: (changeId: string) => boolean;
  onOpenChange: (changeId: string) => void;
  onClosePanel: () => void;
  /** Overlay lines on the graph, such as the replay label and a transport failure. */
  notice?: ReactNode;
}

function TopBar({ state }: { state: State }) {
  const ready = readyCount(state);
  const commit = state.graph?.commit ?? state.run?.base_commit;
  return (
    <header className="flex h-(--top-bar-height) shrink-0 items-center gap-3.5 border-b border-divider bg-shell px-4.5">
      <span className="text-(length:--brand-size) font-bold">Oversight</span>
      {state.run && (
        <p className="flex min-w-0 items-center gap-2.5 text-sm text-muted">
          <span className="truncate text-text">{state.run.workspace}</span>
          <span className="inline-flex items-center gap-1.5">
            <GitBranch aria-hidden="true" className="size-4" /> main
          </span>
          {commit && (
            <span className="font-mono" title={commit}>
              {commit.slice(0, 7)}
            </span>
          )}
        </p>
      )}
      {/* Always mounted, so the counter's arrival is announced and nothing else moves. */}
      <div role="status" aria-label="Review queue" className="ml-auto flex items-center">
        {ready > 0 && (
          <p className="inline-flex h-(--queue-counter-height) items-center gap-2 rounded-full bg-raised px-3.5 text-sm">
            <span aria-hidden="true" className="size-(--status-lamp-size) rounded-full bg-text" />
            <span className="font-mono">{ready}</span>{" "}
            {ready === 1 ? "change ready for review" : "changes ready for review"}
          </p>
        )}
      </div>
    </header>
  );
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
}: Pick<FrameProps, "state" | "canOpenChange" | "onOpenChange">) {
  const cards = selectAgents(state).flatMap((agent) => {
    const data = selectAgentCard(state, agent.agent_id);
    return data ? [data] : [];
  });
  return (
    <aside
      aria-label="Agents"
      className="flex w-(--agents-panel-width) shrink-0 flex-col gap-2.5 overflow-y-auto border-l border-divider bg-panel p-3.5"
    >
      <h2 className="text-(length:--title-size) font-bold">Agents</h2>
      {cards.map((data) => (
        <AgentCard
          key={data.agent.agent_id}
          data={data}
          onOpenDiff={canOpenChange(data.agent.change_id) ? onOpenChange : undefined}
        />
      ))}
      {cards.length === 0 && (
        <p className="text-sm text-muted">
          No agents yet. Each agent gets a card here when it starts.
        </p>
      )}
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
  return (
    <div className="flex h-full flex-col bg-background text-text">
      <TopBar state={state} />
      <div className="flex min-h-0 flex-1">
        <LeftRail watching={view.diff === null} onWatch={onClosePanel} />
        {view.diff && <LeftPanel state={state} view={view} onClose={onClosePanel} />}
        <main className="relative min-w-0 flex-1 bg-ground">
          <GraphView
            selectedId={view.selected}
            onNodeTap={onNodeTap}
            panelOpen={view.diff !== null}
          />
          {notice && <div className="absolute top-6 left-6 text-muted">{notice}</div>}
        </main>
        {view.diff ? (
          <AgentsRail state={state} />
        ) : (
          <AgentsPanel state={state} canOpenChange={canOpenChange} onOpenChange={onOpenChange} />
        )}
      </div>
    </div>
  );
}
