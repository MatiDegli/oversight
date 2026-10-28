import { readFileSync } from "node:fs";

import type { ServerEvent } from "@contracts";
import { expect as browserExpect } from "@playwright/test";
import { expect, it } from "vitest";

import { applyEvent, createInitialState } from "../state";
import { withPage } from "./LiveDiff.browser.support";

const events = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const through = (seq: number) =>
  events.filter((event) => event.seq <= seq).reduce(applyEvent, createInitialState());
const state = through(12);
const agent = state.agents["agent-1"];
if (!agent) throw new Error("Missing fixture agent");
const panel = '[aria-label="Live diff panel"]';

it.each(["review", "live diff"])("%s facts use Mono only for data", async (surface) => {
  await withPage(async (page) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.route("**/events?*", () => {});
    await page.route("**/files?*", (route) =>
      route.fulfill({
        json: {
          path: "shop/pricing.py",
          agent_id: "agent-1",
          content: "",
          hash: "h1",
        },
      }),
    );
    await page.evaluate(
      (value) => {
        window.panelTest.renderApp(value);
      },
      through(surface === "review" ? 20 : 12),
    );
    await page
      .getByRole("button", {
        name: surface === "review" ? "Review change" : "Live diff",
        exact: true,
      })
      .click();
    const facts = page.getByLabel("Facts", { exact: true });
    await browserExpect(facts).toBeVisible();
    for (const outcome of ["passed", "failed", "error", "timeout"] as const) {
      const value = structuredClone(through(20));
      const change = value.changes["change-1"];
      if (!change?.proposal) throw new Error("Missing proposal");
      change.proposal.tests.outcome = outcome;
      value.agentCosts = { "agent-1": outcome === "passed" ? 0.38 : null };
      await page.evaluate((value) => {
        window.panelTest.setState(value);
      }, value);
      const readings = await facts.evaluate((root) => {
        const tokens = getComputedStyle(document.documentElement);
        const textFont = tokens.getPropertyValue("--text-font").trim();
        const monoFont = tokens.getPropertyValue("--code-font").trim();
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        const wrong: string[] = [];
        let numbers = 0;
        while (walker.nextNode()) {
          const node = walker.currentNode;
          const text = node.textContent?.trim() ?? "";
          const parent = node.parentElement;
          if (!text || !parent || parent.closest("svg")) continue;
          const numeric = /^(?:[+−-]|(?:<?US\$)?[+−-]?\d+(?:\.\d+)?)$/.test(text);
          const mono = numeric || parent.closest("code") !== null;
          if (numeric) numbers++;
          if (getComputedStyle(parent).fontFamily !== (mono ? monoFont : textFont))
            wrong.push(text);
        }
        return { wrong, numbers };
      });
      expect(readings.wrong).toEqual([]);
      expect(readings.numbers).toBeGreaterThan(3);
    }
  });
});

for (const [element, text] of [
  ["task", agent.task],
  ["agent", "Agent A"],
  ["status pill", "Working"],
  [
    "edit banner",
    "This diff updates with each edit. Decisions open when the agent proposes the change.",
  ],
  ["read-only line", "Read-only · Inline diff"],
] as const)
  it(`keeps the live diff ${element}`, async () => {
    await withPage(async (page) => {
      await page.setViewportSize({ width: 1920, height: 1080 });
      // This harness seeds the actual app with fixture state; its stream stays pending and sends nothing.
      await page.route("**/events?*", () => {});
      await page.evaluate((value) => {
        window.panelTest.renderApp(value);
      }, state);
      await page.getByRole("button", { name: "Live diff", exact: true }).click();
      const live = page.locator(panel);
      await browserExpect(live.getByText(text, { exact: true })).toBeVisible();
      if (element === "read-only line")
        await browserExpect(live.locator(".lucide-lock-keyhole")).toBeVisible();
      if (element === "status pill")
        await browserExpect(live.getByText(text, { exact: true })).toHaveClass(/rounded-full/);
    });
  });

