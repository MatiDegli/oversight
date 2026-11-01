import { readFileSync } from "node:fs";

import type { ServerEvent } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ImpactConeCard } from "../review";
import { loadReviewPanel } from "../review";
import { applyEvent, createInitialState, selectLiveDiff, stateStore } from "../state";
import type { State, StateStore } from "../state";
import { LiveDiff } from "./LiveDiff";

vi.mock("./monaco", () => ({ monaco: { editor: {} } }));
vi.mock("zustand", () => ({
  useStore: (store: { getState: () => StateStore }, selector: (value: StateStore) => unknown) =>
    selector(store.getState()),
}));
const { ReviewPanel } = await loadReviewPanel();

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
function render(surface: string, value = state, path = "shop/pricing.py") {
  stateStore.setState({ state: value });
  return renderToStaticMarkup(
    surface === "review" ? (
      <ReviewPanel state={value} changeId="change-1" onClose={() => {}} />
    ) : (
      <LiveDiff data={selectLiveDiff(value, "change-1", path)} />
    ),
  );
}
function fact(html: string, label: string) {
  const row = new RegExp(`<li aria-label="${label}"[^>]*>.*?</li>`).exec(html)?.[0];
  expect(row, `Missing ${label} chip`).toBeDefined();
  expect(row).toContain("bg-page-raised");
  expect(row).toContain("border-page-border");
  if (html.includes('class="review-header"')) {
    expect(row).toContain("rounded-[7px]");
    expect(row).toContain("text-sm font-semibold");
    expect(row).not.toMatch(/font-mono|rounded-control|--data-size|py-1/);
  } else {
    expect(row).toContain("rounded-control");
    expect(row).toContain("--data-size");
  }
  return row ?? "";
}
function provenance(html: string, label: string, surface: string) {
  if (surface !== "review") return fact(html, label);
  const caption = /<header class="review-header".*?(<p .*?<\/p>)/.exec(html)?.[1];
  expect(caption, "Missing review provenance caption").toBeDefined();
  expect(html).not.toContain(`aria-label="${label}"`);
  return caption ?? "";
}
const words = (html: string) => html.replace(/<[^>]*>/g, "");
for (const surface of ["review", "live diff"])
  describe(`${surface} facts`, () => {
    it("keeps the file count chip", () => {
      const row = fact(render(surface), "Files");
      expect(words(row)).toBe("1 file");
      if (surface !== "review") expect(row).toContain('font-mono">1</span>');
    });
    it("keeps added and removed counts with signs and surface typography", () => {
      const row = fact(render(surface), "Lines");
      const face = surface === "review" ? "" : "font-mono ";
      expect(row).toContain(`${face}text-added">+2`);
      expect(row).toContain(`${face}text-removed">−0`);
    });
    it("keeps tests with a result icon and surface typography", () => {
      const row = fact(render(surface), "Tests");
      expect(words(row)).toContain("28 passed");
      expect(row).toContain("lucide-check");
      if (surface !== "review") expect(row).toContain("font-mono");
    });
    it("keeps the actual base commit with its full hash available", () => {
      const row = provenance(render(surface), "Base commit", surface);
      expect(row).toContain(`title="${proposal.base_commit}"`);
      expect(row).toMatch(
        new RegExp(`<code [^>]*class="font-mono"[^>]*>${proposal.base_commit.slice(0, 7)}</code>`),
      );
    });
    it("derives the worktree name from the change author even without an agent card", () => {
      for (const agents of [state.agents, {}]) {
        const row = provenance(render(surface, { ...state, agents }), "Worktree", surface);
        expect(row).toMatch(/<code [^>]*class="font-mono"[^>]*>agent-1<\/code>/);
        expect(row).toContain('title=".runtime/worktrees/agent-1"');
        if (surface !== "review") expect(row).not.toContain("unknown");
        expect(words(row)).toContain(
          surface === "review" ? "worktree agent-1" : "Worktree agent-1",
        );
      }
    });
    it("keeps neutral step counts from the agent and run limit", () => {
      const row = provenance(render(surface), "Steps", surface);
      expect(words(row)).toContain(surface === "review" ? "step 5 of 30" : "Step 5 of 30");
      if (surface !== "review") expect(row).toContain("font-mono");
      expect(row).not.toMatch(/text-agent|bg-agent/);
    });
    it("keeps the bounded possibly-affected summary neutral", () => {
      const row = fact(render(surface), "Impact cone");
      expect(words(row)).toContain(
        surface === "review"
          ? "9 possibly affected · up to distance 2"
          : "9 functions possibly affected, up to distance 2",
      );
      if (surface !== "review") expect(row).toContain("font-mono");
      expect(row).not.toMatch(/text-agent|bg-agent/);
    });
    it("keeps the run's model label beside that agent's cost", () => {
      const row = provenance(
        render(surface, { ...state, agentCosts: { "agent-1": 0.38, "agent-2": 99 } }),
        "Model and cost",
        surface,
      );
      expect(row).toContain("Muse Spark");
      expect(words(row)).toContain("Muse Spark · US$0.38");
      if (surface !== "review") expect(row).toContain('font-mono">US$0.38');
      expect(row).not.toContain("99");
    });
    it("never turns absent or unreported costs into zero", () => {
      for (const agentCosts of [{}, { "agent-1": null }] as State["agentCosts"][]) {
        const row = provenance(
          render(surface, { ...state, agentCosts }),
          "Model and cost",
          surface,
        );
        expect(row).toContain(surface === "review" ? "cost unknown" : "Unknown");
        expect(row).not.toContain("US$0");
      }
    });
    it("preserves small reported costs and actual zero", () => {
      expect(
        provenance(
          render(surface, { ...state, agentCosts: { "agent-1": 0.001 } }),
          "Model and cost",
          surface,
        ),
      ).toContain("&lt;US$0.01");
      expect(
        provenance(
          render(surface, { ...state, agentCosts: { "agent-1": 0 } }),
          "Model and cost",
          surface,
        ),
      ).toContain("US$0.00");
    });
    it("shows both cone bounds without claiming completeness", () => {
      const change = state.changes["change-1"];
      if (!change?.proposal) throw new Error("Missing proposal");
      const html = render(surface, {
        ...state,
        run:
          state.run && surface === "live diff"
            ? {
                ...state.run,
                limits: { ...state.run.limits, cone_max_depth: 1, cone_max_nodes: 1 },
              }
            : state.run,
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
      if (surface === "review") {
        expect(words(fact(html, "Impact cone"))).toBe("9 possibly affected · up to distance 2");
        const chips = /<ul aria-label="Facts".*?<\/ul>/.exec(html)?.[0] ?? "";
        expect([...chips.matchAll(/<li /g)]).toHaveLength(4);
        expect(chips).not.toMatch(/Cone depth bound|Cone node bound|circle-help/);
        const graphCard = renderToStaticMarkup(
          <ImpactConeCard
            cone={{ ...change.proposal.impact, omitted_by_depth: 12, omitted_by_limit: 3 }}
            nodes={state.graph?.nodes ?? []}
          />,
        );
        expect(words(graphCard)).toContain("+12 omitted by depth");
        expect(words(graphCard)).toContain("+3 omitted by the node limit");
      } else {
        expect(words(fact(html, "Cone depth bound"))).toContain("+2 omitted by depth");
        expect(words(fact(html, "Cone node bound"))).toContain("+6 omitted by the node limit");
      }
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
        expect(words(row)).toContain(
          outcome === "failed"
            ? "2 failed"
            : outcome === "error"
              ? "Tests could not run"
              : "Tests timed out",
        );
      },
    );
  });
it("derives the live cone before a proposal without borrowing the run's initial base", () => {
  const value = through(12);
  const html = render("live diff", value);
  expect(fact(html, "Tests")).toContain("Tests unknown");
  expect(fact(html, "Base commit")).toContain("Unknown");
  expect(words(fact(html, "Impact cone"))).toContain("9 functions possibly affected");
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
  expect(words(fact(html, "Tests"))).toContain("28 passed");
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

it("leaves the live cone unknown when the graph is unavailable", () => {
  expect(fact(render("live diff", { ...through(12), graph: null }), "Impact cone")).toContain(
    "Possibly affected: unknown",
  );
});
it("uses the current edit's cone and counts while revising, rather than the earlier proposal", () => {
  const change = state.changes["change-1"];
  if (!change?.proposal) throw new Error("Missing proposal");
  const value: State = {
    ...state,
    changes: {
      ...state.changes,
      "change-1": {
        ...change,
        status: "revising",
        lines_added: 17,
        lines_removed: 4,
        proposal: { ...change.proposal, impact: { ...change.proposal.impact, entries: [] } },
      },
    },
  };
  const html = render("live diff", value);
  expect(words(fact(html, "Impact cone"))).toContain("9 functions possibly affected");
  expect(fact(html, "Lines")).toContain("+17");
  expect(fact(html, "Lines")).toContain("−4");
  expect(fact(html, "Tests")).toContain("Tests unknown");
});

it("shows the open file's counts beside its path, separately from multi-file totals", () => {
  const change = state.changes["change-1"];
  const entry = change?.files["shop/pricing.py"];
  if (!change || !entry) throw new Error("Missing file fixture");
  const path = "shop/other.py";
  const value: State = {
    ...state,
    changes: {
      ...state.changes,
      "change-1": {
        ...change,
        files_changed: 2,
        lines_added: 19,
        lines_removed: 7,
        files: {
          ...change.files,
          [path]: {
            ...entry,
            file: { ...entry.file, path, lines_added: 17, lines_removed: 7 },
          },
        },
      },
    },
  };
  for (const [selected, added, removed] of [
    ["shop/pricing.py", 2, 0],
    [path, 17, 7],
  ] as const) {
    const html = render("live diff", value, selected);
    const header = /<header[^>]*>.*?<\/header>/.exec(html)?.[0] ?? "";
    expect(header).toContain(selected);
    expect(header).toContain(`text-added">+${String(added)}`);
    expect(header).toContain(`text-removed">−${String(removed)}`);
    expect(fact(html, "Lines")).toContain("+19");
  }
});
it("shows only the open diff agent's steps beneath the editor, newest first", () => {
  const value = through(12);
  const ownSteps = value.steps["agent-1"] ?? [];
  const html = render("live diff", {
    ...value,
    steps: {
      ...value.steps,
      "agent-other": ownSteps.map((step) => ({ ...step, summary: "Other agent" })),
    },
  });
  const rows = /<ol aria-label="Agent A steps, newest first".*?<\/ol>/.exec(html)?.[0];
  expect(rows).toBeDefined();
  expect(html.indexOf("Last steps")).toBeGreaterThan(html.indexOf("Loading editor fonts"));
  expect(rows).not.toContain("Other agent");
  for (const step of ownSteps) {
    expect(rows).toContain(step.summary);
    expect(rows).toContain(`${String(step.duration_ms)} ms`);
  }
  expect(rows?.indexOf("Step 2")).toBeLessThan(rows?.indexOf("Step 1") ?? 0);
});
it("keeps an empty step history honest and hides it without an open diff", () => {
  expect(render("live diff", { ...through(12), steps: {} })).toContain("No steps yet.");
  expect(render("live diff", createInitialState())).not.toContain("Last steps");
});

it.each(["review", "live diff"])("keeps possible callers explicit in %s", (surface) => {
  for (const [seed, size, possible] of [
    ["shop.inventory:Inventory.reserve", 0, 1],
    ["shop.pricing:compute_price", 9, 2],
    ["shop.cart:Cart.add", 0, 0],
  ] as const) {
    const value = structuredClone(state);
    const change = value.changes["change-1"];
    if (!change) throw new Error("Missing change");
    change.graph.nodes_modified = [seed];
    if (!change.proposal) throw new Error("Missing proposal");
    change.proposal.impact = {
      ...change.proposal.impact,
      seeds: [seed],
      entries: size ? change.proposal.impact.entries : [],
    };
    const html = render(surface, value);
    const summary = words(fact(html, "Impact cone"));
    if (surface === "review") {
      expect(summary).toBe(size ? "9 possibly affected · up to distance 2" : "No exact callers");
      const header = /<header class="review-header".*?<\/header>/.exec(html)?.[0] ?? "";
      expect(header).not.toMatch(/aria-label="Possible callers"|circle-help|not followed/);
    } else if (size) {
      expect(summary).toContain("9 functions possibly affected");
      const extra = fact(html, "Possible callers");
      expect(words(extra)).toContain("+2 possible callers, not followed");
      expect(extra).toContain("lucide-circle-help");
    } else {
      expect(summary).toContain(
        possible
          ? "No exact callers · 1 possible caller, not followed"
          : "No functions possibly affected within the cone bounds",
      );
      expect(html).not.toContain('aria-label="Possible callers"');
    }
    expect(render(surface, { ...value, graph: null })).not.toContain("not followed");
  }
});
