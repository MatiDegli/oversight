import { readFileSync } from "node:fs";

import type { ServerEvent } from "@contracts";
import type { editor } from "monaco-editor";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { applyEvent, createInitialState, selectLiveDiff } from "../state";
import { diffEditorOptions, LiveDiff, modelOptions, showDiff } from "./LiveDiff";

// A distinct active font catches consumers that retain a module-level font default.
vi.mock("../theme", async (importOriginal) => {
  const { themes } = await importOriginal<typeof import("../theme")>();
  return { getActiveTheme: () => ({ ...themes.dark, "code-font": "Test code font" }) };
});

// Monaco needs a browser (pnpm test:browser); these tests check the reducer-to-editor boundary.
vi.mock("./monaco", () => ({ monaco: { editor: {} } }));

const events = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);

describe("live diff inputs over the level 1 fixture", () => {
  it("shows a new selection in the same editor, attaching its models before disposing the old", () => {
    const update = events.find((event) => event.type === "change.updated");
    if (!update) throw new Error("Fixture needs change.updated");
    let state = events.filter((event) => event.seq <= 12).reduce(applyEvent, createInitialState());
    const data = selectLiveDiff(state, "change-1", "shop/pricing.py");
    if (!data) throw new Error("Missing fixture diff");
    const log: string[] = [];
    let created = 0;
    const createModel = (text: string) => {
      const name = `model ${String((created += 1))}`;
      let value = text;
      return {
        name,
        getValue: () => value,
        setValue: (next: string) => {
          value = next;
          log.push(`set ${name}`);
        },
        dispose: () => log.push(`dispose ${name}`),
      } as unknown as editor.ITextModel;
    };
    const diffEditor = {
      setModel: (models: editor.IDiffEditorModel | null) => {
        log.push(`attach ${models ? models.original.getValue() : "none"}`);
      },
    } as Pick<editor.IStandaloneDiffEditor, "setModel">;

    const first = showDiff(diffEditor, undefined, data, createModel);
    expect(first.models.original.getValue()).toBe(update.payload.file.original);
    expect(first.models.modified.getValue()).toBe(update.payload.file.modified);
    expect(log).toEqual([`attach ${data.original}`]);

    state = applyEvent(state, {
      ...update,
      seq: 13,
      payload: {
        ...update.payload,
        file: { ...update.payload.file, modified: "latest full file" },
      },
    });
    const latest = selectLiveDiff(state, "change-1", data.path);
    if (!latest) throw new Error("Missing updated diff");
    expect(showDiff(diffEditor, first, latest, createModel)).toBe(first);
    expect(first.models.modified.getValue()).toBe("latest full file");
    expect(log.slice(1)).toEqual(["set model 2"]);

    const other = showDiff(
      diffEditor,
      first,
      { ...latest, path: "shop/other.py", original: "a", modified: "b" },
      createModel,
    );
    expect(other.models.modified.getValue()).toBe("b");
    expect(log.slice(2)).toEqual(["attach a", "dispose model 1", "dispose model 2"]);
    expect(created).toBe(4);
  });

  it("asks for an inline read-only editor without features that bring their own colors", () => {
    expect(diffEditorOptions("Test code font")).toMatchObject({
      readOnly: true,
      domReadOnly: true,
      originalEditable: false,
      renderSideBySide: false,
      renderIndicators: true,
      fontFamily: "Test code font",
      minimap: { enabled: false },
      links: false,
      contextmenu: false,
      selectionHighlight: false,
      occurrencesHighlight: "off",
      stickyScroll: { enabled: false },
      guides: {
        bracketPairs: false,
        bracketPairsHorizontal: false,
        highlightActiveBracketPair: false,
      },
    });
    expect(modelOptions.bracketColorizationOptions?.enabled).toBe(false);
  });

  it("shows a selection prompt or a read-only banner on the initial render", () => {
    const state = events
      .filter((event) => event.seq <= 12)
      .reduce(applyEvent, createInitialState());
    expect(renderToStaticMarkup(<LiveDiff data={undefined} />)).toContain(
      "Select a changed file to see its live diff.",
    );
    const data = selectLiveDiff(state, "change-1", "shop/pricing.py");
    const html = renderToStaticMarkup(<LiveDiff data={data} />);
    expect(html).toContain("shop/pricing.py");
    expect(html).toContain("Read-only");
    expect(html).toContain("bg-page");
    expect(html).toContain("border-page-border");
    expect(html).not.toContain("bg-background");
    expect(html).toContain("This diff updates with each edit.");
    expect(html).not.toContain("monaco-editor");
  });
});
