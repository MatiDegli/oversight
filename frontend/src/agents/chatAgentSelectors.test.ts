import { selectPresentation } from "../teams/presentation";
import { expect, it } from "vitest";
import type { AgentStatus, ServerEvent } from "@contracts";
import { applyEvent, createInitialState } from "../state";
// eslint-disable-next-line no-restricted-imports -- Unexported test data shared with the actual Frame harness.
import {
  required,
  fold,
  plan,
  proposal,
  resolved,
  scenario,
  seed,
  status,
  step,
  usage,
  event,
} from "../app/chatAgents.fixture";
import { chatAgentCount, selectChatAgents } from "./chatAgentSelectors";

const letters = (events: ServerEvent[]) =>
  selectChatAgents(fold(events)).map((r) => [r.id, r.letter]);
it("consumes the shared team selector, ignoring status.started_by and slot paint", () => {
  const state = fold([
    ...seed,
    plan("p", "lead"),
    status("two", { plan_id: "p", slot: "F" }),
    status("solo"),
    status("explicit", { plan_id: "p", started_by: "other", slot: "C" }),
  ]);
  const presentation = selectPresentation(state).byAgent;
  for (const row of selectChatAgents(state)) {
    expect(row.letter).toBe(presentation[row.id]?.label);
    expect(row.hue).toBe(presentation[row.id]?.hue);
  }
  expect(presentation.two?.team).toBe("lead");
  expect(presentation.explicit?.team).toBe("lead");
  expect(
    selectChatAgents({
      ...state,
      agents: Object.fromEntries(Object.entries(state.agents).reverse()),
    }),
  ).toEqual(selectChatAgents(state));
});
it("keeps letters beyond six independent of shared hues and capacity reuse", () => {
  const events = [
    ...seed,
    ...Array.from({ length: 6 }, (_, i) => status(`a${String(i)}`)),
    { ...resolved("a1"), ts: "2026-11-01T18:53:00Z" },
    { ...resolved("a0"), ts: "2026-11-01T18:54:00Z" },
    status("new"),
    status("next"),
  ];
  const rows = selectChatAgents(fold(events));
  expect(rows.map((row) => row.letter)).toEqual(["A", "B", "C", "D", "E", "F", "G", "H"]);
  expect(rows.slice(-2).map((row) => row.hue)).toEqual(["B", "B"]);
  expect(
    letters([
      ...seed,
      status("first", { slot: "F" }),
      resolved("first"),
      status("reuse", { slot: "F" }),
    ]),
  ).toEqual([
    ["first", "A"],
    ["reuse", "B"],
  ]);
});
it("reconstructs every replay prefix and resets identity with the new run", () => {
  const events = [...scenario("merged"), plan("p"), status("child", { plan_id: "p" })];
  let state = createInitialState();
  events.forEach((e, i) => {
    state = applyEvent(state, { ...e, seq: i + 1 });
    expect(selectChatAgents(state)).toEqual(
      selectChatAgents(fold(JSON.parse(JSON.stringify(events.slice(0, i + 1))) as ServerEvent[])),
    );
  });
  const start = required(seed[0]);
  if (start.type !== "run.started") throw new Error("Missing run");
  state = applyEvent(state, { ...start, seq: 1, payload: { ...start.payload, run_id: "new-run" } });
  expect(selectChatAgents(state)).toEqual([]);
  expect(selectChatAgents(applyEvent(state, { ...status("fresh"), seq: 2 }))[0]?.letter).toBe("A");
});
it.each([
  ["empty", 0],
  ["working", 2],
  ["proposed", 2],
  ["merged", 1],
] as const)("counts held work in %s", (name, held) => {
  expect(chatAgentCount(fold(scenario(name)))).toEqual({ held, total: 2 });
  expect(chatAgentCount(createInitialState())).toBeNull();
});
it("counts paused, halted and conflict until a resolution releases the slot", () => {
  const events = [
    ...seed,
    status("a", { state: "paused" }),
    status("b", { slot: "B", state: "halted" }),
    proposal("a"),
    resolved("a", true),
  ];
  expect(chatAgentCount(fold(events))?.held).toBe(2);
  expect(chatAgentCount(fold([...events, resolved("a")]))?.held).toBe(1);
});
it("keeps absent, null, zero and cumulative priced cost distinct and clamps observed steps", () => {
  const events = [...seed, status("agent-a", { max_steps: 99 })];
  expect(selectChatAgents(fold(events))[0]?.cost).toBeUndefined();
  expect(selectChatAgents(fold([...events, usage("agent-a", 0)]))[0]?.cost).toBe("US$0.00");
  expect(
    selectChatAgents(fold([...events, usage("agent-a", null), usage("agent-a", 1)]))[0]?.cost,
  ).toBe("Cost unknown");
  const row = selectChatAgents(
    fold([...events, usage("agent-a", 1), usage("agent-a", 2), step("agent-a", "read_file", 99)]),
  )[0];
  expect(selectChatAgents({ ...fold(events), run: null })[0]?.maxSteps).toBe(99);
  expect(row).toMatchObject({
    step: 30,
    maxSteps: 30,
    label: "Reading",
    cost: "US$3.00",
    model: "Muse Spark",
  });
});
it.each([
  "starting",
  "working",
  "ready_for_review",
  "revising",
  "paused",
  "halted",
  "stopped",
  "done",
] satisfies AgentStatus["state"][])("keeps %s explicit", (state) => {
  const row = required(selectChatAgents(fold([...seed, status("a", { state })]))[0]);
  expect(row.label).toBe(
    {
      starting: "Starting",
      working: "Working",
      ready_for_review: "Ready for review",
      revising: "Revising",
      paused: "Paused",
      halted: "Halted",
      stopped: "Stopped",
      done: "Done",
    }[state],
  );
});
it("uses focus, model waits and matching review reasons without inventing rule activity", () => {
  const events = [
    ...seed,
    status(),
    event("agent.focus", {
      agent_id: "agent-a",
      mode: "read",
      node_ids: [],
      ranges: [{ path: "shop/pricing.py", start_line: 1, end_line: 2 }],
    }),
  ];
  expect(selectChatAgents(fold(events))[0]).toMatchObject({
    label: "Reading",
    activity: "Reading shop/pricing.py",
  });
  const state = fold(events);
  state.modelWaits["agent-a"] = {
    seq: 5,
    attempt: 1,
    max_attempts: 3,
    cause: "timeout",
    wait_s: 1,
    status: null,
  };
  expect(selectChatAgents(state)[0]?.label).toBe("Waiting");
  const ready = fold(scenario("proposed")),
    change = required(ready.changes["change-agent-a"]);
  expect(selectChatAgents(ready)[0]?.activity).toBe("Waiting for your review");
  change.needsYou = {
    revision: required(change.proposal).revision,
    head: required(change.proposal).head_commit,
    code: "fixture",
    reason: "Held for you by your rules",
  };
  expect(selectChatAgents(ready)[0]?.activity).toBe(change.needsYou.reason);
  change.needsYou.revision++;
  expect(selectChatAgents(ready)[0]?.activity).toBe("Waiting for your review");
  expect(selectChatAgents(fold(scenario("merged")))[1]?.activity).toBe("Merged by your rules");
});

it("keeps member numbers after plan agents finish and a physical slot is reused", () => {
  expect(
    letters([
      ...seed,
      event("plan.updated", {
        plan_id: "p",
        revision: 1,
        started_by: null,
        task: "Two tasks",
        graph_version: "fixture",
        state: "running",
        detail: null,
        queued_task_id: null,
        subtasks: ["first", "second"].map((agent_id) => ({
          agent_id,
          task: "Subtask",
          zone: { node_ids: [], paths: [] },
          depends_on: [],
          state: "running",
        })),
      }),
      status("first", { plan_id: "p" }),
      resolved("first"),
      status("second", { plan_id: "p" }),
      status("first", { state: "done" }),
    ]),
  ).toEqual([
    ["first", "A·1"],
    ["second", "A·2"],
  ]);
});
