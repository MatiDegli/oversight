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
  const renderDetails = (caller = firstCaller) =>
    renderToStaticMarkup(
      <MeasuredCallers
        state={withMeasuredFacts({ ...availableFacts, measured_cone: [caller] })}
        changeId="change-1"
        detailsOnly
      />,
    );
  it("shows only further changed samples, a Mono name and method details", () => {
    const html = renderDetails({
      ...firstCaller,
      rows: [
        ...firstCaller.rows,
        {
          inputs: "longer additional input ".repeat(10),
          before: "before",
          after: "after",
          unstable: false,
        },
      ],
      not_compared: ["Inputs differed"],
      problems: ["Recording stopped"],
      not_probed: "budget exhausted",
    });
    expect(html).toContain('aria-label="Measured callers"');
    expect(html).toContain('<code class="font-mono">Cart.quote_add</code></p>');
    expect(html).toContain("Not compared: Inputs differed");
    expect(html).toContain("Observation problem: Recording stopped");
    expect(html).toContain("Observed samples");
    expect(html).toContain("longer additional input");
    expect(html).not.toContain(firstCaller.rows[0]?.inputs);
    expect(html).not.toMatch(
      /Distance|·|Unmatched|Not probed|Static cone bounds|Comparisons available|recorded for this revision|No caller measurements|Possibly affected/,
    );
    expect(html.match(/<li>/g)).toHaveLength(2);
  });
  it.each([
    { ...firstCaller, compared_count: 0 },
    { ...firstCaller, outcome: "unchanged" as const },
    { ...firstCaller, outcome: "not_compared" as const },
    { ...firstCaller, rows: [] },
    firstCaller,
  ])("omits callers without further qualifying samples (%j)", (caller) => {
    expect(renderDetails(caller)).toBe("");
  });
  it("keeps unstable rows even without an established changed outcome", () => {
    const html = renderDetails({
      ...firstCaller,
      outcome: "not_compared",
      rows: firstCaller.rows.map((row) => ({ ...row, unstable: true })),
    });
    expect(html).toContain("Unstable observation");
    expect(html).toContain(firstCaller.rows[0]?.inputs);
  });
  it.each([null, []])("omits absent or empty caller evidence (%j)", (measured_cone) => {
    expect(
      renderToStaticMarkup(
        <MeasuredCallers
          changeId="change-1"
          detailsOnly
          state={withMeasuredFacts({ ...availableFacts, measured_cone })}
        />,
      ),
    ).toBe("");
  });
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
