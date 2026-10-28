import { expect as browserExpect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { expect, it } from "vitest";

import { data, expectDiff, render, withPage } from "./LiveDiff.browser.support";

async function expectNoDiagnostics(page: Page) {
  // Allow a validator's usual debounce to run before asserting it emitted no diagnostics.
  await page.waitForTimeout(1000);
  expect(await page.evaluate(() => window.panelTest.monaco.editor.getModelMarkers({}))).toEqual([]);
  await browserExpect(page.locator(".squiggly-error, .squiggly-warning")).toHaveCount(0);
}

it("opens files and both diff sides with highlighting only, without language workers or diagnostics", async () => {
  await withPage(async (page) => {
    const workers: string[] = [];
    const requests: string[] = [];
    page.on("worker", (worker) => workers.push(worker.url()));
    page.on("request", (request) => requests.push(request.url()));
    for (const [path, language, content] of [
      ["config.yaml", "yaml", "name: [unterminated"],
      ["config.json", "plaintext", '{"name": invalid,}'],
      ["main.py", "python", "def price():\n    return 1"],
      ["README.md", "markdown", "# Project"],
      ["setup.sh", "shell", 'echo "hello"'],
      ["settings.ini", "ini", "[project]\nname=value"],
      ["pyproject.toml", "plaintext", '[project]\nname = "demo"'],
    ] as const) {
      await page.evaluate(
        ({ path, content }) => {
          window.panelTest.renderFile(path, content);
        },
        { path, content },
      );
      await browserExpect
        .poll(() =>
          page.evaluate(() =>
            window.panelTest.monaco.editor
              .getModels()
              .map((model) => [model.getLanguageId(), model.getValue()]),
          ),
        )
        .toEqual([[language, content]]);
      if (language !== "plaintext") {
        await browserExpect
          .poll(() =>
            page.evaluate(
              ({ language, content }) =>
                window.panelTest.monaco.editor
                  .tokenize(content, language)
                  .flat()
                  .some((token) => token.type !== ""),
              { language, content },
            ),
          )
          .toBe(true);
      }
      await browserExpect(page.locator("header").getByText("No impact analysis")).toHaveCount(
        language === "python" ? 0 : 1,
      );
      await expectNoDiagnostics(page);
      await render(page, {
        ...data,
        path,
        original: `${content}\n# before`,
        modified: `${content}\n# after`,
      });
      await expectDiff(page, content.split("\n").length + 1);
      expect(
        await page.evaluate(() => {
          const models = window.panelTest.current()?.getModel();
          return [models?.original.getLanguageId(), models?.modified.getLanguageId()];
        }),
      ).toEqual([language, language]);
      await browserExpect(page.locator("header").getByText("No impact analysis")).toHaveCount(
        language === "python" ? 0 : 1,
      );
      await expectNoDiagnostics(page);
      expect(await page.evaluate(() => window.panelTest.unknownColors())).toEqual([]);
    }
    await browserExpect.poll(() => workers.length).toBeGreaterThan(0);
    expect(workers.every((url) => url.includes("/editor/editor.worker.js"))).toBe(true);
    expect(
      requests.filter((url) => /languages\/features\/|\/language\/(json|yaml)\//.test(url)),
    ).toEqual([]);
  });
}, 60_000);
