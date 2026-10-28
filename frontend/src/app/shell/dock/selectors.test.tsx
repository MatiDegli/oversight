import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { SuiteResult } from "@contracts";
import { createInitialState } from "../../../state";
import { through } from "../../replay.fixture";
import { LOG_LIMIT, selectActivity, selectTestRuns } from "./selectors";
import { Tests } from "./Tests";
import { Activity } from "./Activity";

const suite: SuiteResult = {
  outcome: "failed",
  passed: 9,
  failed: 2,
  skipped: 0,
  duration_ms: 1250,
  failed_tests: ["tests/test_cart.py::test_total", "tests/test_cart.py::test_discount"],
  cases: null,
  cases_complete: null,
};

it("selects the latest execution per change and preserves other agents and phases", () => {
  const execution = {
    change_id: "change-1",
    agent_id: "agent-1",
    revision: 1,
    phase: "agent" as const,
    tested_commit: null,
    tested_tree: "tree",
    suite,
  };
  const state = {
    ...through(22),
    testExecutions: {
      50: { ...execution, phase: "combined" as const, suite: { ...suite, passed: 10 } },
      30: execution,
      40: { ...execution, agent_id: "agent-2", change_id: "change-2" },
    },
  };
  const runs = selectTestRuns(state);
  expect(runs).toHaveLength(2);
  expect(runs.find((run) => run.id === "change-1")).toMatchObject({
    label: "change-1 · combined",
    suite: { passed: 10 },
  });
  expect(runs.find((run) => run.id === "change-2")?.suite).toEqual(suite);
});

it("supports old recordings, the latest agent step, and the state before any tests", () => {
  expect(selectTestRuns(through(22))[0]?.suite).toEqual(
    through(22).changes["change-1"]?.proposal?.tests,
  );
  const state = through(22);
  state.steps = {
    "agent-1": [
      {
        agent_id: "agent-1",
        step: 1,
        tool: "run_tests",
        summary: "Tests",
        result: "ok",
        error: null,
        duration_ms: 2,
        tests: suite,
      },
      {
        agent_id: "agent-1",
        step: 2,
        tool: "run_tests",
        summary: "Tests",
        result: "ok",
        error: null,
        duration_ms: 3,
        tests: { ...suite, passed: 12 },
      },
    ],
  };
  // With no proposal or execution, the latest agent step is the fallback.
  state.changes = {};
  expect(selectTestRuns(state)[0]?.suite?.passed).toBe(12);
  expect(selectTestRuns(createInitialState())).toEqual([]);
  expect(renderToStaticMarkup(<Tests state={createInitialState()} />)).toContain(
    "Test results will appear here.",
  );
});

it.each(["passed", "failed", "error", "timeout"] as const)(
  "renders %s with counts, duration, named failures and icons",
  (outcome) => {
    const state = through(22);
    const change = state.changes["change-1"];
    if (!change?.proposal) throw new Error("Missing proposal");
    change.proposal = { ...change.proposal, tests: { ...suite, outcome } };
    state.steps = {};
    const html = renderToStaticMarkup(<Tests state={state} />);
    expect(html).toContain(` ${outcome}</span>`);
    expect(html).toContain('class="dock-number">9</span>');
    expect(html).toContain('class="dock-number">2</span>');
    expect(html).toContain("1.25 s");
    for (const name of suite.failed_tests) expect(html).toContain(name);
    expect(html.match(/aria-label="Failed"/g)).toHaveLength(2);
    expect(html).toContain(`lucide-${outcome === "passed" ? "check" : "x"}`);
    expect(html).toContain('data-slot="A"');
  },
);

it("orders two agents' steps chronologically, caps them and states how much is omitted", () => {
  const state = createInitialState();
  for (const [agent, offset] of [
    ["b", 1],
    ["a", 0],
  ] as const) {
    state.steps[agent] = Array.from({ length: 400 }, (_, i) => ({
      agent_id: agent,
      step: i,
      tool: "read_file",
      summary: String(i),
      result: "ok",
      error: null,
      duration_ms: i,
      tests: null,
    }));
    state.stepSeqs[agent] = Object.fromEntries(
      state.steps[agent].map((step) => [step.step, step.step * 2 + offset + 1]),
    );
    state.stepTimes[agent] = Object.fromEntries(
      state.steps[agent].map((step) => [
        step.step,
        new Date((step.step * 2 + offset) * 1000).toISOString(),
      ]),
    );
  }
  const activity = selectActivity(state);
  expect(activity.omitted).toBe(800 - LOG_LIMIT);
  expect(activity.entries).toHaveLength(LOG_LIMIT);
  expect(activity.entries[0]?.id).toBe("step:a:250");
  expect(activity.entries.at(-1)?.id).toBe("step:b:399");
  expect(activity.entries.map((entry) => entry.time)).toEqual(
    activity.entries.map((entry) => entry.time).sort(),
  );
});

