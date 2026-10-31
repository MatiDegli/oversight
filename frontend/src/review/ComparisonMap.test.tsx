import type { ChangeFacts } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import log from "../../../fixtures/events/handwritten-inbox-grants.jsonl?raw";
import { applyEvent, createInitialState } from "../state";
import { parseEvent } from "../transport";
import { ComparisonMap } from "./ComparisonMap";
import { selectChangeCard, selectComparisonSummary } from "./inboxSelectors";

const state = log.trim().split("\n").map(parseEvent).reduce(applyEvent, createInitialState());
const card = selectChangeCard(state, "change-3");
if (!card?.facts) throw new Error("Missing comparison card");
const { change } = card;
const facts = card.facts;
const observation = facts.behavior_diff?.[0];
if (!observation) throw new Error("Missing comparison");
const render = (value?: ChangeFacts) =>
  renderToStaticMarkup(<ComparisonMap state={state} change={change} facts={value} />);
const heading = (html: string) => html.match(/<h3>(.*?)<\/h3>/s)?.[1];

it("bounds a complete comparison to the changed functions linked to its questions", () => {
  const html = render(facts);
  expect(heading(html)).toBe("No other difference in what was compared · 1 function, 24 inputs");
  expect(html).toContain('aria-label="Compared functions and callers"');
  expect(html).toContain(
    "Only captured inputs are covered; other functions and inputs remain unverified.",
  );
  expect(heading(html)).not.toMatch(/svg|passed|function-same/);
  expect(html.match(/<p class="decision-muted">(.*?)<\/p>/s)?.[1]).not.toMatch(/24|1 function/);
});

it("makes no claim when a compared function in an unchanged module differs", () => {
  const value = {
    ...facts,
    behavior_diff: [
      ...(facts.behavior_diff ?? []),
      {
        node_id: "shop.pricing:add_tax",
        compared_count: 1,
        differing_count: 1,
        rows: [],
        not_probed: null,
      },
    ],
  };
  const html = render(value);
  expect(heading(html)).toBe("What was compared · 2 functions, 25 inputs");
  expect(selectComparisonSummary(change, value)).toMatchObject({
    incomplete: false,
    noOtherDifference: false,
  });
  const square = html.match(/<span[^>]*aria-label="add_tax:[^"]*"[^>]*>(.*?)<\/span>/s)?.[0];
  expect(square).toBeDefined();
  expect(square).toContain("Δ");
  expect(square).not.toMatch(/function-same|function-changed/);
});

it.each([
  { ...facts, complete: false, problems: ["Comparison timed out"] },
  { ...facts, behavior_diff: [{ ...observation, not_probed: "Budget exhausted" }] },
  {
    ...facts,
    behavior_diff: [
      { ...observation, rows: observation.rows.map((row) => ({ ...row, unstable: true })) },
    ],
  },
])("keeps an incomplete comparison descriptive and retains its note: %j", (value) => {
  const html = render(value);
  expect(heading(html)).toBe("What was compared · 1 function, 24 inputs");
  expect(html).toContain("Comparison coverage is incomplete.");
});

it("does not excuse a changed function's differences without a linked question", () => {
  expect(heading(render({ ...facts, review_cards: [] }))).toBe(
    "What was compared · 1 function, 24 inputs",
  );
});

it("requires complete caller comparisons too and counts their inputs", () => {
  const measured = {
    node_id: "shop.pricing:add_tax",
    compared_count: 1,
    differing_count: 0,
    rows: [],
    not_probed: null,
    outcome: "not_compared" as const,
    unmatched_before: 1,
    unmatched_after: 0,
    not_compared: ["Unmatched call"],
    complete: false,
    problems: [],
  };
  const value = { ...facts, measured_cone: [measured] };
  expect(heading(render(value))).toBe("What was compared · 2 functions, 25 inputs");
  expect(selectComparisonSummary(change, value).incomplete).toBe(true);
});

it("uses singular input and function, and never claims completeness without measurements", () => {
  expect(
    heading(
      render({
        ...facts,
        behavior_diff: [{ ...observation, compared_count: 1, differing_count: 0, rows: [] }],
      }),
    ),
  ).toBe("No other difference in what was compared · 1 function, 1 input");
  expect(heading(render())).toBe("What was compared · 0 functions, 0 inputs");
});

it("keeps every unmeasured square neutral, including a changed function", () => {
  const html = render({ ...facts, behavior_diff: [] });
  const squares = html.match(/<span[^>]*aria-label="[^"]*"[^>]*>\?<\/span>/g) ?? [];
  expect(squares.length).toBeGreaterThan(0);
  expect(squares.some((square) => square.includes("Inventory.reserve: changed"))).toBe(true);
  for (const square of squares) expect(square).not.toMatch(/function-same|function-changed/);
});

it("uses the passed class and check only for a function compared with zero differences", () => {
  const html = render({
    ...facts,
    behavior_diff: [
      {
        node_id: "shop.pricing:add_tax",
        compared_count: 1,
        differing_count: 0,
        rows: [],
        not_probed: null,
      },
    ],
  });
  const square = html.match(/<span[^>]*aria-label="add_tax:[^"]*"[^>]*>(.*?)<\/span>/s)?.[0];
  expect(square).toBeDefined();
  expect(square).toContain('class="function-same"');
  expect(square).toContain("lucide-check");
});
