import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import type { ServerEvent, SuiteResult } from "@contracts";
import { applyEvent, createInitialState, stateStore } from "../../../state";
import log from "../../../../../fixtures/events/handwritten-level1.jsonl?raw";
import { BottomDock } from "../Status";
import "../shell.css";

const events = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const host = document.createElement("div");
document.body.replaceChildren(host);
const root = createRoot(host);
const suite: SuiteResult = {
  outcome: "failed",
  passed: 8,
  failed: 1,
  skipped: 0,
  duration_ms: 1430,
  failed_tests: ["tests/test_cart.py::test_total"],
  cases: null,
  cases_complete: null,
};
export const dockTest = {
  mount() {
    stateStore.setState({ state: createInitialState() });
    flushSync(() => {
      root.render(
        <>
          <input aria-label="Outside comment" />
          <BottomDock
            onClose={() => {
              root.unmount();
            }}
          />
        </>,
      );
    });
  },
  populate() {
    const state = events.reduce(applyEvent, createInitialState());
    state.steps = { "agent-1": [] };
    state.stepTimes = {};
    state.errors = [
      {
        source: "tools",
        message: "Read failed. Retry the file read.",
        agent_id: "agent-1",
        change_id: null,
        fatal: false,
      },
    ];
    state.testExecutions = {
      100: {
        change_id: "change-1",
        agent_id: "agent-1",
        revision: 1,
        phase: "combined",
        tested_commit: null,
        tested_tree: "tree",
        suite,
      },
    };
    flushSync(() => {
      stateStore.setState({ state });
    });
  },
  steps(count: number) {
    const state = stateStore.getState().state;
    const before = state.steps["agent-1"] ?? [];
    const added = Array.from({ length: count }, (_, i) => ({
      agent_id: "agent-1",
      step: before.length + i + 1,
      tool: "read_file",
      summary: `Read ${String(before.length + i + 1)}`,
      result: "ok" as const,
      error: null,
      duration_ms: 12,
      tests: null,
    }));
    flushSync(() => {
      stateStore.setState({
        state: {
          ...state,
          steps: { "agent-1": [...before, ...added] },
          stepTimes: {
            "agent-1": Object.fromEntries(
              [...before, ...added].map((step) => [
                step.step,
                new Date(step.step * 1000).toISOString(),
              ]),
            ),
          },
        },
      });
    });
  },
};
declare global {
  interface Window {
    dockTest: typeof dockTest;
  }
}
