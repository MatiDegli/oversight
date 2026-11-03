import { openOversight } from "../src/workspaceView.browser.support";
import { mkdir, writeFile } from "node:fs/promises";
import type { Page } from "@playwright/test";
import type { FindingState } from "../src/review/ReviewFindings.fixture";
import type {} from "../src/review/reviewFindings.browser.fixture";
import type { Scene } from "./scenes.ts";

const states: FindingState[] = [
  "loosened",
  "removed",
  "skipped",
  "deleted",
  "function_length",
  "import_added",
  "import_cycle",
  "duplicate_logic",
  "not-recorded",
  "empty-complete",
  "empty-incomplete",
  "populated-complete",
  "populated-incomplete",
  "completeness-unavailable",
];
export const findingFrames = [
  ...states.map((state) => ({ state, width: 1920, theme: "dark" })),
  ...[1920, 1280, 390].flatMap((width) =>
    ["dark", "day"].map((theme) => ({ state: "all" as const, width, theme })),
  ),
  { state: "empty-incomplete" as const, width: 390, theme: "dark" },
  ...[1920, 1280, 390].flatMap((width) =>
    ["dark", "day"].map((theme) => ({ state: "baseline" as const, width, theme })),
  ),
];
export const frameName = (frame: (typeof findingFrames)[number]) =>
  `${frame.state}-${String(frame.width)}-${frame.theme}`;
export async function prepareFindingFrame(page: Page, frame: (typeof findingFrames)[number]) {
  await page.setViewportSize({
    width: frame.width,
    height: frame.width === 1920 ? 1080 : frame.width === 390 ? 844 : 800,
  });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.addScriptTag({
    type: "module",
    content:
      'import {reviewFindings} from "/src/review/reviewFindings.browser.fixture.tsx"; window.reviewFindings = reviewFindings;',
  });
  await page.waitForFunction(() => "reviewFindings" in window);
  await page.evaluate(async ({ state, theme }) => {
    document.documentElement.dataset.theme = theme;
    await window.reviewFindings.mount(state);
    await document.fonts.ready;
  }, frame);
  if (frame.width >= 1280) {
    await openOversight(page);
    await page.getByRole("button", { name: "Functions", exact: true }).click();
    await page.getByRole("button", { name: "Review change", exact: true }).click();
  }
  await page.locator(".review-body").waitFor();
  await page.waitForFunction(
    () => !!document.querySelector('[aria-label="Repository graph"] canvas'),
  );
}
export async function findingMetrics(page: Page, errors: string[]) {
  return page.evaluate((pageErrors) => {
    const evidence = document.querySelector<HTMLElement>(".review-body");
    const details = document.querySelector<HTMLDetailsElement>(".review-details > details");
    const nodes = [
      ...document.querySelectorAll<HTMLElement>(".review-findings, .review-findings *"),
    ];
    return {
      pageErrors,
      viewport: { width: innerWidth, height: innerHeight },
      theme: document.documentElement.dataset.theme,
      overflow: {
        page: Math.max(0, document.documentElement.scrollWidth - innerWidth),
        evidence: evidence ? Math.max(0, evidence.scrollWidth - evidence.clientWidth) : null,
      },
      overflowingFindings: nodes
        .filter((node) => node.scrollWidth > node.clientWidth + 1)
        .map((node) => node.tagName),
      detailsOpen: details?.open,
      headings: [...document.querySelectorAll(".review-findings h3")].map(
        (node) => node.textContent,
      ),
      provenance: "Hand-written evidence fixture; built shell, no model or transport",
    };
  }, errors);
}
export const findingScenes: Scene[] = findingFrames.map((frame) => ({
  name: `finding-${frameName(frame)}`,
  enabled: true,
  log: "proposed",
  async prepare(page) {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await prepareFindingFrame(page, frame);
    const initial = await findingMetrics(page, errors);
    if (initial.detailsOpen) throw new Error("Details must start closed");
    const summary = page.locator(".review-details > details > summary");
    if (frame.state !== "baseline") await summary.click();
    if (frame.state !== "baseline")
      await page
        .locator(".review-findings")
        .first()
        .evaluate((node) => {
          node.scrollIntoView({ block: "start" });
        });
    const out = new URL("../../.shots/review-findings/", import.meta.url);
    await mkdir(out, { recursive: true });
    const name = frameName(frame);
    const metrics = await findingMetrics(page, errors);
    await page.screenshot({ path: new URL(`${name}.png`, out).pathname });
    if (frame.state === "all") {
      await page.getByRole("region", { name: "Structural observations" }).evaluate((node) => {
        node.scrollIntoView({ block: "start" });
      });
      await page.screenshot({ path: new URL(`${name}-structure.png`, out).pathname });
      await writeFile(
        new URL(`${name}-structure.json`, out),
        JSON.stringify(
          {
            ...(await findingMetrics(page, errors)),
            supplemental: "Structural section of the same populated candidate",
          },
          null,
          2,
        ),
      );
    }

    await writeFile(
      new URL(`${name}.json`, out),
      JSON.stringify(
        {
          ...metrics,
          initialDetailsClosed: !initial.detailsOpen,
          comparison:
            frame.state === "baseline"
              ? "Built review and New-parameter calls sibling"
              : `baseline-${String(frame.width)}-${frame.theme}.png`,
        },
        null,
        2,
      ),
    );
    if (
      errors.length ||
      metrics.overflow.page ||
      metrics.overflow.evidence ||
      metrics.overflowingFindings.length
    )
      throw new Error(`Finding frame failed: ${name}: ${JSON.stringify(metrics)}`);
  },
}));
