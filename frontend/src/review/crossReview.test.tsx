import { readFileSync } from "node:fs";
import type { CrossReview, ServerEvent } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";

import golden from "../../../fixtures/events/needs-you.json";
import { CrossReviewNote } from "./CrossReviewNote";
import { decisionView, startAttempt } from "./decisions";
import { matchesNeedsYou, parseNeedsYou } from "../state";
import { applyEvent, createInitialState } from "../state";
const panelEvents = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const through = (seq: number) =>
  panelEvents.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState());
const appendEvent = (state: State, event: ServerEvent) =>
  applyEvent(state, { ...event, seq: state.lastSeq + 1 });
import type { State } from "../state";

const ready = through(20);
const change = ready.changes["change-1"];
const proposal = change?.proposal;
if (!change || !proposal) throw new Error("Missing proposal");
const target = proposal;
const review: CrossReview = {
  change_id: target.change_id,
  revision: target.revision,
  head_commit: target.head_commit,
  base_commit: target.base_commit,
  main_commit: target.base_commit,
  candidate_tree: "tree",
  author_model: "fake-scripted",
  reviewer_model: "fake-scripted-b",
  reviewer_provider: "fake",
  reasoning: null,
  verdict: "unknown",
  findings: [],
  complete: false,
  problems: ["review_invalid: /private/worktree/secret.py"],
};
function reviewed(state = ready, patch: Partial<CrossReview> = {}) {
  return appendEvent(state, {
    seq: 0,
    ts: "2026-10-28T12:00:00Z",
    type: "change.reviewed",
    payload: { ...review, ...patch },
  });
}
function error(
  state: State,
  code = "review_invalid",
  revision = target.revision,
  head = target.head_commit,
) {
  return appendEvent(state, {
    seq: 0,
    ts: "2026-10-28T12:00:00Z",
    type: "error",
    payload: {
      source: "server",
      agent_id: target.agent_id,
      change_id: target.change_id,
      fatal: false,
      message: golden.template
        .replace("{revision}", String(revision))
        .replace("{head}", head)
        .replace("{code}", code)
        .replace("{reason}", "/private/worktree/secret.py; code=main_moved\nopaque"),
    },
  });
}
const current = (state: State) => {
  const found = state.changes[target.change_id];
  if (!found) throw new Error("Missing change");
  return found;
};

it.each(golden.cases)("recognizes only the complete shared golden: $message", (test) => {
  const note = parseNeedsYou(test.message);
  expect(note).toEqual(test.parsed);
  expect(
    matchesNeedsYou(note, { revision: test.current.revision, head_commit: test.current.head }),
  ).toBe(test.matches_current);
});

it.each(golden.codes)(
  "projects the owner's code %s without inventing reviewer identity",
  (code) => {
    const state = error(ready, code);
    expect(current(state).needsYou?.code).toBe(code);
    expect(current(state).crossReview).toBeNull();
    const html = renderToStaticMarkup(<CrossReviewNote change={current(state)} />);
    expect(html).toContain("Needs you");
    expect(html).not.toContain("/private");
    expect(html).not.toContain("fake-scripted");
  },
);

it("retains unknown problems while rendering safe English text without changing focus", () => {
  const state = error(reviewed());
  expect(current(state).crossReview?.problems).toEqual(review.problems);
  expect(state.focus).toBe(ready.focus);
  const html = renderToStaticMarkup(<CrossReviewNote change={current(state)} />);
  expect(html).toContain("Review unavailable");
  expect(html).not.toContain("/private");
});

it("deduplicates target/code and ignores foreign, older revision and older head diagnostics", () => {
  const state = error(ready);
  expect(error(state).errors).toHaveLength(state.errors.length);
  for (const wrong of [
    error(ready, "foreign"),
    error(ready, "ledger", 0),
    error(ready, "ledger", target.revision, "old"),
  ]) {
    expect(current(wrong).needsYou).toBeNull();
    expect(
      decisionView(
        current(wrong),
        wrong.errors,
        startAttempt(ready, target.change_id, "approve"),
        false,
      ),
    ).toEqual({ kind: "pending", action: "approve" });
  }
});

it("a terminal review receipt never releases the person's command wait", () => {
  const state = error(ready);
  expect(
    decisionView(
      current(state),
      state.errors,
      startAttempt(ready, target.change_id, "approve"),
      false,
    ).kind,
  ).toBe("pending");
  expect(
    decisionView(
      current(state),
      state.errors,
      startAttempt(state, target.change_id, "approve"),
      false,
    ).kind,
  ).toBe("pending");
});

it("shows the automatic round between revisions without a stale verdict, then the next review", () => {
  const resolved: ServerEvent = {
    seq: 0,
    ts: "2026-10-28T12:00:00Z",
    type: "change.resolved",
    payload: {
      change_id: target.change_id,
      agent_id: target.agent_id,
      revision: 1,
      head_commit: target.head_commit,
      decision: "changes_requested",
      comment: "Shorten it.",
      merge: null,
      merge_commit: null,
      conflict: null,
      reason: null,
      decided_by: "cross_review",
      reasons: ["Adverse review"],
      review_seq: 21,
      requested_by: null,
      assessment: null,
      answers: null,
    },
  };
  let state = appendEvent(
    reviewed(ready, { verdict: "changes_requested", complete: true }),
    resolved,
  );
  state = appendEvent(state, {
    seq: 0,
    ts: resolved.ts,
    type: "change.proposed",
    payload: { ...target, revision: 2, head_commit: "next" },
  });
  expect(current(state).crossReview).toBeNull();
  expect(current(state).needsYou).toBeNull();
  const betweenReviews = renderToStaticMarkup(<CrossReviewNote change={current(state)} />);
  expect(betweenReviews).toContain('aria-label="Cross-review"');
  expect(betweenReviews).toContain("One automatic Request changes round was used.");
  expect(betweenReviews).not.toMatch(
    /Changes requested|No findings|Review unavailable|fake-scripted|Needs you/,
  );
  state = reviewed(state, {
    revision: 2,
    head_commit: "next",
    verdict: "changes_requested",
    complete: true,
  });
  state = error(state, "round_exhausted", 2, "next");
  expect(current(state).reviews).toHaveLength(2);
  expect(current(state).crossReviewRound).toEqual(resolved.payload);
  const html = renderToStaticMarkup(<CrossReviewNote change={current(state)} />);
  expect(html).toContain("One automatic Request changes round was used");
  expect(html).toContain("Needs you");
  expect(current(state).status).toBe("ready_for_review");
});

it.each(["save", "decision"])(
  "human %s invalidates notes and drops late reviews without Needs you",
  (action) => {
    const state = error(reviewed());
    const source = panelEvents.find(
      (event) => event.type === (action === "save" ? "change.updated" : "change.resolved"),
    );
    if (!source) throw new Error("Missing event");
    const human =
      source.type === "change.updated"
        ? { ...source, payload: { ...source.payload, author: "user" as const } }
        : source;
    const next = reviewed(appendEvent(state, human));
    expect(current(next).personActed).toBe(true);
    expect(current(next).crossReview).toBeNull();
    expect(current(next).needsYou).toBeNull();
    expect(next.errors).toBe(state.errors);
    expect(renderToStaticMarkup(<CrossReviewNote change={current(next)} />)).toBe("");
  },
);
