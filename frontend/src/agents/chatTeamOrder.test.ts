import { expect, it } from "vitest";
// eslint-disable-next-line no-restricted-imports -- Shared event builders exercise the production reducer.
import { event, fold, plan, seed, status } from "../app/chatAgents.fixture";
import { selectChatAgents } from "./chatAgentSelectors";
import { selectChatRows } from "./chatLeadSelectors";

function memberPlan(id: string, ids: string[]) {
  const source = plan(id, "lead");
  if (source.type !== "plan.updated") throw new Error("Missing plan");
  return {
    ...source,
    payload: {
      ...source.payload,
      subtasks: ids.map((agent_id) => ({
        agent_id,
        task: "Member",
        zone: { node_ids: [], paths: [] },
        depends_on: [],
        state: "running" as const,
      })),
    },
  };
}

const state = () =>
  fold([
    ...seed,
    event("lead.status", {
      agent_id: "lead",
      revision: 1,
      task: "Coordinate",
      model: "fake",
      state: "idle",
      step: 0,
      detail: null,
      limits: null,
      requested_plan_id: null,
    }),
    memberPlan("z-first", ["one", "two"]),
    status("two", { plan_id: "z-first", slot: "F" }),
    status("one", { plan_id: "z-first", slot: "B" }),
    memberPlan("a-second", ["three", "four"]),
    status("four", { plan_id: "a-second", slot: "A" }),
    status("three", { plan_id: "a-second", slot: "B" }),
  ]);

it("orders coding rows by team ordinal across plans, independent of seats and status arrival", () => {
  expect(selectChatAgents(state()).map((row) => row.letter)).toEqual(["A·1", "A·2", "A·3", "A·4"]);
});

it("keeps the lead first and orders the combined Chat rail by team ordinal", () => {
  expect(selectChatRows(state()).map((row) => row.letter)).toEqual([
    "A",
    "A·1",
    "A·2",
    "A·3",
    "A·4",
  ]);
});
