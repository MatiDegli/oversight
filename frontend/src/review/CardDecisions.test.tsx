import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { DecisionFeedback } from "./CardDecisions";
import type { DecisionAction } from "./decisions";

it.each<[DecisionAction, string]>([
  ["approve", "Approving: waiting for the merge result."],
  ["reject", "Rejecting: waiting for the server."],
  ["request-changes", "Requesting changes: waiting for the agent."],
])("shows the pending words for %s", (action, words) => {
  const html = renderToStaticMarkup(<DecisionFeedback view={{ kind: "pending", action }} />);
  expect(html).toBe(`<p role="status" class="decision-muted">${words}</p>`);
});
