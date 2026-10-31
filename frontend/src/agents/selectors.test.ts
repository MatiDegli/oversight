import { readFileSync } from "node:fs";
import type { ServerEvent } from "@contracts";
import { describe, expect, it, vi } from "vitest";
import { applyEvent, createInitialState } from "../state";
import { ageWords } from "./age";
import { selectAgentCard } from "./selectors";

const events = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const through = (seq: number) =>
  events.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState());

describe("agent card details", () => {
  it.each([1, 4])("names the next attempt after failed attempt %s", (attempt) => {
    const state = through(18);
    state.modelWaits["agent-1"] = {
      seq: 19,
      attempt,
      max_attempts: 5,
      cause: "timeout",
      wait_s: 2,
    };
    for (const status of ["working", "revising", "halted", "ready_for_review", "done"] as const) {
      const agent = state.agents["agent-1"];
      if (!agent) throw new Error("Missing fixture");
      const current = { ...state, agents: { "agent-1": { ...agent, state: status } } };
      const data = selectAgentCard(current, "agent-1");
      const baseline = selectAgentCard({ ...current, modelWaits: {} }, "agent-1");
      if (status === "working" || status === "revising") {
        expect(data?.activityLabel).toBe("Waiting");
        expect(data?.activity).toBe(`Waiting for model (retry ${String(attempt + 1)}/5)`);
      } else expect(data).toEqual(baseline);
    }
    const step = events.find((event) => event.type === "agent.step");
    if (!step) throw new Error("Missing fixture");
    const resumed = applyEvent(state, { ...step, seq: 19 });
    expect(selectAgentCard(resumed, "agent-1")?.activityLabel).toBe("Working");
    expect(selectAgentCard(resumed, "agent-1")?.activity).not.toContain("Waiting for model");
  });

  it("gets Step n of N from tool results and the run limit, even when status disagrees", () => {
    const state = through(18);
    const agent = state.agents["agent-1"];
    if (!agent || !state.run) throw new Error("Missing fixture");
    const data = selectAgentCard(
      {
        ...state,
        run: { ...state.run, limits: { ...state.run.limits, max_steps: 25 } },
        agents: { "agent-1": { ...agent, step: 0, max_steps: 99 } },
      },
      "agent-1",
    );
    expect(data?.step).toBe(5);
    expect(data?.maxSteps).toBe(25);
    expect(selectAgentCard(createInitialState(), "absent")).toBeUndefined();
  });

  it("keeps unknown cost unknown, no tick absent, and measured zero zero", () => {
    const state = through(18);
    for (const cost of [null, undefined, 0, 0.001]) {
      expect(
        selectAgentCard(
          { ...state, agentCosts: cost === undefined ? {} : { "agent-1": cost } },
          "agent-1",
        )?.cost,
      ).toBe(cost);
    }
  });

  it("reads step age from the event clock, independently of the reader's clock", () => {
    const state = through(18);
    const data = selectAgentCard(state, "agent-1");
    if (!data) throw new Error("Missing fixture");
    const age = () => ageWords(data.stepTimes[1], data.now);
    const expected = age();
    const clock = vi.spyOn(Date, "now").mockReturnValue(Date.UTC(2040, 1, 1));
    try {
      expect(age()).toBe(expected);
      expect(expected).toBe("9 s ago");
    } finally {
      clock.mockRestore();
    }
  });

  it("uses the latest tool summary rather than status detail or stale edit focus", () => {
    const state = through(18);
    const agent = state.agents["agent-1"];
    if (!agent) throw new Error("Missing fixture");
    const data = selectAgentCard(
      { ...state, agents: { "agent-1": { ...agent, detail: "Model says it is editing" } } },
      "agent-1",
    );
    expect(data?.activity).toBe(data?.steps[0]?.summary);
    expect(data?.activity).not.toContain("Model says");
    expect(data?.activityLabel).toBe("Working");
  });

  it("uses focus before the first completed tool and labels successful read/edit tools", () => {
    const state = through(18);
    const focus = state.focus["agent-1"];
    if (!focus) throw new Error("Missing fixture");
    const data = selectAgentCard({ ...state, steps: {} }, "agent-1");
    expect(data?.activity).toContain(focus.ranges[0]?.path);
    expect(data?.activity).toMatch(/Editing|Reading/);
    for (const [tool, label] of [
      ["edit_file", "Editing"],
      ["read_file", "Reading"],
    ]) {
      const step = state.steps["agent-1"]?.[0];
      if (!step || !tool) throw new Error("Missing fixture");
      expect(
        selectAgentCard({ ...state, steps: { "agent-1": [{ ...step, tool }] } }, "agent-1")
          ?.activityLabel,
      ).toBe(label);
      expect(
        selectAgentCard(
          { ...state, steps: { "agent-1": [{ ...step, tool, result: "error" }] } },
          "agent-1",
        )?.activityLabel,
      ).toBe("Working");
    }
  });
});
