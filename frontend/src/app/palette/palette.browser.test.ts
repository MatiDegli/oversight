import { expect as check } from "@playwright/test";
import type { Page } from "@playwright/test";
import { expect, it } from "vitest";
import { withPage, filePanel, input, clickUntouched } from "../fileView.browser.support";
import { through } from "../replay.fixture";

const paletteCss = readFileSync(new URL("./palette.css", import.meta.url), "utf8");

const palette = (page: Page) =>
  page.getByRole("dialog", { name: "Find a function, file or command" });
const search = (page: Page) => palette(page).getByRole("combobox");
async function open(page: Page, query = "") {
  await page.getByRole("button", { name: "Find a function, file or command", exact: true }).click();
  await check(search(page)).toBeFocused();
  await search(page).fill(query);
}
async function command(page: Page, name: string) {
  await open(page, name);
  await palette(page)
    .getByRole("option", { name: new RegExp(`^${name}`) })
    .click();
  await check(palette(page)).toHaveCount(0);
}

it("the top bar search uses field radius and body typography", async () => {
  await withPage(async (page) => {
    const trigger = page.getByRole("button", {
      name: "Find a function, file or command",
      exact: true,
    });
    const tokens = await page.locator("html").evaluate((root) => {
      const style = getComputedStyle(root);
      return {
        radius: style.getPropertyValue("--radius-card").trim(),
        size: style.getPropertyValue("--body-size").trim(),
        font: style.getPropertyValue("--text-font").trim(),
      };
    });
    await check(trigger).toHaveCSS("border-radius", tokens.radius);
    await check(trigger).toHaveCSS("font-size", tokens.size);
    await check(trigger).toHaveCSS("font-family", tokens.font);
  });
});

it("keeps paths and qualified names in Mono, and commands in the text face", async () => {
  await withPage(async (page) => {
    await page.route("**/tree?*", (route) =>
      route.fulfill({
        status: 501,
        json: {
          error: { code: "not_implemented", message: "Replay" },
        },
      }),
    );
    await open(page);
    const fonts = await page.locator("html").evaluate((root) => {
      const style = getComputedStyle(root);
      return {
        code: style.getPropertyValue("--code-font").trim(),
        text: style.getPropertyValue("--text-font").trim(),
      };
    });
    for (const group of ["Functions, methods and classes", "Files", "Commands"]) {
      const rows = palette(page).getByRole("group", { name: group, exact: true });
      const copy = rows.locator(".palette-copy > span");
      expect(await copy.count()).toBeGreaterThan(0);
      if (group !== "Commands") {
        const details = await rows.locator(".palette-detail").allTextContents();
        expect(details.some((text) => text.includes("shop/"))).toBe(true);
        if (group.startsWith("Functions"))
          expect(details.some((text) => text.includes("Cart."))).toBe(true);
      }
      for (const span of await copy.all())
        await check(span).toHaveCSS("font-family", group === "Commands" ? fonts.text : fonts.code);
    }
  });
});

it("every palette color comes from a theme token", async () => {
  await withPage(async (page) => {
    const rawColors = await page.evaluate((css) => {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(css);
      if (sheet.cssRules.length === 0) throw new Error("Palette rules were not parsed");
      const raw: string[] = [];
      function inspect(rules: CSSRuleList) {
        for (const rule of rules) {
          if (rule instanceof CSSGroupingRule) inspect(rule.cssRules);
          if (!(rule instanceof CSSStyleRule)) continue;
          inspect(rule.cssRules);
          for (const property of rule.style) {
            const value = rule.style.getPropertyValue(property);
            const literals = value.replace(/var\(--[\w-]+\)/g, "");
            const candidates =
              literals.match(
                /#[\da-f]+\b|(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\([^)]*\)|[a-z][\w-]*/gi,
              ) ?? [];
            if (
              candidates.some(
                (candidate) =>
                  !["initial", "inherit", "unset", "revert", "revert-layer"].includes(candidate) &&
                  CSS.supports("color", candidate),
              )
            )
              raw.push(`${rule.selectorText} { ${property}: ${value} }`);
          }
        }
      }
      inspect(sheet.cssRules);
      return raw;
    }, paletteCss);
    expect(rawColors).toEqual([]);
  });
});

