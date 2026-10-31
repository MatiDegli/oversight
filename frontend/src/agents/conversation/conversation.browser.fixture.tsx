import type { AgentStatus } from "@contracts";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { applyEvent } from "../../state";
import { ConversationPanel } from "./ConversationPanel";
import { sketchMessages, sketchState } from "./fixture";
import { selectConversation } from "./selectConversation";

let state = sketchState;
let replay = false;
let messages = sketchMessages;
const requests: string[] = [];
let answer: { resolve: () => void; reject: (error: Error) => void } | undefined;
const host = document.createElement("div");
host.style.height = "100vh";
document.body.replaceChildren(host);
const root = createRoot(host);
function render() {
  const conversation = selectConversation(state, "agent-1", messages);
  if (!conversation) throw new Error("Missing fixture agent");
  flushSync(() => {
    root.render(
      <ConversationPanel
        conversation={conversation}
        onOpenDiff={(id) => {
          requests.push(id);
        }}
        {...(replay
          ? {}
          : {
              onSend: (text: string) => {
                requests.push(text);
                return new Promise<void>((resolve, reject) => {
                  answer = { resolve, reject };
                });
              },
              onPause: () => {
                requests.push("Pause");
                return Promise.resolve();
              },
              onResume: () => {
                requests.push("Resume");
                return Promise.resolve();
              },
              onStop: (id: string) => {
                requests.push(`Stop ${id}`);
                return Promise.resolve();
              },
            })}
      />,
    );
  });
}
export const conversationTest = {
  mount(mode: AgentStatus["state"] | "replay" = "working") {
    const agent = sketchState.agents["agent-1"];
    if (!agent) throw new Error("Missing fixture agent");
    state = {
      ...sketchState,
      agents: { "agent-1": { ...agent, state: mode === "replay" ? "revising" : mode } },
    };
    replay = mode === "replay";
    render();
  },
  requests: () => requests,
  answer(ok: boolean) {
    if (ok) answer?.resolve();
    else answer?.reject(new Error("offline"));
  },
  append() {
    state = applyEvent(state, {
      seq: state.lastSeq + 1,
      ts: "2026-10-27T17:04:00.000Z",
      type: "agent.step",
      payload: {
        agent_id: "agent-1",
        step: (state.steps["agent-1"]?.length ?? 0) + 1,
        tool: "read_file",
        summary: "Read pricing.py",
        result: "ok",
        error: null,
        error_kind: null,
        duration_ms: 1,
        tests: null,
      },
    });
    render();
  },
  withheld() {
    messages = sketchMessages.map((message) => ({ ...message, text: null }));
    render();
  },
};
declare global {
  interface Window {
    conversationTest: typeof conversationTest;
  }
}
