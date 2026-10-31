import { chromium, expect } from "@playwright/test";
import type { Browser } from "@playwright/test";
import { renderToStaticMarkup } from "react-dom/server";
import { afterAll, beforeAll, it } from "vitest";
import inboxLog from "../../../fixtures/events/handwritten-inbox-grants.jsonl?raw";
import level2Log from "../../../fixtures/events/scripted-level2.jsonl?raw";
import { applyEvent, createInitialState } from "../state";
import { parseEvent } from "../transport";
import { ChangeCard } from "./ChangeCard";
import { ReviewFacts } from "./ReviewFacts";
import { ReviewPanel } from "./ReviewPanel";

let browser: Browser;
beforeAll(async () => {
  browser = await chromium.launch();
});
afterAll(async () => {
  await browser.close();
});

it("retains the merged inbox card's grant receipt and comparison map", async () => {
  const state = inboxLog
    .trim()
    .split("\n")
    .map(parseEvent)
    .reduce(applyEvent, createInitialState());
  const page = await browser.newPage();
  try {
    await page.setContent(renderToStaticMarkup(<ChangeCard state={state} changeId="change-1" />));
    const card = page.getByRole("complementary", { name: "Change card" });
    await expect(card.getByRole("heading", { name: "Merged by rules", exact: true })).toBeVisible();
    await expect(card.getByText(/Under grant/)).toContainText("grant-pricing");
    await expect(
      card.getByRole("heading", { name: "What was compared · 1 function, 24 inputs", exact: true }),
    ).toBeVisible();
    await expect(
      card.getByRole("region", { name: "Compared functions and callers" }),
    ).toBeVisible();
  } finally {
    await page.close();
  }
});

const events = level2Log.trim().split("\n").map(parseEvent);
const recordedFacts = events.find(
  (event) =>
    event.type === "change.facts_recorded" &&
    event.payload.edited_paths.includes("shop/pricing.py"),
);
if (recordedFacts?.type !== "change.facts_recorded")
  throw new Error("Missing recorded level 2 pricing facts");
const changeId = recordedFacts.payload.change_id;
const state = events
  .filter((event) => event.seq <= recordedFacts.seq)
  .reduce(applyEvent, createInitialState());
const proposal = state.changes[changeId]?.proposal;
if (!proposal) throw new Error("Missing recorded level 2 proposal");

it("keeps recorded test-run evidence available in the closed Details component", async () => {
  const page = await browser.newPage();
  try {
    await page.setContent(
      renderToStaticMarkup(<ReviewFacts state={state} proposal={proposal} panel />),
    );
    const signals = page.getByRole("region", { name: "Details" });
    await expect(signals.locator("details").first()).not.toHaveAttribute("open");
    await expect(signals.getByText(/Tests in this change:/)).toBeHidden();
    await signals.getByText("Details", { exact: true }).click();
    await expect(signals.locator("details").first()).toHaveAttribute("open", "");
    await expect(signals).not.toContainText("Tests in this change: 0 recorded test changes");
    await expect(signals).not.toContainText("added_tests producer unavailable (package 4)");
    await expect(signals.getByRole("list", { name: "Added tests" })).toContainText(
      "tests/test_pricing.py::test_unknown_coupon_keeps_total: passes before and after",
    );
    for (const phase of ["base_probe", "head_probe", "agent", "proposal"])
      await expect(signals).toContainText(`(${phase}): passed`);
    await expect(signals).toContainText("run_findings producer unavailable (package 5)");
    await expect(signals).not.toContainText("Size:");
  } finally {
    await page.close();
  }
});

it("renders recorded level 2 facts without comparisons as state d", async () => {
  const page = await browser.newPage();
  try {
    await page.setContent(
      renderToStaticMarkup(
        <ReviewPanel state={state} changeId={changeId} onClose={() => undefined} />,
      ),
    );
    const panel = page.getByRole("complementary", { name: "Review panel" });
    await expect(
      panel.getByText("Nothing was compared for this change.", { exact: true }),
    ).toBeVisible();
    await expect(panel.getByRole("listitem", { name: "Tests", exact: true })).toContainText(
      `${String(proposal.tests.passed)} passed`,
    );
    await expect(panel.getByRole("region", { name: "Review signals" })).toHaveCount(0);
    await expect(panel.getByRole("region", { name: "What was compared", exact: true })).toHaveCount(
      0,
    );
    await expect(panel.getByText("Details", { exact: true })).toHaveCount(0);
    await expect(panel.getByRole("button", { name: /shop\/pricing.py/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  } finally {
    await page.close();
  }
});
