import { readFileSync } from "node:fs";

import type { AgentStatus, ChangeResolved, ServerEvent } from "@contracts";
import { describe, expect, it, vi } from "vitest";

import { applyEvent, createInitialState, selectSpend } from "../state";
import type { State } from "../state";
import { createCommandClient } from "../transport";
import {
  createTaskBoxStore,
  nextSlot,
  selectedSlot,
  slotChoice,
  slotUsage,
  startBlock,
  startFailure,
} from "./taskBox";

// The level 1 log: agent A starts at seq 3, proposes at 19, is ready at 20, approved and merged at
// 21, done at 23.
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

function agentState(state: State, agentState: AgentStatus["state"]) {
  const agent = state.agents["agent-1"];
  if (!agent) throw new Error("The fixture has agent-1 from seq 3");
  return applyEvent(state, {
    seq: state.lastSeq + 1,
    ts,
    type: "agent.status",
    payload: { ...agent, state: agentState },
  });
}
const resolved = (state: State, payload: Partial<ChangeResolved>) =>
  applyEvent(state, {
    seq: state.lastSeq + 1,
    ts,
    type: "change.resolved",
    payload: {
      review_seq: null,
      requested_by: null,
      head_commit: null,
      change_id: "change-1",
      agent_id: "agent-1",
      revision: 1,
      decision: "rejected",
      reason: null,
      comment: null,
      merge: null,
      merge_commit: null,
      conflict: null,
      decided_by: null,
      reasons: [],
      assessment: null,
      answers: null,
      ...payload,
    },
  });
/** The same run with only these slots, as the level 1 backend announces it: slot A. */
function withSlots(state: State, slots: ("A" | "B")[]): State {
  if (!state.run) throw new Error("The fixture starts with run.started");
  const model = state.run.slots[0]?.default_model ?? "muse-spark";
  return {
    ...state,
    run: { ...state.run, slots: slots.map((slot) => ({ slot, default_model: model })) },
  };
}

const levelOne = (seq: number) => withSlots(through(seq), ["A"]);

describe("slotUsage", () => {
  it("counts the slots that hold an agent, until its change is resolved", () => {
    expect(slotUsage(createInitialState())).toBeNull();
    expect(slotUsage(through(2))).toEqual({ held: 0, total: 2 });
    expect(slotUsage(through(14))).toEqual({ held: 1, total: 2 });
    // Waiting for review still holds the slot; the merge frees it.
    expect(slotUsage(through(20))).toEqual({ held: 1, total: 2 });
    expect(slotUsage(through(21))).toEqual({ held: 0, total: 2 });
    expect(slotUsage(levelOne(14))).toEqual({ held: 1, total: 1 });
  });
});

describe("nextSlot (K14)", () => {
  const bothHeld = () => {
    const state = through(14);
    const a = state.agents["agent-1"];
    if (!a) throw new Error("Missing agent fixture");
    return {
      ...state,
      agents: {
        ...state.agents,
        "agent-2": {
          ...a,
          agent_id: "agent-2",
          change_id: "change-2",
          slot: "B" as const,
        },
      },
    };
  };

  it("takes B while A is held, none while both are held, and A after its merge", () => {
    expect(nextSlot(createInitialState())).toBeNull();
    expect(nextSlot(through(14))).toBe("B");
    expect(nextSlot(bothHeld())).toBeNull();
    expect(nextSlot(through(21))).toBe("A");
    expect(startBlock(bothHeld(), "B")).toBe("Agent B is working.");
  });

  it.each(["baseten", "fake", "meta"] as const)(
    "skips a braked Meta A for a %s B; if both are Meta it keeps A selectable",
    (provider) => {
      const state = through(2);
      if (!state.run) throw new Error("Missing run fixture");
      state.run = {
        ...state.run,
        budget: { ...state.run.budget, spent_usd: 149 },
        models: [
          { id: "meta", label: "Muse Spark", provider: "meta", model: "meta", reasoning: null },
          { id: "b", label: "B model", provider, model: "b", reasoning: null },
        ],
        slots: [
          { slot: "A", default_model: "meta" },
          { slot: "B", default_model: "b" },
        ],
      };
      expect(nextSlot(state)).toBe(provider === "meta" ? "A" : "B");
      expect(startBlock(state, "A")).toBeNull();
      expect(selectedSlot(state, slotChoice(state, "A"))).toBe("A");
    },
  );

  it("keeps the person's override until that slot is taken, including while the box is folded", () => {
    const free = through(2);
    if (!free.run) throw new Error("Missing run fixture");
    const choice = slotChoice(free, "B");
    expect(selectedSlot(free, choice)).toBe("B");
    expect(selectedSlot(through(14), choice)).toBe("B");
    expect(selectedSlot(bothHeld(), choice)).toBeNull();
    const b = bothHeld().agents["agent-2"];
    const released = {
      ...through(21),
      agents: { ...through(21).agents, "agent-2": { ...b, state: "done" as const } },
    };
    expect(selectedSlot(released, choice)).toBe("A");
    expect(selectedSlot(withSlots(free, ["A"]), choice)).toBe("A");
    expect(selectedSlot({ ...free, run: { ...free.run, run_id: "new-run" } }, choice)).toBe("A");
  });
});

