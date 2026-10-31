import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { ConversationPanel } from "./ConversationPanel";
import { Composer } from "./Composer";
import { sketchState } from "./fixture";
import { selectConversation } from "./selectConversation";
import { TimelineEntry } from "./rows";

it("renders optional header chips from retained facts and withholds message text", () => {
  const conversation = selectConversation(sketchState, "agent-1", [
    { seq: 14, ts: "2026-10-27T17:02:02Z", text: null },
  ]);
  if (!conversation) throw new Error("Missing fixture");
  conversation.header = {
    ...conversation.header,
    reasoning: null,
    cost: null,
    zonePaths: ["pricing.py", "cart.py", "orders.py"],
  };
  const html = renderToStaticMarkup(<ConversationPanel conversation={conversation} />);
  expect(html).toContain("Zone: pricing.py and 2 more");
  expect(html).toContain("Cost unknown");
  expect(html).not.toContain("reasoning");
  expect(html).toContain("Message sent. Its text is not in this recording.");
  conversation.header = { ...conversation.header, cost: undefined, round: 1, zonePaths: [] };
  const empty = renderToStaticMarkup(<ConversationPanel conversation={conversation} />);
  expect(empty).not.toMatch(/Cost unknown|US\$|Zone:|Round 1/);
});

it("renders failed and skipped test counts, case details and server errors without raw output", () => {
  const step = sketchState.steps["agent-1"]?.find((step) => step.tests);
  if (!step?.tests) throw new Error("Missing tests");
  const html = renderToStaticMarkup(
    <TimelineEntry
      changeId="change-1"
      entry={{
        kind: "step",
        seq: 7,
        ts: undefined,
        step: {
          ...step,
          result: "error",
          error: "Tests failed",
          tests: {
            ...step.tests,
            outcome: "failed",
            passed: 1,
            failed: 1,
            skipped: 1,
            failed_tests: ["tests/test_pricing.py::test_coupon"],
            cases: [
              {
                test_id: "tests/test_pricing.py::test_coupon",
                outcome: "failed",
                detail: "Expected 10 cents",
              },
              { test_id: "tests/test_pricing.py::test_price", outcome: "passed", detail: null },
              { test_id: "tests/test_pricing.py::test_tax", outcome: "skipped", detail: null },
            ],
            cases_complete: true,
          },
        },
      }}
    />,
  );
  expect(html).toContain("1 passed in 0.40s, 1 failed, 1 skipped");
  expect(html).toContain("tests/test_pricing.py::test_coupon · Expected 10 cents");
  expect(html).toContain("tests/test_pricing.py · 1 failed");
  expect(html).toContain("Tests failed");
  expect(html).not.toContain("View in diff");
});

it.each([false, null, undefined])(
  "omits per-file totals when case completeness is %s but keeps failure details",
  (complete) => {
    const step = sketchState.steps["agent-1"]?.find((step) => step.tests);
    if (!step?.tests) throw new Error("Missing tests");
    const tests = { ...step.tests };
    if (complete === undefined) Reflect.deleteProperty(tests, "cases_complete");
    else tests.cases_complete = complete;
    const html = renderToStaticMarkup(
      <TimelineEntry
        changeId="change-1"
        entry={{
          kind: "step",
          seq: 7,
          ts: undefined,
          step: {
            ...step,
            tests: {
              ...tests,
              outcome: "failed",
              failed: 1,
              failed_tests: ["tests/test_pricing.py::test_coupon"],
              cases: [
                ...(tests.cases ?? []),
                {
                  test_id: "tests/test_pricing.py::test_coupon",
                  outcome: "failed",
                  detail: "Expected 10 cents",
                },
              ],
            },
          },
        }}
      />,
    );
    expect(html).toContain("28 passed in 0.40s, 1 failed");
    expect(html).toContain("tests/test_pricing.py::test_coupon · Expected 10 cents");
    expect(html).not.toContain("tests/test_pricing.py ·");
  },
);

it("explains a disabled message box when only the pause callback is available", () => {
  const conversation = selectConversation(sketchState, "agent-1", []);
  if (!conversation) throw new Error("Missing fixture");
  const html = renderToStaticMarkup(
    <Composer header={conversation.header} onPause={() => Promise.resolve()} />,
  );
  expect(html).toContain('aria-disabled="true"');
  expect(html).toContain('readOnly=""');
  expect(html).toContain("Agent A is not running.");
  const captionId = /aria-describedby="([^"]+)"/.exec(html)?.[1];
  expect(captionId).toBeDefined();
  expect(html).toContain(`id="${captionId ?? ""}"`);
});
