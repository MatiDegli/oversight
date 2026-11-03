import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { expect as check, type Browser, type Page } from "@playwright/test";
import { createServer, type ViteDevServer } from "vite";
import { afterAll, beforeAll, expect, it } from "vitest";
import type { GraphProbeHost } from "../graph";
import { launchBrowser } from "../browserLaunch.support";
import { status } from "./chatAgents.fixture";
import { observedPath, railConflict, type RailScene } from "./teamRail.fixture";
import {
  captureContext,
  evidenceRoot,
  measureFrame,
  measureFoldedCaptions,
  validateCoverage,
  writeFrame,
} from "./teamRailEvidence.testSupport";
import type {} from "./teamRail.browser.fixture";

const shots = process.env.TEAM_RAIL_SHOTS === "1";
let context: Awaited<ReturnType<typeof captureContext>> | undefined;
let server: ViteDevServer, browser: Browser, html: string, url: string;
beforeAll(async () => {
  if (shots) context = await captureContext();
  server = await createServer({
    root: fileURLToPath(new URL("../../", import.meta.url)),
    cacheDir: fileURLToPath(new URL("cache/", evidenceRoot)),
    server: { host: "127.0.0.1", port: 0, strictPort: false },
  });
  html = await server.transformIndexHtml(
    "/",
    '<!doctype html><html><body><div id="root" style="height:100vh"></div><script type="module">import "/src/theme/index.css";import "/src/app/teamRail.browser.fixture.tsx";</script></body></html>',
  );
  await server.listen();
  url = server.resolvedUrls?.local[0] ?? "";
  browser = await launchBrowser();
}, 60_000);
afterAll(async () => {
  if (shots && !context) return;
  await browser.close();
  await server.close();
});
async function open(scene: RailScene = "chat-busy", width = 1920, theme = "dark") {
  const page = await browser.newPage({
    viewport: { width, height: width === 1920 ? 1080 : width === 390 ? 844 : 800 },
    reducedMotion: "reduce",
  });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.goto(url);
  await page.waitForFunction(() => !!window.teamRailTest);
  await page.evaluate(
    ({ scene, graph, theme }) => {
      document.documentElement.dataset.theme = theme;
      window.teamRailTest.mount(scene, graph);
    },
    { scene, graph: context?.graph, theme },
  );
  await page.evaluate(() => document.fonts.ready);
  await check(page.getByText("handwritten · team rail", { exact: true })).toBeVisible();
  await check(page.getByRole("list", { name: "Conversation timeline" })).toBeVisible();
  return { page, errors };
}
const disclosure = (page: Page, title = "Lead A") =>
  page.getByRole("button", { name: `Members of ${title}`, exact: true });
const timeline = (page: Page) => page.getByRole("list", { name: "Conversation timeline" });
const draft = (page: Page) =>
  page.getByRole("textbox", { name: "Message Agent A·1", exact: true });
