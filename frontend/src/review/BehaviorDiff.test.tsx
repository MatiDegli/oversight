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
  expect(html).toContain("Methods and classes are not probed yet.");
});

it("keeps the sample summary short and the varied-row qualification intact", () => {
  const html = renderToStaticMarkup(
    <BehaviorDiff
      detailsOnly
      changeId="change-1"
      state={withBehaviorFacts(required(behaviorFacts[1]))}
    />,
  );
  expect(html).toMatch(/>Samples \(1\)<\/summary>/);
  expect(html).toContain("Results varied between runs; this is not an established difference.");
  expect(html).toContain("Up to eight samples per node; counts cover all compared inputs.");
  expect(html).not.toContain("Inputs compared:");
});
