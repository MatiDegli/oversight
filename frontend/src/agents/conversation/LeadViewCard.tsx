import type { LeadViewEvent, LeadViewKind } from "@contracts";
import type { State } from "../../state";
import { resolveViewReferences } from "../../state";
// eslint-disable-next-line no-restricted-imports -- Reuse the current-candidate evidence block without eagerly loading it in the shell.
import { BehaviorDiff } from "../../review/BehaviorDiff";
import { agentSlotStyle } from "../../theme";
import { presentationFor } from "../../teams/presentation";
import { EventTime } from "./rows";
import type { LeadHeader } from "./selectConversation";
import "./leadViewCards.css";
import { PreviewOption } from "./PreviewOption";

export interface LeadViewActions {
  onHighlight?: (nodeIds: string[], key: string) => void;
  onFile?: (changeId: string, path: string) => void;
  onMoment?: (seq: number) => void;
}

type CardView = Extract<LeadViewKind, { kind: "highlight" | "files" | "behavior" | "moment" }>;

function selectCardView(view: LeadViewKind): CardView | null {
  switch (view.kind) {
    case "highlight":
    case "files":
    case "behavior":
    case "moment":
      return view;
    case "preview":
      return null;
    default: {
      const unknown: Exclude<LeadViewKind, CardView> = view;
      // eslint-disable-next-line @typescript-eslint/no-meaningless-void-operator -- Today never; future contract kinds intentionally render nothing.
      void unknown;
      return null;
    }
  }
}

/** Unknown future kinds are deliberately invisible; the four known cases remain typed. */
export function viewCardTitle(view: LeadViewKind): string | null {
  switch (view.kind) {
    case "highlight":
      return "Graph references";
    case "files":
      return "Changed files";
    case "behavior":
      return "Measured behavior";
    case "moment":
      return "Run moment";
    case "preview":
      return "Suggested app preview";
    default: {
      const unknown: Exclude<
        LeadViewKind,
        { kind: "highlight" | "files" | "behavior" | "moment" }
      > = view;
      // eslint-disable-next-line @typescript-eslint/no-meaningless-void-operator -- Today never; future contract kinds intentionally render nothing.
      void unknown;
      return null;
    }
  }
}

/** Speech and references stay separate. Every render resolves against the displayed prefix. */
export function LeadViewCard({
  state,
  event,
  header,
  actions = {},
  replay = false,
}: {
  state: State;
  event: LeadViewEvent;
  header: LeadHeader;
  actions?: LeadViewActions;
  replay?: boolean;
}) {
  if (event.payload.view.kind === "preview")
    return <PreviewOption state={state} event={event} replay={replay} />;
  const { caption, through_seq: throughSeq } = event.payload;
  const view = selectCardView(event.payload.view);
  if (view === null) return null;
  const title = viewCardTitle(view);
  if (title === null) return null;
  const references = resolveViewReferences(state, event.payload);
  return (
    <article className="lead-view-card" aria-label={title} style={agentSlotStyle(header.hue)}>
      <header>
        <strong>Lead {header.letter}</strong>
        <EventTime ts={event.ts} />
      </header>
      <p dir="auto" className={caption === null ? "lead-view-note" : "lead-view-caption"}>
        {caption ?? "Caption omitted or withheld in this source."}
      </p>
      {(view.kind !== "behavior" || references.length === 0) && <h3>{title}</h3>}
      {references.length === 0 ? (
        <p className="lead-view-note">
          No referenced items are available at this point in the run.
        </p>
      ) : (
        <CardContents
          state={state}
          view={view}
          references={references}
          actions={actions}
          frameKey={`view:${String(event.seq)}`}
          replay={replay}
        />
      )}
      <p className="lead-view-note">
        Requested through event {throughSeq}; references follow current state.
      </p>
    </article>
  );
}

