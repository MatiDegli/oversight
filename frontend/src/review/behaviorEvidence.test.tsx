import type { ChangeFacts, NodeBehaviorDiff } from "@contracts";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { applyEvent, createInitialState } from "../state";
import { BehaviorDiff } from "./BehaviorDiff";
import { selectBehaviorEvidence } from "./behaviorEvidence";
import {
  behaviorEvents,
  behaviorFacts,
  behaviorState,
  withBehaviorFacts,
  required,
} from "./behavior.fixture";
import { ReviewFacts } from "./ReviewFacts";

const available = required(behaviorFacts[0]);
const probe = required(required(available.behavior_diff)[0]);
const caller = {
  ...probe,
  outcome: "changed" as const,
  unmatched_before: 0,
  unmatched_after: 0,
  not_compared: [],
  complete: true,
  problems: [],
};
const proposal = required(required(behaviorState.changes["change-1"]).proposal);
const select = (...facts: ChangeFacts[]) =>
  selectBehaviorEvidence(withBehaviorFacts(...facts), "change-1");
const render = (...facts: ChangeFacts[]) =>
  renderToStaticMarkup(<BehaviorDiff state={withBehaviorFacts(...facts)} changeId="change-1" />);
const factsFor = (...nodes: NodeBehaviorDiff[]): ChangeFacts => ({
  ...available,
  behavior_diff: nodes,
});

