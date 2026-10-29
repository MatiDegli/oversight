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
  await expect(panel).toContainText("Inventory.reserve · Agent B");
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
  await expect(panel).toContainText("compute_price · Agent A");
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

test.describe("phone practice", () => {
  test.use({ hasTouch: true, isMobile: true, deviceScaleFactor: 2 });
  for (const [width, height] of [
    [390, 844],
    [430, 932],
  ] as const) {
    test(`completes the real practice at ${String(width)}x${String(height)}`, async ({ page }) => {
      await page.setViewportSize({ width, height });
      const requests: string[] = [];
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      page.on("request", (request) => {
        if (/\/(agents|changes|events|files|graph|impact|tree)(\/|\?|$)/.test(request.url()))
          requests.push(request.url());
      });
      await page.goto("/oversight/");
      await expect(page.locator(".narrow-notice")).toBeVisible();
      await expect
        .poll(async () => Object.keys((await positions(page)) ?? {}).length)
        .toBeGreaterThan(0);
      const graph = page.locator('[aria-label="Repository graph"]');
      const instance = await graph.evaluateHandle((host: GraphProbeHost) => host.oversightGraph);
      await page.getByRole("button", { name: "Try two-change practice", exact: true }).tap();
      await phoneStage(page);
      await phoneCapture(page, width, "introduction");
      await firstPhoneDecision(page, width);
      await finishPhoneDecisions(page, width);
      await page.setViewportSize({ width: 1920, height: 1080 });
      await expect(page.locator(".focus-panel")).toHaveCSS("width", "880px");
      expect(
        await instance.evaluate(
          (original) =>
            original ===
            document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
              ?.oversightGraph,
        ),
      ).toBe(true);
      await expect(graph).toBeVisible();
      await provenance(page);
      await page.setViewportSize({ width, height });
      await expect(page.locator(".phone-practice")).toBeVisible();
      await practice(page).getByRole("button", { name: "Try again" }).tap();
      await expect(practice(page).getByRole("button", { name: "Start practice" })).toBeVisible();
      await practice(page).getByRole("button", { name: "Leave practice" }).tap();
      await expect(page.locator(".narrow-notice")).toBeVisible();
      expect(requests).toEqual([]);
      expect(errors).toEqual([]);
    });
  }
});

async function phoneStage(page: Page) {
  await expect(page.locator(".pp-status")).toHaveText(
    "Replay of a real recorded run · Oct 27, 2026",
  );
  await expect(page.locator(".pp-status")).toBeInViewport({ ratio: 1 });
  await expect(page.locator(".narrow-notice")).toBeHidden();
  await expect(page.getByRole("tab")).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    page.viewportSize()?.width,
  );
  expect((await page.locator(".pp-graph").boundingBox())?.height).toBeLessThanOrEqual(201);
  await expect(page.locator(".pp-graph")).toBeInViewport({ ratio: 1 });
  const sizes = await practice(page)
    .getByRole("button")
    .evaluateAll((buttons) =>
      buttons.map((button) => ({
        text: button.textContent,
        width: button.getBoundingClientRect().width,
        height: button.getBoundingClientRect().height,
      })),
    );
  for (const size of sizes) {
    expect(size.height, size.text).toBeGreaterThanOrEqual(44);
    expect(size.width, size.text).toBeGreaterThanOrEqual(44);
  }
}
async function phoneDecision(page: Page, index: number, summary: string) {
  await phoneStage(page);
  await expect(practice(page)).toContainText(`Decision ${String(index)} of 2`);
  await expect(practice(page).getByRole("region", { name: summary })).toBeVisible();
  await expect(practice(page).getByRole("button", { name: /Show diff/ })).toHaveAttribute(
    "aria-expanded",
    "false",
  );
  await expect(practice(page).locator(".pp-code")).toHaveCount(0);
  await expect(practice(page).getByRole("button", { name: "Next", exact: true })).toHaveCount(0);
  await expect(practice(page).getByRole("button", { name: "Show findings" })).toHaveCount(0);
  const pair = practice(page).locator(".pp-choice");
  const styles = await pair.evaluateAll((buttons) =>
    buttons.map((button) => {
      const s = getComputedStyle(button);
      return [
        s.backgroundColor,
        s.color,
        s.borderColor,
        s.font,
        button.getBoundingClientRect().width,
        button.getBoundingClientRect().height,
      ];
    }),
  );
  expect(styles[0]).toEqual(styles[1]);
}
async function checkDiff(page: Page, width: number, state: string) {
  const panel = practice(page);
  await expect(panel.getByText("Tests added", { exact: true })).toHaveCount(0);
  await panel.getByRole("button", { name: /Show diff/ }).tap();
  await expect(panel.getByRole("button", { name: /Hide diff/ })).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  await expect(panel.locator(".pp-file").first()).toBeVisible();
  await phoneCapture(page, width, `${state}-diff-open`);
  await panel.locator("summary").filter({ hasText: "Tests added" }).tap();
  const helper = panel.locator("summary").filter({ hasText: "Called helper" });
  if (await helper.count()) await helper.tap();
  await expect
    .poll(() => panel.locator(".pp-code-line[data-continuation]").count())
    .toBeGreaterThan(0);
  expect(
    await panel
      .locator(".pp-source")
      .evaluateAll((rows) =>
        rows.every(
          (row) =>
            [...row.querySelectorAll("code")]
              .map((code) =>
                code.textContent === " " && row.getAttribute("data-source") === ""
                  ? ""
                  : code.textContent,
              )
              .join("") === row.getAttribute("data-source"),
        ),
      ),
  ).toBe(true);
  expect(
    await panel
      .locator(".pp-code-line code")
      .evaluateAll((codes) =>
        codes.every(
          (code) =>
            getComputedStyle(code).fontSize === "15px" && code.scrollWidth <= code.clientWidth,
        ),
      ),
  ).toBe(true);
  await expect(page.locator(".pp-graph")).toBeInViewport({ ratio: 1 });
  await panel.getByRole("button", { name: /Hide diff/ }).tap();
  await expect(panel.locator(".pp-code")).toHaveCount(0);
}
async function phoneCapture(page: Page, width: number, state: string) {
  if (width !== 390) return;
  await page.locator(".pp-main").evaluate((main) => {
    main.scrollTop = 0;
  });
  if (state.endsWith("diff-open"))
    await page.locator(".pp-main").evaluate((main) => {
      const file = main.querySelector<HTMLElement>(".pp-file");
      if (file)
        main.scrollTop += file.getBoundingClientRect().top - main.getBoundingClientRect().top;
    });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: `../.shots/phone-practice/build/${state}-390.png` });
}

