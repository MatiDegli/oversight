import { readFileSync } from "node:fs";

import type { ServerEvent } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ReviewPanel } from "../review";
import { applyEvent, createInitialState, selectLiveDiff, stateStore } from "../state";
import type { State, StateStore } from "../state";
import { LiveDiff } from "./LiveDiff";

vi.mock("./monaco", () => ({ monaco: { editor: {} } }));
vi.mock("zustand", () => ({
  useStore: (store: { getState: () => StateStore }, selector: (value: StateStore) => unknown) =>
    selector(store.getState()),
}));
const events = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const through = (seq: number) =>
  events.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState());
const state = through(20);
const proposal = state.changes["change-1"]?.proposal;
if (!proposal) throw new Error("Missing proposal");

/** Rendering both actual parents catches a row accidentally disconnected from either surface. */
function render(surface: string, value = state) {
  stateStore.setState({ state: value });
  return renderToStaticMarkup(
    surface === "review" ? (
      <ReviewPanel state={value} changeId="change-1" onClose={() => {}} />
    ) : (
      <LiveDiff data={selectLiveDiff(value, "change-1", "shop/pricing.py")} />
    ),
  );
}
function fact(html: string, label: string) {
  const row = new RegExp(`<li aria-label="${label}"[^>]*>.*?</li>`).exec(html)?.[0];
  expect(row, `Missing ${label} chip`).toBeDefined();
  expect(row).toContain("bg-page-raised");
  expect(row).toContain("border-page-border");
  expect(row).toContain("rounded-control");
  expect(row).toContain("--data-size");
  return row ?? "";
}
for (const surface of ["review", "live diff"])
  describe(`${surface} facts`, () => {
    it("keeps the file count chip", () => {
      expect(fact(render(surface), "Files")).toContain('font-mono">1</span>');
    });
    it("keeps added and removed counts with signs and mono type", () => {
      const row = fact(render(surface), "Lines");
      expect(row).toContain('font-mono text-added">+2');
      expect(row).toContain('font-mono text-removed">−0');
    });
    it("keeps tests with a result icon and mono count", () => {
      const row = fact(render(surface), "Tests");
      expect(row).toContain("28 passed");
      expect(row).toContain("lucide-check");
      expect(row).toContain("font-mono");
    });
    it("keeps the actual base commit with its full hash available", () => {
      const row = fact(render(surface), "Base commit");
      expect(row).toContain(`title="${proposal.base_commit}"`);
      expect(row).toContain(`font-mono">${proposal.base_commit.slice(0, 7)}`);
    });
    it("shows missing worktree metadata honestly", () => {
      expect(fact(render(surface), "Worktree")).toContain("Worktree unknown");
    });
    it("keeps neutral step counts from the agent and run limit", () => {
      const row = fact(render(surface), "Steps");
      expect(row).toMatch(/Step 5 of 30/);
      expect(row).toContain("font-mono");
      expect(row).not.toMatch(/text-agent|bg-agent/);
    });
    it("keeps the bounded possibly-affected summary neutral", () => {
      const row = fact(render(surface), "Impact cone");
      expect(row).toContain("9 functions possibly affected, up to distance 2");
      expect(row).toContain("font-mono");
      expect(row).not.toMatch(/text-agent|bg-agent/);
    });
    it("keeps the run's model label beside that agent's cost", () => {
      const row = fact(
        render(surface, { ...state, agentCosts: { "agent-1": 0.38, "agent-2": 99 } }),
        "Model and cost",
      );
      expect(row).toContain("Muse Spark");
      expect(row).toContain('font-mono">US$0.38');
      expect(row).not.toContain("99");
    });
    it("never turns absent or unreported costs into zero", () => {
      for (const agentCosts of [{}, { "agent-1": null }] as State["agentCosts"][]) {
        const row = fact(render(surface, { ...state, agentCosts }), "Model and cost");
        expect(row).toContain("Unknown");
        expect(row).not.toContain("US$0");
      }
    });
    it("preserves small reported costs and actual zero", () => {
      expect(
        fact(render(surface, { ...state, agentCosts: { "agent-1": 0.001 } }), "Model and cost"),
      ).toContain("&lt;US$0.01");
      expect(
        fact(render(surface, { ...state, agentCosts: { "agent-1": 0 } }), "Model and cost"),
      ).toContain("US$0.00");
    });
    it("shows both cone bounds without claiming completeness", () => {
      const change = state.changes["change-1"];
      if (!change?.proposal) throw new Error("Missing proposal");
      const html = render(surface, {
        ...state,
        changes: {
          ...state.changes,
          "change-1": {
            ...change,
            proposal: {
              ...change.proposal,
              impact: { ...change.proposal.impact, omitted_by_depth: 12, omitted_by_limit: 3 },
            },
          },
        },
      });
      expect(fact(html, "Cone depth bound")).toContain("+12 omitted by depth");
      expect(fact(html, "Cone node bound")).toContain("+3 omitted by the node limit");
    });
    it.each(["failed", "error", "timeout"] as const)(
      "keeps %s tests explicit with an icon",
      (outcome) => {
        const change = state.changes["change-1"];
        if (!change?.proposal) throw new Error("Missing proposal");
        const html = render(surface, {
          ...state,
          changes: {
            ...state.changes,
            "change-1": {
              ...change,
              proposal: {
                ...change.proposal,
                tests: { ...change.proposal.tests, outcome, failed: 2 },
              },
            },
          },
        });
        const row = fact(html, "Tests");
        expect(row).toContain("lucide-x");
        expect(row).toContain(
          outcome === "failed"
            ? "2 failed"
            : outcome === "error"
              ? "Tests could not run"
              : "Tests timed out",
        );
      },
    );
  });
it("keeps live unknowns before a proposal without borrowing the run's initial base", () => {
  const value = through(12);
  const html = render("live diff", value);
  expect(fact(html, "Tests")).toContain("Tests unknown");
  expect(fact(html, "Base commit")).toContain("Unknown");
  expect(fact(html, "Impact cone")).toContain("Possibly affected: unknown");
});
it("uses the last recorded agent test execution before proposing", () => {
  const value = through(12);
  const html = render("live diff", {
    ...value,
    testExecutions: {
      12: {
        change_id: "change-1",
        agent_id: "agent-1",
        revision: 1,
        phase: "agent",
        tested_commit: "base",
        tested_tree: "tree",
        suite: proposal.tests,
      },
    },
  });
  expect(fact(html, "Tests")).toContain("28 passed");
});
it("keeps the live update and proposal banner", () => {
  expect(render("live diff")).toContain(
    "This diff updates with each edit. Decisions open when the agent proposes the change.",
  );
});
it("keeps the read-only lock line", () => {
  const html = render("live diff");
  expect(html).toContain("lucide-lock-keyhole");
  expect(html).toContain("Read-only · Inline diff");
});
