import { readFileSync } from "node:fs";

import type { ChangeResolved, ServerEvent } from "@contracts";
import { describe, expect, it, vi } from "vitest";

import { applyEvent, createInitialState } from "../state";
import type { State } from "../state";
import { CommandError } from "../transport";
import { decisionView, failureMessage, sendDecision, startAttempt } from "./decisions";
import type { DecisionAttempt } from "./decisions";

// The level 1 log: proposed at seq 19, ready at 20, approved and merged at 21.
const log = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const through = (seq: number) =>
  log.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState());
const ts = "2026-10-27T14:03:40.000Z";
const resolved = (state: State, payload: Partial<ChangeResolved>) =>
  applyEvent(state, {
    seq: state.lastSeq + 1,
    ts,
    type: "change.resolved",
    payload: {
      change_id: "change-1",
      agent_id: "agent-1",
      revision: 1,
      decision: "rejected",
      reason: null,
      comment: null,
      merge: null,
      merge_commit: null,
      conflict: null,
      ...payload,
    },
  });
const errorFor = (state: State, change_id: string | null) =>
  applyEvent(state, {
    seq: state.lastSeq + 1,
    ts,
    type: "error",
    payload: {
      source: "git",
      message: `merge failed for ${String(change_id)}`,
      agent_id: null,
      change_id,
      fatal: false,
    },
  });
function view(state: State, attempt?: DecisionAttempt, recorded = false) {
  const change = state.changes["change-1"];
  if (!change) throw new Error("The fixture has no change-1");
  return decisionView(change, state.errors, attempt, recorded);
}

describe("the decision area's state", () => {
  it("stays pending from the click until change.resolved, which 202 alone never ends", () => {
    const ready = through(20);
    expect(view(ready)).toEqual({ kind: "open", failure: null });
    const approving = startAttempt(ready, "change-1", "approve");
    expect(view(ready, approving)).toEqual({ kind: "pending", action: "approve" });
    // Later events that are not the answer keep it pending.
    expect(view(errorFor(ready, null), approving).kind).toBe("pending");
    expect(view(errorFor(ready, "change-9"), approving).kind).toBe("pending");
    const merged = view(through(21), approving);
    expect(merged.kind).toBe("decided");
    expect(merged.kind === "decided" && merged.resolution.merge).toBe("merged");
    const rejecting = startAttempt(ready, "change-1", "reject");
    expect(view(resolved(ready, {}), rejecting)).toMatchObject({
      kind: "decided",
      resolution: { decision: "rejected" },
    });
  });

  it("ends pending on an error event naming the change and shows its message", () => {
    const ready = through(20);
    const approving = startAttempt(ready, "change-1", "approve");
    expect(view(errorFor(ready, "change-1"), approving)).toEqual({
      kind: "open",
      failure: "merge failed for change-1",
    });
    // An error from before the click does not end a later attempt.
    const earlier = errorFor(ready, "change-1");
    expect(view(earlier, startAttempt(earlier, "change-1", "approve")).kind).toBe("pending");
  });

  it("shows a 4xx's message, and a plain line for a 500 or a lost request", () => {
    const conflict = new CommandError(409, "wrong_state", "The change was already decided.");
    expect(failureMessage("approve", conflict)).toBe("The change was already decided.");
    expect(failureMessage("reject", new CommandError(404, "not_found", "No change-9."))).toBe(
      "No change-9.",
    );
    const internal = new CommandError(500, "internal", "Traceback (most recent call last)");
    expect(failureMessage("approve", internal)).toBe(
      "Approve failed: the server hit an internal error. Try again.",
    );
    expect(failureMessage("reject", new TypeError("Failed to fetch"))).toBe(
      "Reject did not reach the server. Check the connection and try again.",
    );
    const failed: DecisionAttempt = {
      phase: "failed",
      action: "approve",
      revision: 1,
      message: "Busy.",
    };
    expect(view(through(20), failed)).toEqual({ kind: "open", failure: "Busy." });
  });

  it("offers only Reject after an approval that did not merge", () => {
    const ready = through(20);
    const conflicted = resolved(ready, {
      decision: "approved",
      merge: "conflict",
      conflict: { kind: "merge", paths: ["shop/pricing.py"], tests: null },
    });
    expect(view(conflicted, startAttempt(ready, "change-1", "approve"))).toMatchObject({
      kind: "conflict",
      failure: null,
    });
  });

  it("starts a new revision idle: an attempt on revision 1 does not come back with revision 2", () => {
    const ready = through(20);
    const approving = startAttempt(ready, "change-1", "approve");
    expect(approving.revision).toBe(1);
    // Approved, then not merged: the change keeps only Reject, and the person asks for a redo.
    const conflicted = resolved(ready, {
      decision: "approved",
      merge: "conflict",
      conflict: { kind: "merge", paths: ["shop/pricing.py"], tests: null },
    });
    expect(view(conflicted, approving).kind).toBe("conflict");
    const proposed = log.find((event) => event.type === "change.proposed");
    if (proposed?.type !== "change.proposed") throw new Error("The fixture has no proposal");
    const again = applyEvent(conflicted, {
      ...proposed,
      seq: conflicted.lastSeq + 1,
      payload: { ...proposed.payload, revision: 2 },
    });
    expect(again.changes["change-1"]?.resolution).toBeNull();
    // Without the revision, the old attempt (resolution null at its click) reads as pending again.
    expect(view(again, approving)).toEqual({ kind: "open", failure: null });
    const failed: DecisionAttempt = {
      phase: "failed",
      action: "approve",
      revision: 1,
      message: "Busy.",
    };
    expect(view(again, failed)).toEqual({ kind: "open", failure: null });
    // An attempt on the new revision counts as usual.
    expect(view(again, startAttempt(again, "change-1", "approve")).kind).toBe("pending");
  });

  it("in a recorded run shows the recorded decision and never a pending state", () => {
    expect(view(through(20), undefined, true)).toEqual({ kind: "recorded", resolution: null });
    expect(view(through(21), undefined, true)).toMatchObject({
      kind: "recorded",
      resolution: { decision: "approved", merge: "merged" },
    });
  });
});

describe("sending a decision", () => {
  const client = (result: Promise<unknown>) => ({
    approveChange: vi.fn(() => result),
    rejectChange: vi.fn(() => result),
  });

  it("records pending before the request and nothing more on 202", async () => {
    const accepted = client(Promise.resolve(undefined));
    const records: DecisionAttempt[] = [];
    const sent = sendDecision(accepted, through(20), "change-1", "approve", "  ", (attempt) => {
      records.push(attempt);
    });
    expect(records).toEqual([startAttempt(through(20), "change-1", "approve")]);
    await sent;
    expect(records).toHaveLength(1);
    expect(accepted.approveChange).toHaveBeenCalledWith("change-1", {});
    expect(accepted.rejectChange).not.toHaveBeenCalled();
  });

  it("records the 409's message, and sends the trimmed comment", async () => {
    const refused = client(Promise.reject(new CommandError(409, "wrong_state", "Not proposed.")));
    const records: DecisionAttempt[] = [];
    await sendDecision(refused, through(20), "change-1", "reject", " too broad \n", (attempt) => {
      records.push(attempt);
    });
    expect(refused.rejectChange).toHaveBeenCalledWith("change-1", { comment: "too broad" });
    expect(records.map((attempt) => attempt.phase)).toEqual(["pending", "failed"]);
    expect(records[1]).toEqual({
      phase: "failed",
      action: "reject",
      revision: 1,
      message: "Not proposed.",
    });
  });
});