function CardContents({
  state,
  view,
  references,
  actions,
  replay,
  frameKey,
}: {
  state: State;
  view: CardView;
  references: ReturnType<typeof resolveViewReferences>;
  actions: LeadViewActions;
  replay: boolean;
  frameKey: string;
}) {
  switch (view.kind) {
    case "highlight":
      return (
        <HighlightReferences
          state={state}
          nodeIds={references as string[]}
          onHighlight={actions.onHighlight}
          frameKey={frameKey}
        />
      );
    case "files":
      return (
        <ul>
          {(references as [string, string][]).map(([changeId, path]) => (
            <li key={`${changeId}:${path}`}>
              <button
                type="button"
                disabled={!actions.onFile}
                onClick={() => actions.onFile?.(changeId, path)}
              >
                <code>{path}</code>
                <span className="lead-view-note">
                  {" "}
                  ·{" "}
                  <AgentReference
                    state={state}
                    agentId={state.changes[changeId]?.agent_id ?? null}
                  />{" "}
                  · {state.changes[changeId]?.status}
                </span>
              </button>
            </li>
          ))}
        </ul>
      );
    case "behavior":
      return <BehaviorDiff state={state} changeId={view.change_id} />;
    case "moment":
      return (
        <MomentReference
          state={state}
          seq={view.event_seq}
          replay={replay}
          onMoment={actions.onMoment}
        />
      );
    default: {
      const unknown: Exclude<LeadViewKind, CardView> = view;
      // eslint-disable-next-line @typescript-eslint/no-meaningless-void-operator -- Future contract kinds intentionally render nothing.
      void unknown;
      return null;
    }
  }
}

function HighlightReferences({
  state,
  nodeIds,
  onHighlight,
  frameKey,
}: {
  state: State;
  nodeIds: string[];
  onHighlight: LeadViewActions["onHighlight"];
  frameKey: string;
}) {
  return (
    <>
      <ul>
        {nodeIds.map((id) => {
          const node =
            state.graph?.nodes.find((node) => node.id === id) ??
            Object.values(state.changes)
              .flatMap((change) => change.graph.nodes_added)
              .find((node) => node.id === id);
          return (
            <li key={id}>
              <code>{node?.qualname ?? id}</code>
              {node && <span className="lead-view-note"> · {node.path}</span>}
            </li>
          );
        })}
      </ul>
      <button
        type="button"
        disabled={!onHighlight}
        onClick={() => onHighlight?.(nodeIds, frameKey)}
      >
        Show {nodeIds.length} {nodeIds.length === 1 ? "node" : "nodes"} on graph
      </button>
    </>
  );
}

function MomentReference({
  state,
  seq,
  replay,
  onMoment,
}: {
  state: State;
  seq: number;
  replay: boolean;
  onMoment: LeadViewActions["onMoment"];
}) {
  const fact = state.eventFacts[seq];
  if (!fact) return null;
  return (
    <>
      <dl>
        <dt>Event</dt>
        <dd>
          {seq} · {fact.type}
        </dd>
        <dt>Time</dt>
        <dd>
          <EventTime ts={fact.ts} />
        </dd>
        <dt>Agent</dt>
        <dd>
          <AgentReference state={state} agentId={fact.agentId} />
        </dd>
      </dl>
      {replay ? (
        <button type="button" disabled={!onMoment} onClick={() => onMoment?.(seq)}>
          View event {seq}
        </button>
      ) : (
        <p className="lead-view-note">Rewind available in replay.</p>
      )}
    </>
  );
}

function AgentReference({ state, agentId }: { state: State; agentId: string | null }) {
  if (agentId === null) return <>No agent</>;
  const identity = presentationFor(state, agentId);
  if (!identity) return <>Agent unavailable</>;
  return (
    <span className="lead-view-agent" style={agentSlotStyle(identity.hue)}>
      {identity.kind === "lead" && !state.agents[agentId] ? "Lead" : "Agent"} {identity.label}
    </span>
  );
}
