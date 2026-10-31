import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { PhonePractice } from "./PhonePractice";
import { diffPreview } from "./PhoneReview";
import { PhoneDiff } from "./PhoneDiff";
import { AgentLine, PhoneGraph } from "./PhoneGraph";
import { freshAttempt } from "./PracticePanel";
import { manifest, validateManifest } from "./manifest";
import { recording } from "./recording.fixture";
import { calledHelper, countAt, diffRows, wrapSource } from "./phoneEvidence";
const practice = validateManifest(manifest, recording);
const actions = {
  onStart: vi.fn(),
  onAnswer: vi.fn(),
  onNext: vi.fn(),
  onReveal: vi.fn(),
  onRestart: vi.fn(),
  onExit: vi.fn(),
};
it.each([false, true])("lists per-agent impact counts without zeros (merged: %s)", (merged) => {
  const html = renderToStaticMarkup(<PhoneGraph practice={practice} merged={merged} />);
  expect(html).toContain('class="pp-graph-foot">A: 9 possibly affected, B: none</p>');
});
it.each([
  ["muse-spark", "Recorded contributor"],
  ["unlisted-model", "unlisted-model"],
])("labels agent B from its recorded model %s", (model, label) => {
  const events = structuredClone(practice.events);
  for (const event of events) {
    if (event.type === "agent.status" && event.payload.slot === "B") event.payload.model = model;
    if (event.type === "run.started") {
      const fallback = event.payload.models.find((entry) => entry.id === "muse-spark");
      if (!fallback) throw new Error("Missing fallback model");
      fallback.label = "Recorded contributor";
    }
  }
  const fallback = validateManifest(manifest, {
    ...recording,
    text: events.map((event) => JSON.stringify(event)).join("\n"),
  });
  const entry = fallback.cases[0];
  if (!entry) throw new Error("Missing case");
  const html = renderToStaticMarkup(<AgentLine practice={fallback} entry={entry} />);
  expect(html).toContain("Agent B");
  expect(html).toContain(`<span>${label}</span>`);
  expect(html).not.toContain("Muse Spark Open");
});
it.each([
  [0, "Both were approved in the recording."],
  [1, "1 approved and 1 rejected in the recording."],
  [2, "Both were rejected in the recording."],
])("derives the finish description with %i recorded rejections", (rejections, description) => {
  const candidate = structuredClone(manifest);
  const events = structuredClone(practice.events);
  for (const entry of candidate.cases.slice(0, rejections)) {
    entry.reference = "Reject";
    for (const event of events)
      if (event.type === "change.resolved" && event.payload.change_id === entry.changeId) {
        event.payload.decision = "rejected";
        event.payload.merge = null;
        event.payload.merge_commit = null;
      }
  }
  const changed = validateManifest(candidate, {
    ...recording,
    text: events.map((event) => JSON.stringify(event)).join("\n"),
  });
  const html = renderToStaticMarkup(
    <PhonePractice
      {...actions}
      practice={changed}
      attempt={{ ...freshAttempt(), started: true, revealed: true, finished: true }}
    />,
  );
  expect(html).toContain(description);
});
it.each([0, 1])(
  "starts decision %i with the verbatim agent claim, facts and all code collapsed",
  (index) => {
    const entry = practice.cases[index];
    if (!entry) throw new Error("Missing case");
    const html = renderToStaticMarkup(
      <PhonePractice
        {...actions}
        practice={practice}
        attempt={{ ...freshAttempt(), started: true, index }}
      />,
    );
    expect(html).toContain(entry.title);
    expect(html).toContain(entry.proposal.summary);
    expect(html).toContain("Nothing was compared for this change.");
    expect(html).toContain('aria-label="Agent note"');
    expect(html.indexOf("Review lead")).toBeLessThan(html.indexOf("Agent note"));
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("Show diff");
    expect(html).not.toMatch(
      /class="pp-file|class="pp-code|>Tests added<|>Called helper|Head |Revision|>Next<|>Show findings</,
    );
    expect(html).toContain(entry.proposal.task);
    expect(html).toContain(">Approve</button>");
    expect(html).toContain(">Reject</button>");
  },
);
it("derives cone folds and the new exact callee from the recording", () => {
  const html = renderToStaticMarkup(
    <PhonePractice {...actions} practice={practice} attempt={freshAttempt()} />,
  );
  expect(html).toContain("7 direct callers");
  expect(html).toContain("2 at distance 2");
  expect(html).toContain("New exact call");
  const first = practice.cases[0];
  if (!first) throw new Error("Missing case");
  expect(calledHelper(practice, first)?.name).toBe("_check_quantity");
  expect(practice.cases[0]?.proposal.impact.entries).toEqual([]);
});
it.each([1, 2])("derives decision previews and graph counts with only distance %i", (distance) => {
  const changed = structuredClone(practice);
  const entry = changed.cases[1];
  if (!entry) throw new Error("Missing case");
  entry.proposal.task = "Read this verbatim task, including its final words.";
  entry.proposal.impact.entries = entry.proposal.impact.entries
    .filter((node) => node.distance === distance)
    .slice(0, 2);
  const count = countAt(entry, distance);
  const preview = `${String(count)} ${distance === 1 ? "direct callers" : "at distance 2"}`;
  const html = renderToStaticMarkup(
    <PhonePractice
      {...actions}
      practice={changed}
      attempt={{ ...freshAttempt(), started: true, index: 1 }}
    />,
  );
  expect(html).toContain(`Recorded task</span> <span>${entry.proposal.task}`);
  expect(html).not.toContain("Possibly affected</span>");
  const graph = renderToStaticMarkup(
    <PhoneGraph practice={changed} entry={entry} merged={false} />,
  );
  expect(graph).toContain(preview);
  expect(html).not.toMatch(/Possibly affected<\/span> <span>(?:0 |[^<]*, 0 )/);
});
it("keeps the zero-cone row absent and the diff disclosures boxed with right chevrons", () => {
  const entry = practice.cases[0];
  if (!entry) throw new Error("Missing case");
  const html = renderToStaticMarkup(
    <PhonePractice
      {...actions}
      practice={practice}
      attempt={{ ...freshAttempt(), started: true }}
    />,
  );
  expect(html).not.toContain("Possibly affected</span>");
  expect(html).toContain("pp-decision-row");
  expect(html).toContain("lucide-chevron-down");
  expect(html).not.toContain("lucide-chevron-right");
  const diff = renderToStaticMarkup(<PhoneDiff practice={practice} entry={entry} />);
  expect(diff).toContain('class="pp-disclosure"');
  expect(diff).toContain("lucide-chevron-right");
  expect(diff).not.toContain("pp-decision-row");
});
it("reveals both solid merged pieces only with recorded decisions", () => {
  const html = renderToStaticMarkup(
    <PhonePractice
      {...actions}
      practice={practice}
      attempt={{
        ...freshAttempt(),
        started: true,
        revealed: true,
        answers: { "change-2": "Approve", "change-1": "Reject" },
      }}
    />,
  );
  expect(html).toContain("Recorded decisions");
  for (const label of ["Agent’s test runs", /review facts/i, /cross-review/i, "Review history"])
    expect(html.split(label).length - 1).toBeGreaterThanOrEqual(2);
  expect(html).toContain("2 runs, last 29 of 29 passed");
  expect(html).toContain("Not recorded for this revision: review facts and cross-review.");
  expect(html).toContain("Both merged");
  expect(html).toContain("Same decision");
  expect(html).toContain("Different decision");
  expect(html).not.toMatch(/is-pending|New exact call/);
});
it("preserves source when wrapping at Python boundaries without orphan closers", () => {
  const lines = practice.events.flatMap((event) =>
    event.type === "change.updated"
      ? [event.payload.file.original, event.payload.file.modified].flatMap(
          (source) => source?.split("\n") ?? [],
        )
      : [],
  );
  for (const capacity of [26, 30])
    for (const source of lines) {
      const parts = wrapSource(source, capacity);
      expect(parts.map((part) => part.text).join("")).toBe(source);
      expect(parts.slice(1).every((part) => part.indent > 0)).toBe(true);
      expect(parts.slice(1).some((part) => /^[\s\])}:,.;]+$/.test(part.text))).toBe(false);
      expect(parts.every((part) => part.text.length + part.indent <= capacity)).toBe(true);
    }
});
it("computes actual additions and deletions with original line numbers", () => {
  expect(diffRows("old\nsame\n", "new\nsame\n")).toEqual([
    { kind: "added", line: 1, text: "new" },
    { kind: "removed", line: 1, text: "old" },
    { kind: "context", line: 2, text: "same" },
  ]);
});