// The recorded log behind the braked-A scene and browser test: B's tick, from a non-Meta model,
// carries the shared ledger's total and brake; B's change resolves at seq 6.
describe("the recorded braked log", () => {
  const braked = readFileSync(
    new URL("../../../fixtures/events/handwritten-two-slots-braked.jsonl", import.meta.url),
    "utf8",
  )
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as ServerEvent);
  const upTo = (seq: number) =>
    braked.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState());

  it("preselects B once its change is resolved, with the ledger's total read from B's tick", () => {
    const state = upTo(6);
    expect(selectSpend(state)).toEqual({
      spentUsd: 136,
      budgetUsd: 150,
      brake: "blocked",
    });
    expect(startBlock(state, "B")).toBeNull();
    expect(nextSlot(state)).toBe("B");
    expect(selectedSlot(state, null)).toBe("B");
  });

  it("keeps B held, so A is preselected, while its change is unresolved", () => {
    expect(startBlock(upTo(4), "B")).toBe("Agent B is working.");
    expect(nextSlot(upTo(4))).toBe("A");
  });
});

describe("why a start is blocked", () => {
  it("is free once the server is up and before any agent exists", () => {
    expect(startBlock(levelOne(2))).toBeNull();
  });

  it("waits for run.started: no slots are known before it", () => {
    expect(startBlock(createInitialState())).toBe("Waiting for the server.");
  });

  it("names the agent that holds the only slot, by what it is doing", () => {
    expect(startBlock(levelOne(3))).toBe("Agent A is starting.");
    expect(startBlock(levelOne(4))).toBe("Agent A is working.");
    expect(startBlock(levelOne(14))).toBe("Agent A is working.");
    expect(startBlock(levelOne(20))).toBe("Agent A is waiting for your review.");
    expect(startBlock(agentState(levelOne(14), "revising"))).toBe(
      "Agent A is revising its change.",
    );
    expect(startBlock(agentState(levelOne(14), "halted"))).toBe(
      "Agent A is halted and waiting for your review.",
    );
  });

  it("frees the slot when the change is merged, rejected or the agent was stopped", () => {
    expect(startBlock(levelOne(21))).toBeNull();
    expect(startBlock(levelOne(23))).toBeNull();
    const rejected = resolved(levelOne(14), { decision: "rejected" });
    expect(startBlock(rejected)).toBeNull();
    const stopped = resolved(agentState(levelOne(14), "stopped"), {
      decision: "rejected",
      reason: "stopped",
    });
    expect(startBlock(stopped)).toBeNull();
  });

  it("keeps the slot held while a merge conflict waits for the person", () => {
    const conflict = resolved(levelOne(20), {
      decision: "approved",
      merge: "conflict",
      conflict: { kind: "merge", paths: ["shop/pricing.py"], tests: null },
    });
    expect(startBlock(conflict)).toBe("Agent A is waiting for your review.");
  });

  it("is free while any slot is free, and lists every holder when none is", () => {
    // The fixture announces both slots: B stays free while A works.
    const two = through(14);
    expect(startBlock(two)).toBeNull();
    const agent = two.agents["agent-1"];
    if (!agent) throw new Error("The fixture has agent-1");
    const both = applyEvent(two, {
      seq: two.lastSeq + 1,
      ts,
      type: "agent.status",
      payload: {
        ...agent,
        agent_id: "agent-2",
        slot: "B",
        change_id: "change-2",
        state: "ready_for_review",
      },
    });
    expect(startBlock(both)).toBe("Agent A is working. Agent B is waiting for your review.");
  });
});

const created = { agent_id: "agent-1", change_id: "change-1", slot: "A" };
const reply = (status: number, body: unknown) =>
  new Response(typeof body === "string" ? body : JSON.stringify(body), { status });
const refusal = (status: number, code: string, message: string) =>
  reply(status, { error: { code, message } });

/** A store wired to the real command client over a fake fetch, as the app wires it. */
function setup(answer: () => Promise<Response>) {
  const request = vi.fn<typeof fetch>(answer);
  const client = createCommandClient(request);
  const store = createTaskBoxStore();
  const start = () => store.getState().start(client.startAgent);
  return { request, store, start };
}

