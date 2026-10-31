import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { applyEvent, createInitialState } from "../state";
import { parseEvent } from "../transport";
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
    const html = renderToStaticMarkup(<ReviewHeader state={state} changeId={id} />);
    expect([...html.matchAll(/<li .*?<\/li>/g)].map(([li]) => words(li))).toEqual(copy);
    const caption = words(html.match(/<p .*?<\/p>/)?.[0] ?? "");
    expect(caption).toBe(
      `Agent ${String(state.agents[agent]?.slot)} · ${agent === "agent-2" ? "Muse Spark Open · cost unknown" : "Muse Spark · &lt;US$0.01"} · step ${agent === "agent-2" ? "11" : "8"} of 30 · worktree ${agent} · base ${String(state.changes[id]?.proposal?.base_commit.slice(0, 7))}`,
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
