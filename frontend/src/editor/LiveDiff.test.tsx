import { readFileSync } from "node:fs";

import type { ServerEvent } from "@contracts";
import { DiffEditor } from "@monaco-editor/react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { applyEvent, createInitialState, selectLiveDiff } from "../state";
import { InlineDiff, LiveDiff } from "./LiveDiff";

// Monaco needs a browser; these tests check the real reducer-to-DiffEditor boundary.
vi.mock("./monaco", () => ({ monaco: { editor: {} } }));

const events = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);

describe("live diff inputs over the level 1 fixture", () => {
  it("passes complete files to the same inline read-only DiffEditor with stable model paths", () => {
    const update = events.find((event) => event.type === "change.updated");
    if (!update) throw new Error("Fixture needs change.updated");
    let state = events.filter((event) => event.seq <= 12).reduce(applyEvent, createInitialState());
    const data = selectLiveDiff(state, "change-1", "shop/pricing.py");
    if (!data) throw new Error("Missing fixture diff");
    const first = InlineDiff({ data });
    expect(first.type).toBe(DiffEditor);
    expect(first.key).toBeNull();
    expect(first.props).toMatchObject({
      original: update.payload.file.original,
      modified: update.payload.file.modified,
      language: "python",
      options: {
        readOnly: true,
        domReadOnly: true,
        originalEditable: false,
        renderSideBySide: false,
        renderIndicators: true,
      },
    });
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
    const next = InlineDiff({ data: latest });
    expect(next.type).toBe(first.type);
    expect(next.key).toBe(first.key);
    expect(next.props.originalModelPath).toBe(first.props.originalModelPath);
    expect(next.props.modifiedModelPath).toBe(first.props.modifiedModelPath);
    expect(next.props.original).toBe(first.props.original);
    expect(next.props.modified).toBe("latest full file");
  });

  it("shows a selection prompt or a read-only banner, without creating Monaco before fonts", () => {
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
    expect(html).toContain("This diff updates with each edit.");
    expect(html).not.toContain("monaco-editor");
  });
});
