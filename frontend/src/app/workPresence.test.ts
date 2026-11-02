import { expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { ServerEvent } from "@contracts";
import log from "../../../fixtures/events/recorded-demo.jsonl?raw";
import { applyEvent, createInitialState } from "../state";
import { selectWorkPresence } from "./workPresence";
import { FilesProvider } from "./FilesContext";
import { WhereTheyWork } from "./shell/WhereTheyWork";

const events = log
  .trim()
  .split("\n")
  .map((s) => JSON.parse(s) as ServerEvent);
function sample<T extends ServerEvent["type"]>(type: T) {
  const event = events.find((e): e is Extract<ServerEvent, { type: T }> => e.type === type);
  if (!event) throw new Error(`Missing ${type}`);
  return event;
}
function fixture() {
  let state = applyEvent(createInitialState(), sample("run.started"));
  const send = (event: ServerEvent) => {
    state = applyEvent(state, { ...event, seq: state.lastSeq + 1 });
    expect(state.gap).toBe(false);
  };
  const status = (agent_id = "agent-1", slot = "A", value = "working") => {
    send({
      ...sample("agent.status"),
      payload: {
        ...sample("agent.status").payload,
        agent_id,
        slot,
        state: value,
        change_id: `change-${agent_id}`,
      } as Extract<ServerEvent, { type: "agent.status" }>["payload"],
    });
  };
  status();
  return { send, status, state: () => state, presence: () => selectWorkPresence(state) };
}
it.each<[number | null, number | null, string]>([
  [null, null, "Whole file"],
  [null, 9, "Whole file"],
  [8, null, "from line 8"],
  [8, 8, "line 8"],
  [8, 9, "lines 8 to 9"],
])("renders observed range %s–%s as %s", (start_line, end_line, label) => {
  const f = fixture();
  f.send({
    ...sample("agent.focus"),
    payload: {
      agent_id: "agent-1",
      mode: "read",
      ranges: [{ path: "range.py", start_line, end_line }],
      node_ids: [],
    },
  });
  const html = renderToStaticMarkup(
    createElement(FilesProvider, {
      state: f.state(),
      openFile: () => undefined,
      children: createElement(WhereTheyWork),
    }),
  );
  expect(html).toContain(`<div class="work-range">${label}</div>`);
  if (start_line !== null) expect(html).not.toContain("Whole file");
  expect(html).toContain("Files and observed functions. Explore their connections on the graph.");
});
it.each(["working", "revising"])(
  "restores the latest focus on %s after pause until a new focus arrives",
  (value) => {
    const f = fixture();
    f.send({
      ...sample("agent.focus"),
      payload: {
        agent_id: "agent-1",
        mode: "read",
        ranges: [{ path: "retained.py", start_line: 8, end_line: null }],
        node_ids: [],
      },
    });
    const observed = structuredClone(f.presence().current);
    f.status("agent-1", "A", "paused");
    expect(f.presence().current).toEqual([]);
    f.status("agent-1", "A", value);
    expect(f.presence().current).toEqual(observed);
    f.send({
      ...sample("agent.focus"),
      payload: {
        agent_id: "agent-1",
        mode: "edit",
        ranges: [{ path: "replacement.py", start_line: null, end_line: null }],
        node_ids: [],
      },
    });
    expect(f.presence().current[0]).toMatchObject({
      mode: "edit",
      files: [{ path: "replacement.py" }],
    });
  },
);
it("keeps range-only paths, null bounds and distinct observed ranges without mutation", () => {
  const f = fixture();
  const ranges = [
    { path: "no/graph.py", start_line: 2, end_line: 4 },
    { path: "no/graph.py", start_line: 8, end_line: 9 },
    { path: "whole.py", start_line: null, end_line: null },
  ];
  f.send({
    ...sample("agent.focus"),
    payload: {
      agent_id: "agent-1",
      mode: "read",
      ranges: [...ranges, { path: "no/graph.py", start_line: 2, end_line: 4 }],
      node_ids: ["unknown"],
    },
  });
  const before = structuredClone(f.state());
  expect(f.presence().current[0]?.files).toEqual([
    { path: "no/graph.py", ranges: ranges.slice(0, 2), functions: [] },
    { path: "whole.py", ranges: [ranges[2]], functions: [] },
  ]);
  expect(f.state()).toEqual(before);
  f.send({
    ...sample("agent.focus"),
    payload: { agent_id: "agent-1", mode: "edit", ranges: [], node_ids: ["unknown"] },
  });
  expect(f.presence().current).toEqual([]);
});
it("resolves node-only focus from main and the owner's overlay, never another agent's", () => {
  const f = fixture();
  f.send(sample("graph.snapshot"));
  const node = sample("graph.snapshot").payload.nodes.find((n) => n.kind === "method");
  if (!node) throw new Error("Missing method");
  f.send({
    ...sample("agent.focus"),
    payload: { agent_id: "agent-1", mode: "read", ranges: [], node_ids: [node.id] },
  });
  expect(f.presence().current[0]?.files).toEqual([
    { path: node.path, ranges: [], functions: [node.qualname] },
  ]);
  const update = sample("change.updated");
  f.send({
    ...update,
    payload: {
      ...update.payload,
      change_id: "change-agent-1",
      graph: {
        ...update.payload.graph,
        nodes_added: [{ ...node, path: "own.py", qualname: "Own.method" }],
        nodes_modified: ["unknown"],
      },
    },
  });
  f.send({
    ...update,
    payload: {
      ...update.payload,
      agent_id: "other",
      change_id: "other",
      graph: { ...update.payload.graph, nodes_added: [{ ...node, path: "other.py" }] },
    },
  });
  f.send({
    ...sample("agent.focus"),
    payload: {
      agent_id: "agent-1",
      mode: "edit",
      ranges: [],
      node_ids: [node.id, node.id, "unknown"],
    },
  });
  expect(f.presence().current[0]?.files).toEqual([
    { path: "own.py", functions: ["Own.method"], ranges: [] },
  ]);
  expect(f.presence().pending).toEqual([]);
  const proposal = sample("change.proposed");
  f.send({
    ...proposal,
    payload: {
      ...proposal.payload,
      agent_id: "agent-1",
      change_id: "change-agent-1",
      graph: {
        ...proposal.payload.graph,
        nodes_added: [{ ...node, path: "proposed.py", qualname: "", name: "fallback" }],
      },
    },
  });
  expect(f.presence().current[0]?.files).toEqual([
    { path: "proposed.py", functions: ["fallback"], ranges: [] },
  ]);
});
it.each(["paused", "halted", "stopped", "done", "ready_for_review", "starting"])(
  "drops current claims after %s",
  (value) => {
    const f = fixture();
    f.send({
      ...sample("agent.focus"),
      payload: { ...sample("agent.focus").payload, agent_id: "agent-1" },
    });
    expect(f.presence().current).toHaveLength(1);
    f.status("agent-1", "A", value);
    expect(f.presence().current).toEqual([]);
  },
);
it("replaces focus, keeps same-slot identities and clears both consumers on a new run", () => {
  const f = fixture();
  f.status("agent-2");
  for (const agent_id of ["agent-1", "agent-2"])
    f.send({
      ...sample("agent.focus"),
      payload: {
        agent_id,
        mode: "read",
        ranges: [{ path: "shared.py", start_line: 1, end_line: null }],
        node_ids: [],
      },
    });
  expect(f.presence().current.map((g) => g.agentId)).toEqual(["agent-1", "agent-2"]);
  f.status("agent-1", "A", "revising");
  f.send({
    ...sample("agent.focus"),
    payload: {
      agent_id: "agent-1",
      mode: "edit",
      ranges: [{ path: "new.py", start_line: null, end_line: null }],
      node_ids: [],
    },
  });
  expect(f.presence().current[0]).toMatchObject({ mode: "edit", files: [{ path: "new.py" }] });
  const run = sample("run.started");
  const reset = applyEvent(f.state(), {
    ...run,
    seq: 1,
    payload: { ...run.payload, run_id: "new-run" },
  });
  expect(selectWorkPresence(reset)).toEqual({ hasAgents: false, current: [], pending: [] });
});
it("uses exact proposal files/order/statistics, replacement and resolution lifecycle", () => {
  const f = fixture();
  const proposal = sample("change.proposed");
  const payload = {
    ...proposal.payload,
    agent_id: "agent-1",
    change_id: "change-agent-1",
    files: [
      { path: "only/proposal.py", status: "added" as const, lines_added: 17, lines_removed: 0 },
      { path: "second.py", status: "modified" as const, lines_added: 2, lines_removed: 3 },
    ],
  };
  f.send({ ...proposal, payload });
  expect(
    f.presence().pending[0]?.files.map(({ path, lines_added, lines_removed }) => ({
      path,
      lines_added,
      lines_removed,
    })),
  ).toEqual(
    payload.files.map(({ path, lines_added, lines_removed }) => ({
      path,
      lines_added,
      lines_removed,
    })),
  );
  f.send({ ...proposal, payload: { ...payload, revision: 2, files: payload.files.slice(1) } });
  expect(f.presence().pending[0]?.files).toHaveLength(1);
  const resolution = sample("change.resolved");
  for (const [decision, merge, count] of [
    ["approved", "merged", 0],
    ["rejected", null, 0],
    ["changes_requested", null, 0],
    ["approved", "conflict", 1],
  ] as const) {
    f.send({ ...proposal, payload });
    f.send({
      ...resolution,
      payload: {
        ...resolution.payload,
        agent_id: "agent-1",
        change_id: payload.change_id,
        decision,
        merge,
        conflict:
          merge === "conflict" ? { kind: "merge", paths: ["second.py"], tests: null } : null,
      },
    });
    expect(f.presence().pending).toHaveLength(count);
    if (count) expect(f.presence().pending[0]?.conflict).toBe("merge");
  }
});
