import { createHash } from "node:crypto";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { selectNodeChips } from "../graph/nodeChip"; // eslint-disable-line no-restricted-imports -- Regression across the approved graph/review boundary.
import { NewParameterCalls } from "./NewParameterCalls";
import { nodeId, parameterState, parameterStates } from "./NewParameterCalls.fixture";
import { parameterExtensions } from "./parameterExtensions";
import { availableFacts, withMeasuredFacts } from "./measuredCallers.fixture";
import { ReviewBody } from "./ReviewBody";
import { comparisonPreview, selectReviewBody } from "./reviewBodyEvidence";

const render = (state: ReturnType<typeof parameterState>, phone = false) =>
  renderToStaticMarkup(
    <ReviewBody
      state={state}
      changeId="change-1"
      diff={<div>Diff</div>}
      showCrossReview={false}
      phone={phone}
    />,
  );
it("names the inner evidence focus target for its function", () => {
  const state = parameterState("values");
  const entry = parameterExtensions(state, "change-1")[0];
  if (!entry) throw new Error("Missing extension");
  const target = render(state)
    .match(/<section\b[^>]*>/g)
    ?.find((tag) => tag.includes(`id="${entry.target}"`));
  expect(target).toContain('aria-label="compute_price"');
});
it.each(parameterStates)("renders supplied values and separated counts: %s", (name) => {
  const state = parameterState(name),
    entries = parameterExtensions(state, "change-1");
  const html = render(state);
  for (const entry of entries) {
    expect(html).toContain(entry.node_id.split(":").at(-1));
    expect(html).toContain("<p>compute_price");
    for (const sample of entry.rows) {
      expect(html).toContain(sample.inputs);
      expect(html).toContain(sample.after);
      expect(html).toContain(renderToStaticMarkup(<code>{sample.before}</code>));
    }
    if (entry.run_count) expect(html).toContain(`${String(entry.run_count)} calls run with`);
    if (entry.rejected_before)
      expect(html).toContain(`Base rejected ${String(entry.rejected_before)} calls.`);
  }
  expect(html).not.toMatch(/Base rejected 0|0 calls run|49|third positional|supplies cap_percent/);
  if (entries.some((entry) => entry.rows.length > 0)) {
    expect(html).toContain('data-agent-id="agent-1"');
    expect(html).toContain("After · Agent A");
    expect(html).toContain("--slot-graphic:var(--agent-a)");
    expect(html).not.toContain("[object Object]");
  }
  if (name === "partial") {
    expect(html).toContain("8 samples shown from 48 calls run.");
    expect(html).toContain("Some calls were not measured: recording budget reached.");
  }
  if (name === "unavailable") {
    expect(html).toContain("the measurement run failed");
    expect(html).not.toMatch(/Samples|What was compared<|aria-label="Details"/);
  }
  expect(html).not.toContain('<details class="parameter-samples" open');
  const without = structuredClone(state);
  const facts = without.changeFacts["change-1"]?.at(-1);
  if (!facts) throw new Error("Missing facts");
  facts.parameter_extensions = null;
  const counts = (source: typeof state) => {
    const view = selectReviewBody(source, "change-1");
    return [
      view.kind,
      view.summary,
      view.callers,
      view.card?.kind,
      view.facts?.review_cards?.length ?? 0,
    ];
  };
  expect(counts(state)).toEqual(counts(without));
  expect(comparisonPreview(state, "change-1")).toBe(comparisonPreview(without, "change-1"));
  expect(selectNodeChips(state, null).map(({ text }) => text)).toEqual(
    name === "mixed" ? ["2 of 6 differ"] : [],
  );
});
it.each([false, true])(
  "keeps the empty DOM identical and Details in comparison (phone=%s)",
  (phone) => {
    const html = render(parameterState("none"), phone);
    expect(html).toBe(render(parameterState("none", true), phone));
    expect(html).not.toContain("parameter-calls");
    // Readable structure assertions explain a base-hash failure before inspecting bytes.
    expect(
      html.match(/aria-label="(?:Review context|Agent note|What was compared|Details)"/g),
    ).toEqual([
      'aria-label="Review context"',
      'aria-label="Agent note"',
      'aria-label="What was compared"',
      'aria-label="Details"',
    ]);
    expect(html).toMatch(/comparison-bound[^>]*>[^<]*<\/p><section aria-label="Details"/);
    expect(html).toContain("<details><summary");
    expect(html.indexOf('aria-label="Details"')).toBeGreaterThan(
      html.indexOf('aria-label="What was compared"'),
    );
    // Byte-for-byte hashes rendered from ReviewBody at the approved base SHA.
    expect(createHash("sha256").update(html).digest("hex")).toBe(
      phone
        ? "3e65a31f8f7f4ad2d873d437d9be10d8d0625676a9cd113a440e30896b7c04d3"
        : "cb6850f229d0572e7f00593fc07c45afa18a798e9b47bc77819ee0f3505c5180",
    );
  },
);
it("singularizes one call and rejection and never exposes unknown limitations", () => {
  const entries = parameterExtensions(parameterState("rejected"), "change-1");
  const first = entries[0];
  if (!first) throw new Error("Missing extension");
  const html = renderToStaticMarkup(
    <NewParameterCalls
      entries={[
        {
          ...first,
          run_count: 1,
          rejected_before: 1,
          rows: first.rows.slice(0, 1),
          not_probed: "internal_unknown_probe_reason",
        },
      ]}
    />,
  );
  expect(html).toContain("1 call run with");
  expect(html).toContain("Base rejected 1 call.");
  expect(html).toContain("Some calls were not measured.");
  expect(html).not.toMatch(/1 calls|internal_unknown_probe_reason/);
});
it("singularizes one retained sample from multiple calls", () => {
  const first = parameterExtensions(parameterState("partial"), "change-1")[0];
  if (!first) throw new Error("Missing extension");
  const html = renderToStaticMarkup(
    <NewParameterCalls
      entries={[{ ...first, run_count: 5, rejected_before: 1, rows: first.rows.slice(0, 1) }]}
    />,
  );
  expect(html).toContain("1 sample shown from 5 calls run.");
});
it("retains every entry and equal or unstable row, escaping event text", () => {
  const entries = parameterExtensions(parameterState("values"), "change-1");
  const first = entries[0];
  if (!first) throw new Error("Missing extension");
  const html = renderToStaticMarkup(
    <NewParameterCalls
      entries={[
        first,
        {
          ...first,
          node_id: "other:<script>",
          target: "other",
          rows: [{ inputs: "<img>", before: "int:7", after: "int:7", unstable: true }],
        },
      ]}
    />,
  );
  expect(html.match(/<li>/g)).toHaveLength(3);
  expect(html.match(/int:7/g)).toHaveLength(2);
  expect(html).toContain("&lt;img&gt;");
  expect(html).toContain("&lt;script&gt;");
  expect(html).toContain("Results varied between runs");
});
it("selects only the latest whole payload for change, revision and head", () => {
  const state = parameterState("mixed"),
    facts = state.changeFacts["change-1"]?.[0];
  if (!facts) throw new Error("Missing facts");
  const history = (state.changeFacts["change-1"] = [
    facts,
    { ...facts, revision: 2 },
    { ...facts, head_commit: "other" },
    { ...facts, change_id: "other" },
  ]);
  expect(parameterExtensions(state, "change-1")[0]?.node_id).toBe(nodeId);
  history.push({ ...facts, parameter_extensions: null });
  expect(parameterExtensions(state, "change-1")).toEqual([]);
  expect(selectNodeChips(state, null)[0]?.extension).toBeUndefined();
});

it("keeps actual measured callers unchanged when extension totals change", () => {
  const state = withMeasuredFacts({
    ...availableFacts,
    parameter_extensions: parameterExtensions(parameterState("mixed"), "change-1"),
  });
  const before = selectReviewBody(state, "change-1");
  expect(before.callers.measured).toBeGreaterThan(0);
  const facts = state.changeFacts["change-1"]?.at(-1);
  if (!facts) throw new Error("Missing facts");
  facts.parameter_extensions = parameterExtensions(parameterState("partial"), "change-1");
  const after = selectReviewBody(state, "change-1");
  expect([after.kind, after.callers, after.summary]).toEqual([
    before.kind,
    before.callers,
    before.summary,
  ]);
});
