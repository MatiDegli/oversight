import { readFileSync } from "node:fs";

import type { ServerEvent, SuiteResult } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { applyEvent, createInitialState, selectAgentCard } from "../state";
import type { State } from "../state";
import { AgentCard } from "./AgentCard";

const events = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const through = (seq: number) =>
  events.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState());

function render(state: State, onReview?: () => void) {
  const data = selectAgentCard(state, "agent-1");
  if (!data) throw new Error("Fixture needs agent-1");
  return renderToStaticMarkup(<AgentCard data={data} onReview={onReview} />);
}

/** The text of the card's summary, tags removed: what a person reads, in order. */
function summary(html: string): string {
  const block = /<dl[^>]*>.*?<\/dl>/s.exec(html)?.[0] ?? "";
  return block
    .replace(/<[^>]*>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** The fixture's ready state with the proposal's tests replaced. */
function readyWith(tests: Partial<SuiteResult>): State {
  const state = through(20);
  const change = state.changes["change-1"];
  if (!change?.proposal) throw new Error("Fixture needs the proposal");
  return {
    ...state,
    changes: {
      ...state.changes,
      "change-1": {
        ...change,
        proposal: { ...change.proposal, tests: { ...change.proposal.tests, ...tests } },
      },
    },
  };
}

describe("AgentCard over the level 1 fixture", () => {
  it("renders the running task, letter, status and every tool result", () => {
    const running = render(through(4));
    expect(running).toContain("Working");
    expect(running).toContain("Cap the combined discount at 30% in compute_price");
    expect(running).toContain('aria-label="Agent A"');
    expect(running).toContain("text-agent-a-text");
    expect(running).toContain("No steps yet.");
    for (const event of events) {
      if (event.type !== "agent.step") continue;
      const html = render(through(event.seq));
      expect(html).toContain(`Step ${String(event.payload.step)}`);
      expect(html).toContain(event.payload.tool);
      expect(html).toContain(event.payload.summary);
      expect(html).toContain(`${String(event.payload.duration_ms)} ms`);
      expect(html).toContain("Ok");
    }
  });

  it("adds Ready for review only on agent.status, and keeps steps newest first", () => {
    const working = render(through(19));
    expect(working).not.toContain("Ready for review");
    expect(working).not.toContain('role="status"');
    expect(/<article[^>]*>/.exec(working)?.[0]).not.toContain("border-agent-a");
    expect(working).toContain("bg-card");
    expect(working).toContain("border-card-border");
    expect(working).toContain("bg-raised");
    expect(working).toContain("bg-agent-a-tint text-agent-a-text");
    expect(working.indexOf("Step 5")).toBeLessThan(working.indexOf("Step 1"));
    const ready = render(through(20));
    expect(ready).toContain('role="status"');
    expect(ready).toContain("Ready for review");
    expect(ready).toContain("border-agent-a");
    expect(render(through(23))).not.toContain("Ready for review");
  });

  it("shows each step's age against the latest event, newest first, and moves it as events arrive", () => {
    // Steps arrived at :16.975, :18.975, :21.925, :24.305 and :26.665; the latest event is the last.
    const ages = (html: string) =>
      [...html.matchAll(/text-muted">((?:now|\d+ s ago|\d+ min ago))<\/span>/g)].map((m) => m[1]);
    expect(ages(render(through(18)))).toEqual(["now", "now", "now", "7 s ago", "9 s ago"]);
    // Twenty seconds on (the done status at :46.255) every step has aged and none says "now".
    expect(ages(render(through(23)))).toEqual([
      "19 s ago",
      "21 s ago",
      "24 s ago",
      "27 s ago",
      "29 s ago",
    ]);
    // A step with no recorded time says nothing rather than a wrong age.
    const state = { ...through(18), stepTimes: {} };
    expect(ages(render(state))).toEqual([]);
  });

  it("keeps the duration and the result word under the summary, away from the age", () => {
    const html = render(through(18));
    expect(html).toMatch(/Ok<span class="font-mono text-muted"> · \d+ ms<\/span>/);
  });

  it("names the model as the run does, not by its id", () => {
    const html = render(through(4));
    expect(html).toContain("Muse Spark");
    expect(html).not.toContain(">muse-spark<");
  });

  it("shows a lamp filled in the agent's color only when the change is ready", () => {
    const lamp = (html: string) =>
      /<span aria-hidden="true" class="[^"]*rounded-full[^"]*">/.exec(html)?.[0];
    expect(lamp(render(through(19)))).toContain("bg-card");
    expect(lamp(render(through(19)))).toContain("animate-pulse");
    expect(lamp(render(through(20)))).toContain("bg-agent-a");
    expect(lamp(render(through(20)))).not.toContain("animate-pulse");
    expect(lamp(render(through(20)))).not.toContain("bg-card");
  });

  it("renders an error step and the halted reason, and identifies slot B in words and color", () => {
    let state = through(10);
    const previous = state.steps["agent-1"]?.[1];
    const agent = state.agents["agent-1"];
    if (!previous || !agent) throw new Error("Fixture needs step and agent");
    state = applyEvent(state, {
      seq: 11,
      ts: events[0]?.ts ?? "",
      type: "agent.step",
      payload: { ...previous, step: 3, result: "error", error: "File changed; read it again" },
    });
    state = applyEvent(state, {
      seq: 12,
      ts: events[0]?.ts ?? "",
      type: "agent.status",
      payload: { ...agent, slot: "B", state: "halted", detail: "Step limit reached", step: 3 },
    });
    const html = render(state);
    expect(html).toContain("Halted");
    expect(html).toContain("Step limit reached");
    expect(html).toContain("Error: File changed; read it again");
    expect(html).toContain("text-failed-text");
    expect(html).toContain('aria-label="Agent B"');
    expect(html).toContain("text-agent-b-text");
    expect(html).not.toContain("Ready for review");
  });

  it("summarizes a ready change with tests, change, steps and cost instead of the step list", () => {
    const ready = render(through(20));
    expect(summary(ready)).toBe(
      "Tests 28 of 28 pass Change +2 −0 in 1 file Steps 5 of 30 Cost US$0.01",
    );
    expect(ready).not.toContain('aria-label="Agent A steps, newest first"');
    expect(ready).not.toContain('role="progressbar"');
    expect(ready).toContain('aria-label="Agent A summary"');
    // A check beside the words and green on the count: never color alone.
    expect(ready).toMatch(/lucide-circle-check[^>]*text-passed/);
    expect(ready).toContain("text-passed");
  });

  it("tells failed tests with a cross and the count, and a run that did not finish in words", () => {
    const failed = render(readyWith({ outcome: "failed", passed: 26, failed: 2 }));
    expect(summary(failed)).toContain("Tests 26 of 28 pass");
    expect(failed).toMatch(/lucide-circle-x[^>]*text-failed-text/);
    expect(failed).not.toContain("lucide-circle-check");
    expect(summary(render(readyWith({ outcome: "passed", passed: 26, skipped: 3 })))).toContain(
      "26 of 26 pass, 3 skipped",
    );
    expect(summary(render(readyWith({ outcome: "error", passed: 0 })))).toContain(
      "Tests Could not run",
    );
    expect(summary(render(readyWith({ outcome: "timeout", passed: 0 })))).toContain(
      "Tests Timed out",
    );
  });

  it("colors a line count only when there is something added or removed", () => {
    // The fixture adds 2 and removes 0: a zero removal is not a removal, so it is not red.
    const html = render(through(20));
    expect(html).toMatch(/font-bold text-passed">\+2</);
    expect(html).toMatch(/text-muted">−0</);
    expect(html).not.toMatch(/text-failed-text[^>]*>−0</);
  });

  it("titles the card one level under the panel's heading", () => {
    expect(render(through(4))).toMatch(/<h3 [^>]*>Agent A<\/h3>/);
  });

  it("pluralizes the files and shows a removal count", () => {
    const state = through(20);
    const change = state.changes["change-1"];
    if (!change?.proposal) throw new Error("Fixture needs the proposal");
    const proposal = {
      ...change.proposal,
      lines_added: 26,
      lines_removed: 1,
      files: [...change.proposal.files, ...change.proposal.files],
    };
    const html = render({
      ...state,
      changes: { ...state.changes, "change-1": { ...change, proposal } },
    });
    expect(summary(html)).toContain("Change +26 −1 in 2 files");
    expect(html).toMatch(/font-bold text-failed-text">−1</);
  });

  it("shows no summary of tests or lines until the proposal has arrived, and no cost before a tick", () => {
    const state = through(20);
    const proposed = { ...state, changes: {} };
    expect(summary(render(proposed))).toBe("Steps 5 of 30 Cost US$0.01");
    expect(summary(render({ ...proposed, agentCosts: {} }))).toBe("Steps 5 of 30");
  });

  it("shows an unreported cost as unknown, never as a zero", () => {
    const state = through(20);
    expect(summary(render({ ...state, agentCosts: { "agent-1": null } }))).toContain(
      "Cost Unknown",
    );
    const working = render({ ...through(11), agentCosts: { "agent-1": null } });
    expect(working).toContain("Cost unknown");
    expect(working).not.toContain("US$0.00");
  });

  it("puts the cost so far beside the step count while working, and keeps a small cost off zero", () => {
    // 0.005296 so far rounds to a cent; 0.001264 is under half a cent.
    expect(render(through(11))).toMatch(/title="Cost so far"[^>]*>US\$0\.01</);
    expect(render(through(5))).toMatch(/title="Cost so far"[^>]*>&lt;US\$0\.01</);
  });

  it("offers Review change in the agent's color only when the change is ready and can be opened", () => {
    expect(render(through(20))).not.toContain("Review change");
    const ready = render(through(20), () => undefined);
    const button = /<button [^>]*>.*?<\/button>/s.exec(ready)?.[0] ?? "";
    expect(button).toContain("Review change");
    expect(button).toContain("bg-agent-a");
    expect(button).toContain("text-on-agent");
    expect(button).toContain("lucide-eye");
    expect(render(through(19), () => undefined)).not.toContain("Review change");
  });
});

it("offers Stop and discard on working, revising, ready and halted cards with a command", () => {
  const data = selectAgentCard(through(20), "agent-1");
  if (!data) throw new Error("Missing agent");
  for (const state of [
    "working",
    "revising",
    "ready_for_review",
    "done",
    "stopped",
    "halted",
    "paused",
    "starting",
  ] as const) {
    const html = renderToStaticMarkup(
      <AgentCard
        data={{ ...data, agent: { ...data.agent, state } }}
        onStop={() => Promise.resolve()}
      />,
    );
    expect(html.includes("Stop and discard")).toBe(
      ["working", "revising", "ready_for_review", "halted"].includes(state),
    );
    expect(html).not.toContain("Confirm stop and discard");
  }
  expect(renderToStaticMarkup(<AgentCard data={data} />)).not.toContain("Stop and discard");
});
