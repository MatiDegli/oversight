import type { LeadViewKind, ServerEvent } from "@contracts";
import { lazy, Suspense, useCallback, useState } from "react";
import { useStore } from "zustand";
import references from "../../../fixtures/views/reference-cases.json";
import { stateStore } from "../state";
// eslint-disable-next-line no-restricted-imports -- Synthetic browser source uses the production controller.
import { createReplayPlayer } from "../transport/replayPlayer";
import type { ReplayPlayer } from "../transport";
import { createCommandClient } from "../transport";
// eslint-disable-next-line no-restricted-imports -- Isolated full-length capture of the production card with the same event fold.
import { LeadViewCard } from "../agents/conversation/LeadViewCard";
// eslint-disable-next-line no-restricted-imports -- Derive the capture attribution from the production thread selector.
import { selectLeadConversation } from "../agents/conversation/selectLeadConversation";
import type { View } from "./view";
import { Frame } from "./Frame";
import { createRecordedFiles } from "./recordedFiles";
import { useViewHistory } from "./useViewHistory";
import {
  closePanel,
  conversationTab,
  navigateView,
  openChangeFile,
  tabbedView,
  viewReducer,
} from "./view";

const ts = "2026-11-03T00:00:00Z";
export const longViewPaths = Array.from(
  { length: 8 },
  (_, i) =>
    `shop/${"recorded_reference_directory/".repeat(6)}reference_${String(i + 1)}_with_a_long_readable_filename.py`,
);
/** Synthetic reference facts, never recording evidence. Every row enters the production fold. */
export function viewScenario(): ServerEvent[] {
  const events = structuredClone(references.events) as ServerEvent[];
  const graph = events.findLast((event) => event.type === "graph.snapshot");
  if (graph?.type === "graph.snapshot" && graph.payload.nodes[0]) {
    graph.payload.nodes.push({
      ...graph.payload.nodes[0],
      id: "n4",
      name: "unreferenced",
      qualname: "unreferenced",
      path: "unreferenced.py",
    });
    graph.payload.positions = {
      n1: { x: 100, y: 100 },
      n2: { x: 350, y: 100 },
      n4: { x: 1800, y: 200 },
    };
  }
  const measured = events.findLast((event) => event.type === "change.facts_recorded");
  if (measured?.type === "change.facts_recorded" && measured.payload.behavior_diff?.[0]) {
    measured.payload.behavior_diff[0].differing_count = 1;
    measured.payload.behavior_diff[0].rows = [
      { inputs: "x=-1", before: "-1", after: "ValueError", unstable: false },
    ];
  }
  const update = events.find((event) => event.type === "change.updated");
  if (update?.type !== "change.updated") throw new Error("Missing synthetic file");
  for (const path of longViewPaths)
    events.push({
      ...update,
      payload: {
        ...update.payload,
        file: {
          ...update.payload.file,
          path,
          original: "# synthetic before\n",
          modified: "# synthetic after\n",
        },
      },
    });
  events.push({
    seq: 0,
    ts,
    type: "lead.status",
    payload: {
      agent_id: "agent-9",
      revision: 1,
      task: "Inspect the synthetic run references",
      model: "fake",
      state: "idle",
      step: 4,
      detail: null,
      limits: null,
      requested_plan_id: null,
    },
  });
  const through = events.length;
  const views: LeadViewKind[] = [
    { kind: "highlight", references: [{ kind: "agent", agent_id: "agent-1" }] },
    { kind: "files", references: longViewPaths.map((path) => ({ change_id: "change-1", path })) },
    { kind: "behavior", change_id: "change-1" },
    { kind: "moment", event_seq: 6 },
  ];
  for (const view of views)
    events.push({
      seq: 0,
      ts,
      type: "lead.view",
      payload: {
        agent_id: "agent-9",
        through_seq: through,
        caption: view.kind === "files" ? null : `Synthetic ${view.kind} references.`,
        view,
      },
    });
  events.push({
    seq: 0,
    ts,
    type: "lead.reply",
    payload: {
      agent_id: "agent-9",
      answers_seq: null,
      woken_by: null,
      through_seq: through,
      cut: null,
      text: "Later reply: all original cards remain in Chat.",
    },
  });
  return events.map((event, i) => ({
    ...event,
    seq: i + 1,
    ts: new Date(Date.parse(ts) + i * 100).toISOString(),
  }));
}
let player: ReplayPlayer | null = null;
export async function prepareViewReplay(replay: boolean) {
  player?.dispose();
  player = null;
  if (!replay) return;
  player = createReplayPlayer({
    store: stateStore,
    source: {
      text: viewScenario()
        .map((event) => JSON.stringify(event))
        .join("\n"),
    },
  });
  await player.ready;
  player.pause();
  player.seekSeq(viewScenario().length);
}
export const viewReplaySnapshot = () => ({
  snapshot: player?.getSnapshot(),
  seq: stateStore.getState().state.lastSeq,
});
export function ViewCardsHarness({ live }: { live: boolean }) {
  const state = useStore(stateStore, (store) => store.state);
  const { history, session } = useViewHistory(player);
  const [view, setView] = useState(() =>
    viewReducer(tabbedView, { type: "open", tab: conversationTab(tabbedView, state, "agent-9") }),
  );
  const readChatState = useCallback(() => history?.getSnapshot()?.chat, [history]);
  const onReplayCommit = useCallback((reconcile: (view: View) => View) => {
    setView(reconcile);
  }, []);
  const commands = createCommandClient();
  return (
    <Frame
      state={state}
      chatState={session?.chat}
      readChatState={readChatState}
      view={view}
      replay={player}
      historySession={
        session && history ? { eventSeq: session.eventSeq, back: history.back } : undefined
      }
      onViewMoment={history?.open}
      onViewHighlight={(nodeIds, key) => {
        setView((current) => ({
          ...current,
          referenceHighlight: { nodeIds, frameKey: key, runId: state.run?.run_id ?? "" },
        }));
      }}
      onViewFile={(id, path) => {
        setView((current) => {
          const next = openChangeFile(state, id, path);
          return next ? navigateView(current, next, state) : current;
        });
      }}
      onReplayCommit={onReplayCommit}
      files={createRecordedFiles(() => stateStore.getState().state)}
      onNodeTap={() => {
        setView((current) => viewReducer(current, { type: "watch" }));
      }}
      canOpenChange={() => false}
      onOpenChange={() => {}}
      onClosePanel={() => {
        setView(closePanel);
      }}
      onTabAction={(action) => {
        setView((current) => viewReducer(current, action));
      }}
      onOpenConversation={(id) => {
        setView((current) =>
          viewReducer(current, {
            type: "open",
            tab: conversationTab(current, session?.chat ?? state, id),
          }),
        );
      }}
      onStartLead={live ? commands.startLead : undefined}
      onStartAgent={live ? commands.startAgent : undefined}
      onStopLead={live ? commands.stopLead : undefined}
      onMessageLead={live ? (id, text) => commands.messageLead(id, { text }) : undefined}
      replayLabel="Synthetic view fixture · scripted facts"
    />
  );
}