it("keeps live diff facts updated without switching to review or taking focus", async () => {
  await withPage(async (page) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.route("**/events?*", () => {});
    await page.evaluate((value) => {
      window.panelTest.renderApp(value);
    }, state);
    await page.getByRole("button", { name: "Live diff", exact: true }).click();
    await browserExpect(page.locator(`${panel} [aria-label="Facts"]`)).toBeVisible();
    await page.evaluate(() => {
      const input = document.createElement("input");
      input.setAttribute("aria-label", "Keep focus");
      document.body.append(input);
      input.focus();
    });
    await page.evaluate((value) => {
      window.panelTest.setState(value);
    }, through(20));
    await browserExpect(page.getByLabel("Keep focus")).toBeFocused();
    await browserExpect(page.locator('[aria-label="Review panel"]')).toHaveCount(0);
    await browserExpect(page.locator(`${panel} [aria-label="Tests"]`)).toContainText("28 passed");
    const chipStyle = await page.locator(`${panel} [aria-label="Steps"]`).evaluate((chip) => {
      const style = getComputedStyle(chip);
      const tokens = getComputedStyle(document.documentElement);
      const number = chip.querySelector(".font-mono");
      return {
        background: style.backgroundColor,
        size: style.fontSize,
        radius: style.borderRadius,
        font: number ? getComputedStyle(number).fontFamily : "",
        expectedFont: tokens.getPropertyValue("--code-font").trim(),
      };
    });
    expect(chipStyle.size).toBe("14px");
    expect(chipStyle.radius).toBe("7px");
    expect(chipStyle.font).toBe(chipStyle.expectedFont);
  });
});

it("keeps last steps visible below Monaco with bounded scrolling and stable focus", async () => {
  await withPage(async (page) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.route("**/events?*", () => {});
    const entry = state.steps["agent-1"]?.[0];
    if (!entry) throw new Error("Missing fixture step");
    const value = {
      ...state,
      steps: {
        ...state.steps,
        "agent-1": Array.from({ length: 30 }, (_, i) => ({
          ...entry,
          step: i + 1,
          summary: `Completed tool ${String(i + 1)}`,
        })),
      },
    };
    await page.evaluate((input) => {
      window.panelTest.renderApp(input);
    }, value);
    await page.getByRole("button", { name: "Live diff", exact: true }).click();
    const live = page.locator(panel);
    const rows = live.getByRole("list", { name: "Agent A steps, newest first" });
    await browserExpect(live.getByRole("heading", { name: "Last steps" })).toBeVisible();
    await browserExpect(rows.getByRole("listitem")).toHaveCount(30);
    await browserExpect(rows.getByRole("listitem").first()).toContainText("Completed tool 30");
    await browserExpect(live.locator(".monaco-diff-editor")).toBeVisible();
    const editorBox = await live.locator(".monaco-diff-editor").boundingBox();
    const rowsBox = await rows.boundingBox();
    expect(editorBox?.height).toBeGreaterThan(100);
    expect(rowsBox?.y).toBeGreaterThanOrEqual((editorBox?.y ?? 0) + (editorBox?.height ?? 0));
    expect((rowsBox?.y ?? 0) + (rowsBox?.height ?? 0)).toBeLessThanOrEqual(1080);
    expect(await rows.evaluate((list) => list.scrollHeight > list.clientHeight)).toBe(true);
    await rows.focus();
    await page.evaluate(
      (input) => {
        window.panelTest.setState(input);
      },
      {
        ...value,
        steps: {
          ...value.steps,
          "agent-1": [...value.steps["agent-1"], { ...entry, step: 31, summary: "Latest edit" }],
        },
      },
    );
    await browserExpect(rows).toBeFocused();
    await browserExpect(rows.getByRole("listitem").first()).toContainText("Latest edit");
  });
});
