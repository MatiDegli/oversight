import { mkdir, writeFile } from "node:fs/promises";
import { expect as check } from "@playwright/test";
import { expect, it } from "vitest";
import { recorded, early, hosted, lead } from "./usage.fixture";
import {
  required,
  enter,
  refresh,
  camera,
  row,
  groupingStyle,
  coverageStyles,
  directory,
  usageBrowserSupport,
} from "./usage.browser.support";

const { open } = usageBrowserSupport("presentation-");

it.each(["dark", "day"])(
  "renders incomplete and ranged values as lower bounds in %s",
  async (theme) => {
    const data = structuredClone(hosted);
    data.cost.coverage = "incomplete";
    data.hours.coverage = "incomplete";
    data.cost.problems = ["tokens are invalid"];
    data.hours.problems = ["time moved backwards"];
    data.cost.ranged_calls = 1;
    const page = await open(data, { theme });
    try {
      await enter(page);
      await check(page.locator(".usage-coverage.is-incomplete")).toHaveCount(2);
      await coverageStyles(page);
      await check(page.locator(".usage-facts")).toContainText("At least US$0.00184");
      await check(page.locator(".usage-facts")).toContainText("At least 33.0 s");
      await check(page.locator(".usage-facts")).toContainText("“tokens are invalid”");
      await check(page.locator(".usage-facts")).toContainText("“time moved backwards”");
      data.cost.coverage = "missing_metrics";
      data.cost.problems = ["1 call has unreported tokens"];
      data.cost.usd = null;
      await page.route("**/usage", (route) => route.fulfill({ json: data }));
      await refresh(page);
      await check(page.locator(".usage-facts")).toContainText("“1 call has unreported tokens”");
      await coverageStyles(page);
      await check(page.locator(".usage-facts")).not.toContainText("From");
    } finally {
      await page.close();
    }
  },
);
it("keeps static replay without a Usage entry or request and preserves the phone notice", async () => {
  let requests = 0;
  const page = await open(recorded, {
    replay: true,
    handler: (route) => {
      requests++;
      return route.fulfill({ json: recorded });
    },
  });
  try {
    await camera(page);
    await check(page.getByRole("button", { name: "Usage", exact: true })).toHaveCount(0);
    expect(requests).toBe(0);
    for (const theme of ["dark", "day"]) {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.evaluate((theme) => {
        document.documentElement.dataset.theme = theme;
      }, theme);
      await check(page.getByText("This window is too narrow.", { exact: false })).toBeVisible();
      if (process.env.USAGE_SCREEN_SHOTS === "1") {
        await mkdir(directory, { recursive: true });
        await page.screenshot({ path: `${directory}phone-390-${theme}.png` });
      }
    }
  } finally {
    await page.close();
  }
});
it.each([1920, 1280])(
  "proves the seven approved desktop states at %ipx in both themes",
  async (width) => {
    const measurements: unknown[] = [];
    for (const theme of ["dark", "day"])
      for (const name of [
        "complete",
        "missing",
        "no-accepted",
        "by-agent",
        "by-team",
        "by-model",
        "by-role",
      ]) {
        const data =
          name === "complete"
            ? hosted
            : name === "no-accepted"
              ? early
              : name.startsWith("by-")
                ? lead
                : recorded;
        const page = await open(data, {
          width,
          theme,
          query: name === "missing" ? "recorded" : name === "no-accepted" ? "early" : "",
        });
        try {
          await enter(page);
          await check(page.locator(".usage-scope")).toContainText(String(data.seq));
          if (name.startsWith("by-")) {
            const button = page.getByRole("button", { name: `By ${name.slice(3)}`, exact: true });
            await button.focus();
            await page.keyboard.press("Enter");
            await check(button).toHaveAttribute("aria-pressed", "true");
            await groupingStyle(page);
            if (name === "by-agent") await check(row(page, "Reviews")).toContainText("——10.0 s");
            if (name === "by-team") {
              await check(row(page, "Lead team")).toContainText("At least 48.3 s");
              await check(row(page, "Total")).toHaveCount(0);
            }
            if (name === "by-model") {
              await check(row(page, "Muse Spark Open")).toContainText("At least 18.1 s");
              await check(row(page, "Muse Spark").first()).toContainText("At least 30.2 s");
            }
            if (name === "by-role")
              expect(await page.locator("tbody tr td:first-of-type").allTextContents()).toEqual([
                "19",
                "3",
                "2",
                "1",
              ]);
          } else {
            await check(page.locator(".usage-closing")).toContainText(
              name === "no-accepted" ? "32.7 s" : "33.0 s",
            );
            if (name === "complete") {
              await check(page.locator(".usage-facts")).toContainText("US$0.00184");
              await check(page.locator(".usage-facts")).toContainText("US$0.000918");
              await check(row(page, "Agent B")).toContainText("US$0.000859");
              await check(page.locator(".usage-grouping button")).toHaveCount(0);
            } else if (name === "missing") {
              await check(page.locator(".usage-facts")).toContainText("At least US$0.000978");
              await check(page.locator(".usage-facts")).toContainText("At least US$0.000489");
              await check(page.locator(".usage-facts")).toContainText("7 of 17 calls priced");
              await check(page.locator(".usage-scope")).toHaveText(
                "Whole recorded run, up to event 69",
              );
            } else {
              await check(page.getByText("Not available", { exact: true })).toBeVisible();
              await check(row(page, "Agent A")).toContainText("21.2 s so far");
            }
            if (width === 1280 && name !== "no-accepted") {
              const bottom = await page
                .locator(".usage-scope")
                .evaluate((node) => node.getBoundingClientRect().top);
              const b = await row(page, "Agent B").boundingBox();
              expect(required(b).y + required(b).height).toBeLessThanOrEqual(bottom + 1);
              const scroll = page.locator(".usage-scroll");
              await scroll.evaluate((node) => {
                node.scrollTop = node.scrollHeight;
              });
              expect(await scroll.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);
              await scroll.evaluate((node) => {
                node.scrollTop = 0;
              });
            }
          }
          await page.evaluate(() => document.fonts.ready);
          await coverageStyles(page);
          if (name === "missing") {
            await check(row(page, "Agent B").getByRole("cell").nth(1)).toHaveText("No list price");
            const measurement = await page.evaluate(() => {
              const element = (selector: string) => {
                const node = document.querySelector(selector);
                if (!node) throw new Error(selector);
                return node;
              };
              const rect = (selector: string) => element(selector).getBoundingClientRect();
              return {
                width: innerWidth,
                theme: document.documentElement.dataset.theme,
                panel: rect(".focus-panel").width,
                inset: rect(".usage-closing").left - rect(".focus-panel").left,
                closing: getComputedStyle(element(".usage-closing")).fontSize,
                mono: getComputedStyle(element(".usage-closing .usage-number")).fontSize,
                headerY: rect(".usage-panel thead").top,
                footer: rect(".usage-scope").height,
              };
            });
            expect(measurement.panel).toBe(width === 1920 ? 880 : 608);
            expect(measurement.inset).toBe(width === 1920 ? 32 : 28);
            expect(measurement.closing).toBe("20px");
            expect(measurement.mono).toBe("19px");
            expect(
              Math.abs(measurement.headerY - (width === 1920 ? 460 : 583)),
            ).toBeLessThanOrEqual(1);
            expect(measurement.footer).toBe(46);
            measurements.push(measurement);
          }
          if (process.env.USAGE_SCREEN_SHOTS === "1") {
            await mkdir(directory, { recursive: true });
            await page.screenshot({ path: `${directory}${name}-${String(width)}-${theme}.png` });
          }
        } finally {
          await page.close();
        }
      }
    if (process.env.USAGE_SCREEN_SHOTS === "1")
      await writeFile(
        `${directory}measurements-${String(width)}.json`,
        JSON.stringify(measurements, null, 2),
      );
  },
  90_000,
);