async function firstPhoneDecision(page: Page, width: number) {
  await practice(page).getByRole("button", { name: "Start practice" }).tap();
  await phoneDecision(page, 1, "Agent B's summary");
  await expect(page.getByLabel("Decision-time graph", { exact: true })).toContainText(
    "New exact call",
  );
  await expect(page.getByLabel("Decision-time graph", { exact: true })).toContainText(
    "0 possibly affected",
  );
  await phoneCapture(page, width, "decision-1");
  await checkDiff(page, width, "decision-1");
  const approve = practice(page).getByRole("button", { name: "Approve", exact: true });
  await approve.focus();
  for (const key of ["Control+Enter", "Enter", "Space"]) await page.keyboard.press(key);
  await approve.dispatchEvent("click", { detail: 0 });
  await expect(practice(page).getByRole("button", { name: "Next", exact: true })).toHaveCount(0);
  await approve.tap();
  await expect(practice(page).locator(".pp-saved")).toContainText("Approve");
  await phoneCapture(page, width, "decision-1-answered");
}

async function finishPhoneDecisions(page: Page, width: number) {
  await practice(page).getByRole("button", { name: "Next", exact: true }).tap();
  await phoneDecision(page, 2, "Agent A's summary");
  await expect(page.getByLabel("Decision-time graph", { exact: true })).toContainText(
    "7 direct callers",
  );
  await expect(page.getByLabel("Decision-time graph", { exact: true })).toContainText(
    "2 at distance 2",
  );
  await phoneCapture(page, width, "decision-2");
  await checkDiff(page, width, "decision-2");
  await practice(page).getByRole("button", { name: "Reject", exact: true }).tap();
  await expect(practice(page).locator(".pp-saved")).toContainText("Reject");
  await phoneCapture(page, width, "decision-2-answered");
  await practice(page).getByRole("button", { name: "Show findings" }).tap();
  await expect(practice(page).getByRole("heading", { name: "Recorded decisions" })).toBeVisible();
  await expect(practice(page)).toContainText("Same decision");
  await expect(practice(page)).toContainText("Different decision");
  await expect(page.getByLabel("Recorded graph after both merges")).toContainText("Both merged");
  await expect(page.locator(".pp-taxi.is-pending")).toHaveCount(0);
  await phoneStage(page);
  await phoneCapture(page, width, "reveal");
  await practice(page).getByRole("button", { name: "See your result" }).tap();
  await expect(practice(page)).toContainText("1 of 2 decisions matched");
  await expect(practice(page)).toContainText("Agreement is not proof");
  await phoneStage(page);
  await phoneCapture(page, width, "finish");
}
