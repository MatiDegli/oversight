import { renderToReadableStream, renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { ConversationPanel } from "./ConversationPanel";
import { Composer } from "./Composer";
import { sketchState } from "./fixture";
import { selectConversation, type Conversation } from "./selectConversation";
import { TimelineEntry } from "./rows";

async function panelMarkup(conversation: Conversation) {
  const stream = await renderToReadableStream(<ConversationPanel conversation={conversation} />);
  await stream.allReady;
  return (await new Response(stream).text()).replace(/<!--.*?-->/gs, "");
}

it("keeps a positive cost below half a cent visible in the header chip", async () => {
  const conversation = selectConversation(sketchState, "agent-1", []);
  if (!conversation) throw new Error("Missing fixture");
  conversation.header.cost = 0.001;
  expect(await panelMarkup(conversation)).toContain(">&lt;US$0.01</span>");
});

it("renders optional header chips from retained facts and withholds message text", async () => {
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
  const html = await panelMarkup(conversation);
  expect(html).toContain("Zone: pricing.py and 2 more");
  expect(html).toContain("Cost unknown");
  expect(html).not.toContain("reasoning");
  expect(html).toContain("Message sent. Its text is not in this recording.");
  conversation.header = { ...conversation.header, cost: undefined, round: 1, zonePaths: [] };
  const empty = await panelMarkup(conversation);
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

it.each([
  ["working", "onPause", "Pause"],
  ["revising", "onPause", "Pause"],
  ["paused", "onResume", "Resume"],
  ["working", "onStop", "Stop and discard"],
  ["revising", "onStop", "Stop and discard"],
  ["paused", "onStop", "Stop and discard"],
] as const)("keeps %s controls truthful with only %s", (state, callback, label) => {
  const conversation = selectConversation(sketchState, "agent-1", []);
  if (!conversation) throw new Error("Missing fixture");
  const html = renderToStaticMarkup(
    <Composer
      header={{ ...conversation.header, state }}
      {...{ [callback]: () => Promise.resolve() }}
    />,
  );
  expect(html).toContain(label);
  expect(html).not.toContain("Agent A is not running.");
  expect(html).not.toContain("<textarea");
  expect(html).not.toContain(">Send<");
  expect(html).not.toContain("Recorded run:");
});

it("explains an inactive agent's disabled message box", () => {
  const conversation = selectConversation(sketchState, "agent-1", []);
  if (!conversation) throw new Error("Missing fixture");
  const html = renderToStaticMarkup(
    <Composer
      header={{ ...conversation.header, state: "ready_for_review" }}
      onSend={() => Promise.resolve()}
    />,
  );
  expect(html).toContain('aria-disabled="true"');
  expect(html).toContain('readOnly=""');
  expect(html).toContain("Agent A is not running.");
  const captionId = /aria-describedby="([^"]+)"/.exec(html)?.[1];
  expect(captionId).toBeDefined();
  expect(html).toContain(`id="${captionId ?? ""}"`);
});
