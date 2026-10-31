import { readFileSync } from "node:fs";
import type { ServerEvent } from "@contracts";
import { expect, it } from "vitest";
import { applyEvent, createInitialState } from "../state";
import { selectNodeChips } from "./nodeChip";

const events = (name: string) =>
  readFileSync(
    new URL(`../../../fixtures/events/handwritten-${name}.jsonl`, import.meta.url),
    "utf8",
  )
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as ServerEvent);
const log = events("behavior-review");
const at = (seq: number, source = log) =>
  source.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState());
const required = <T>(value: T | undefined | null): T => {
  if (value == null) throw new Error("Missing fixture");
  return value;
};

it("projects eight samples and the published count without treating missing facts as zero", () => {
  expect(selectNodeChips(at(20), null)).toEqual([]);
  const chip = required(selectNodeChips(at(21), null)[0]);
  expect(chip).toMatchObject({
    text: "9 of 10 differ",
    footer: "8 of 9 differing inputs shown",
    limitation: null,
  });
  expect(chip.rows).toHaveLength(8);
  const partial = selectNodeChips(at(22), null);
  expect(partial).toHaveLength(1);
  expect(partial[0]).toMatchObject({
    text: "varied on 1 of 2 inputs",
    differing: 0,
    limitation: "Behavior probing was unavailable; no further reason was published.",
  });
});
it.each([23, 24])("has no chip at seq %i", (seq) => {
  expect(selectNodeChips(at(seq), null)).toEqual([]);
});
it.each([21, 22, 23, 24])("never turns measured callers into chips at seq %i", (seq) => {
  expect(selectNodeChips(at(seq, events("measured-cone")), null)).toEqual([]);
});
it("filters by the lit set and drops resolved, superseded and measured equal evidence", () => {
  const state = at(21),
    change = required(state.changes["change-1"]),
    proposal = required(change.proposal);
  const chips = selectNodeChips(state, null);
  expect(selectNodeChips(state, [])).toEqual([]);
  expect(selectNodeChips(state, [required(chips[0]).nodeId])).toEqual(chips);
  expect(
    selectNodeChips({ ...state, changes: { "change-1": { ...change, status: "approved" } } }, null),
  ).toEqual([]);
  expect(
    selectNodeChips(
      { ...state, changes: { "change-1": { ...change, proposal: { ...proposal, revision: 2 } } } },
      null,
    ),
  ).toEqual([]);
  const facts = required(state.changeFacts["change-1"]?.[0]);
  const node = required(facts.behavior_diff?.[0]);
  const equal = {
    ...state,
    changeFacts: {
      "change-1": [{ ...facts, behavior_diff: [{ ...node, differing_count: 0, rows: [] }] }],
    },
  };
  expect(selectNodeChips(equal, null)).toEqual([]);
});
it("keeps two changes on one node in change-id order with neutral agent letters", () => {
  const state = at(21),
    change = required(state.changes["change-1"]);
  const facts = required(state.changeFacts["change-1"]?.[0]);
  const second = {
    ...change,
    change_id: "change-2",
    agent_id: "agent-2",
    proposal: { ...required(change.proposal), change_id: "change-2", agent_id: "agent-2" },
  };
  const agent = required(state.agents["agent-1"]);
  const two = {
    ...state,
    agents: { ...state.agents, "agent-2": { ...agent, agent_id: "agent-2", slot: "B" as const } },
    changes: { "change-2": second, ...state.changes },
    changeFacts: { ...state.changeFacts, "change-2": [{ ...facts, change_id: "change-2" }] },
  };
  expect(selectNodeChips(two, null).map((chip) => chip.text)).toEqual([
    "A 9 of 10 differ",
    "B 9 of 10 differ",
  ]);
});
