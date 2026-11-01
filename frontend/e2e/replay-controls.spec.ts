import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import type { ServerEvent } from "@contracts";
import type { GraphProbeHost } from "../src/graph";
import { browserLaunchOptions } from "../src/browserLaunch.support";

const shots = process.env.REPLAY_CONTROLS_SHOTS === "1";
test.use({ launchOptions: browserLaunchOptions({ webGL: shots }), reducedMotion: "reduce" });
const events = readFileSync(
  new URL("../../fixtures/events/recorded-demo.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
let offset = 0;
const offsets = events.map((event, i) => {
  const previous = events[i - 1];
  if (previous)
    offset += Math.min(2000, Math.max(0, Date.parse(event.ts) - Date.parse(previous.ts)));
  return offset;
});
const slider = (page: Page) => page.getByRole("slider", { name: "Position in the recording" });
const row = (page: Page) => page.getByRole("region", { name: "Replay controls" });
async function prefix(page: Page, position: number) {
  const applied = events.filter((_, i) => (offsets[i] ?? Infinity) <= position);
  await expect(page.locator("#root")).toHaveAttribute("data-seq", String(applied.at(-1)?.seq));
  const graph = applied
    .filter((event) => event.type === "graph.snapshot" || event.type === "graph.delta")
    .at(-1);
  if (graph)
    await expect(page.getByRole("banner").first()).toContainText(graph.payload.commit.slice(0, 7));
}
async function seek(page: Page, seconds: number) {
  const box = await slider(page).boundingBox();
  if (!box) throw new Error("No slider");
  if (seconds === 0) await slider(page).press("Home");
  else
    await slider(page).click({ position: { x: (box.width * (seconds * 1000 + 1)) / 20178, y: 8 } });
  await prefix(page, seconds * 1000);
}
async function openFile(page: Page, path: string) {
  await page.getByRole("button", { name: "Find a function, file or command", exact: true }).click();
  await page.getByRole("combobox").fill(path);
  await expect(page.getByRole("option", { selected: true })).toContainText(
    path.split("/").at(-1) ?? path,
  );
  await page.getByRole("combobox").press("Enter");
}
async function start(page: Page) {
  await page.clock.install();
  await page.goto("/oversight/");
  await expect(row(page)).toBeVisible();
  await row(page).getByRole("button", { name: "Pause", exact: true }).click();
  await expect
    .poll(() =>
      page
        .locator('[aria-label="Repository graph"]')
        .evaluate((host: GraphProbeHost) => Boolean(host.oversightGraph)),
    )
    .toBe(true);
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
}

test("real App controls preserve prefixes, navigation, files and session ownership", async ({
  page,
}) => {
  const backend: string[] = [],
    errors: string[] = [];
  page.on("request", (request) => {
    if (/\/(agents|changes|events|files|graph|health|impact|tree)([/?]|$)/.test(request.url()))
      backend.push(request.url());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  await start(page);
  const graph = await page
    .locator('[aria-label="Repository graph"]')
    .evaluateHandle((host: GraphProbeHost) => host.oversightGraph);
  await seek(page, 8);
  const paused = await slider(page).getAttribute("aria-valuenow");
  await page.clock.runFor(2200);
  await expect(slider(page)).toHaveAttribute("aria-valuenow", paused ?? "");
  await expect(row(page).getByRole("group", { name: "Playback speed" })).toBeVisible();
  for (const rate of [2, 4, 1]) {
    const button = row(page).getByRole("button", { name: `${String(rate)}×`, exact: true });
    await button.click();
    await expect(button).toHaveAttribute("aria-pressed", "true");
    expect(await button.evaluate((node) => getComputedStyle(node).fontWeight)).toBe("700");
  }
  await row(page).getByRole("button", { name: "Play", exact: true }).click();
  await seek(page, 5);
  await expect(row(page)).toContainText("Playing");
  await row(page).getByRole("button", { name: "4×", exact: true }).click();
  await page.clock.runFor(500);
  await row(page).getByRole("button", { name: "Pause", exact: true }).click();
  expect(Number(await slider(page).getAttribute("aria-valuenow"))).toBeCloseTo(7001, -2);
  await prefix(page, 7001);
  await row(page).getByRole("button", { name: "1×", exact: true }).click();
  await seek(page, 8);
  const mark = row(page).getByRole("button", {
    name: "Agent A · Ready for review, 0:16",
    exact: true,
  });
  const seq = await page.locator("#root").getAttribute("data-seq");
  await mark.hover();
  await expect(page.getByRole("tooltip")).toContainText("32 of 32 pass");
  await expect(page.getByRole("tooltip")).toContainText("+28 −0 in 2 files");
  await expect(page.locator(".replay-ghost")).toBeVisible();
  await expect(page.locator("#root")).toHaveAttribute("data-seq", seq ?? "");
  await expect(page.getByRole("tab")).toHaveCount(0);
  await mark.focus();
  await mark.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await slider(page).focus();
  await mark.focus();
  await expect(page.getByRole("tooltip")).toBeVisible();
  await mark.press("Enter");
  await prefix(page, 16965);
  await expect(row(page)).toContainText("Paused");
  await seek(page, 8);
  const box = await slider(page).boundingBox();
  if (!box) throw new Error("No slider");
  await page.mouse.move(box.x + box.width * 0.5, box.y + 8);
  await page.mouse.down();
  await expect(page.locator("#root")).toHaveAttribute("data-seq", seq ?? "");
  await slider(page).dispatchEvent("pointercancel", { pointerId: 1 });
  await page.mouse.up();
  await expect(page.locator("#root")).toHaveAttribute("data-seq", seq ?? "");
  await slider(page).click({ position: { x: box.width * 0.5, y: 8 } });
  await prefix(page, 20178 * 0.5);
  await slider(page).press("Home");
  await slider(page).press("Shift+ArrowRight");
  await slider(page).press("ArrowLeft");
  await prefix(page, 4000);
  await slider(page).press("End");
  await prefix(page, 20178);
  await expect(row(page)).toContainText("End of recording");
  await expect(row(page).getByRole("button", { name: "Restart", exact: true })).toHaveClass(
    /is-primary/,
  );
  await row(page).getByRole("button", { name: "Play again", exact: true }).click();
  await expect(row(page).getByRole("button", { name: "Pause", exact: true })).toBeVisible();
  await row(page).getByRole("button", { name: "Pause", exact: true }).click();
  await seek(page, 8);
  await openFile(page, "shop/inventory.py");
  const file = page.getByRole("complementary", { name: "File view" });
  await expect
    .poll(async () => {
      await page.clock.runFor(32);
      return file.locator(".monaco-editor .view-lines").first().textContent();
    })
    .toContain("Inventory");
  const commit = await page.getByRole("banner").first().textContent();
  await seek(page, 5);
  await expect(page.getByRole("tab", { selected: true })).toContainText("inventory.py");
  await expect(file.getByRole("alert")).toContainText("Loading the file did not reach the server");
  expect(await page.getByRole("banner").first().textContent()).toBe(commit);
  await seek(page, 8);
  await expect(file.getByRole("alert")).toHaveCount(0);
  await expect
    .poll(async () => {
      await page.clock.runFor(32);
      return file.locator(".monaco-editor .view-lines").first().textContent();
    })
    .toContain("Inventory");
  await page.getByRole("button", { name: "Close tab inventory.py" }).click();
  await seek(page, 15);
  await page.getByRole("button", { name: "Review change", exact: true }).click();
  const reviewTab = page.getByRole("tab", { name: /Review/ });
  await expect(reviewTab).toHaveCount(1);
  await seek(page, 8);
  await expect(reviewTab).toHaveCount(0);
  await page.getByRole("button", { name: "Live diff", exact: true }).first().click();
  const diff = page.getByRole("tab", { name: /Live diff/ });
  await expect(diff).toHaveCount(1);
  await seek(page, 5);
  await expect(diff).toHaveCount(0);
  await seek(page, 8);
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await seek(page, 5);
  await expect(page.getByRole("tab", { name: "Settings", exact: true })).toHaveCount(1);
  await page.getByRole("button", { name: "Close tab Settings" }).click();
  await page.getByRole("button", { name: "Conversation", exact: true }).first().click();
  const conversation = page.getByRole("tab", { name: /conversation/ });
  await seek(page, 5);
  await expect(conversation).toHaveCount(1);
  await seek(page, 0);
  await expect(conversation).toHaveCount(0);
  await row(page).getByRole("button", { name: "Restart", exact: true }).click();
  await expect(row(page).getByRole("button", { name: "Pause", exact: true })).toBeVisible();
  await row(page).getByRole("button", { name: "Pause", exact: true }).click();
  await seek(page, 8);
  await expect(row(page)).toContainText("Paused");
  await page.getByRole("button", { name: "Two-change practice", exact: true }).click();
  await expect(row(page)).toHaveCount(0);
  await page.clock.runFor(30000);
  await expect(page.locator("#root")).toHaveAttribute("data-seq", "63");
  // Record the first visible row, including the commit before a new session's effect runs.
  // An eventual assertion alone would miss a disposed controller briefly reappearing.
  const resumed = await page.evaluateHandle(() => {
    let first: { position: string | null; state: string | null } | null = null;
    const observer = new MutationObserver(() => {
      const row = document.querySelector(".replay-bar");
      if (row && !first)
        first = {
          position: row.querySelector('[role="slider"]')?.getAttribute("aria-valuenow") ?? null,
          state: row.querySelector("small")?.textContent ?? null,
        };
    });
    observer.observe(document.body, { childList: true, subtree: true, attributes: true });
    return {
      read: () => {
        observer.disconnect();
        return first;
      },
    };
  });
  await page.getByRole("button", { name: "Return to ordinary replay", exact: true }).click();
  await expect(row(page)).toBeVisible();
  expect(await resumed.evaluate((probe) => probe.read())).toEqual({
    position: "0",
    state: "Playing",
  });
  await resumed.dispose();
  await expect(row(page)).toContainText("Playing");
  await expect(slider(page)).toHaveAttribute("aria-valuenow", "0");
  await prefix(page, 0);
  await page.clock.runFor(2000);
  await expect(slider(page)).toHaveAttribute("aria-valuenow", "2000");
  await prefix(page, 2000);
  await page.clock.runFor(32);
  expect(
    await graph.evaluate(
      (original) =>
        original ===
        document.querySelector<GraphProbeHost>('[aria-label="Repository graph"]')?.oversightGraph,
    ),
  ).toBe(true);
  const status = page.getByLabel("Status line");
  expect((await status.textContent())?.match(/Real recorded run/g)).toHaveLength(1);
  await page.setViewportSize({ width: 1279, height: 800 });
  await expect(row(page)).toBeHidden();
  expect({ backend, errors }).toEqual({ backend: [], errors: [] });
});

test("1280px controls keep target sizes, keyboard rings and tooltip clearance", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await start(page);
  await page.evaluate(() => document.fonts.ready);
  const sizes = await row(page).evaluate((root) => {
    const rect = (selector: string) => {
      const element = root.querySelector(selector);
      if (!element) throw new Error(`Missing ${selector}`);
      return element.getBoundingClientRect();
    };
    const marks = [...root.querySelectorAll(".replay-mark")].map((element) =>
      element.getBoundingClientRect(),
    );
    return {
      row: root.getBoundingClientRect().width,
      height: root.getBoundingClientRect().height,
      control: rect(".replay-button").height,
      strip: rect(".replay-strip").width,
      targets: marks.map((mark) => [mark.width, mark.height]),
      spacing: Math.min(...marks.slice(1).map((mark, i) => mark.x - (marks[i]?.x ?? 0))),
      speeds: [...root.querySelectorAll(".replay-speed button")].map(
        (element) => element.getBoundingClientRect().width,
      ),
      time: rect(".replay-time").width,
    };
  });
  expect(sizes).toMatchObject({ row: 824, height: 44, control: 34, strip: 446 });
  expect(sizes.targets).toEqual(Array.from({ length: 5 }, () => [24, 24]));
  expect(sizes.speeds).toEqual([36, 36, 36]);
  expect(sizes.time).toBeGreaterThanOrEqual(100);
  expect(Math.abs(sizes.spacing - 27)).toBeLessThanOrEqual(1);
  expect(sizes.spacing).toBeGreaterThanOrEqual(24);
  for (const target of [
    row(page).getByRole("button", { name: "Play", exact: true }),
    slider(page),
    row(page).locator(".replay-mark").first(),
    row(page).getByRole("button", { name: "1×", exact: true }),
  ]) {
    // Leave and return with real keyboard traversal to exercise :focus-visible.
    await target.press("Tab");
    await page.keyboard.press("Shift+Tab");
    await expect(target).toBeFocused();
    expect(
      await target.evaluate((element) => {
        const style = getComputedStyle(element);
        return [style.outlineWidth, style.outlineOffset, style.outlineStyle];
      }),
    ).toEqual(["2px", "2px", "solid"]);
  }
  await row(page)
    .getByRole("button", { name: "Agent A · Ready for review, 0:16", exact: true })
    .hover();
  await expect(page.getByRole("tooltip")).toBeVisible();
  const tooltip = await page.getByRole("tooltip").boundingBox();
  const bar = await row(page).boundingBox();
  if (!tooltip || !bar) throw new Error("Missing tooltip or row bounds");
  expect(Math.abs(bar.y - (tooltip.y + tooltip.height) - 8)).toBeLessThanOrEqual(1);
});

test.describe("replay hardening", () => {
  test.use({ hasTouch: true, viewport: { width: 1280, height: 800 } });
  test("touch interruptions recover and long tooltip text stays inside the strip", async ({
    page,
    context,
  }) => {
    await start(page);
    await seek(page, 8);
    const box = await slider(page).boundingBox();
    if (!box) throw new Error("No slider");
    const client = await context.newCDPSession(page);
    const first = { x: box.x + box.width * 0.4, y: box.y + 8, id: 1 };
    const second = { ...first, x: box.x + box.width * 0.8, id: 2 };
    const seq = await page.locator("#root").getAttribute("data-seq");
    const unchanged = async () => {
      await expect(page.locator("#root")).toHaveAttribute("data-seq", seq ?? "");
      await expect(page.locator(".replay-ghost")).toHaveCount(0);
    };
    await page.mouse.move(5, 5);
    await client.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [first] });
    await client.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [first, second],
    });
    await client.send("Input.dispatchTouchEvent", {
      type: "touchMove",
      touchPoints: [first, { ...second, x: second.x + 10 }],
    });
    const ghost = await page.locator(".replay-ghost").boundingBox();
    expect(ghost?.x).toBeCloseTo(first.x, 0);
    await client.send("Input.dispatchTouchEvent", { type: "touchCancel", touchPoints: [] });
    await unchanged();
    for (const interruption of ["capture", "window"]) {
      await client.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [first] });
      await slider(page).evaluate((element, reason) => {
        if (reason === "window") window.dispatchEvent(new Event("blur"));
        else {
          // Release the actual captured pointer, rather than synthesizing lostpointercapture.
          for (let id = 1; id < 20; id++)
            if (element.hasPointerCapture(id)) element.releasePointerCapture(id);
        }
      }, interruption);
      await client.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      await unchanged();
    }
    await client.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [first] });
    await client.send("Input.dispatchTouchEvent", {
      type: "touchMove",
      touchPoints: [{ ...first, x: box.x + box.width + 20 }],
    });
    await client.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await prefix(page, 20178);
    await page.touchscreen.tap(first.x, first.y);
    await prefix(page, 20178 * 0.4);
    await expect(row(page)).toContainText("Paused");
    const mark = row(page).getByRole("button", { name: "Agent A · Merged, 0:18", exact: true });
    await mark.focus();
    const tooltip = page.getByRole("tooltip");
    await expect(tooltip).toBeVisible();
    // Exercise text expansion without replacing the recording or its prefix.
    await tooltip.locator("strong").evaluate((title) => {
      title.textContent = "長い名前🙂مرحبا".repeat(20);
    });
    const bounds = await tooltip.evaluate((element) => {
      const strip = element.parentElement?.getBoundingClientRect();
      const rect = element.getBoundingClientRect();
      return {
        inside: !!strip && rect.left >= strip.left && rect.right <= strip.right,
        wraps: element.scrollWidth <= element.clientWidth,
      };
    });
    expect(bounds).toEqual({ inside: true, wraps: true });
  });
});

