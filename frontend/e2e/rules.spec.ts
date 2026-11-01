// Exact fake-only backend recording; public replay semantics and the shared reducer.
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";
import type { ServerEvent } from "@contracts";

import { before, engaged, installHolds, release } from "./holds";

const text = readFileSync(
  new URL("../../fixtures/events/scripted-rules.jsonl", import.meta.url),
  "utf8",
);
const receipt = JSON.parse(
  readFileSync(
    new URL("../../fixtures/events/scripted-rules.jsonl.provenance.json", import.meta.url),
    "utf8",
  ),
) as { exporter: string; sha256: string; event_count: number };
const events = text
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const resolution = events.find((e) => e.type === "change.resolved");
const reverted = events.find((e) => e.type === "revert.resolved");
const notice = events.find((e) => e.type === "error" && e.payload.message.startsWith("Needs you"));
if (resolution?.type !== "change.resolved" || reverted?.type !== "revert.resolved" || !notice)
  throw new Error("Rules recording must contain a merge, Revert and Needs you");
const outcome = reverted.payload;
if (outcome.result !== "reverted" || !outcome.revert_commit)
  throw new Error("Rules recording must contain a successful inverse commit");
const inverseCommit = outcome.revert_commit;
const holds = {
  merge: before(events, resolution, 1785),
  revert: before(events, reverted, 1787),
  notice: before(events, notice, 1789),
};

test("rules recording: provenance, reasons, Revert and Needs you remain read-only", async ({
  page,
}) => {
  expect(receipt.exporter).toBe("oversight.fake_recording.v1");
  expect(receipt.sha256).toBe(createHash("sha256").update(text).digest("hex"));
  expect(receipt.event_count).toBe(events.length);
  expect(resolution.payload.decided_by).toBe("rules");
  expect(resolution.payload.assessment?.rules).toHaveLength(9);
  expect(resolution.payload.assessment?.rules.every((r) => r.result === "pass")).toBe(true);
  const commands: string[] = [];
  page.on("request", (request) => {
    if (["POST", "PUT", "DELETE"].includes(request.method())) commands.push(request.url());
  });
  await page.addInitScript(installHolds, Object.values(holds));
  await page.goto("/rules");
  await engaged(page, holds.merge);
  const review = page.getByRole("complementary", { name: "Review panel" });
  await page
    .getByRole("article", { name: "Agent A" })
    .getByRole("button", { name: "Review change" })
    .click();
  await release(page, holds.merge);
  await engaged(page, holds.revert);
  await expect(review.getByText("Merged by your rules", { exact: true })).toBeVisible();
  await expect(review.getByText(/Tests pass, a second model's review/)).toBeVisible();
  await expect(review.getByText("Revert is not available in a replay.")).toBeVisible();
  await expect(review.getByRole("button", { name: "Revert", exact: true })).toHaveCount(0);
  await review.getByText("Details", { exact: true }).click();
  await expect(review.getByText("Protected paths: passed", { exact: true })).toBeVisible();
  await expect(review.getByText("Known spend: passed", { exact: true })).toBeVisible();
  await release(page, holds.revert);
  await engaged(page, holds.notice);
  await expect(review.getByText(`Reverted in main ${inverseCommit.slice(0, 7)}`)).toBeVisible();
  await expect(review.getByText("Merged by your rules", { exact: true })).toBeVisible();
  await review.getByRole("button", { name: "Close review" }).click();
  await page
    .getByRole("article", { name: "Agent B" })
    .getByRole("button", { name: "Review change" })
    .click();
  await release(page, holds.notice);
  await expect(page.locator("#root")).toHaveAttribute("data-seq", String(events.at(-1)?.seq));
  await expect(
    review.getByText("Needs you because a check was unavailable", { exact: true }),
  ).toBeVisible();
  await expect(review.getByText(/review_invalid: invalid reviewer output/)).toBeVisible();
  await expect(review.getByRole("button", { name: "Approve and merge" })).toBeDisabled();
  await expect(review.getByText(/^Replay of a scripted fake recording/)).toBeVisible();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(
    page.getByText("Setting the merge mode is not available in a replay."),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /Merge alone when safe/ })).toHaveAttribute(
    "aria-disabled",
    "true",
  );
  expect(commands).toEqual([]);
});
