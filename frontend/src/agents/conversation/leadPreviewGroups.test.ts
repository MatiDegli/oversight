import { expect, it } from "vitest";
import type { LeadEntry } from "./selectConversation";
import { groupLeadPreviews } from "./leadPreviewGroups";

const status = (seq: number, state = "working"): LeadEntry => ({
  kind: "lead-status",
  seq,
  ts: "same",
  status: { agent_id: "lead", revision: 1, state } as never,
});
const view = (seq: number, kind = "preview", lead = "lead"): LeadEntry => ({
  kind: "lead-view",
  seq,
  event: {
    type: "lead.view",
    seq,
    ts: "same",
    payload: {
      agent_id: lead,
      through_seq: 1,
      caption: null,
      view: { kind, agent_id: "agent" } as never,
    },
  },
});
const reply = (
  seq: number,
  answers: number | null = null,
  wake: number[] | null = null,
): LeadEntry => ({
  kind: "lead-reply",
  seq,
  limits: null,
  event: {
    type: "lead.reply",
    seq,
    ts: "same",
    payload: {
      agent_id: "lead",
      answers_seq: answers,
      woken_by: wake,
      through_seq: seq - 1,
      text: null,
      cut: "time",
    },
  },
});
const message: LeadEntry = { kind: "message", seq: 5, ts: "same", text: "next" };
it("groups opening previews in their order, preserving every other row and original input", () => {
  const entries = [status(1), view(2), view(3, "moment"), view(4), reply(6)];
  const before = structuredClone(entries);
  const rows = groupLeadPreviews(entries);
  expect(rows.map((r) => r.entry.seq)).toEqual([1, 3, 6]);
  expect(rows[2]?.previews.map((e) => e.seq)).toEqual([2, 4]);
  expect(entries).toEqual(before);
  expect(entries.map((e) => e.seq)).toEqual([1, 2, 3, 4, 6]);
});
it("associates message and autonomous wake turns from reply evidence", () => {
  expect(
    groupLeadPreviews([status(1), reply(2), message, status(6), view(7), reply(8, 5)])[4]?.previews,
  ).toHaveLength(1);
  expect(
    groupLeadPreviews([status(1), reply(2), status(6), view(7), reply(8, null, [4])])[3]?.previews,
  ).toHaveLength(1);
});
it.each([
  [status(1), view(2)],
  [view(2), reply(3)],
  [status(1), view(2), message, reply(6, 5)],
  [status(1), view(2), status(3, "idle"), status(4), reply(6)],
  [status(1), reply(2), view(3), reply(4)],
  [status(1), view(2), reply(3), reply(4)],
  [status(1), view(2, "preview", "other"), reply(3)],
])(
  "retains ambiguous/incomplete/cross-turn previews once at original position: %j",
  (...entries) => {
    const rows = groupLeadPreviews(entries);
    expect(rows.map((r) => r.entry.seq)).toEqual(entries.map((e) => e.seq));
    expect(rows.filter((r) => r.fallback)).toHaveLength(1);
  },
);
it("a later matching reply groups a prefix without requiring text or changing the prefix", () => {
  const prefix = [status(1), view(2)];
  expect(groupLeadPreviews(prefix)[1]?.fallback).toBe(true);
  expect(groupLeadPreviews([...prefix, reply(3)])[1]?.previews).toHaveLength(1);
  expect(prefix).toHaveLength(2);
});

it("does not infer an opening turn from an incomplete status prefix", () => {
  const current = status(1);
  if (current.kind !== "lead-status") throw new Error("Missing status");
  current.status.revision = 4;
  expect(groupLeadPreviews([current, view(2), reply(3)])[1]?.fallback).toBe(true);
});

it("retains projection identity for an unchanged timeline and regroups a new reply prefix", () => {
  const prefix = [status(1), view(2)];
  const fallback = groupLeadPreviews(prefix);
  expect(groupLeadPreviews(prefix)).toBe(fallback);
  expect(groupLeadPreviews(prefix)[1]).toBe(fallback[1]);

  const extended = [...prefix, reply(3)];
  const grouped = groupLeadPreviews(extended);
  expect(grouped).not.toBe(fallback);
  expect(grouped[1]?.previews).toEqual([prefix[1]]);
  expect(groupLeadPreviews(extended)).toBe(grouped);
  expect(groupLeadPreviews(prefix)).toBe(fallback);
  expect(fallback[1]?.fallback).toBe(true);
});
