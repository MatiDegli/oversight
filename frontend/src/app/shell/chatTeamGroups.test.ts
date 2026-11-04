import { expect, it } from "vitest";
import type { LeadStatus, ServerEvent } from "@contracts";
import { event, fold, plan, seed, status } from "../chatAgents.fixture";
import { chatTeamGroups } from "./chatTeamGroups";

function lead(id: string) {
  return event("lead.status", {
    agent_id: id,
    revision: 1,
    task: "Coordinate",
    model: "muse-spark",
    state: "idle",
    step: 1,
    detail: null,
    limits: null,
    requested_plan_id: null,
  } satisfies LeadStatus);
}
function team(id: string, owner: string | null, ids: string[]) {
  const p = plan(id, owner);
  if (p.type !== "plan.updated") throw new Error("Missing plan");
  return [
    {
      ...p,
      payload: {
        ...p.payload,
        state: "running" as const,
        subtasks: ids.map((agent_id) => ({
          agent_id,
          task: "Member",
          zone: { node_ids: [], paths: [] },
          depends_on: [],
          state: "running" as const,
        })),
      },
    },
    ...ids.map((agent_id) => status(agent_id, { plan_id: id })),
  ];
}
function groups(events: ServerEvent[]) {
  return chatTeamGroups(fold([...seed, ...events]));
}

it("wraps two lead teams and keeps person-started C ungrouped", () => {
  const result = groups([
    lead("lead-a"),
    lead("lead-b"),
    ...team("p1", "lead-a", ["a1", "a2"]),
    ...team("p2", "lead-b", ["b1"]),
    status("lone"),
  ]);
  expect(result.map((g) => [g.id, g.grouped, g.members.map((m) => m.id)])).toEqual([
    ["lead-a", true, ["a1", "a2"]],
    ["lead-b", true, ["b1"]],
    ["lone", false, ["lone"]],
  ]);
  expect(result[2]?.identity.label).toBe("C");
});
it("keeps team-wide labels across two plans of one lead", () => {
  const [group] = groups([
    lead("lead"),
    ...team("p1", "lead", ["a1", "a2"]),
    ...team("p2", "lead", ["a3", "a4"]),
  ]);
  expect(group?.members.map((m) => m.letter)).toEqual(["A·1", "A·2", "A·3", "A·4"]);
});
it("uses the exact Q2 heading in the lead heading position", () => {
  const result = groups([lead("lead"), ...team("p2", null, ["b1", "b2"])]);
  expect(result[1]?.summary).toBe("Plan B · 2 agents · 2 working");
});
it("keeps distinct teams when K3 reuses a hue", () => {
  const result = groups(Array.from({ length: 7 }, (_, i) => lead(`lead-${String(i)}`)));
  expect(result).toHaveLength(7);
  expect(result[0]?.identity.hue).toBe(result[6]?.identity.hue);
  expect(new Set(result.map((g) => g.id)).size).toBe(7);
});
it("shows a lead without workers and retains proposed-plan attention", () => {
  expect(groups([lead("lead")])[0]?.summary).toBe("Lead A · 0 agents");
  const proposed = plan("p1", "lead");
  if (proposed.type !== "plan.updated") throw new Error("Missing plan");
  expect(
    groups([lead("lead"), { ...proposed, payload: { ...proposed.payload, state: "proposed" } }])[0]
      ?.summary,
  ).toBe("Lead A · 1 plan needs you");
});
it("counts working and revising, preserving waiting and ended members", () => {
  const [group] = groups([
    ...team("p", null, ["a", "b", "c", "d"]),
    status("b", { plan_id: "p", state: "revising" }),
    status("c", { plan_id: "p", state: "ready_for_review" }),
    status("d", { plan_id: "p", state: "done" }),
  ]);
  expect(group?.summary).toBe("Plan A · 4 agents · 2 working · 1 needs you");
});
it.each([1, 2])("pluralizes %i coding members independently of the lead", (count) => {
  const [group] = groups([
    lead("lead"),
    ...team(
      "p",
      "lead",
      Array.from({ length: count }, (_, i) => `a${String(i)}`),
    ),
  ]);
  expect(group?.summary).toBe(
    `Lead A · ${String(count)} ${count === 1 ? "agent" : "agents"} · ${String(count)} working`,
  );
});