const ReferenceGraph = lazy(() =>
  import("./shell/GraphStage").then((module) => ({ default: module.GraphStage })),
);
export function ReferenceGraphHarness() {
  const state = useStore(stateStore, (store) => store.state);
  const [view, setView] = useState({
    ...tabbedView,
    referenceHighlight: {
      nodeIds: ["n1", "n2"],
      frameKey: "synthetic-reference",
      runId: state.run?.run_id ?? "",
    },
  });
  return (
    <div className="flex h-full">
      <Suspense>
        <ReferenceGraph
          state={state}
          view={view}
          onNodeTap={() => {
            setView({ ...view, referenceHighlight: { ...view.referenceHighlight, nodeIds: [] } });
          }}
        />
      </Suspense>
    </div>
  );
}

export function FullFilesCapture() {
  const state = stateStore.getState().state;
  const conversation = selectLeadConversation(state, "agent-9");
  const event = state.leadViews["agent-9"]?.find((event) => event.payload.view.kind === "files");
  if (!conversation || !event) throw new Error("Missing synthetic files card");
  return (
    <div style={{ maxWidth: 880, marginInline: "auto", paddingBlock: 16 }}>
      <p style={{ paddingInline: 22, color: "var(--muted)" }}>
        Synthetic view fixture · eight recorded references
      </p>
      <LeadViewCard
        state={state}
        event={event}
        header={conversation.header}
        actions={{ onFile: () => {} }}
      />
    </div>
  );
}
