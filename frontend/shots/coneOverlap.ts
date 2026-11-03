import { mkdir, readFile, writeFile } from "node:fs/promises";
import type { Page } from "@playwright/test";
import { openOversight } from "../src/workspaceView.browser.support.ts";
import type {} from "../src/review/paramEvidence.browser.fixture";
import type { ConeState } from "../src/review/ConeOverlap.fixture";
import type { Scene } from "./scenes.ts";

const pairs = [1920, 1280, 390].flatMap((width) =>
  ["dark", "day"].map((theme) => ({ width, theme })),
);
const states: ConeState[] = [
  "no-peers",
  "intersection",
  "disjoint",
  "missing-graph",
  "partial-graph",
  "merged",
  "rejected",
  "stopped",
  "revision-added",
  "revision-removed",
  "several-peers",
  "self-only",
  "replay-prefix",
];
const frames =
  process.env.OVERSIGHT_SHOTS_CONE === "baseline"
    ? pairs.map((pair) => ({ ...pair, state: "baseline" as const }))
    : [
        ...states.map((state) => ({ state, width: 1920, theme: "dark" })),
        ...pairs
          .filter((pair) => pair.width !== 1920 || pair.theme !== "dark")
          .map((pair) => ({ ...pair, state: "several-peers" as const })),
        { state: "missing-graph" as const, width: 390, theme: "dark" },
      ];
const out = new URL("../../.shots/cone-overlap/", import.meta.url);
async function compare(page: Page, name: string, baseline: string) {
  const [before, after] = await Promise.all([
    readFile(new URL(`${baseline}.png`, out)),
    readFile(new URL(`${name}.png`, out)),
  ]);
  const comparison = await page.context().newPage();
  try {
    await comparison.setViewportSize({ width: 1600, height: 1000 });
    await comparison.setContent(
      `<html><body style="margin:16px;font:16px sans-serif"><h1>Built review / current cone evidence</h1><div style="display:flex;gap:16px"><figure style="margin:0;width:50%"><figcaption>${baseline}</figcaption><img style="width:100%" src="data:image/png;base64,${before.toString("base64")}"></figure><figure style="margin:0;width:50%"><figcaption>${name}</figcaption><img style="width:100%" src="data:image/png;base64,${after.toString("base64")}"></figure></div></body></html>`,
    );
    await comparison.screenshot({
      path: new URL(`${name}-comparison.png`, out).pathname,
      fullPage: true,
    });
  } finally {
    await comparison.close();
  }
}
export const coneScenes: Scene[] = frames.map(({ width, theme, state }) => ({
  name: `cone-${state}-${String(width)}-${theme}`,
  enabled: true,
  firstView: true,
  themes: [{ name: theme, enabled: true }],
  log: "proposed",
  async prepare(page) {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.setViewportSize({
      width,
      height: width === 1920 ? 1080 : width === 390 ? 844 : 800,
    });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.addScriptTag({
      type: "module",
      content:
        'import {paramEvidence} from "/src/review/paramEvidence.browser.fixture.tsx"; import {coneHarness} from "/src/review/ConeOverlap.fixture.tsx"; window.paramEvidence = paramEvidence; window.coneHarness = coneHarness;',
    });
    await page.waitForFunction(() => "coneHarness" in window);
    await page.evaluate(
      async ({ state, theme }) => {
        document.documentElement.dataset.theme = theme;
        if (state === "baseline") await window.paramEvidence.mount("mixed");
        else await window.coneHarness.mount(state);
        await document.fonts.ready;
      },
      { state, theme },
    );
    if (width >= 1280) {
      await openOversight(page);
      await page.getByRole("button", { name: "Functions", exact: true }).click();
      await page.getByRole("button", { name: "Review change", exact: true }).click();
    }
    await page.locator(".review-body").waitFor();
    const evidence = page.locator(
      state === "baseline" ||
        ["no-peers", "merged", "rejected", "stopped", "self-only"].includes(state)
        ? ".parameter-calls"
        : ".cone-overlap",
    );
    await evidence.evaluate((node) => {
      node.scrollIntoView({ block: "center" });
    });
    await mkdir(out, { recursive: true });
    const name = `${state}-${String(width)}-${theme}`;
    await page.screenshot({ path: new URL(`${name}.png`, out).pathname });
    const metrics = await page.evaluate(() => {
      const evidence = document.querySelector<HTMLElement>(".review-body");
      return {
        page: Math.max(0, document.documentElement.scrollWidth - innerWidth),
        evidence: evidence ? Math.max(0, evidence.scrollWidth - evidence.clientWidth) : null,
      };
    });
    const baseline = `baseline-${String(width)}-${theme}`;
    await writeFile(
      new URL(`${name}.json`, out),
      JSON.stringify(
        {
          state,
          width,
          theme,
          errors,
          overflow: metrics,
          comparison: `${name}-comparison.png`,
          baseline: `${baseline}.png`,
          provenance: "Hand-written accepted events; built review shell, no model",
        },
        null,
        2,
      ),
    );
    if (state !== "baseline") await compare(page, name, baseline);
    if (errors.length || metrics.page || metrics.evidence)
      throw new Error(JSON.stringify({ errors, metrics }));
  },
}));
