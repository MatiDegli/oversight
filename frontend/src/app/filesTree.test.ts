import { expect, it } from "vitest";
import type { ServerEvent } from "@contracts";
import log from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { applyEvent, createInitialState } from "../state";
import type { State } from "../state";
import { fileMarks, recordedTree, visibleTree } from "./filesTree";
import { selectWorkPresence } from "./workPresence";
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
  const agent = Object.values(before.changes)[0]?.agent_id;
  const files = createRecordedFiles(() => before);
  expect((await files.readFile(entry.file.path, agent)).content).toBe(entry.file.modified);
  await expect(files.readFile(entry.file.path, "agent-999")).rejects.toThrow("no source contents");
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

it("agent reads use the latest update to the path across recorded revisions", async () => {
  const first = events.find((event) => event.type === "change.updated");
  const run = events[0];
  if (!first || !run) throw new Error("Fixture needs a run and an edit");
  const path = first.payload.file.path;
  const agent = first.payload.agent_id;
  let current = applyEvent(createInitialState(), run);
  const files = createRecordedFiles(() => current);
  const update = (change_id: string, modified: string, filePath = path, agent_id = agent) => {
    current = applyEvent(current, {
      ...first,
      seq: current.lastSeq + 1,
      payload: {
        ...first.payload,
        change_id,
        agent_id,
        file: { ...first.payload.file, path: filePath, modified },
      },
    });
    expect(current.gap).toBe(false);
  };
  update("change-1", "first revision");
  update("change-2", "second revision");
  expect((await files.readFile(path, agent)).content).toBe("second revision");
  // Updating the first inserted change must win over object insertion order.
  update("change-1", "latest revision");
  update("change-2", "unrelated file", "shop/other.py");
  update("change-3", "other agent", path, "agent-999");
  expect((await files.readFile(path, agent)).content).toBe("latest revision");
});

it("marks only the chosen deleted revision and explains why it cannot open", async () => {
  const base = Object.values(state.changes)[0];
  if (!base) throw new Error("Fixture missing change");
  const path = "shop/deleted.py";
  const deleted = {
    ...base,
    resolution: null,
    files: {
      [path]: {
        author: "agent" as const,
        node_ids: [],
        file: {
          path,
          original: "original contents",
          modified: null,
          status: "deleted" as const,
          lines_added: 0,
          lines_removed: 1,
        },
      },
    },
  };
  let current: State = { ...state, changes: { deletion: deleted } };
  const files = createRecordedFiles(() => current);
  const row = () =>
    visibleTree(recordedTree(current), new Set(["shop"])).find((entry) => entry.path === path);
  expect(row()).toMatchObject({ deleted: false });
  expect((await files.readFile(path)).content).toBe("original contents");
  await expect(files.readFile(path, base.agent_id)).rejects.toThrow(
    "file was deleted in this change",
  );
  current = {
    ...current,
    changes: { deletion: { ...deleted, resolution: base.resolution } },
  };
  expect(row()).toMatchObject({ deleted: true });
  await expect(files.readFile(path)).rejects.toThrow("file was deleted in this change");
  await expect(files.readFile("no-recorded-contents.py")).rejects.toThrow("no source contents");
});

it("adapts the same agent/path/mode through focus, proposals, resolutions and reset", () => {
  let current = createInitialState();
  for (const event of events) {
    current = applyEvent(current, event);
    const before = structuredClone(current);
    const presence = selectWorkPresence(current);
    const expected = new Map<string, { agentId: string; slot: string; mode: string }[]>();
    for (const group of [...presence.current, ...presence.pending])
      for (const file of group.files) {
        const marks = expected.get(file.path) ?? [];
        if (!marks.some((m) => m.agentId === group.agentId))
          marks.push({ agentId: group.agentId, slot: group.slot, mode: group.mode });
        expected.set(file.path, marks);
      }
    expect(fileMarks(current)).toEqual(expected);
    expect(current).toEqual(before);
  }
  const run = events.find((e) => e.type === "run.started");
  if (!run) throw new Error("Missing run");
  expect(
    fileMarks(
      applyEvent(current, { ...run, seq: 1, payload: { ...run.payload, run_id: "fresh" } }),
    ),
  ).toEqual(new Map());
});

it("keeps identities sharing a slot and gives current focus precedence over pending", () => {
  const agent = Object.values(state.agents)[0];
  const change = Object.values(state.changes).find((c) => c.proposal);
  const path = change?.proposal?.files[0]?.path;
  if (!agent || !change || !path) throw new Error("Missing fixture proposal");
  const shared = {
    ...state,
    agents: {
      first: { ...agent, agent_id: "first", state: "working" as const },
      second: { ...agent, agent_id: "second", state: "working" as const },
    },
    changes: {
      pending: {
        ...change,
        agent_id: "first",
        proposal: change.proposal ? { ...change.proposal, agent_id: "first" } : null,
        status: "ready_for_review" as const,
      },
    },
    focus: {
      first: {
        agent_id: "first",
        mode: "edit" as const,
        ranges: [{ path, start_line: null, end_line: null }],
        node_ids: [],
      },
      second: {
        agent_id: "second",
        mode: "read" as const,
        ranges: [{ path, start_line: null, end_line: null }],
        node_ids: [],
      },
    },
  };
  expect(fileMarks(shared).get(path)).toEqual([
    { agentId: "first", slot: agent.slot, mode: "edit" },
    { agentId: "second", slot: agent.slot, mode: "read" },
  ]);
});
