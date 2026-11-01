import type { RevertResolvedEvent } from "@contracts";
import { expect, it } from "vitest";
import { applyEvent } from "../state";
import { headerState } from "./ReviewHeader.fixture";
import { revertView, type RevertAttempt } from "./revert";

const merged = () => headerState("merged");
function setup() {
  const state = merged();
  const view = revertView(state, "change-1");
  if (view.kind === "none") throw new Error("Missing rules merge");
  const attempt: RevertAttempt = {
    run: state.run,
    target: view.body,
    afterSeq: state.lastSeq,
    phase: "pending",
  };
  const event: RevertResolvedEvent = {
    seq: state.lastSeq + 1,
    ts: state.clock ?? "",
    type: "revert.resolved",
    payload: {
      ...view.body,
      change_id: "change-1",
      revision: 1,
      decided_by: "person",
      result: "needs_you",
      revert_commit: null,
      reason: "Main moved",
      paths: [],
      tests: null,
    },
  };
  const change = state.changes["change-1"];
  if (!change?.resolution || !state.graph) throw new Error("Missing merge");
  return { state, view, attempt, event, change, resolution: change.resolution };
}
it.each(["person", "conflict", "rejected", "open", "hash", "seq", "graph"])(
  "omits Revert for %s",
  (kind) => {
    const { state, change, resolution } = setup();
    if (kind === "person") resolution.decided_by = "person";
    if (kind === "conflict") resolution.merge = "conflict";
    if (kind === "rejected") {
      change.status = "rejected";
      resolution.decision = "rejected";
      resolution.merge = null;
    }
    if (kind === "open") change.resolution = null;
    if (kind === "hash") resolution.merge_commit = null;
    if (kind === "seq") change.resolutionEvent = null;
    if (kind === "graph") state.graph = null;
    expect(revertView(state, "change-1")).toEqual({ kind: "none" });
  },
);
it("gets the ready command from the resolution envelope, receipt and main graph", () => {
  const { state, view, change, resolution } = setup();
  expect(view).toEqual({
    kind: "ready",
    body: {
      resolution_seq: change.resolutionEvent?.seq,
      merge_commit: resolution.merge_commit,
      main_commit: state.graph?.commit,
    },
  });
  state.mergeMode = { mode: "ask_me", revision: 99 };
  expect(revertView(state, "change-1")).toEqual(view);
});
it.each(["resolution_seq", "merge_commit", "change_id"] as const)(
  "ignores another %s while pending",
  (key) => {
    const { state, attempt, event } = setup();
    expect(revertView(state, "change-1", attempt).kind).toBe("pending");
    if (key === "resolution_seq") event.payload[key] += 1;
    else event.payload[key] += "other";
    expect(revertView(applyEvent(state, event), "change-1", attempt).kind).toBe("pending");
  },
);
it("projects needs-you, waits for a new retry result, and takes the last matching outcome", () => {
  const { state, attempt, event } = setup();
  const next = applyEvent(state, event);
  expect(revertView(next, "change-1", attempt)).toMatchObject({
    kind: "needs_you",
    reason: "Main moved",
  });
  const retry = { ...attempt, afterSeq: next.lastSeq };
  expect(revertView(next, "change-1", retry).kind).toBe("pending");
  const success = {
    ...event,
    seq: event.seq + 1,
    payload: {
      ...event.payload,
      result: "reverted" as const,
      reason: null,
      revert_commit: "ba324d1",
      tests: state.changes["change-1"]?.proposal?.tests ?? null,
    },
  };
  const final = applyEvent(next, success);
  expect(revertView(final, "change-1", retry)).toMatchObject({
    kind: "reverted",
    revert_commit: "ba324d1",
  });
  // Replaying the same envelopes produces the same outcome as the live reducer.
  expect(revertView([event, success].reduce(applyEvent, state), "change-1")).toEqual(
    revertView(final, "change-1"),
  );
});
it("ends a pending attempt only for a later did-not-run error naming this change", () => {
  const { state, attempt } = setup();
  const message = "Revert of change-1 did not run: inverse unavailable";
  const event = {
    type: "error" as const,
    seq: state.lastSeq + 1,
    ts: state.clock ?? "",
    payload: { source: "server" as const, fatal: false, message, agent_id: null, change_id: null },
  };
  expect(revertView(applyEvent(state, event), "change-1", attempt)).toMatchObject({
    kind: "refused",
    message,
  });
  expect(
    revertView(
      applyEvent(state, {
        ...event,
        payload: { ...event.payload, message: "Revert of change-10 did not run" },
      }),
      "change-1",
      attempt,
    ).kind,
  ).toBe("pending");
  expect(
    revertView(state, "change-1", { ...attempt, phase: "refused", message: "refusal" }),
  ).toMatchObject({ kind: "refused", message: "refusal" });
  expect(revertView(state, "change-1", { ...attempt, run: null }).kind).toBe("ready");
});