describe("starting an agent from the box", () => {
  it("sends the person's chosen slot without a model override, and preserves the choice across a refusal", async () => {
    const { request, store } = setup(() =>
      Promise.resolve(refusal(403, "spend_brake", "Meta credits are blocked.")),
    );
    const choice = slotChoice(through(2), "A");
    store.getState().choose(choice);
    store.getState().setDraft("  Keep this task  ");
    await store.getState().start(createCommandClient(request).startAgent, "A");
    expect(JSON.parse(request.mock.calls[0]?.[1]?.body as string)).toEqual({
      task: "Keep this task",
      slot: "A",
    });
    expect(store.getState()).toMatchObject({
      choice,
      draft: "  Keep this task  ",
      failure: "Meta credits are blocked.",
    });
    store.getState().choose(slotChoice(through(2), "B"));
    expect(store.getState().failure).toBeNull();
  });
  it("sends the trimmed task and nothing else, to POST /agents", async () => {
    const { request, store, start } = setup(() => Promise.resolve(reply(201, created)));
    store.getState().setDraft("  Cap the discount at 30%\n\n  ");
    await start();
    expect(request).toHaveBeenCalledTimes(1);
    const [path, init] = request.mock.calls[0] ?? [];
    expect(path).toBe("/agents");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ task: "Cap the discount at 30%" }));
  });

  it("sends nothing for a blank or whitespace task", async () => {
    const { request, store, start } = setup(() => Promise.resolve(reply(201, created)));
    await start();
    for (const draft of ["", " ", "\n\t \n"]) {
      store.getState().setDraft(draft);
      await start();
    }
    expect(request).not.toHaveBeenCalled();
    expect(store.getState().pending).toBe(false);
  });

  it("is pending until the answer, sends once, and clears the box on 201", async () => {
    let answer: (response: Response) => void = () => undefined;
    const { request, store, start } = setup(
      () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    );
    store.getState().setDraft("Cap the discount");
    const first = start();
    expect(store.getState().pending).toBe(true);
    await start();
    expect(request).toHaveBeenCalledTimes(1);
    answer(reply(201, created));
    await first;
    expect(store.getState()).toMatchObject({ draft: "", pending: false, failure: null });
  });

  it.each([
    ["spend_brake", 403, "Spending is above 90% of the budget; new runs are refused."],
    ["slot_busy", 409, "Slot A is busy."],
    ["wrong_state", 409, "Main is suspect; check the workspace first."],
    ["invalid_request", 422, "task must not be empty."],
  ])(
    "shows a %s refusal's message, keeps the task and starts again after it",
    async (code, status, message) => {
      let attempt = 0;
      const { request, store, start } = setup(() =>
        Promise.resolve(++attempt === 1 ? refusal(status, code, message) : reply(201, created)),
      );
      store.getState().setDraft("Cap the discount");
      await start();
      expect(store.getState()).toMatchObject({ draft: "Cap the discount", pending: false });
      expect(store.getState().failure).toBe(message);
      await start();
      expect(request).toHaveBeenCalledTimes(2);
      expect(store.getState()).toMatchObject({ draft: "", pending: false, failure: null });
    },
  );

  it("shows a 500's message and a proxy's bare HTTP line, and re-enables", async () => {
    const cases: [Response, string][] = [
      [refusal(500, "internal", "Boom."), "The server failed to start the agent: Boom."],
      [reply(502, "<html>Bad Gateway</html>"), "The server failed to start the agent: HTTP 502"],
    ];
    for (const [response, line] of cases) {
      const { store, start } = setup(() => Promise.resolve(response));
      store.getState().setDraft("Cap the discount");
      await start();
      expect(store.getState()).toMatchObject({ draft: "Cap the discount", pending: false });
      expect(store.getState().failure).toContain(line);
    }
  });

  it("says a network failure did not reach the server, and re-enables", async () => {
    const { store, start } = setup(() => Promise.reject(new TypeError("Failed to fetch")));
    store.getState().setDraft("Cap the discount");
    await start();
    expect(store.getState()).toMatchObject({ draft: "Cap the discount", pending: false });
    expect(store.getState().failure).toBe(
      "The request did not reach the server. Check the connection and try again.",
    );
    expect(startFailure(new Error("anything else"))).toContain("did not reach the server");
  });

  it("drops a failure's line when the person edits the task", async () => {
    const { store, start } = setup(() => Promise.resolve(refusal(409, "slot_busy", "Busy.")));
    store.getState().setDraft("Cap the discount");
    await start();
    expect(store.getState().failure).toBe("Busy.");
    store.getState().setDraft("Cap the discount at 30%");
    expect(store.getState().failure).toBeNull();
  });
});
