import type { ServerEvent } from "@contracts";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import log from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { applyEvent, createInitialState } from "../state";
import { AgentCard } from "./AgentCard";
import { selectAgentCard } from "./selectors";

const state = log
  .trim()
  .split("\n")
  .slice(0, 18)
  .map((line) => JSON.parse(line) as ServerEvent)
  .reduce(applyEvent, createInitialState());
const host = document.createElement("div");
document.body.replaceChildren(host);
const root = createRoot(host);
export const modelWaitTest = {
  mount(waiting: boolean) {
    const next = waiting
      ? applyEvent(state, {
          seq: 19,
          ts: state.clock ?? "",
          type: "model.attempt_failed",
          payload: {
            agent_id: "agent-1",
            change_id: "change-1",
            role: "agent",
            model: "muse-spark",
            provider: "meta",
            attempt: 2,
            max_attempts: 5,
            cause: "timeout",
            status: null,
            retry_after_s: null,
            wait_s: 2,
            final: null,
          },
        })
      : state;
    flushSync(() => {
      root.render(
        <main className="flex min-h-screen gap-6 bg-ground p-3.5">
          {[next, state].map((value, index) => {
            const data = selectAgentCard(value, "agent-1");
            if (!data) throw new Error("Missing agent");
            return (
              <div key={index} className="w-(--agents-panel-width) shrink-0 bg-panel p-3.5">
                <AgentCard data={data} onOpenDiff={() => undefined} />
              </div>
            );
          })}
        </main>,
      );
    });
  },
};
declare global {
  interface Window {
    modelWaitTest: typeof modelWaitTest;
  }
}
