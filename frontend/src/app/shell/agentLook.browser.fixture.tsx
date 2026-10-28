import type { ServerEvent, Slot } from "@contracts";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import log from "../../../../fixtures/events/handwritten-level1.jsonl?raw";
import { applyEvent, createInitialState, stateStore } from "../../state";
import { Frame } from "../Frame";
import { initialView } from "../view";

const base = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent)
  .filter((event) => event.seq <= 20)
  .reduce(applyEvent, createInitialState());
const host = document.createElement("div");
host.style.height = "100vh";
document.body.replaceChildren(host);
const root = createRoot(host);

/** Synthetic look evidence only: admission, overlapping marks and folding are separate fronts. */
export function showAgentLook(slot: Slot, review: boolean) {
  const state = structuredClone(base);
  const agent = state.agents["agent-1"];
  if (!agent || !state.run) throw new Error("Missing fixture agent");
  agent.slot = slot;
  state.run.slots = [{ slot, default_model: agent.model }];
  stateStore.setState({ state });
  flushSync(() => {
    root.render(
      <Frame
        state={state}
        view={review ? { ...initialView, review: agent.change_id } : initialView}
        onNodeTap={() => undefined}
        canOpenChange={() => true}
        canReview={() => true}
        onOpenChange={() => undefined}
        onReview={() => undefined}
        onClosePanel={() => undefined}
        replayOf="synthetic A–F look harness"
      />,
    );
  });
}
