import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MeasuredCallers } from "./MeasuredCallers";
import {
  availableFacts,
  firstCaller,
  measuredFacts,
  measuredState,
  withMeasuredFacts,
} from "./measuredCallers.fixture";

describe("measured caller presentation", () => {
  it.each(measuredFacts)("renders each availability state and its reasons", (facts) => {
    const html = renderToStaticMarkup(
      <MeasuredCallers state={withMeasuredFacts(facts)} changeId="change-1" />,
    );
    expect(html).toContain("Measured callers · Possibly affected</h3>");
    expect(html).toContain(
      "Measurements cover observed invocations only; other callers may be affected.",
    );
    for (const caller of facts.measured_cone ?? []) {
      for (const reason of [
        ...caller.not_compared,
        ...caller.problems,
        ...(caller.not_probed ? [caller.not_probed] : []),
      ])
        expect(html).toContain(reason);
    }
    expect(html).not.toMatch(
      /text-passed|text-failed|amber|safe|unaffected|equivalent|complete coverage/,
    );
    expect(html).not.toMatch(/<details open|overflow-|fixed|w-\[/);
  });

  it("reports invocation counts independently from sample length and escapes sample text", () => {
    const html = renderToStaticMarkup(
      <MeasuredCallers
        changeId="change-1"
        state={withMeasuredFacts({
          ...availableFacts,
          measured_cone: [
            {
              ...firstCaller,
              rows: [
                {
                  inputs: '<script>alert("x")</script>',
                  before: "<b>before</b>",
                  after: "<b>after</b>",
                  unstable: true,
                },
              ],
            },
          ],
        })}
      />,
    );
    const text = html.replace(/<[^>]*>/g, "");
    expect(text).toContain("12 invocations compared · 3 differing");
    expect(text).toContain("Static cone bounds: depth 3 · 40 nodes");
    expect(text).toContain("Unmatched before: 0 · Unmatched after: 0");
    for (const label of ["Inputs", "Before", "After", "Unstable observation"])
      expect(html).toContain(label);
    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
  });

  it("distinguishes absent and empty evidence and returns null without a proposal", () => {
    expect(
      renderToStaticMarkup(<MeasuredCallers state={measuredState} changeId="change-1" />),
    ).toContain("Caller measurements not recorded for this revision");
    expect(
      renderToStaticMarkup(
        <MeasuredCallers
          state={withMeasuredFacts({ ...availableFacts, measured_cone: [] })}
          changeId="change-1"
        />,
      ),
    ).toContain("No caller measurements in this cone");
    expect(renderToStaticMarkup(<MeasuredCallers state={measuredState} changeId="missing" />)).toBe(
      "",
    );
  });
});
