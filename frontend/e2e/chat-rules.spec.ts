import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";
import type { ServerEvent } from "@contracts";

import { before, engaged, installHolds, release } from "./holds";

const events = readFileSync(
  new URL("../../fixtures/events/scripted-rules.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const proposal = events.find(
  (event) => event.type === "change.proposed" && event.payload.change_id === "change-1",
);
const resolution = events.find(
  (event) => event.type === "change.resolved" && event.payload.change_id === "change-1",
);
const reverted = events.find(
  (event) => event.type === "revert.resolved" && event.payload.change_id === "change-1",
);
if (
  proposal?.type !== "change.proposed" ||
  resolution?.type !== "change.resolved" ||
  reverted?.type !== "revert.resolved"
)
  throw new Error("Rules recording must contain the proposal, merge and Revert for change-1");
const hold = before(events, reverted, 1787);

test.beforeEach(async ({ page }) => {
  await page.addInitScript(installHolds, [hold]);
});

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("Chat rules receipt opens the recorded review", async ({ page }) => {
  const candidate = { change_id: "change-1", agent_id: "agent-1", revision: 1 };
  expect(proposal.payload).toMatchObject(candidate);
  expect(resolution.payload).toMatchObject({
    ...candidate,
    decision: "approved",
    merge: "merged",
    decided_by: "rules",
  });
  const rules = resolution.payload.assessment?.rules;
  expect(rules?.map(({ rule }) => rule)).toEqual([
    "added_tests_fail_before",
    "combined_tests",
    "cross_review",
    "existing_tests_untouched",
    "known_spend",
    "protected_paths",
    "small_change",
    "small_cone",
    "test_integrity",
  ]);
  expect(rules?.every(({ result }) => result === "pass")).toBe(true);

  await page.goto("/rules");
  await engaged(page, hold);
  const workspace = page.getByRole("group", { name: "Workspace view", exact: true });
  await workspace.getByRole("button", { name: "Oversight", exact: true }).click();
  await page
    .getByRole("article", { name: "Agent A", exact: true })
    .getByRole("button", { name: "Conversation", exact: true })
    .click();
  const chat = workspace.getByRole("button", { name: "Chat", exact: true });
  await chat.click();
  await expect(
    page.getByRole("tab", { name: /Agent A · conversation$/, includeHidden: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(chat).toHaveAttribute("aria-pressed", "true");
  const receipt = page
    .getByRole("list", { name: "Conversation timeline", exact: true })
    .getByRole("article", { name: "Merged by your rules", exact: true });
  await expect(receipt).toBeVisible();
  await expect(receipt.getByText("Merged by your rules", { exact: true })).toBeVisible();
  await expect(
    receipt.getByText(
      "Tests pass, a second model's review found nothing blocking, and the change is small (at most 3 files and 60 lines). 6 more rules passed.",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    receipt.getByText("Merge mode: Merge alone when safe", { exact: true }),
  ).toBeVisible();
  await receipt.getByRole("button", { name: "Open its review", exact: true }).click();
  await expect(page.getByRole("tab", { name: /Review · change-1/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  const review = page.getByRole("complementary", { name: "Review panel", exact: true });
  await expect(review).toBeVisible();
  await expect(review.getByText("Merged by your rules", { exact: true })).toBeVisible();
  await expect(chat).toHaveAttribute("aria-pressed", "true");
  await review.getByText("Details", { exact: true }).click();
  for (const label of [
    "Added tests fail before the change: passed",
    "Tests on the combined result: passed",
    "Second-model review: passed",
    "Existing tests untouched: passed",
    "Known spend: passed",
    "Protected paths: passed",
    "Change size: passed",
    "Possibly affected functions: passed",
    "Test integrity: passed",
  ]) {
    await expect(review.getByText(label, { exact: true })).toBeVisible();
  }
  await release(page, hold);
});
