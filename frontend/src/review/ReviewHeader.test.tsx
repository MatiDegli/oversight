import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { applyEvent, createInitialState } from "../state";
import { parseEvent } from "../transport";
import { presentationFor } from "../teams/presentation";
import { ChangeFactsRow } from "./ChangeFactsRow";
import { ReviewHeader } from "./ReviewHeader";
import { headerCases, headerState } from "./ReviewHeader.fixture";

vi.mock("../editor", () => ({ EditorDiff: () => <div>Inline diff</div> }));
const words = (html: string) => html.replace(/<[^>]*>/g, "");

it.each([
  ["a", ["1 file", "+2 −0", "28 passed", "No exact callers"]],
  ["b", ["1 file", "+2 −0", "28 passed", "9 possibly affected · up to distance 2"]],
  ["merged", ["1 file", "+2 −0", "28 passed", "No exact callers"]],
  ["approvedCone", ["1 file", "+2 −0", "28 passed", "9 possibly affected · up to distance 2"]],
] as const)("shows only four decision chips for fixture %s", (name, copy) => {
  const html = renderToStaticMarkup(
    <ReviewHeader state={headerState(name)} changeId={headerCases[name].changeId} />,
  );
  expect([...html.matchAll(/<li .*?<\/li>/g)].map(([li]) => words(li))).toEqual(copy);
  for (const [li] of html.matchAll(/<li .*?<\/li>/g)) expect(li).not.toContain("font-mono");
  expect(html).not.toMatch(
    /CircleHelp|circle-help|aria-label="Possible callers"|aria-label="Base commit"/,
  );
});

it("uses recorded model, cost, step, worktree and base in one caption, preserving unknown cost", () => {
  const log = readFileSync(
    new URL("../../../fixtures/events/recorded-demo.jsonl", import.meta.url),
    "utf8",
  );
  const state = log
    .trim()
    .split("\n")
    .map(parseEvent)
    .filter((e) => e.seq <= 62)
    .reduce(applyEvent, createInitialState());
  for (const [id, agent, copy] of [
    ["change-2", "agent-2", ["2 files", "+9 −0", "29 passed", "No exact callers"]],
    [
      "change-1",
      "agent-1",
      ["2 files", "+28 −0", "32 passed", "9 possibly affected · up to distance 2"],
    ],
  ] as const) {
    const identity = presentationFor(state, agent);
    if (!identity) throw new Error("Missing recorded author identity");
    const html = renderToStaticMarkup(<ReviewHeader state={state} changeId={id} />);
    expect([...html.matchAll(/<li .*?<\/li>/g)].map(([li]) => words(li))).toEqual(copy);
    const caption = words(html.match(/<p .*?<\/p>/)?.[0] ?? "");
    expect(caption).toBe(
      `Agent ${identity.label} · ${agent === "agent-2" ? "Muse Spark Open · cost unknown" : "Muse Spark · &lt;US$0.01"} · step ${agent === "agent-2" ? "11" : "8"} of 30 · worktree ${agent} · base ${String(state.changes[id]?.proposal?.base_commit.slice(0, 7))}`,
    );
    expect(html).toContain('<code class="font-mono"');
  }
});

it("keeps the live diff's complete facts and existing copy", () => {
  const html = renderToStaticMarkup(
    <ChangeFactsRow state={headerState("b")} changeId="change-1" live />,
  );
  expect([...html.matchAll(/<li aria-label="([^"]+)"/g)].map(([, label]) => label)).toEqual([
    "Files",
    "Lines",
    "Tests",
    "Base commit",
    "Impact cone",
    "Model and cost",
    "Worktree",
    "Steps",
    "Possible callers",
  ]);
  const text = words(html);
  for (const copy of [
    "1 file",
    "+2 −0",
    "28 passed",
    "Base 8cbf753",
    "9 functions possibly affected, up to distance 2",
    "Muse Spark · US$0.01",
    "Worktree agent-1",
    "Step 5 of 30",
    "+2 possible callers, not followed",
  ])
    expect(text).toContain(copy);
  expect(html).toContain("lucide-circle-help");
});

it("shows recorded stage times and confirmed revision rounds beside the provenance", () => {
  const state = readFileSync(
    new URL("../../../fixtures/events/stage-clocks.jsonl", import.meta.url),
    "utf8",
  )
    .trim()
    .split("\n")
    .map(parseEvent)
    .reduce(applyEvent, createInitialState());
  const html = renderToStaticMarkup(<ReviewHeader state={state} changeId="change-1" />);
  expect(html).toContain("1 revision round");
  expect(html).toContain('aria-label="Stage times"');
  expect(html).toContain('aria-label="Agent work, 2 minutes 30 seconds"');
  expect(html).toContain('aria-label="Automatic checks, 30 seconds"');
  expect(html).toContain('aria-label="Waiting for your decision, 1 minute 30 seconds"');
  expect(html).toContain('aria-label="Test runs, 1 minute"');
  expect([...html.matchAll(/<li .*?<\/li>/g)]).toHaveLength(4);
});

it("shows only known agent work from the bundled recording, omitting unknown waits", () => {
  const state = readFileSync(
    new URL("../../../fixtures/events/recorded-demo.jsonl", import.meta.url),
    "utf8",
  )
    .trim()
    .split("\n")
    .map(parseEvent)
    .reduce(applyEvent, createInitialState());
  for (const [changeId, seconds] of [
    ["change-1", 21],
    ["change-2", 11],
  ] as const) {
    const html = renderToStaticMarkup(<ReviewHeader state={state} changeId={changeId} />);
    expect(html).toContain(`aria-label="Agent work, ${String(seconds)} seconds"`);
    expect(html).not.toMatch(/Automatic checks|Waiting for your decision|Test runs/);
  }
});

const longTask = `Fix the discount rounding in compute_price ${"so every order total matches the receipt to the cent, ".repeat(4)}and keep the callers unchanged.`;

function withTask(task: string) {
  const state = structuredClone(headerState("b"));
  const change = state.changes["change-1"];
  if (!change?.proposal) throw new Error("Missing fixture proposal");
  change.proposal.task = task;
  return state;
}

it("clamps the task headline to two lines and offers no toggle for a short task", () => {
  const html = renderToStaticMarkup(<ReviewHeader state={headerState("b")} changeId="change-1" />);
  const heading = /<h2 [^>]*>/.exec(html)?.[0] ?? "";
  expect(heading).toContain("line-clamp-2");
  expect(html).not.toContain("Show full task");
  expect(html).not.toContain("Show less");
});

it("keeps a long task whole in the DOM behind the clamp, with a collapsed Show full task toggle", () => {
  expect(longTask.length).toBeGreaterThan(140);
  const html = renderToStaticMarkup(
    <ReviewHeader state={withTask(longTask)} changeId="change-1" />,
  );
  const heading = /<h2 [^>]*>(.*?)<\/h2>/.exec(html);
  expect(heading?.[0]).toContain("line-clamp-2");
  expect(heading?.[1]).toBe(longTask);
  const toggle = /<button [^>]*>Show full task<\/button>/.exec(html)?.[0] ?? "";
  expect(toggle).toContain('type="button"');
  expect(toggle).toContain('aria-expanded="false"');
  expect(toggle).not.toMatch(/autofocus|tabindex/i);
});
