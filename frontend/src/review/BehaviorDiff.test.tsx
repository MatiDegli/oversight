import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { BehaviorDiff } from "./BehaviorDiff";
import { behaviorFacts, required, withBehaviorFacts } from "./behavior.fixture";

it("uses function labels alone in panel details, without availability statuses", () => {
  const html = renderToStaticMarkup(
    <BehaviorDiff
      detailsOnly
      changeId="change-1"
      state={withBehaviorFacts(required(behaviorFacts[1]))}
    />,
  );
  expect(html).toContain('<code class="font-mono">compute_price</code></p>');
  expect(html).not.toMatch(/ · |Available|Partial|Unavailable/);
  expect(html).toContain("methods and classes are not measured yet.");
});

it("keeps the sample summary short and the varied-row qualification intact", () => {
  const html = renderToStaticMarkup(
    <BehaviorDiff
      detailsOnly
      changeId="change-1"
      state={withBehaviorFacts(required(behaviorFacts[1]))}
    />,
  );
  expect(html).toContain("Samples (1)</summary>");
  expect(html).toContain("lucide-chevron-right");
  expect(html).toContain("group-open:rotate-90");
  expect(html).toContain("Results varied between runs; this is not an established difference.");
  expect(html).toContain("Up to eight samples per function; counts cover all compared inputs.");
  expect(html).not.toContain("Inputs compared:");
});

it.each([
  [
    "side effect: randomness; side effect: time access",
    "compute_price: it uses randomness; it reads the clock.",
  ],
  ["future server diagnostic", null],
])("uses only known reason words in Details: %s", (reason, line) => {
  const facts = structuredClone(required(behaviorFacts[0]));
  const node = required(facts.behavior_diff?.[0]);
  node.not_probed = reason;
  node.compared_count = 0;
  node.differing_count = 0;
  node.rows = [];
  const html = renderToStaticMarkup(
    <BehaviorDiff detailsOnly changeId="change-1" state={withBehaviorFacts(facts)} />,
  );
  expect(html).not.toContain(reason);
  if (line) expect(html).toContain(line);
  else {
    expect(html).not.toContain("compute_price:");
    expect(html).toContain("No input comparisons were recorded.");
  }
});
