import type { ServerEvent, TreeResponse } from "@contracts";
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { useStore } from "zustand";
import log from "../../../fixtures/events/recorded-demo.jsonl?raw";
import { applyEvent, createInitialState, stateStore } from "../state";
import { Frame } from "./Frame";
import { FilesProvider } from "./FilesContext";
import { InitialShellLayout } from "./shell/useShell";
import { conversationTab, tabbedView, viewReducer } from "./view";

const events = log
  .trim()
  .split("\n")
  .map((s) => JSON.parse(s) as ServerEvent);
function sample<T extends ServerEvent["type"]>(type: T) {
  const event = events.find((e): e is Extract<ServerEvent, { type: T }> => e.type === type);
  if (!event) throw new Error(`Missing ${type}`);
  return event;
}
const send = (event: ServerEvent) => {
  flushSync(() => {
    stateStore.getState().applyEvents([{ ...event, seq: stateStore.getState().state.lastSeq + 1 }]);
  });
};
function status(agent_id: string, state: "working" | "ready_for_review" | "done", slot: "A" | "B") {
  const event = sample("agent.status");
  send({
    ...event,
    payload: { ...event.payload, agent_id, change_id: `change-${agent_id}`, state, slot },
  });
}
function focus(agent_id: string, mode: "read" | "edit", path: string, node_ids: string[] = []) {
  send({
    ...sample("agent.focus"),
    payload: {
      agent_id,
      mode,
      ranges: [
        {
          path,
          start_line: mode === "edit" ? (path === "shop/inventory.py" ? 26 : 14) : 20,
          end_line: mode === "edit" ? (path === "shop/inventory.py" ? 29 : 21) : 39,
        },
      ],
      node_ids,
    },
  });
}
function propose(agent_id: string, many = false) {
  const event = sample("change.proposed");
  send({
    ...event,
    payload: {
      ...event.payload,
      agent_id,
      change_id: `change-${agent_id}`,
      lines_added: many ? 666 : 21,
      lines_removed: 0,
      files: (many
        ? Array.from(
            { length: 45 },
            (_, i) => `tests/long_segment_that_must_remain_readable_${String(i)}.py`,
          )
        : ["shop/pricing.py", "tests/test_pricing.py"]
      ).map((path, i) => ({ path, status: "modified", lines_added: i ? 15 : 6, lines_removed: 0 })),
    },
  });
}
let loads = 0;
const opened: string[] = [];
const loadTree = (): Promise<TreeResponse> => {
  loads++;
  const state = stateStore.getState().state;
  return Promise.resolve({
    run_id: state.run?.run_id ?? "missing-fixture-run",
    agent_id: null,
    graph_version: null,
    directories: ["shop", "tests"],
    files: [
      "shop/pricing.py",
      "shop/inventory.py",
      "tests/test_pricing.py",
      ...Array.from({ length: 60 }, (_, i) => `shop/z${String(i).padStart(2, "0")}.py`),
    ].map((path) => ({ path, hash: null })),
    truncated: false,
  });
};
function Harness({ working }: { working: boolean }) {
  const state = useStore(stateStore, (s) => s.state);
  const [view, setView] = useState(() =>
    working
      ? viewReducer(tabbedView, {
          type: "open",
          tab: conversationTab(tabbedView, state, "agent-1"),
        })
      : tabbedView,
  );
  return (
    <FilesProvider
      state={state}
      loadTree={loadTree}
      openFile={(path) => {
        opened.push(path);
      }}
    >
      <Frame
        state={state}
        view={view}
        onNodeTap={() => undefined}
        canOpenChange={() => false}
        onOpenChange={() => undefined}
        onClosePanel={() => undefined}
        onTabAction={(action) => {
          setView((v) => viewReducer(v, action));
        }}
        onStartAgent={() => Promise.resolve()}
        onMessageAgent={() => Promise.resolve()}
        replayLabel="Synthetic presence fixture · recorded event schema"
      />
    </FilesProvider>
  );
}
const host = document.getElementById("root");
if (!host) throw new Error("Missing fixture root");
const root = createRoot(host);
export const chatWhereTest = {
  mount(working = false) {
    stateStore.setState({
      state: [sample("run.started"), sample("graph.snapshot")].reduce(
        applyEvent,
        createInitialState(),
      ),
    });
    if (working) {
      status("agent-1", "working", "A");
      status("agent-2", "working", "B");
    }
    loads = 0;
    opened.length = 0;
    flushSync(() => {
      root.render(
        <InitialShellLayout value="Chat">
          <Harness working={working} />
        </InitialShellLayout>,
      );
    });
  },
  stage(value: "working" | "proposed" | "merged") {
    if (value === "working") {
      focus("agent-1", "edit", "shop/pricing.py", ["shop.pricing:compute_price"]);
      focus("agent-2", "read", "shop/inventory.py", ["shop.inventory:Inventory.reserve"]);
    } else if (value === "proposed") {
      propose("agent-1");
      status("agent-1", "ready_for_review", "A");
      focus("agent-2", "edit", "shop/inventory.py", ["shop.inventory:Inventory.reserve"]);
    } else {
      propose("agent-2");
      this.resolve("approved", "merged", "agent-2");
      status("agent-2", "done", "B");
    }
  },
  resolve(
    decision: "approved" | "rejected" | "changes_requested",
    merge: "merged" | "conflict" | null = null,
    agent_id = "agent-1",
  ) {
    const event = sample("change.resolved");
    send({
      ...event,
      payload: {
        ...event.payload,
        agent_id,
        change_id: `change-${agent_id}`,
        decision,
        merge,
        conflict:
          merge === "conflict" ? { kind: "merge", paths: ["shop/pricing.py"], tests: null } : null,
      },
    });
  },
  resume: () => {
    status("agent-1", "working", "A");
  },
  focus,
  many: () => {
    propose("agent-1", true);
  },
  loads: () => loads,
  opened: () => opened,
};
Object.assign(window, { chatWhereTest });
declare global {
  interface Window {
    chatWhereTest: typeof chatWhereTest;
  }
}