if (shots)
  for (const width of [1920, 1280])
    for (const theme of ["dark", "day"]) {
      test(`captures ${String(width)} ${theme}`, async ({ page }) => {
        await page.setViewportSize({ width, height: width === 1920 ? 1080 : 800 });
        await page.addInitScript((name) => {
          document.addEventListener(
            "DOMContentLoaded",
            () => {
              document.documentElement.dataset.theme = name;
            },
            { once: true },
          );
        }, theme);
        await start(page);
        const directory = new URL("../../.shots/replay-controls/", import.meta.url);
        mkdirSync(directory, { recursive: true });
        const capture = async (state: string) => {
          await page.evaluate(() => document.fonts.ready);
          await page.clock.runFor(80);
          await page.screenshot({
            path: new URL(`${state}-${String(width)}-${theme}.png`, directory).pathname,
          });
        };
        await seek(page, 8);
        await row(page).getByRole("button", { name: "Play", exact: true }).click();
        await slider(page).blur();
        await page.mouse.move(5, 5);
        await capture("playing");
        const boxes = await row(page).evaluate((root) => {
          const rect = (selector: string) => {
            const node = root.querySelector(selector);
            if (!node) throw new Error("Missing measured control");
            return node.getBoundingClientRect();
          };
          const marks = [...root.querySelectorAll(".replay-mark")].map((node) =>
            node.getBoundingClientRect(),
          );
          return {
            row: root.getBoundingClientRect().width,
            height: root.getBoundingClientRect().height,
            control: rect(".replay-button").height,
            strip: rect(".replay-strip").width,
            spacing: Math.min(...marks.slice(1).map((mark, i) => mark.x - (marks[i]?.x ?? 0))),
            targets: marks.map((mark) => [mark.width, mark.height]),
            speeds: [...root.querySelectorAll(".replay-speed button")].map(
              (node) => node.getBoundingClientRect().width,
            ),
            time: rect(".replay-time").width,
          };
        });
        writeFileSync(
          new URL(`measure-${String(width)}-${theme}.json`, directory),
          JSON.stringify(boxes, null, 2),
        );
        expect(boxes).toMatchObject({
          row: width === 1920 ? 1464 : 824,
          height: 44,
          control: 34,
          strip: width === 1920 ? 1018 : 446,
        });
        expect(Math.abs(boxes.spacing - (width === 1920 ? 61 : 27))).toBeLessThanOrEqual(1);
        expect(boxes.targets).toEqual(Array.from({ length: 5 }, () => [24, 24]));
        expect(boxes.speeds).toEqual([36, 36, 36]);
        expect(boxes.time).toBeGreaterThanOrEqual(100);
        await row(page).getByRole("button", { name: "Pause", exact: true }).click();
        await seek(page, 15);
        // Keyboard traversal supplies :focus-visible, without programmatic focus on Play.
        await slider(page).press("Shift+Tab");
        await page.keyboard.press("Shift+Tab");
        await expect(row(page).getByRole("button", { name: "Play", exact: true })).toBeFocused();
        expect(
          await row(page)
            .getByRole("button", { name: "Play", exact: true })
            .evaluate((button) => {
              const style = getComputedStyle(button);
              return [style.outlineWidth, style.outlineOffset, style.outlineStyle];
            }),
        ).toEqual(["2px", "2px", "solid"]);
        await capture("paused");
        await seek(page, 8);
        await row(page).getByRole("button", { name: "Play", exact: true }).click();
        await row(page)
          .getByRole("button", { name: "Agent A · Ready for review, 0:16", exact: true })
          .hover();
        await expect(page.getByRole("tooltip")).toBeVisible();
        const tooltip = await page.getByRole("tooltip").boundingBox();
        const bar = await row(page).boundingBox();
        if (!tooltip || !bar) throw new Error("Missing tooltip or row bounds");
        expect(Math.abs(bar.y - (tooltip.y + tooltip.height) - 8)).toBeLessThanOrEqual(1);
        await capture("seeking");
        await page.mouse.move(5, 5);
        await slider(page).press("End");
        await slider(page).blur();
        await expect(row(page)).toContainText("End of recording");
        await capture("ended");
      });
    }
