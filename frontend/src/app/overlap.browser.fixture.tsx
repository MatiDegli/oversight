import type { ServerEvent } from "@contracts";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { applyEvent, stateStore } from "../state";
// eslint-disable-next-line no-restricted-imports -- Shared test fixture, never a product dependency.
import { baseMoment, readEvents, sameNodeEvents, required } from "../state/overlap.fixture";
import { Frame } from "./Frame";
import { initialView } from "./view";

let state = baseMoment();
let navigation = 0;
const host = document.createElement("div");
host.style.height = "100vh";
document.body.replaceChildren(host);
const root = createRoot(host);
function navigate() {
  navigation++;
}
function render() {
  stateStore.setState({ state });
  flushSync(() => {
    root.render(
      <Frame
        state={state}
        view={initialView}
        onNodeTap={navigate}
        canOpenChange={() => true}
        onOpenChange={navigate}
        onClosePanel={navigate}
        canReview={(id) =>
          Object.values(state.agents).some(
            (agent) => agent.change_id === id && agent.state === "ready_for_review",
          )
        }
        onReview={navigate}
      />,
    );
  });
}
export const overlapTest = {
  mount() {
    render();
  },
  apply(moment: "read" | "same-node") {
    const events = moment === "read" ? readEvents : sameNodeEvents;
    state = events.reduce(applyEvent, baseMoment());
    render();
  },
  ready() {
    const agent = required(state.agents["agent-2"]);
    const event: ServerEvent = {
      seq: state.lastSeq + 1,
      ts: state.clock ?? "",
      type: "agent.status",
      payload: { ...agent, state: "ready_for_review" },
    };
    state = applyEvent(state, event);
    render();
  },
  navigation: () => navigation,
};
declare global {
  interface Window {
    overlapTest: typeof overlapTest;
  }
}
