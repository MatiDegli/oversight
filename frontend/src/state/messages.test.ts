import { expect, it } from "vitest";
import { applyEvent } from "./applyEvent";
import { event, run, started } from "./state.support";

it("retains accepted messages per agent in sequence, including withheld text", () => {
  const first = event(2, "agent.message", { agent_id: "agent-1", text: "Read pricing.py" });
  const before = started();
  const state = [
    first,
    event(3, "agent.message", { agent_id: "agent-2", text: "Read inventory.py" }),
    event(4, "agent.message", { agent_id: "agent-1", text: null }),
  ].reduce(applyEvent, before);
  expect(state.messages).toEqual({
    "agent-1": [
      { seq: 2, ts: first.ts, text: "Read pricing.py" },
      { seq: 4, ts: first.ts, text: null },
    ],
    "agent-2": [{ seq: 3, ts: first.ts, text: "Read inventory.py" }],
  });
  expect(before.messages).toEqual({});
  expect(
    applyEvent(state, event(1, "run.started", { ...run, run_id: "new-run" })).messages,
  ).toEqual({});
});

it("does not retain a message across a sequence gap", () => {
  const state = applyEvent(
    started(),
    event(3, "agent.message", { agent_id: "agent-1", text: null }),
  );
  expect(state.gap).toBe(true);
  expect(state.lastSeq).toBe(1);
  expect(state.messages).toEqual({});
});
