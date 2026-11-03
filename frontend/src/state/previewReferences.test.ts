import { expect, it } from "vitest";
import type { LeadView, ServerEvent } from "@contracts";
import fixture from "../../../fixtures/views/reference-cases.json";
import { applyEvent, createInitialState } from "./index";
import { resolvePreviewReference } from "./previewReferences";

const events = fixture.events as ServerEvent[];
function setup() {
  const state = events.slice(0, 6).reduce(applyEvent, createInitialState());
  const agent = state.agents["agent-1"];
  if (!agent) throw new Error("Missing fixture agent");
  state.agents[agent.agent_id] = { ...agent, state: "ready_for_review" };
  const request: LeadView = {
    agent_id: "lead",
    through_seq: 6,
    caption: "speech",
    view: { kind: "preview", agent_id: agent.agent_id },
  };
  state.leadViews.lead = [
    { type: "lead.view", seq: 7, ts: "2026-11-03T00:00:00Z", payload: request },
  ];
  state.lastSeq = 7;
  return { state, request };
}
it("resolves the exact current proposal without mutating inputs", () => {
  const { state, request } = setup();
  const before = structuredClone(state);
  expect(resolvePreviewReference(state, request).candidate).toMatchObject({
    run_id: state.run?.run_id,
    agent_id: "agent-1",
    change_id: "change-1",
    revision: 1,
  });
  expect(state).toEqual(before);
  state.agents["agent-1"] = { ...required(state.agents["agent-1"]), state: "halted" };
  expect(resolvePreviewReference(state, request).candidate).not.toBeNull();
});
it.each(["horizon", "edit", "resolution", "revision", "head", "run", "state", "gap"])(
  "refuses %s evidence rather than retargeting",
  (kind) => {
    const { state, request } = setup();
    const change = required(state.changes["change-1"]);
    if (kind === "horizon") request.through_seq = 5;
    if (kind === "edit") change.fileUpdateSeq = { "a.py": 7 };
    if (kind === "resolution") change.resolution = { merge: null } as never;
    if (kind === "revision" || kind === "head") {
      change.proposal = { ...required(change.proposal), revision: 2, head_commit: "new" };
      change.proposalEvent = { seq: 7 };
    }
    if (kind === "run") state.leadViews = {};
    if (kind === "state")
      state.agents["agent-1"] = { ...required(state.agents["agent-1"]), state: "working" };
    if (kind === "gap") state.gap = true;
    const result = resolvePreviewReference(state, request);
    expect(result.candidate).toBeNull();
    expect(result.reason).toEqual(expect.any(String));
  },
);

function required<T>(value: T | undefined | null): T {
  if (value == null) throw new Error("Missing fixture evidence");
  return value;
}