it.each([
  ["approved", "merged", "Approved · merged"],
  ["approved", "conflict", "Approved · conflict"],
  ["changes_requested", null, "Changes requested"],
  ["rejected", null, "Rejected"],
] as const)("keeps %s/%s, proposals and errors visibly undated", (decision, merge, title) => {
  const state = through(22);
  const change = state.changes["change-1"];
  if (!change?.resolution) throw new Error("Missing resolution");
  change.resolution = { ...change.resolution, decision, merge, comment: "Review comment" };
  change.proposalEvent = { seq: 19 };
  change.resolutionEvent = { seq: 21 };
  state.errorEvents = [{ seq: 23 }];
  state.errors = [
    { source: "tools", message: "Read failed", agent_id: "agent-1", change_id: null, fatal: false },
  ];
  const entries = selectActivity(state).entries;
  expect(entries.find((entry) => entry.id === "proposal:change-1")).toMatchObject({
    time: null,
    agentId: "agent-1",
  });
  expect(entries.find((entry) => entry.id === "resolution:change-1")).toMatchObject({
    time: null,
    title: `${title} · change-1`,
    summary: "Review comment",
  });
  expect(entries.at(-1)).toMatchObject({
    title: "Error · tools",
    summary: "Read failed",
    failed: true,
    time: null,
  });
});

it("retains step tool, summary, error, duration and its source time", () => {
  const state = through(18);
  state.steps = {
    "agent-1": [
      {
        agent_id: "agent-1",
        step: 99,
        tool: "edit_file",
        summary: "Edit cart.py",
        result: "error",
        error: "Read the file again",
        duration_ms: 215,
        tests: null,
      },
    ],
  };
  state.stepTimes = { "agent-1": { 99: "2026-10-28T14:00:00Z" } };
  state.stepSeqs = { "agent-1": { 99: 100 } };
  expect(selectActivity(state).entries).toEqual([
    {
      id: "step:agent-1:99",
      agentId: "agent-1",
      seq: 100,
      time: "2026-10-28T14:00:00Z",
      title: "edit_file · error",
      summary: "Edit cart.py · Read the file again",
      failed: true,
      duration: 215,
    },
  ]);
});

it("keeps a proposal's own result instead of borrowing a later run's tests", () => {
  const state = through(22);
  const change = state.changes["change-1"];
  if (!change?.proposal) throw new Error("Missing proposal");
  const step = state.steps["agent-1"]?.[0];
  if (!step) throw new Error("Missing step");
  state.steps["agent-1"] = [{ ...step, tests: suite }];
  expect(selectTestRuns(state)[0]?.suite).toEqual(change.proposal.tests);
  // Tolerate retained state without test evidence; do not invent another run's result.
  change.proposal = { ...change.proposal, tests: null } as unknown as typeof change.proposal;
  expect(selectTestRuns(state)[0]?.suite).toBeNull();
  const html = renderToStaticMarkup(<Tests state={state} />);
  expect(html).toContain("no test result");
  expect(html).not.toContain("9</span> passed");
});

it("places undated entries by seq so old records do not crowd the bounded window", () => {
  const state = through(22);
  const change = state.changes["change-1"];
  if (!change) throw new Error("Missing change");
  change.proposalEvent = { seq: 1 };
  change.resolutionEvent = { seq: 303 };
  const step = state.steps["agent-1"]?.[0];
  if (!step) throw new Error("Missing step");
  state.steps = { "agent-1": Array.from({ length: 300 }, (_, i) => ({ ...step, step: i + 3 })) };
  state.stepSeqs = {
    "agent-1": Object.fromEntries((state.steps["agent-1"] ?? []).map((s) => [s.step, s.step])),
  };
  state.stepTimes = { "agent-1": { 4: "2026-10-28T14:00:00Z" } };
  state.errors = [
    { source: "tools", message: "Old error", agent_id: null, change_id: null, fatal: false },
  ];
  state.errorEvents = [{ seq: 2 }];
  const activity = selectActivity(state);
  expect(activity.omitted).toBe(3);
  expect(activity.entries).toHaveLength(LOG_LIMIT);
  expect(activity.entries[0]?.id).toBe("step:agent-1:4");
  expect(activity.entries.at(-1)?.id).toBe("resolution:change-1");
  expect(activity.entries.map((entry) => entry.seq)).toEqual(
    Array.from({ length: 300 }, (_, i) => i + 4),
  );
  const html = renderToStaticMarkup(<Activity state={state} />);
  expect(html).toContain("No time");
  expect(html).not.toContain("Order unavailable");
  expect(html).not.toContain("shown after steps");
});