describe("bounded direct behavior evidence", () => {
  it("preserves all published nodes, including removed and unknown ids, in order", () => {
    const nodes = select(required(behaviorFacts[1]));
    expect(nodes?.map((node) => node.node_id)).toEqual(
      required(required(behaviorFacts[1]).behavior_diff).map((node) => node.node_id),
    );
    expect(nodes?.map((node) => node.status)).toEqual(["Partial", "Unavailable", "Unavailable"]);
    expect(nodes?.at(-1)?.label).toBe("removed.module:old_function");
    expect(nodes?.[1]?.label).toBe("Cart.total");
  });
  it("does not confuse overall facts completeness or measured callers with direct evidence", () => {
    expect(
      select({ ...available, complete: false, problems: ["Other evidence missing"] })?.[0]?.status,
    ).toBe("Available");
    expect(
      select({
        ...available,
        behavior_diff: null,
        measured_cone: [caller],
      }),
    ).toBeNull();
    expect(
      render({
        ...available,
        measured_cone: [{ ...caller, node_id: "caller:only" }],
      }),
    ).not.toContain("caller:only");
  });
  it.each([
    ["reason", { ...probe, not_probed: "probe unavailable" }],
    ["instability", { ...probe, rows: [{ ...required(probe.rows[0]), unstable: true }] }],
  ])("marks admitted comparisons with %s partial", (_, node) => {
    expect(select(factsFor(node))?.[0]?.status).toBe("Partial");
  });
  it.each([
    ["unsupported node kind", "This node type is not probed yet."],
    ["version unavailable", "The base or proposed version is unavailable."],
    ["probe unavailable", "Behavior probing was unavailable; no further reason was published."],
    ["future category <keep>", "future category <keep>"],
  ])("preserves only the published reason for %s", (reason, expected) => {
    expect(select(factsFor({ ...probe, not_probed: reason }))?.[0]?.limitation).toBe(expected);
  });
  it.each(["method", "class"] as const)(
    "uses the specific wording only for confirmed %s nodes",
    (kind) => {
      const state = withBehaviorFacts(factsFor({ ...probe, not_probed: "unsupported node kind" }));
      state.graph = {
        ...required(state.graph),
        nodes: required(state.graph).nodes.map((node) =>
          node.id === probe.node_id ? { ...node, kind } : node,
        ),
      };
      expect(selectBehaviorEvidence(state, "change-1")?.[0]?.limitation).toBe(
        "Methods and classes are not probed yet.",
      );
    },
  );
  it.each([
    ["revision", { ...available, revision: available.revision + 1 }],
    ["head", { ...available, head_commit: "other-head" }],
  ])("excludes stale %s", (_, stale) => {
    expect(select(stale)).toBeNull();
    expect(render(stale)).toBe("");
  });
  it("never falls back when the latest matching payload is null or omitted", () => {
    expect(select(available, { ...available, behavior_diff: null })).toBeNull();
    const omitted = { ...available };
    Reflect.deleteProperty(omitted, "behavior_diff");
    expect(select(available, omitted)).toBeNull();
    expect(select(available, { ...available, head_commit: "other" })?.[0]?.node_id).toBe(
      probe.node_id,
    );
  });
  it("returns null for missing changes, facts and probes; keeps the empty list distinct", () => {
    expect(select()).toBeNull();
    expect(selectBehaviorEvidence(createInitialState(), "missing")).toBeNull();
    expect(render(required(behaviorFacts[3]))).toContain(
      "No behavior was recorded for changed functions.",
    );
    const html = renderToStaticMarkup(
      <ReviewFacts state={withBehaviorFacts()} proposal={proposal} />,
    );
    expect(html).toContain("Behavior evidence was not recorded for this revision.");
    expect(
      renderToStaticMarkup(
        <ReviewFacts state={withBehaviorFacts(required(behaviorFacts[2]))} proposal={proposal} />,
      ),
    ).toContain("Behavior evidence was not recorded for this revision.");
  });
  it("retains whole event payloads through replay and clears them on a new run", () => {
    const replay = behaviorEvents.reduce(applyEvent, behaviorState);
    expect(replay.changeFacts["change-1"]).toEqual(behaviorFacts);
    expect(selectBehaviorEvidence(replay, "change-1")).toEqual([]);
    const reset = applyEvent(replay, {
      seq: 1,
      ts: "2026-10-29T16:00:00Z",
      type: "run.started",
      payload: { ...required(replay.run), run_id: "new-run" },
    });
    expect(reset.changeFacts).toEqual({});
    expect(selectBehaviorEvidence(reset, "change-1")).toBeNull();
  });
  it("keeps eight rows and counts 10/9 without inventing an equal count beyond retained rows", () => {
    const html = render(available);
    expect(select(available)?.[0]?.rows).toHaveLength(8);
    expect(html.match(/<dt>Input<\/dt>/g)).toHaveLength(8);
    expect(html).toContain('Inputs compared: <span class="font-mono">10</span>');
    expect(html).toContain('Differences measured: <span class="font-mono">9</span>');
    expect(html).not.toMatch(/Same.result|equal result|No difference measured/);
    expect(html).toContain("counts cover all compared inputs");
    expect(html).toContain("These bounded inputs do not establish equivalence.");
  });
  it("marks every unstable sample without presenting it as a stable difference", () => {
    const html = render(required(behaviorFacts[1]));
    expect(html).toContain("Results varied between runs; this is not an established difference.");
    expect(html).not.toContain("No difference measured");
  });
  it.each([
    [1, 0, null, false, true],
    [0, 0, null, false, false],
    [1, 1, null, false, false],
    [1, 0, "probe unavailable", false, false],
    [1, 0, null, true, false],
  ] as const)(
    "qualifies the zero-difference sentence (%s/%s/%s/%s)",
    (compared, differing, reason, unstable, shown) => {
      const node = {
        ...probe,
        compared_count: compared,
        differing_count: differing,
        not_probed: reason,
        rows: unstable ? [{ ...required(probe.rows[0]), unstable }] : [],
      };
      expect(render(factsFor(node)).includes("No difference measured on these inputs")).toBe(shown);
      expect(render(factsFor(node))).not.toMatch(/Same.result/);
    },
  );
  it("escapes and losslessly wraps long opaque display strings, whitespace and exceptions", () => {
    const text = '<script>alert("x")</script> &\n  exception:ValueError ' + "x".repeat(2000);
    const html = render(
      factsFor({ ...probe, rows: [{ inputs: text, before: text, after: text, unstable: false }] }),
    );
    expect(html.match(/&lt;script&gt;/g)).toHaveLength(3);
    expect(html).not.toContain("<script>");
    expect(html).toContain("\n  exception:ValueError " + "x".repeat(2000));
    expect(html).toContain("whitespace-pre-wrap wrap-anywhere");
    expect(html).not.toMatch(/text-passed|text-failed|text-agent|amber/);
    expect(html).toContain("<h3");
  });
});
