import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { PracticePanel, freshAttempt } from "./PracticePanel";
import { manifest, validateManifest } from "./manifest";
import { recording } from "./recording.fixture";
const practice = validateManifest(manifest, recording);
const handlers = {
  onAnswer: vi.fn(),
  onStart: vi.fn(),
  onNext: vi.fn(),
  onReveal: vi.fn(),
  onRestart: vi.fn(),
  onExit: vi.fn(),
};

it("makes desktop Try again primary and keeps Return to ordinary replay secondary", () => {
  const html = renderToStaticMarkup(
    <PracticePanel
      practice={practice}
      attempt={{ ...freshAttempt(), started: true, revealed: true }}
      {...handlers}
    />,
  );
  const classes = (label: string) => {
    const match = html.match(new RegExp(`<button class="([^"]+)"[^>]*>${label}</button>`));
    expect(match, label).not.toBeNull();
    return match?.[1]?.split(" ") ?? [];
  };
  const retry = classes("Try again");
  const exit = classes("Return to ordinary replay");
  for (const primary of [
    "h-(--button-primary-height)",
    "bg-text",
    "text-page",
    "hover:bg-text-hover",
    "text-(length:--label-size)",
    "font-semibold",
  ]) {
    expect(retry).toContain(primary);
    expect(exit).not.toContain(primary);
  }
  expect(exit).toContain("border-control-edge");
  for (const button of [retry, exit]) {
    expect(button).toContain("focus-visible:outline-2");
    expect(button).toContain("focus-visible:outline-offset-2");
    expect(button).toContain("focus-visible:outline-text");
  }
});

it.each([
  [0, "Inventory.reserve, Agent B"],
  [1, "compute_price, Agent A"],
] as const)("uses comma-separated decision copy for case %s", (index, heading) => {
  const source = structuredClone(practice);
  const item = source.cases[index];
  if (!item) throw new Error("Missing case");
  item.proposal.files = [
    { path: "shop/pricing.py", status: "modified", lines_added: 1, lines_removed: 0 },
    { path: "tests/test_pricing.py", status: "modified", lines_added: 2, lines_removed: 0 },
  ];
  const html = renderToStaticMarkup(
    <PracticePanel
      practice={source}
      attempt={{ ...freshAttempt(), started: true, index }}
      {...handlers}
    />,
  );
  expect(html).toContain(`>${heading}</h2>`);
  expect(html).toContain(">shop/pricing.py, tests/test_pricing.py</p>");
  expect(html).not.toContain(" · ");
});

it("withholds historical findings until reveal; uses only recorded proposal facts", () => {
  const attempt = { ...freshAttempt(), started: true };
  const before = renderToStaticMarkup(
    <PracticePanel practice={practice} attempt={attempt} {...handlers} />,
  );
  expect(before).toMatch(/disabled=""[^>]*>Show findings/);
  expect(before).not.toMatch(/Recorded decision:|Matches with recorded/);
  const answers = { "change-2": "Reject", "change-1": "Approve" } as const;
  const revealed = renderToStaticMarkup(
    <PracticePanel
      practice={practice}
      attempt={{ ...attempt, answers, revealed: true }}
      {...handlers}
    />,
  );
  expect(revealed).toContain("Matches with recorded decisions: 1/2.");
  expect(revealed).toContain("Your practice answer: Reject");
  expect(revealed).toContain("Recorded decision: Approve");
  for (const label of ["Agent’s test runs", /review facts/i, /cross-review/i, "Review history"])
    expect(revealed.split(label).length - 1).toBeGreaterThanOrEqual(2);
  expect(revealed).toContain("2 runs, last 29 of 29 passed");
  expect(revealed).toContain("29 passing tests");
  expect(revealed).toContain("32 passing tests");
  expect(revealed).toContain("No possibly affected entries");
  expect(revealed).toContain("9 possibly affected entries");
  expect(revealed).toContain(
    "Missing evidence does not establish a clean review, behavior or safety.",
  );
  expect(revealed).not.toMatch(/Behavioral example|Measured result|Clean review|Safe to merge/);
  expect(Object.values(handlers).every((handler) => handler.mock.calls.length === 0)).toBe(true);
});

it("derives future case titles and slots from decision-time data", () => {
  const future = structuredClone(practice);
  const item = future.cases[0];
  if (!item) throw new Error("Missing case");
  item.proposal.task = "normalize_cart must reject empty carts.";
  const agent = future.cut.agents[item.proposal.agent_id];
  if (!agent) throw new Error("Missing frozen agent");
  agent.slot = "A";
  const attempt = { ...freshAttempt(), started: true };
  const decision = renderToStaticMarkup(
    <PracticePanel practice={future} attempt={attempt} {...handlers} />,
  );
  expect(decision).toContain("normalize_cart, Agent A");
  expect(decision).not.toContain("Inventory, Agent B");
  const findings = renderToStaticMarkup(
    <PracticePanel practice={future} attempt={{ ...attempt, revealed: true }} {...handlers} />,
  );
  expect(findings).toContain('class="font-semibold">normalize_cart</h2>');
});

it.each([false, true])("shows only nonzero omissions (revealed=%s)", (revealed) => {
  const source = structuredClone(practice);
  const entry = source.cases[0];
  if (!entry) throw new Error("Missing case");
  const render = () =>
    renderToStaticMarkup(
      <PracticePanel
        practice={source}
        attempt={{ ...freshAttempt(), started: true, revealed }}
        {...handlers}
      />,
    );
  expect(render()).not.toContain("omitted");
  entry.proposal.impact.omitted_by_depth = 12;
  expect(render()).toContain("12 more omitted by depth.");
  expect(render()).not.toContain("omitted by limit");
  entry.proposal.impact.omitted_by_depth = 0;
  entry.proposal.impact.omitted_by_limit = 7;
  expect(render()).toContain("7 more omitted by limit.");
  expect(render()).not.toContain("omitted by depth");
});

it("uses the case count as the findings score denominator", () => {
  const source = structuredClone(practice);
  const entry = source.cases[0];
  if (!entry) throw new Error("Missing case");
  source.cases.push({ ...entry, changeId: "third-change" });
  const html = renderToStaticMarkup(
    <PracticePanel
      {...handlers}
      practice={source}
      attempt={{
        ...freshAttempt(),
        started: true,
        revealed: true,
        answers: { "change-2": "Approve" },
      }}
    />,
  );
  expect(html).toContain('<p role="status">Matches with recorded decisions: 1/3.</p>');
});
