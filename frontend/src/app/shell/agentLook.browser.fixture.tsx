import type { GraphSnapshot } from "@contracts";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import log from "../../../../fixtures/events/handwritten-team-colors.jsonl?raw";
import { applyEvent, createInitialState, stateStore } from "../../state";
import { parseEvent } from "../../transport";
import { FilesProvider } from "../FilesContext";
import { Frame } from "../Frame";
import { initialView, viewReducer, surfaceTab, conversationTab } from "../view";

export const teamCuts = {
  "graph-busy": 19,
  "chat-flat": 19,
  inbox: 21,
  "graph-rest": 37,
  "seventh-team": 58,
};
export type TeamScene = keyof typeof teamCuts;
const source = log.trim().split("\n").map(parseEvent);
let root: Root | undefined;

/** Real components, original offline graph, contract-validated handwritten event prefixes. */
export function showTeamColors(graph: GraphSnapshot, scene: TeamScene) {
  root?.unmount();
  const opening = source[0];
  if (!opening || opening.type !== "run.started") throw new Error("Missing handwritten run");
  const events = [
    opening,
    { type: "graph.snapshot" as const, seq: 2, ts: opening.ts, payload: graph },
    ...source.slice(1, teamCuts[scene]).map((event) => ({ ...event, seq: event.seq + 1 })),
  ];
  const state = events.reduce(applyEvent, createInitialState());
  if (state.gap) throw new Error("Team capture has an event gap");
  stateStore.setState({ state });
  const host = document.createElement("div");
  host.style.height = "100vh";
  document.body.replaceChildren(host);
  root = createRoot(host);
  const view =
    scene === "inbox" || scene === "seventh-team"
      ? viewReducer(initialView, { type: "open", tab: surfaceTab(initialView, "inbox") })
      : scene === "chat-flat"
        ? viewReducer(initialView, {
            type: "open",
            tab: conversationTab(initialView, state, "agent-2"),
          })
        : initialView;
  flushSync(() =>
    root?.render(
      <FilesProvider state={state} openFile={() => undefined}>
        <Frame
          state={state}
          view={view}
          onNodeTap={() => undefined}
          canOpenChange={() => true}
          canReview={() => true}
          onMessageAgent={() => Promise.resolve()}
          onOpenChange={() => undefined}
          onReview={() => undefined}
          onClosePanel={() => undefined}
          replayLabel="Handwritten events over Boltons d31d821"
          replayOf="handwritten events over Boltons d31d821"
        />
      </FilesProvider>,
    ),
  );
}