it("clicking the top bar search opens the palette with Group typography", async () => {
  await withPage(async (page) => {
    await page.route("**/tree?*", (route) =>
      route.fulfill({
        status: 501,
        json: { error: { code: "not_implemented", message: "Replay" } },
      }),
    );
    await page
      .getByRole("button", { name: "Find a function, file or command", exact: true })
      .click();
    await check(palette(page)).toBeVisible();
    await check(search(page)).toBeFocused();
    for (const name of ["Functions, methods and classes", "Files", "Commands"]) {
      const heading = palette(page).getByRole("heading", { name, exact: true });
      await check(heading).toHaveCSS("font-size", "11.5px");
      await check(heading).toHaveCSS("font-weight", "600");
      await check(heading).toHaveCSS("letter-spacing", "0.92px");
      await check(heading).toHaveCSS("text-transform", "uppercase");
    }
  });
});

it("Ctrl K opens from shell controls; arrows, count, empty results and Enter follow combobox semantics", async () => {
  await withPage(async (page) => {
    await page.route("**/tree?*", (route) =>
      route.fulfill({
        status: 501,
        json: { error: { code: "not_implemented", message: "Replay" } },
      }),
    );
    await page.getByRole("button", { name: "Files", exact: true }).focus();
    await page.keyboard.press("Control+k");
    await check(search(page)).toBeFocused();
    await check(search(page)).toHaveAttribute("aria-expanded", "true");
    await check(search(page)).toHaveAttribute("aria-autocomplete", "list");
    for (const name of ["Functions, methods and classes", "Files", "Commands"])
      await check(palette(page).getByRole("group", { name, exact: true })).toBeVisible();
    const options = palette(page).getByRole("option");
    await check(palette(page).getByRole("status")).toHaveText(
      `${String(await options.count())} matches`,
    );
    const first = await search(page).getAttribute("aria-activedescendant");
    await page.keyboard.press("ArrowDown");
    expect(await search(page).getAttribute("aria-activedescendant")).not.toBe(first);
    await page.keyboard.press("ArrowUp");
    await check(search(page)).toHaveAttribute("aria-activedescendant", first ?? "");
    await search(page).fill("not-a-match-123");
    await check(options).toHaveCount(0);
    await check(palette(page).getByRole("status")).toHaveText("0 matches");
    await check(search(page)).not.toHaveAttribute("aria-activedescendant");
    await page.keyboard.press("Enter");
    await check(palette(page)).toBeVisible();
    await search(page).fill("shop/cart.py");
    await page.keyboard.press("Enter");
    await check(palette(page)).toHaveCount(0);
    await check(filePanel(page).getByRole("heading", { name: "shop/cart.py" })).toBeVisible();
  });
});

it("leaves Ctrl K as Monaco's chord prefix in an editable editor", async () => {
  await withPage(async (page) => {
    await clickUntouched(page);
    await input(page).focus();
    await check(input(page)).toBeFocused();
    await page.keyboard.press("Control+k");
    await check(palette(page)).toHaveCount(0);
    await page.keyboard.press("Control+c");
    await check(palette(page)).toHaveCount(0);
    await check(filePanel(page).getByText("Unsaved edits", { exact: true })).toBeVisible();
  });
});

it("Ctrl K opens from read-only Monaco; Escape restores its exact focus without closing the tab", async () => {
  await withPage(async (page) => {
    await page.getByRole("button", { name: /change ready for review/ }).click();
    const original = page
      .locator('.monaco-editor [role="textbox"][aria-autocomplete="none"]')
      .first();
    await original.focus();
    await page.keyboard.press("Control+k");
    await check(search(page)).toBeFocused();
    const tabs = await page
      .getByRole("tablist", { name: "Open files and changes" })
      .getByRole("tab")
      .count();
    await page.keyboard.press("Escape");
    await check(original).toBeFocused();
    await check(palette(page)).toHaveCount(0);
    await check(
      page.getByRole("tablist", { name: "Open files and changes" }).getByRole("tab"),
    ).toHaveCount(tabs);
  });
});

