import { expect, it } from "vitest";
import type { ServerEvent } from "@contracts";
import log from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { applyEvent, createInitialState } from "../state";
import { recordedTree, visibleTree } from "./filesTree";
import { createRecordedFiles } from "./recordedFiles";

const events = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const state = events.reduce(applyEvent, createInitialState());

it("builds only recorded paths, deduplicating parents and file updates", () => {
  const tree = recordedTree(state);
  expect(tree.files.map((file) => file.path)).toEqual(
    [
      ...new Set([
        ...(state.graph?.nodes.filter((node) => node.kind === "module").map((node) => node.path) ??
          []),
        ...Object.values(state.changes).flatMap((change) => Object.keys(change.files)),
      ]),
    ].sort(),
  );
  expect(tree.directories).toContain("shop");
  expect(tree.files.every((file) => file.hash === null)).toBe(true);
  expect(tree.truncated).toBe(false);
});

it("sorts folders before files and preserves hierarchy as folders unfold", () => {
  const tree = {
    directories: ["z", "a", "a/nested"],
    files: [
      { path: "a/one.py", hash: null },
      { path: "readme.md", hash: null },
    ],
  };
  expect(visibleTree(tree, new Set()).map((row) => row.path)).toEqual(["a", "z", "readme.md"]);
  const rows = visibleTree(tree, new Set(["a"]));
  expect(rows.map((row) => row.path)).toEqual(["a", "a/nested", "a/one.py", "z", "readme.md"]);
  expect(rows[2]).toMatchObject({ depth: 2, parent: "a", position: 2, siblings: 2 });
});

it("keeps capped entries reachable when their parent was omitted", () => {
  expect(
    visibleTree(
      { directories: [], files: [{ path: "one/two/a.py", hash: null }] },
      new Set(["one", "one/two"]),
    ).map((row) => row.path),
  ).toEqual(["one", "one/two", "one/two/a.py"]);
});

it("recorded file views expose exact carried contents and refuse writes", async () => {
  const files = createRecordedFiles(() => state);
  const change = Object.values(state.changes).find(
    (change) => change.resolution?.merge === "merged",
  );
  if (!change) throw new Error("Fixture has no merge");
  const entry = Object.values(change.files)[0];
  if (!entry) throw new Error("Fixture has no file");
  expect(files.readOnly).toBe(true);
  expect((await files.readFile(entry.file.path)).content).toBe(entry.file.modified);
  await expect(
    files.saveFile({ path: entry.file.path, content: "x", base_hash: "recorded" }),
  ).rejects.toThrow("read-only");
  await expect(files.readFile("missing.py")).rejects.toThrow("no source contents");
});

it("before a merge the recorded main file is the original", async () => {
  const before = events
    .filter((event) => event.type !== "change.resolved" && event.seq <= 20)
    .reduce(applyEvent, createInitialState());
  const entry = Object.values(Object.values(before.changes)[0]?.files ?? {})[0];
  if (!entry) throw new Error("Fixture has no edit");
  expect((await createRecordedFiles(() => before).readFile(entry.file.path)).content).toBe(
    entry.file.original,
  );
});

it("successive recorded merges use resolution order, independent of agent start order", async () => {
  const base = Object.values(state.changes)[0];
  if (!base) throw new Error("Fixture missing change");
  const make = (resolutionSeq: number, modified: string) => ({
    ...base,
    resolutionSeq,
    files: {
      "shop/example.py": {
        author: "agent" as const,
        node_ids: [],
        file: {
          path: "shop/example.py",
          original: "base",
          modified,
          status: "modified" as const,
          lines_added: 1,
          lines_removed: 1,
        },
      },
    },
  });
  const current = { ...state, changes: { first: make(30, "latest"), second: make(20, "older") } };
  expect((await createRecordedFiles(() => current).readFile("shop/example.py")).content).toBe(
    "latest",
  );
});