async function inspect(page: Page, name: string) {
  await mkdir(evidenceRoot, { recursive: true });
  await page.screenshot({
    path: fileURLToPath(new URL(`inspection-${name}.png`, evidenceRoot)),
    animations: "disabled",
  });
}
it.each(["dark", "day"])(
  "keeps grouped phone Chat, lone C and explicit observed file chips usable in %s",
  async (theme) => {
    const { page, errors } = await open("chat-busy", 390, theme);
    try {
      await check(disclosure(page)).toHaveAttribute("aria-expanded", "true");
      await check(page.locator('.chat-lone-agent [data-agent-origin="lone-c"]')).toContainText(
        "Agent C",
      );
      await check(page.locator(".chat-agent-name").filter({ hasText: /^A·1$/ })).toHaveText("A·1");
      const chip = page.getByRole("button", {
        name: `Open ${observedPath} · Agent A·1 · Editing`,
        exact: true,
      });
      await check(chip).toHaveAttribute("aria-description", "lines 12 to 28");
      await chip.focus();
      await page.keyboard.press("Enter");
      expect(await page.evaluate(() => window.teamRailTest.opened())).toEqual([observedPath]);
      const metrics = await measureFrame(page);
      for (const values of Object.values(metrics))
        for (const value of values) {
          expect(value.scrollWidth).toBeLessThanOrEqual(value.clientWidth + 1);
          expect(value.right).toBeLessThanOrEqual(391);
        }
      await page.locator(".chat-shell").evaluate((el) => {
        el.scrollTop = 0;
      });
      await inspect(page, `390-${theme}`);
      expect(errors).toEqual([]);
    } finally {
      await page.close();
    }
  },
);
it("retains selected actor, thread element, scroll, draft, focus and pending command across collapse, events and 1920→390→1280", async () => {
  const { page, errors } = await open();
  try {
    const thread = await timeline(page).elementHandle();
    await timeline(page).evaluate((el) => {
      el.scrollTop = 40;
    });
    const scroll = await timeline(page).evaluate((el) => el.scrollTop);
    await draft(page).fill("Preserve this draft");
    await disclosure(page).focus();
    await page.keyboard.press("Space");
    await check(disclosure(page)).toBeFocused();
    await check(page.locator(".chat-team-summary[data-selected=true]")).toHaveCount(1);
    await check(page.locator('[data-agent-origin="a1"]')).toBeHidden();
    await check(disclosure(page, "Lead B")).toHaveAttribute("aria-expanded", "true");
    await page.evaluate(
      (events) => {
        window.teamRailTest.deliver(events);
      },
      [status("a2", { plan_id: "plan-a1", state: "ready_for_review" })],
    );
    await check(disclosure(page)).toBeFocused();
    await check(
      page.getByRole("heading", {
        name: "Lead A · 3 agents · 2 working, 1 needs you",
        exact: true,
      }),
    ).toBeVisible();
    await check(page.locator(".chat-lead-row .chat-lead-team")).toHaveCount(0);
    await page.locator(".chat-team-summary h3").first().click();
    expect(await page.evaluate(() => window.teamRailTest.calls())).toEqual([]);
    for (const width of [390, 1280]) {
      await page.setViewportSize({ width, height: 800 });
      await check(disclosure(page)).toHaveAttribute("aria-expanded", "false");
      await check(draft(page)).toHaveValue("Preserve this draft");
      expect(
        await thread.evaluate(
          (el) => el === document.querySelector('[aria-label="Conversation timeline"]'),
        ),
      ).toBe(true);
    }
    expect(await timeline(page).evaluate((el) => el.scrollTop)).toBe(scroll);
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await check(page.getByRole("button", { name: "Send", exact: true })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    await disclosure(page).click();
    await disclosure(page).click();
    expect(await page.evaluate(() => window.teamRailTest.calls())).toEqual([
      { id: "a1", text: "Preserve this draft" },
    ]);
    await page.evaluate(() => {
      window.teamRailTest.finishMessage();
    });
    await disclosure(page).click();
    await check(page.locator('[data-agent-origin="a1"]')).toHaveAttribute("aria-pressed", "true");
    expect(errors).toEqual([]);
  } finally {
    await page.close();
  }
});
it("shows exact Q2 and preserves pending merge/test conflict descriptions in keyboard-accessible chips", async () => {
  const { page, errors } = await open("chat-leadless", 390);
  try {
    await check(
      page.getByRole("heading", { name: "Plan B · 2 agents · 2 working", exact: true }),
    ).toBeVisible();
    await disclosure(page, "Plan B").click();
    await check(page.locator('[data-agent-origin="b1"]')).toBeHidden();
    for (const kind of ["merge", "tests"] as const) {
      await page.evaluate(
        (events) => {
          window.teamRailTest.deliver(events);
        },
        railConflict("a3", kind),
      );
      const words = kind === "merge" ? "merge conflicts with main" : "combined tests failed";
      const chip = page.locator('.work-group[data-mode="pending"] .work-chip').first();
      await check(chip).toHaveAccessibleName(new RegExp(words));
      await chip.focus();
      await page.keyboard.press("Enter");
      await check(chip).toBeFocused();
    }
    expect(errors).toEqual([]);
  } finally {
    await page.close();
  }
});
async function graphReady(page: Page) {
  await check
    .poll(() =>
      page.evaluate(() =>
        document
          .querySelector<GraphProbeHost>('[aria-label="Repository graph"]')
          ?.oversightGraph?.animating(),
      ),
    )
    .toBe(false);
}
async function lampAt(page: Page, edge: "first" | "last") {
  const tile =
    edge === "first"
      ? page.locator(".agents-rail-tile").first()
      : page.locator(".agents-rail-tile").last();
  await tile.scrollIntoViewIfNeeded();
  const metric = await tile.evaluate((el) => {
    const avatar = el.querySelector("[data-agent-origin]"),
      lamp = el.querySelector(".agents-rail-lamp"),
      rail = el.closest(".agents-rail");
    if (!avatar || !lamp || !rail) throw new Error("Missing lamp geometry");
    const rect = (node: Element) => {
      const r = node.getBoundingClientRect();
      return { x: r.x, y: r.y, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
    };
    return {
      origin: avatar.getAttribute("data-agent-origin"),
      avatar: rect(avatar),
      lamp: rect(lamp),
      rail: rect(rail),
      tile: rect(el),
    };
  });
  for (const bound of [metric.avatar, metric.tile, metric.rail]) {
    expect(metric.lamp.x).toBeGreaterThanOrEqual(bound.x);
    expect(metric.lamp.y).toBeGreaterThanOrEqual(bound.y);
    expect(metric.lamp.right).toBeLessThanOrEqual(bound.right);
    expect(metric.lamp.bottom).toBeLessThanOrEqual(bound.bottom);
  }
  return metric;
}
async function expectWholeCaptions(page: Page) {
  const captions = await measureFoldedCaptions(page);
  expect(captions.filter((caption) => caption.state)).toHaveLength(11);
  for (const caption of captions) {
    if (caption.state) expect(caption.fragments, caption.text).toHaveLength(1);
    for (const word of caption.words) {
      expect(word.fragments, word.text).toHaveLength(1);
      const fragment = word.fragments[0];
      if (!fragment) throw new Error("Missing caption fragment");
      expect(fragment.x, word.text).toBeGreaterThanOrEqual(caption.tile.x);
      expect(fragment.right, word.text).toBeLessThanOrEqual(caption.tile.right);
    }
  }
}
it.each(["dark", "day"])(
  "scrolls eleven folded agents with stable gutter and full lamps in %s",
  async (theme) => {
    const { page, errors } = await open("chat-busy", 1280, theme);
    try {
      await page.getByRole("button", { name: "Oversight", exact: true }).click();
      const rail = page.locator(".agents-rail");
      await check(rail).toBeVisible();
      await graphReady(page);
      const size = () =>
        rail.evaluate((el) => ({
          width: el.getBoundingClientRect().width,
          clientWidth: el.clientWidth,
          gutter: getComputedStyle(el).scrollbarGutter,
        }));
      const before = await size();
      await page.evaluate(
        (events) => {
          window.teamRailTest.deliver(events);
        },
        (["starting", "paused", "halted", "stopped", "done", "ready_for_review"] as const).map(
          (state, i) => status(`extra-${String(i)}`, { state }),
        ),
      );
      await check(page.locator(".agents-rail-tile")).toHaveCount(11);
      expect(await size()).toEqual(before);
      expect(before.gutter).toBe("stable");
      await expectWholeCaptions(page);
      expect(await rail.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
      expect((await lampAt(page, "first")).origin).toBe("a1");
      expect((await lampAt(page, "last")).origin).toBe("extra-5");
      expect(await rail.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
      await inspect(page, `folded-${theme}`);
      expect(errors).toEqual([]);
    } finally {
      await page.close();
    }
  },
);
it.skipIf(!shots)(
  "captures and validates all 20 handwritten Boltons frames from the clean source commit",
  async () => {
    if (!context) throw new Error("Missing capture context");
    for (const scene of [
      "chat-busy",
      "chat-collapsed-selected",
      "chat-leadless",
      "agents-folded-overflow",
    ] as const) {
      for (const width of scene === "agents-folded-overflow" ? [1280] : [1920, 1280, 390])
        for (const theme of ["dark", "day"]) {
          const { page, errors } = await open(scene, width, theme);
          try {
            const actions = [
              "Mount production Frame from parsed handwritten envelopes and original pinned Boltons snapshot/layout",
              `Theme ${theme}; viewport ${String(width)}`,
            ];
            if (scene === "chat-collapsed-selected") {
              await disclosure(page).click();
              actions.push("Fold Lead A with a1 still selected");
            }
            let lamps: unknown = null;
            if (scene === "agents-folded-overflow") {
              await graphReady(page);
              await expectWholeCaptions(page);
              lamps = { first: await lampAt(page, "first"), last: await lampAt(page, "last") };
              actions.push("Measure first tile; scroll to and measure last tile");
            }
            if (width === 390)
              await page.locator(".chat-shell").evaluate((el) => {
                el.scrollTop = 0;
              });
            await writeFrame(page, scene, theme, actions, errors, context, lamps);
          } finally {
            await page.close();
          }
        }
    }
    await validateCoverage(context.sourceSha);
  },
  120_000,
);
