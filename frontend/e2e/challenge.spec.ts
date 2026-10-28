import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import type { GraphProbeHost } from "../src/graph";

const practice = (page: Page) =>
  page.getByRole("region", { name: "Two-change practice", exact: true });
const positions = (page: Page) =>
  page
    .locator('[aria-label="Repository graph"]')
    .evaluate((host: GraphProbeHost) => host.oversightGraph?.positions());
async function provenance(page: Page) {
  const status = page.getByLabel("Status line");
  await expect(status).toContainText("Recorded run · Oct 27, 2026 · Real recorded run");
  expect((await status.textContent())?.match(/Oct 27, 2026/g)).toHaveLength(1);
}
async function capture(page: Page, name: string) {
  await page.screenshot({ path: `../.shots/challenge/${name}.png` });
}

test("actual static two-change practice: local decisions, frozen shell and honest findings", async ({
  page,
}) => {
  const requests: string[] = [];
  const errors: string[] = [];
  page.on("request", (request) => requests.push(new URL(request.url()).pathname));
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/oversight/");
  await expect(practice(page)).toHaveCount(0);
  await provenance(page);
  await expect
    .poll(async () => Object.keys((await positions(page)) ?? {}).length)
    .toBeGreaterThan(0);
  await page.getByRole("button", { name: "Details", exact: true }).click();
  await expect
    .poll(() =>
      page
        .locator('[aria-label="Repository graph"]')
        .evaluate((host: GraphProbeHost) => host.oversightGraph?.animating()),
    )
    .toBe(false);
  const graph = await page.locator('[aria-label="Repository graph"]').elementHandle();
  const before = await positions(page);
  await page.getByRole("button", { name: "Find a function, file or command", exact: true }).click();
  await page.getByRole("combobox").fill("shop/pricing.py");
  await expect(page.getByRole("option", { selected: true })).toContainText("pricing.py");
  await page.getByRole("combobox").press("Enter");
  const active = await page.getByRole("tab", { selected: true }).textContent();
  const entry = page.getByRole("button", { name: "Two-change practice", exact: true });
  await entry.focus();
  await page.clock.install();
  await page.clock.runFor(8000);
  await expect(entry).toBeFocused();
  await expect(page.getByRole("tab", { selected: true })).toHaveText(active ?? "");
  await page.getByRole("button", { name: "Two-change practice", exact: true }).click();
  await expect(page.locator("#root")).toHaveAttribute("data-seq", "63");
  await expect(
    practice(page).getByRole("heading", { name: "Would you approve these changes?" }),
  ).toBeVisible();
  await provenance(page);
  await capture(page, "introduction-1920");
  const frozen = await positions(page);
  for (const [id, point] of Object.entries(before ?? {})) expect(frozen?.[id]).toEqual(point);
  const start = practice(page).getByRole("button", { name: "Start practice" });
  await start.focus();
  await page.clock.fastForward(120_000);
  await expect(start).toBeFocused();
  await expect(page.getByRole("tab", { name: /Two-change practice/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page.locator("#root")).toHaveAttribute("data-seq", "63");
  await start.click();
  const panel = practice(page);
  await expect(panel).toContainText("Inventory · Agent B");
  await expect(panel).toContainText("29 passing tests");
  await expect(panel.getByRole("button", { name: "Show findings" })).toBeDisabled();
  const approve = panel.getByRole("button", { name: "Approve", exact: true });
  await approve.focus();
  for (const key of ["Control+Enter", "Enter", "Space"]) {
    await page.keyboard.press(key);
    await expect(panel.getByText(/Practice answer saved/)).toHaveCount(0);
  }
  await approve.dispatchEvent("click", { detail: 0 });
  await expect(panel.getByText(/Practice answer saved/)).toHaveCount(0);
  await capture(page, "decision-1920");
  await panel.getByRole("button", { name: "Reject", exact: true }).dblclick();
  await expect(panel).toContainText("Practice answer saved: Reject");
  await expect(approve).toBeDisabled();
  await expect(panel).toContainText("Decision 1 of 2");
  await page.getByRole("button", { name: "Close tab Two-change practice" }).click();
  await expect(panel).toHaveCount(0);
  await page.getByRole("button", { name: "Open practice", exact: true }).click();
  await expect(panel).toContainText("Practice answer saved: Reject");
  await expect(panel.getByRole("button", { name: "Show findings" })).toBeDisabled();
  await panel.getByRole("button", { name: "Next", exact: true }).click();
  await expect(panel).toContainText("Pricing · Agent A");
  await expect(panel).toContainText("32 passing tests");
  await expect(panel).toContainText("9 possibly affected entries");
  await expect(panel.getByText(/Recorded decision:/)).toHaveCount(0);
  await approve.click();
  await expect(panel).toContainText("Practice answer saved: Approve");
  await expect(panel).toContainText("Decision 2 of 2");
  await panel.getByRole("button", { name: "Show findings" }).dblclick();
  await expect(panel).toContainText("Matches with recorded decisions: 1/2.");
  const inventory = panel.getByRole("region", { name: "Findings for change-2" });
  await expect(inventory).toContainText("Your practice answer: Reject");
  await expect(inventory).toContainText("Recorded decision: Approve · merged into main");
  await expect(inventory).toContainText("29 passing tests · 0 possibly affected entries");
  await expect(panel.getByRole("region", { name: "Findings for change-1" })).toContainText(
    "32 passing tests · 9 possibly affected entries",
  );
  await expect(panel).toContainText(
    "Detailed review facts, cross-review and behavior evidence were not recorded.",
  );
  await expect(
    panel.getByText(/Behavioral example|Measured result|Clean review|Safe to merge/),
  ).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Possibly affected", exact: true })).toBeVisible();
  await expect(page.locator("#root")).toHaveAttribute("data-seq", "63");
  await provenance(page);
  await capture(page, "reveal-1920");
  await page.setViewportSize({ width: 1280, height: 1080 });
  await expect(page.getByText("This window is too narrow.", { exact: false })).toBeHidden();
  expect((await page.locator(".focus-panel").boundingBox())?.width).toBe(608);
  expect((await page.locator(".graph-stage").boundingBox())?.width).toBe(560);
  await provenance(page);
  expect(
    await page.locator("header").evaluate((header) => header.scrollWidth <= header.clientWidth),
  ).toBe(true);
  await capture(page, "reveal-1280");
  await page.getByRole("button", { name: "Maximize focus panel" }).click();
  await page.getByRole("button", { name: "Show graph", exact: true }).click();
  expect(
    await graph.evaluate(
      (host) => host === document.querySelector('[aria-label="Repository graph"]'),
    ),
  ).toBe(true);
  expect(await positions(page)).toEqual(frozen);
  await panel.getByRole("button", { name: "Try again" }).click();
  await panel.getByRole("button", { name: "Start practice" }).click();
  await expect(approve).toBeEnabled();
  await expect(panel.getByText(/Practice answer saved|Matches with recorded/)).toHaveCount(0);
  await panel.getByRole("button", { name: "Return to ordinary replay" }).click();
  await expect(panel).toHaveCount(0);
  for (let i = 0; i < 80; i++) await page.clock.fastForward(2000);
  await expect(page.locator("#root")).toHaveAttribute("data-seq", "69");
  await provenance(page);
  await page.reload();
  await page.getByRole("button", { name: "Two-change practice", exact: true }).click();
  await expect(practice(page).getByRole("button", { name: "Start practice" })).toBeVisible();
  expect(
    requests.filter((path) =>
      /^\/(?:oversight\/)?(agents|changes|events|files|graph|health|impact|tree|history|queue|leads)(\/|\?|$)/.test(
        path,
      ),
    ),
  ).toEqual([]);
  expect(errors).toEqual([]);
});
