import type {} from "./ImpactCone.browser.fixture";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check } from "@playwright/test";
import { expect, it } from "vitest";
// The shell fixture is shared test infrastructure, not a product dependency.
// eslint-disable-next-line no-restricted-imports
import { panel, withPage } from "../app/review.browser.support";

const shots = fileURLToPath(new URL("../../../.shots/review-cone/", import.meta.url));
const sketchValues = {
  width: 384,
  paddingX: 16,
  paddingY: 14,
  title: 15,
  titleWeight: 700,
  titleLine: 22,
  summary: 13,
  summaryLine: 18,
  summaryGap: 2,
  groupGap: 8,
  groupTop: 12,
  groupBottom: 4,
  row: 28,
  label: 14,
  word: 13,
  outOfView: 13,
  columnGap: 10,
  lastColumn: 82,
  footerBorder: 1,
  footerTop: 10,
  footerPadding: 10,
  footerSize: 13,
};

it("matches the approved cone geometry in b review and captures b, a and d at both widths", async () => {
  await mkdir(shots, { recursive: true });
  for (const width of [1920, 1280])
    for (const scene of ["b", "a", "d"] as const) {
      await withPage(false, async (page) => {
        await page.setViewportSize({ width, height: 1080 });
        await page.addScriptTag({
          type: "module",
          content:
            'import {setConeScene} from "/src/review/ImpactCone.browser.fixture.tsx"; window.coneScene = setConeScene;',
        });
        await page.waitForFunction(() => !!window.coneScene);
        await page.evaluate((scene) => {
          window.coneScene(scene);
        }, scene);
        await page.getByRole("button", { name: "Review change", exact: true }).click();
        await panel(page).locator(".monaco-diff-editor").waitFor();
        await page.evaluate(() => document.fonts.ready);
        const card = page.getByRole("region", { name: "Possibly affected", exact: true });
        await check(card).toBeVisible();
        if (scene === "a") {
          await check(card).toContainText("No exact callers · 1 possible caller, not followed");
          await check(card.locator("li, footer")).toHaveCount(0);
        } else {
          await check(card.locator("li")).toHaveCount(9);
          await check(card).toContainText("9 functions up to distance 2");
          if (scene === "b") await check(card).toContainText("differs");
          else
            expect(await card.locator("li > * > span:nth-child(2)").allTextContents()).toEqual(
              Array<string>(9).fill(""),
            );
        }
        const actual = await card.evaluate((el) => {
          const num = (node: Element, property: string) =>
            parseFloat(getComputedStyle(node).getPropertyValue(property));
          const title = el.querySelector("h2");
          const summary = el.querySelector(":scope > p");
          if (!title || !summary) throw new Error("Missing cone heading");
          const group = el.querySelector("h3");
          const row = el.querySelector("li");
          const grid = row?.firstElementChild;
          const label = grid?.children[0];
          const word = grid?.children[1];
          const visibility = grid?.children[2];
          const footer = el.querySelector("footer");
          return {
            width: el.getBoundingClientRect().width,
            paddingX: num(el, "padding-left"),
            paddingY: num(el, "padding-top"),
            title: num(title, "font-size"),
            titleWeight: num(title, "font-weight"),
            titleLine: num(title, "line-height"),
            summary: num(summary, "font-size"),
            summaryLine: num(summary, "line-height"),
            summaryGap: num(summary, "margin-top"),
            ...(group?.parentElement && {
              groupGap: num(group, "gap"),
              groupTop: num(group.parentElement, "margin-top"),
              groupBottom: num(group, "margin-bottom"),
            }),
            ...(row &&
              grid &&
              label &&
              word &&
              visibility && {
                row: row.getBoundingClientRect().height,
                label: num(label, "font-size"),
                word: num(word, "font-size"),
                outOfView: num(visibility, "font-size"),
                columnGap: num(grid, "column-gap"),
                lastColumn: visibility.getBoundingClientRect().width,
              }),
            ...(footer && {
              footerBorder: num(footer, "border-top-width"),
              footerTop: num(footer, "margin-top"),
              footerPadding: num(footer, "padding-top"),
              footerSize: num(footer, "font-size"),
            }),
          };
        });
        for (const [key, value] of Object.entries(actual))
          expect(
            Math.abs(value - sketchValues[key as keyof typeof sketchValues]),
            key,
          ).toBeLessThanOrEqual(1);
        expect(
          await card
            .locator("li > * > span")
            .evaluateAll((cells) =>
              cells.every(
                (cell) =>
                  getComputedStyle(cell).whiteSpace === "nowrap" ||
                  cell.getBoundingClientRect().height <= 18,
              ),
            ),
        ).toBe(true);
        if (scene === "b")
          await check(card.locator("footer")).toHaveCSS("border-top-style", "solid");
        const key = `${scene}-${String(width)}-dark`;
        await page.screenshot({ path: `${shots}${key}.png` });
        const product = await card.screenshot({ path: `${shots}${key}-card.png` });
        const sketchName = scene === "a" && width === 1280 ? "a-1280-dark" : `${scene}-dark`;
        const sketch = await readFile(
          new URL(`../../../docs/sketches/review/${sketchName}.png`, import.meta.url),
        );
        const rows = Object.entries(actual)
          .map(
            ([key, value]) =>
              `<tr><td>${key}</td><td>${String(value)}</td><td>${String(sketchValues[key as keyof typeof sketchValues])}</td></tr>`,
          )
          .join("");
        await writeFile(
          `${shots}${key}.json`,
          JSON.stringify(
            {
              actual,
              sketch: sketchValues,
              radius: { actual: 10, sketch: 12, source: "DESIGN.md:738" },
            },
            null,
            2,
          ),
        );
        const browser = page.context().browser();
        if (!browser) throw new Error("Missing browser");
        const report = await browser.newPage();
        try {
          await report.setViewportSize({ width: 2400, height: 1400 });
          await report.setContent(
            `<style>body{background:#eee;color:#111;font:16px sans-serif}td{padding:2px 16px}.pair{display:flex;gap:24px;align-items:start}</style><p>Product · hand-written ${scene} fixture at ${String(width)}px / docs/sketches/review/${sketchName}.png; radius 10px per DESIGN (sketch 12px)</p><table><tr><th>Value</th><th>Product</th><th>Sketch</th></tr>${rows}</table><div class="pair"><img src="data:image/png;base64,${product.toString("base64")}"><img width="${String(width)}" src="data:image/png;base64,${sketch.toString("base64")}"></div>`,
          );
          await report.screenshot({ path: `${shots}${key}-compare.png`, fullPage: true });
        } finally {
          await report.close();
        }
      });
    }
}, 120_000);