it("renders the retained finish state after a phone/desktop remount", () => {
  const attempt = {
    ...freshAttempt(),
    started: true,
    revealed: true,
    finished: true,
    answers: { "change-2": "Approve", "change-1": "Reject" } as const,
  };
  const html = renderToStaticMarkup(
    <PhonePractice {...actions} practice={practice} attempt={attempt} />,
  );
  expect(html).toContain("You’ve reviewed both changes.");
  expect(html).toContain("1 of 2");
  expect(html).toContain("decisions matched");
  expect(html).toContain("Agreement is not proof");
  expect(html).toContain("Watch the recording");
});

it("uses words for a finish with no matching decisions", () => {
  const html = renderToStaticMarkup(
    <PhonePractice
      {...actions}
      practice={practice}
      attempt={{
        ...freshAttempt(),
        started: true,
        revealed: true,
        finished: true,
        answers: { "change-2": "Reject", "change-1": "Reject" },
      }}
    />,
  );
  expect(html).toContain('<p class="pp-score">None of your decisions matched the recording.</p>');
});

it.each([1, 2])("previews a diff with %i files", (count) => {
  const proposal = practice.cases[0]?.proposal;
  if (!proposal) throw new Error("Missing proposal");
  expect(diffPreview({ ...proposal, files: proposal.files.slice(0, count) })).toBe(
    `shop/inventory.py${count === 2 ? " and 1 more file" : ""} · +9 −0`,
  );
});