it("Escape returns to the review comment with text intact and no decision command or request", async () => {
  await withPage(async (page) => {
    const posts: string[] = [];
    page.on("request", (request) => {
      if (request.method() === "POST") posts.push(request.url());
    });
    await page.getByRole("button", { name: /change ready for review/ }).click();
    const comment = page.getByRole("textbox", {
      name: "Comment (required for Request changes)",
      exact: true,
    });
    await comment.fill("Keep the comment and my focus.");
    await page.keyboard.press("Control+k");
    await check(search(page)).toBeFocused();
    const commands = palette(page).getByRole("group", { name: "Commands", exact: true });
    await check(commands.getByRole("option")).toHaveCount(6);
    await check(commands).not.toContainText(/approve|request changes|reject|stop|discard/i);
    for (const text of ["Approve", "Request changes", "Reject", "Stop and discard"]) {
      await search(page).fill(text);
      await check(palette(page).getByRole("option")).toHaveCount(0);
      await page.keyboard.press("Enter");
    }
    await page.keyboard.press("Escape");
    await check(comment).toBeFocused();
    await check(comment).toHaveValue("Keep the comment and my focus.");
    expect(posts).toEqual([]);
  });
});

it("uses the live Files listing, including non-Python files, and opens file view", async () => {
  await withPage(async (page) => {
    const runId = through(20).run?.run_id;
    await page.route("**/tree?*", (route) =>
      route.fulfill({
        json: {
          run_id: runId,
          agent_id: null,
          graph_version: null,
          directories: ["docs"],
          files: [{ path: "docs/guide.md", hash: "h1" }],
          truncated: true,
        },
      }),
    );
    await page.getByRole("button", { name: "Files", exact: true }).click();
    await page.getByRole("treeitem", { name: "docs", exact: true }).click();
    await check(page.getByRole("treeitem", { name: "guide.md", exact: true })).toBeVisible();
    await open(page, "guide.md");
    await check(palette(page).getByRole("option")).toHaveCount(1);
    await check(palette(page)).toContainText("File listing is truncated");
    await page.keyboard.press("Enter");
    await check(filePanel(page).getByRole("heading", { name: "docs/guide.md" })).toBeVisible();
  });
});

it("commands toggle sidebar and dock, maximize, restore, rescan once and close the tab", async () => {
  await withPage(async (page) => {
    await clickUntouched(page);
    await page.route("**/tree?*", (route) =>
      route.fulfill({
        status: 501,
        json: { error: { code: "not_implemented", message: "Replay" } },
      }),
    );
    const rescans: string[] = [];
    await page.route("**/graph/rescan", (route) => {
      rescans.push(route.request().method());
      return route.fulfill({ status: 202 });
    });
    await command(page, "Toggle sidebar");
    await check(page.getByLabel("Files sidebar")).toBeVisible();
    await command(page, "Toggle sidebar");
    await check(page.getByLabel("Files sidebar")).toHaveCount(0);
    await command(page, "Toggle bottom dock");
    await check(page.locator(".bottom-dock")).toBeVisible();
    await command(page, "Maximize focus panel");
    await check(page.getByRole("button", { name: "Show graph", exact: true })).toBeVisible();
    await command(page, "Restore graph");
    await check(
      page.getByRole("button", { name: "Maximize focus panel", exact: true }),
    ).toBeVisible();
    await command(page, "Rescan graph");
    await check.poll(() => rescans).toEqual(["POST"]);
    await command(page, "Close focus tab");
    await check(page.getByLabel("Focus panel", { exact: true })).toHaveCount(0);
    await command(page, "Focus task box");
    await check(
      page.getByRole("textbox", { name: "Task for the new agent", exact: true }),
    ).toBeFocused();
  });
});

it("events never open a palette or move focus, and updating results never chooses one", async () => {
  await withPage(async (page) => {
    await clickUntouched(page);
    const trigger = page.getByRole("button", {
      name: "Find a function, file or command",
      exact: true,
    });
    await trigger.focus();
    await page.evaluate(() => {
      window.fileViewTest.refreshFiles("graph.delta");
    });
    await check(trigger).toBeFocused();
    await check(palette(page)).toHaveCount(0);
    const activeTab = await page.getByRole("tab", { selected: true }).textContent();
    await open(page, "compute");
    await page.evaluate(() => {
      window.fileViewTest.commit("new-commit");
    });
    await check(search(page)).toBeFocused();
    await check(search(page)).toHaveValue("compute");
    await page.keyboard.press("Escape");
    await check(trigger).toBeFocused();
    expect(await page.getByRole("tab", { selected: true }).textContent()).toBe(activeTab);
  });
});
import { readFileSync } from "node:fs";
