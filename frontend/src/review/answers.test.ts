import { beforeEach, expect, it, vi } from "vitest";
import log from "../../../fixtures/events/handwritten-inbox-grants.jsonl?raw";
import { applyEvent, createInitialState } from "../state";
import { createCommandClient, parseEvent } from "../transport";
import { candidateKey, currentCards, answeredCards, useAnswers, cardKey } from "./answers";
import { sendDecision } from "./decisions";
const state = log.trim().split("\n").map(parseEvent).reduce(applyEvent, createInitialState());
function required<T>(value: T | null | undefined): T {
  if (value == null) throw new Error("Missing fixture value");
  return value;
}
const id = "change-3";
const cards = currentCards(state, id);
const client = { approveChange: vi.fn(), requestChanges: vi.fn(), rejectChange: vi.fn() };
beforeEach(() => {
  useAnswers.setState({ drafts: {}, grants: {} });
  vi.clearAllMocks();
});
const answer = (index: number, value: "yes" | "no" | "unsure") => {
  useAnswers.getState().answer(candidateKey(state, id), cardKey(required(cards[index])), value);
};
const target = {
  revision: required(required(state.changes[id]).proposal).revision,
  head_commit: required(required(state.changes[id]).proposal).head_commit,
};
it("sends exact published cards with person answers in publication order", async () => {
  answer(1, "yes");
  answer(0, "yes");
  await sendDecision(client, state, id, "approve", "", vi.fn());
  expect(client.approveChange).toHaveBeenCalledWith(id, {
    ...target,
    answers: cards.map((card) => ({ ...card, answer: "yes", answered_by: "person" })),
  });
});
it.each(["request-changes", "reject"] as const)(
  "sends partial %s answers, and omits the field with none",
  async (action) => {
    const fn = action === "reject" ? client.rejectChange : client.requestChanges;
    await sendDecision(client, state, id, action, "Revise", vi.fn());
    expect(fn).toHaveBeenLastCalledWith(id, { ...target, comment: "Revise" });
    answer(1, "no");
    await sendDecision(client, state, id, action, "Revise", vi.fn());
    expect(fn).toHaveBeenLastCalledWith(id, {
      ...target,
      comment: "Revise",
      answers: [{ ...cards[1], answer: "no", answered_by: "person" }],
    });
  },
);
it("starts unanswered on a new revision or head, while both surfaces share the current answers", () => {
  answer(0, "yes");
  expect(answeredCards(state, id)).toHaveLength(1);
  for (const update of [{ revision: target.revision + 1 }, { head_commit: "new-head" }]) {
    const next = structuredClone(state);
    Object.assign(required(required(next.changes[id]).proposal), update);
    next.changeFacts[id] = required(next.changeFacts[id]).map((facts) => ({ ...facts, ...update }));
    expect(answeredCards(next, id)).toEqual([]);
  }
});
it("never sends Approve with unanswered, No or Unsure cards", async () => {
  for (const value of [undefined, "no", "unsure"] as const) {
    if (value) answer(0, value);
    await sendDecision(client, state, id, "approve", "", vi.fn());
  }
  expect(client.approveChange).not.toHaveBeenCalled();
});
it.each(["module", "run"] as const)("sends a %s grant only with Approve", async (kind) => {
  const change = required(state.changes["change-2"]);
  const grant = {
    reason: "possible_callers" as const,
    scope: {
      kind,
      value: kind === "module" ? "shop.pricing" : required(state.run).run_id,
      expires: "run" as const,
    },
  };
  useAnswers.getState().grant(candidateKey(state, change.change_id), grant);
  await sendDecision(client, state, change.change_id, "approve", "", vi.fn());
  expect(client.approveChange).toHaveBeenLastCalledWith(change.change_id, {
    revision: required(change.proposal).revision,
    head_commit: required(change.proposal).head_commit,
    grant,
  });
  await sendDecision(client, state, change.change_id, "reject", "", vi.fn());
  expect(client.rejectChange.mock.calls[0]?.[1]).not.toHaveProperty("grant");
});
it("revokes through the encoded path without a body", async () => {
  const request = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 202 }));
  await createCommandClient(request).revokeGrant("grant /1");
  expect(request).toHaveBeenCalledWith("/grants/grant%20%2F1/revoke", {
    method: "POST",
    signal: undefined,
  });
});
