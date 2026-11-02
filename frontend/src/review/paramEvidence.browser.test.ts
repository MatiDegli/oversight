import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check } from "@playwright/test";
import type { Browser, Page } from "@playwright/test";
import { createServer } from "vite";
import type { ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import { launchBrowser } from "../browserLaunch.support";
import { parameterStates } from "./NewParameterCalls.fixture";
import type { ParameterState } from "./NewParameterCalls.fixture";
import type {} from "./paramEvidence.browser.fixture";

const out = fileURLToPath(new URL("../../../.shots/param-evidence/", import.meta.url));
let server: ViteDevServer, browser: Browser, html: string, url: string;
const reports: unknown[] = [];
beforeAll(async () => {
  await mkdir(out, { recursive: true });
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: `${out}cache`,
    optimizeDeps: { entries: ["src/review/paramEvidence.browser.fixture.tsx"] },
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><script type="module">import "/src/theme/index.css"; import {paramEvidence} from "/src/review/paramEvidence.browser.fixture.tsx"; window.paramEvidence = paramEvidence;</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60000);
afterAll(async () => {
  if (reports.length) await writeFile(`${out}frames.json`, JSON.stringify(reports, null, 2));
  await browser.close();
  await server.close();
});
async function mounted(
  width: number,
  theme: string,
  name: ParameterState,
  test: (page: Page, errors: string[]) => Promise<void>,
) {
  const page = await browser.newPage({
    viewport: { width, height: width === 1920 ? 1080 : width === 390 ? 844 : 800 },
    reducedMotion: "reduce",
  });
  page.setDefaultTimeout(8000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
    await page.goto(url);
    await page.waitForFunction(() => "paramEvidence" in window, undefined, { timeout: 30000 });
    await page.evaluate(
      async ({ name, theme }) => {
        document.documentElement.dataset.theme = theme;
        await window.paramEvidence.mount(name);
        await document.fonts.ready;
      },
      { name, theme },
    );
    if (width >= 1280) {
      await page.getByRole("button", { name: "Functions", exact: true }).click();
      await page.getByRole("button", { name: "Review change", exact: true }).click();
    }
    await page.locator(".review-body").waitFor();
    await page.waitForFunction(
      () => !!document.querySelector('[aria-label="Repository graph"] canvas'),
    );
    await test(page, errors);
    expect(errors).toEqual([]);
  } finally {
    await page.close();
  }
}
const chip = (page: Page) => page.locator("[data-chip-node] button");
const section = (page: Page) => page.locator(".parameter-calls > section");
const samples = (page: Page) => page.locator(".parameter-samples");
it.each(["dark", "day"])(
  "colors phone After evidence from its author without ancestor styles in %s",
  async (theme) => {
    await mounted(390, theme, "mixed", async (page) => {
      await samples(page).locator("summary").click();
      const after = page.locator('.parameter-pair > [data-agent-id="agent-1"]');
      await check(after).toHaveCount(4);
      for (const cell of await after.all()) {
        await check(cell.locator("span")).toHaveText("After · Agent A");
        const colors = await cell.evaluate((el) => {
          const reference = document.createElement("div");
          reference.style.cssText =
            "border-color:var(--agent-a);background:var(--agent-a-tint);color:var(--agent-a-text)";
          document.body.append(reference);
          const expected = getComputedStyle(reference);
          const actual = getComputedStyle(el);
          const label = el.querySelector("span");
          if (!label) throw new Error("Missing After label");
          const result = {
            actual: [actual.borderTopColor, actual.backgroundColor, getComputedStyle(label).color],
            expected: [expected.borderTopColor, expected.backgroundColor, expected.color],
          };
          reference.remove();
          return result;
        });
        expect(colors.actual).toEqual(colors.expected);
      }
    });
  },
);
async function frameReport(page: Page, filename: string, errors: string[]) {
  const overflow = await page.evaluate(() => ({
    page: document.documentElement.scrollWidth - innerWidth,
    body:
      (document.querySelector(".review-body")?.scrollWidth ?? 0) -
      (document.querySelector(".review-body")?.clientWidth ?? 0),
  }));
  return { filename, pageErrors: [...errors], overflow };
}
it("records actual page errors in frame evidence and fails the clean-page gate", async () => {
  const evidence: Awaited<ReturnType<typeof frameReport>>[] = [];
  await expect(
    mounted(1920, "dark", "none", async (page, errors) => {
      await page.evaluate(() => {
        setTimeout(() => {
          throw new Error("frame-error-sentinel");
        });
      });
      await check.poll(() => errors.length).toBe(1);
      evidence.push(await frameReport(page, "error-sentinel", errors));
    }),
  ).rejects.toThrow();
  expect(evidence).toHaveLength(1);
  expect(evidence[0]?.pageErrors).toEqual(["frame-error-sentinel"]);
});
it.each([1920, 1280, 390])(
  "captures the approved states at %i in both themes",
  async (width) => {
    for (const theme of ["dark", "day"])
      for (const name of parameterStates) {
        if (width === 390 && name !== "partial" && name !== "mixed") continue;
        await mounted(width, theme, name, async (page, errors) => {
          if (await page.locator(".review-diff-viewport:not([hidden])").count())
            await check(page.locator(".monaco-diff-editor")).toBeVisible({ timeout: 30000 });
          if (["rejected", "values", "mixed"].includes(name))
            await samples(page).locator("summary").click();
          if (name === "mixed" && width > 700) await chip(page).click();
          if (name === "mixed" && width === 390)
            await check(page.locator("[data-chip-popover]")).toHaveCount(0);
          if (name !== "none")
            await page.locator(".parameter-calls").evaluate((el) => {
              el.scrollIntoView({ block: "start" });
            });
          if (name === "mixed" && width > 700)
            await check(
              page.getByRole("button", { name: "see review", exact: true }),
            ).toBeVisible();
          const filename = `${name}${width === 1920 ? "" : `-${String(width)}`}-${theme}`;
          const report = await frameReport(page, filename, errors);
          reports.push(report);
          expect(report.overflow).toEqual({ page: 0, body: 0 });
          await page.screenshot({ path: `${out}${filename}.png` });
        });
      }
  },
  120000,
);
it.each(["pointer", "Enter", "Space"])(
  "scrolls then focuses only on explicit %s activation",
  async (activation) => {
    await mounted(1920, "dark", "mixed", async (page) => {
      await chip(page).click();
      await page.evaluate(() => {
        const log: string[] = [];
        Reflect.set(window, "navigationOrder", log);
        const target = document.querySelector<HTMLElement>(".parameter-calls > section");
        if (!target) throw new Error("Missing target");
        const scroll = target.scrollIntoView.bind(target),
          focus = target.focus.bind(target);
        target.scrollIntoView = (options) => {
          log.push("scroll");
          scroll(options);
        };
        target.focus = (options) => {
          log.push(`focus:${String(options?.preventScroll)}`);
          focus(options);
        };
      });
      const button = page.getByRole("button", { name: "see review", exact: true });
      if (activation === "pointer") await button.click();
      else {
        await button.focus();
        await button.press(activation);
      }
      await check(section(page)).toBeFocused();
      await check(section(page)).toHaveAccessibleName("compute_price");
      expect(await page.evaluate(() => Reflect.get(window, "navigationOrder") as string[])).toEqual(
        ["scroll", "focus:true"],
      );
      expect(await section(page).evaluate((el) => el.getBoundingClientRect().top)).toBeGreaterThan(
        0,
      );
      await check(samples(page)).not.toHaveAttribute("open");
      await check(
        page.getByRole("button", { name: "Approve and merge", exact: true }),
      ).toBeDisabled();
    });
  },
);
it("keeps focus, scroll, and user disclosures when evidence arrives and updates", async () => {
  await mounted(1280, "dark", "none", async (page) => {
    await page.getByText("Details", { exact: true }).click();
    const control = page.getByRole("textbox", { name: "Comment for the agent" });
    await control.focus();
    const before = await page.locator(".review-body").evaluate((el) => {
      // Bound the fixture's viewport so this assertion exercises actual scrolling.
      (el as HTMLElement).style.maxHeight = "240px";
      el.scrollTop = 80;
      return el.scrollTop;
    });
    expect(before).toBeGreaterThan(0);
    await page.evaluate(() => {
      window.paramEvidence.update("rejected");
    });
    await check(control).toBeFocused();
    expect(await page.locator(".review-body").evaluate((el) => el.scrollTop)).toBe(before);
    await check(page.locator(".review-details > details")).toHaveAttribute("open", "");
    await check(samples(page)).not.toHaveAttribute("open");
    await samples(page).locator("summary").click();
    await control.focus();
    const scroll = await page.locator(".review-body").evaluate((el) => el.scrollTop);
    await page.evaluate(() => {
      window.paramEvidence.update("values");
    });
    await check(samples(page)).toHaveAttribute("open", "");
    await check(control).toBeFocused();
    expect(await page.locator(".review-body").evaluate((el) => el.scrollTop)).toBe(scroll);
    await check(page.locator("[data-chip-popover]")).toHaveCount(0);
  });
});
it.each([390, 900, 1279])(
  "retains 22px phone chips and 44px hits with neighbors at %i",
  async (width) => {
    await mounted(width, "day", "mixed", async (page) => {
      await check(chip(page)).toBeVisible();
      const geometry = await chip(page).evaluate((el) => {
        const rect = el.getBoundingClientRect(),
          css = getComputedStyle(el, "::before");
        return {
          height: rect.height,
          hit: parseFloat(css.height),
          outside: document.elementFromPoint(rect.x + rect.width / 2, rect.y - 10) === el,
        };
      });
      expect(geometry).toEqual({ height: 22, hit: 44, outside: true });
      await chip(page).click({ position: { x: 20, y: -10 }, force: true });
      await check(page.getByRole("button", { name: "see review", exact: true })).toBeVisible();
      const button = page.getByRole("button", { name: "see review", exact: true });
      await check(button).toHaveCSS("min-height", "44px");
      await page.keyboard.press("Tab");
      await check(button).toBeFocused();
      await check(button).toHaveCSS("outline-style", "solid");
      await check(button).toHaveCSS("outline-width", "2px");
      await button.press("Enter");
      await check(section(page)).toBeFocused();
      expect(await page.locator(".pp-main").evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
      await page.keyboard.press("Escape");
      await page.evaluate(() => {
        window.paramEvidence.neighbor();
      });
      // Two real changes share a node; padding must not steal the adjacent chip's hits.
      await check(chip(page)).toHaveCount(2);
      const stage = page.getByRole("main", { name: "Graph stage" });
      for (let i = 0; i < 3 && !(await chip(page).nth(1).isVisible()); i++)
        await stage.getByRole("button", { name: "Zoom in", exact: true }).click();
      for (const neighbor of await chip(page).all()) {
        await check(neighbor).toBeVisible();
        expect(
          await neighbor.evaluate((el) => {
            const rect = el.getBoundingClientRect();
            return [rect.x + 3, rect.right - 3].every((x) =>
              [-10, rect.height / 2, rect.height + 10].every(
                (y) => document.elementFromPoint(x, rect.y + y)?.closest("button") === el,
              ),
            );
          }),
        ).toBe(true);
        await neighbor.click({ position: { x: 5, y: -10 }, force: true });
        await check(neighbor).toHaveAttribute("aria-expanded", "true");
        await neighbor.press("Escape");
      }
    });
  },
);

it.each([1280, 1920])(
  "retains desktop chip and action geometry with neighbors at %i",
  async (width) => {
    await mounted(width, "day", "mixed", async (page) => {
      await check(chip(page)).toBeVisible();
      const geometry = await chip(page).evaluate((el) => {
        const rect = el.getBoundingClientRect();
        return {
          height: rect.height,
          before: getComputedStyle(el, "::before").content,
          outside: document.elementFromPoint(rect.x + rect.width / 2, rect.y - 10) === el,
        };
      });
      expect(geometry).toEqual({ height: 22, before: "none", outside: false });
      await chip(page).click({ position: { x: 20, y: -10 }, force: true });
      await check(chip(page)).toHaveAttribute("aria-expanded", "false");
      await check(page.locator("[data-chip-popover]")).toHaveCount(0);
    });
    await mounted(width, "day", "mixed", async (page) => {
      await chip(page).click();
      const button = page.getByRole("button", { name: "see review", exact: true });
      expect(
        await button.evaluate((el) => {
          const rect = el.getBoundingClientRect(),
            css = getComputedStyle(el);
          return {
            height: rect.height,
            minHeight: css.minHeight,
            display: css.display,
            decoration: css.textDecorationLine,
            offset: css.textUnderlineOffset,
            padding: css.padding,
            border: css.borderWidth,
            before: getComputedStyle(el, "::before").content,
            after: getComputedStyle(el, "::after").content,
            outside: document.elementFromPoint(rect.x + rect.width / 2, rect.y - 10) === el,
          };
        }),
      ).toEqual({
        height: 19.5,
        minHeight: "0px",
        display: "inline-flex",
        decoration: "underline",
        offset: "2px",
        padding: "0px",
        border: "0px",
        before: "none",
        after: "none",
        outside: false,
      });
      await page.keyboard.press("Tab");
      await check(button).toBeFocused();
      await check(button).toHaveCSS("outline-style", "solid");
      await check(button).toHaveCSS("outline-width", "2px");
      await button.press("Enter");
      await check(section(page)).toBeFocused();
      await check(page.locator(".review-body")).toHaveCSS("overflow-y", "auto");
      expect(await page.locator(".review-body").evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
      await page.keyboard.press("Escape");
      await check(section(page)).toBeFocused();
      await page.evaluate(() => {
        window.paramEvidence.neighbor();
      });
      await check(chip(page)).toHaveCount(2);
      const stage = page.getByRole("main", { name: "Graph stage" });
      for (let i = 0; i < 3 && !(await chip(page).nth(1).isVisible()); i++)
        await stage.getByRole("button", { name: "Zoom in", exact: true }).click();
      // Keep both chips inside the desktop graph's clipped viewport after zooming.
      await page.mouse.move(width - 130, 150);
      await page.mouse.down();
      await page.mouse.move(width - 400, 150, { steps: 10 });
      await page.mouse.up();
      for (const [index, neighbor] of (await chip(page).all()).entries()) {
        await check(neighbor).toBeVisible();
        await check
          .poll(() =>
            neighbor.evaluate((el) => {
              const rect = el.getBoundingClientRect();
              return (
                rect.height === 22 &&
                getComputedStyle(el, "::before").content === "none" &&
                [rect.x + 3, rect.right - 3].every(
                  (x) =>
                    document.elementFromPoint(x, rect.y + rect.height / 2)?.closest("button") ===
                    el,
                )
              );
            }),
          )
          .toBe(true);
        await neighbor.click({ position: { x: 5, y: 11 } });
        await check(neighbor).toHaveAttribute("aria-expanded", "true");
        await check(page.locator("[data-chip-popover]")).toHaveAttribute(
          "id",
          (await neighbor.getAttribute("aria-controls")) ?? "",
        );
        await neighbor.press("Escape");
        await check(page.locator("[data-chip-popover]")).toHaveCount(0);
        await check(neighbor).toBeFocused();
        await neighbor.click({ position: { x: 5, y: 11 } });
        await button.click();
        if (index === 0) await check(section(page)).toBeFocused();
        else {
          await check(page.locator('[data-chip-popover] [role="status"]')).toHaveText(
            "The review for these calls is not open.",
          );
          await check(button).toBeFocused();
        }
        await page.keyboard.press("Escape");
        await check(page.locator("[data-chip-popover]")).toHaveCount(0);
      }
    });
  },
);

it("reports a closed review when its explicit navigation has no mounted target", async () => {
  await mounted(1920, "dark", "mixed", async (page) => {
    await page.getByRole("button", { name: "Close review", exact: true }).click();
    await check(section(page)).toHaveCount(0);
    await chip(page).click();
    const button = page.getByRole("button", { name: "see review", exact: true });
    const status = page.locator('[data-chip-popover] [role="status"]');
    await check(status).toHaveCount(1);
    await check(status).toBeEmpty();
    const statusNode = await status.elementHandle();
    await button.click();
    await check(status).toHaveText("The review for these calls is not open.");
    await check(button).toBeFocused();
    await check(section(page)).toHaveCount(0);
    // Open the real review without an outside pointer closing the popover first.
    await page.getByRole("button", { name: "Review change", exact: true }).evaluate((el) => {
      if (el instanceof HTMLElement) el.click();
    });
    await check(section(page)).toHaveCount(1);
    await check(status).toBeEmpty();
    expect(await statusNode.evaluate((el) => el.isConnected)).toBe(true);
  });
});

it("omits unavailable echo counters and singularizes one call", async () => {
  await mounted(1920, "dark", "mixed", async (page) => {
    await page.evaluate(() => {
      window.paramEvidence.extensionOnly("unavailable");
    });
    await chip(page).click();
    const echo = page.locator("[data-chip-popover] > p");
    await check(echo).toHaveText("Calls using cap_percent; see review.");
    await page.evaluate(() => {
      window.paramEvidence.singleCall();
    });
    await check(echo).toHaveText("1 call using cap_percent; see review.");
    await page.evaluate(() => {
      window.paramEvidence.extensionOnly("mixed");
    });
    await check(echo).toHaveText("4 calls using cap_percent; see review.");
  });
});

it.each([1920, 1280, 390])("wraps long parameter names at %i", async (width) => {
  await mounted(width, "day", "mixed", async (page) => {
    const parameter = "parameter_with_long_name_".repeat(12) + "日本語";
    await page.evaluate((parameter) => {
      window.paramEvidence.extensionOnly("mixed", [parameter]);
    }, parameter);
    await chip(page).click();
    const popover = page.locator("[data-chip-popover]");
    await check(popover.locator("p code")).toHaveText(parameter);
    expect(await popover.evaluate((el) => el.scrollWidth - el.clientWidth)).toBe(0);
    expect(await section(page).evaluate((el) => el.scrollWidth - el.clientWidth)).toBe(0);
    await check(page.getByRole("button", { name: "see review", exact: true })).toBeVisible();
  });
});

it("keeps the existing question as the sole approval gate and has no approval shortcut", async () => {
  await mounted(1920, "dark", "mixed", async (page) => {
    const approve = page.getByRole("button", { name: "Approve and merge", exact: true });
    await check(approve).toBeDisabled();
    await page.evaluate(() => {
      window.paramEvidence.extensionOnly("partial");
    });
    await check(approve).toBeDisabled();
    await page.getByRole("button", { name: "Yes, keep it", exact: true }).click();
    await check(approve).toBeEnabled();
    await approve.focus();
    for (const key of ["Control+Enter", "Enter", "Space"]) await page.keyboard.press(key);
    expect(await page.evaluate(() => window.paramEvidence.decisions)).toEqual([]);
  });
});
it("preserves a focused Details summary through relocation and refuses stale navigation", async () => {
  await mounted(1280, "dark", "none", async (page) => {
    const summary = page.locator(".review-details > details > summary");
    await summary.click();
    await summary.focus();
    await page.evaluate(() => {
      window.paramEvidence.update("rejected");
    });
    await check(summary).toBeFocused();
    await check(page.locator(".review-details > details")).toHaveAttribute("open", "");
    const target = await section(page).getAttribute("id");
    await page.evaluate((target) => {
      window.paramEvidence.revise();
      window.paramEvidence.visit(target ?? "");
    }, target);
    await check(section(page)).toHaveCount(0);
    await check(page.getByRole("button", { name: "see review", exact: true })).toHaveCount(0);
  });
});
it("does not restore Details focus after the person clicks blank text", async () => {
  await mounted(1280, "dark", "none", async (page) => {
    const summary = page.locator(".review-details > details > summary");
    await summary.click();
    await check(summary).toBeFocused();
    await page.locator(".review-agent-note > p").first().click();
    expect(await page.evaluate(() => document.activeElement === document.body)).toBe(true);
    await page.evaluate(() => {
      window.paramEvidence.update("rejected");
    });
    await check(section(page)).toHaveCount(1);
    await check(summary).not.toBeFocused();
    expect(await page.evaluate(() => document.activeElement === document.body)).toBe(true);
    await check(page.locator(".review-details > details")).toHaveAttribute("open", "");
  });
});
