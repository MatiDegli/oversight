import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";

// eslint-disable-next-line no-restricted-imports -- Reuse line A's hand-written fixture without changing review.
import { behaviorFacts, required, withBehaviorFacts } from "../review/behavior.fixture";
import type { State } from "../state";
import { NodeBehavior } from "./NodeBehavior";

const available = required(behaviorFacts[0]);
const probe = required(required(available.behavior_diff)[0]);

it("renders the agent letter, review counts and at most eight opaque input/before/after rows", () => {
  const ninth = { inputs: "ninth sample", before: "hidden", after: "hidden", unstable: false };
  const state = withBehaviorFacts({
    ...available,
    behavior_diff: [{ ...probe, rows: [...probe.rows, ninth] }],
  });
  const html = renderToStaticMarkup(<NodeBehavior state={state} nodeId={probe.node_id} />);
  expect(html).toContain('aria-label="Agent A"');
  expect(html).toContain("text-agent-a-text");
  expect(html).toContain('Inputs compared: <span class="font-mono">10</span>');
  expect(html).toContain('Differences measured: <span class="font-mono">9</span>');
  for (const label of ["Input", "Before", "After"]) {
    expect(html.match(new RegExp(`<dt>${label}</dt>`, "g"))).toHaveLength(8);
  }
  expect(html).not.toContain("ninth sample");
  expect(html).toContain("These bounded inputs do not establish equivalence.");
});

it("shows the published limitation and instability without claiming no difference", () => {
  const state = withBehaviorFacts(required(behaviorFacts[1]));
  const html = renderToStaticMarkup(<NodeBehavior state={state} nodeId={probe.node_id} />);
  expect(html).toContain("Partial");
  expect(html).toContain("Behavior probing was unavailable; no further reason was published.");
  expect(html).toContain("Results varied between runs; this is not an established difference.");
  expect(html).not.toContain("No difference measured");
  expect(html).not.toContain("Methods and classes");
});

it("renders nothing for absent evidence or a node without probes", () => {
  expect(NodeBehavior({ state: withBehaviorFacts(), nodeId: probe.node_id })).toBeNull();
  expect(NodeBehavior({ state: withBehaviorFacts(available), nodeId: "missing" })).toBeNull();
});

it("renders each change on the node separately with its own agent letter", () => {
  const state: State = withBehaviorFacts(available);
  const first = required(state.changes["change-1"]);
  const agent = required(state.agents["agent-1"]);
  state.agents = { ...state.agents, "agent-2": { ...agent, agent_id: "agent-2", slot: "B" } };
  state.changes = {
    ...state.changes,
    "change-2": {
      ...first,
      change_id: "change-2",
      agent_id: "agent-2",
      proposal: { ...required(first.proposal), change_id: "change-2", agent_id: "agent-2" },
    },
  };
  state.changeFacts = {
    ...state.changeFacts,
    "change-2": [{ ...available, change_id: "change-2" }],
  };
  const html = renderToStaticMarkup(<NodeBehavior state={state} nodeId={probe.node_id} />);
  expect(html.indexOf('aria-label="Agent A"')).toBeLessThan(html.indexOf('aria-label="Agent B"'));
  expect(html.match(/Inputs compared:/g)).toHaveLength(2);
});

it("qualifies measured no difference and renders unavailable evidence separately", () => {
  const state = withBehaviorFacts({
    ...available,
    behavior_diff: [{ ...probe, compared_count: 1, differing_count: 0, rows: [] }],
  });
  expect(renderToStaticMarkup(<NodeBehavior state={state} nodeId={probe.node_id} />)).toContain(
    "No difference measured on these inputs",
  );
  const html = renderToStaticMarkup(
    <NodeBehavior
      state={withBehaviorFacts(required(behaviorFacts[1]))}
      nodeId="shop.cart:Cart.total"
    />,
  );
  expect(html).toContain("Unavailable");
  expect(html).toContain("Methods and classes are not probed yet.");
  expect(html).not.toContain("No difference measured");
});

it("escapes opaque sample text while retaining whitespace and wrapping classes", () => {
  const text = "<script> &\n  exception:ValueError";
  const state = withBehaviorFacts({
    ...available,
    behavior_diff: [
      {
        ...probe,
        rows: [{ inputs: text, before: text, after: text, unstable: false }],
      },
    ],
  });
  const html = renderToStaticMarkup(<NodeBehavior state={state} nodeId={probe.node_id} />);
  expect(html.match(/&lt;script&gt; &amp;\n {2}exception:ValueError/g)).toHaveLength(3);
  expect(html).toContain("whitespace-pre-wrap wrap-anywhere font-mono text-text");
});
