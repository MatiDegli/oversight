import { expect as browserExpect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { expect, it } from "vitest";

import { data, expectDiff, render, withPage } from "./LiveDiff.browser.support";

const withClock = (test: (page: Page) => Promise<void>) => withPage(test, { clock: true });

async function expectNoDiagnostics(page: Page) {
  const observation = await page.evaluateHandle(() => {
    const editor = window.panelTest.monaco.editor;
    const markers = [...editor.getModelMarkers({})];
    const subscription = editor.onDidChangeMarkers(() => {
      markers.push(...editor.getModelMarkers({}));
    });
    const setModelMarkers = editor.setModelMarkers;
    // Marker events are batched; capture even markers cleared in the same callback.
    editor.setModelMarkers = (...args) => {
      setModelMarkers(...args);
      markers.push(...editor.getModelMarkers({}));
    };
    const selector = ".squiggly-error, .squiggly-warning";
    const hasSquiggle = (node: Node) =>
      node instanceof Element && (node.matches(selector) || node.querySelector(selector) !== null);
    let squiggles = hasSquiggle(document.documentElement);
    const recordSquiggles = (records: MutationRecord[]) => {
      for (const record of records) {
        squiggles ||= hasSquiggle(record.target) || [...record.addedNodes].some(hasSquiggle);
        squiggles ||= /(?:^|\s)squiggly-(?:error|warning)(?:\s|$)/.test(record.oldValue ?? "");
      }
    };
    const observer = new MutationObserver(recordSquiggles);
    observer.observe(document.documentElement, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["class"],
      attributeOldValue: true,
    });
    return {
      result() {
        recordSquiggles(observer.takeRecords());
        return { markers, squiggles };
      },
      dispose() {
        editor.setModelMarkers = setModelMarkers;
        subscription.dispose();
        observer.disconnect();
      },
    };
  });
  try {
    // Run every timer through the validator's debounce window, without wall-clock sleep.
    await page.clock.runFor(1000);
    const result = await observation.evaluate((value) => value.result());
    expect(result.markers).toEqual([]);
    expect(result.squiggles).toBe(false);
  } finally {
    await observation.evaluate((value) => {
      value.dispose();
    });
    await observation.dispose();
  }
  expect(await page.evaluate(() => window.panelTest.monaco.editor.getModelMarkers({}))).toEqual([]);
  await browserExpect(page.locator(".squiggly-error, .squiggly-warning")).toHaveCount(0);
}

it("opens files and both diff sides with highlighting only, without language workers or diagnostics", async () => {
  await withClock(async (page) => {
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
      ["pyproject.toml", "ini", '[project]\nname = "demo"'],
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
      requests.filter((url) =>
        /\/languages\/features\/(json|css|html)\/|\/language\/(json|yaml)\//.test(url),
      ),
    ).toEqual([]);
  });
}, 60_000);

it("detects a delayed marker even when it is cleared before the observation ends", async () => {
  await withClock(async (page) => {
    await render(page);
    await expectDiff(page, 8);
    await page.evaluate(() => {
      const { monaco } = window.panelTest;
      const model = window.panelTest.current()?.getModel()?.modified;
      if (!model) throw new Error("Expected a modified model");
      setTimeout(() => {
        monaco.editor.setModelMarkers(model, "delayed-validator", [
          {
            severity: monaco.MarkerSeverity.Error,
            message: "Delayed diagnostic",
            startLineNumber: 1,
            startColumn: 1,
            endLineNumber: 1,
            endColumn: 2,
          },
        ]);
        monaco.editor.setModelMarkers(model, "delayed-validator", []);
      }, 500);
    });
    await expect(expectNoDiagnostics(page)).rejects.toHaveProperty(
      "actual.0.message",
      "Delayed diagnostic",
    );
    expect(await page.evaluate(() => window.panelTest.monaco.editor.getModelMarkers({}))).toEqual(
      [],
    );
    await browserExpect(page.locator(".squiggly-error, .squiggly-warning")).toHaveCount(0);
  });
});

it.each(["squiggly-error", "squiggly-warning"])(
  "detects a delayed %s even when its class is removed before observation ends",
  async (className) => {
    await withClock(async (page) => {
      await page.evaluate((name) => {
        setTimeout(() => {
          const squiggle = document.createElement("span");
          document.body.append(squiggle);
          squiggle.className = name;
          squiggle.className = "";
          squiggle.remove();
        }, 500);
      }, className);
      await expect(expectNoDiagnostics(page)).rejects.toMatchObject({
        actual: true,
        expected: false,
      });
      await browserExpect(page.locator(".squiggly-error, .squiggly-warning")).toHaveCount(0);
    });
  },
);
