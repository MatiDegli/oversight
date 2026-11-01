import type { GraphNode } from "@contracts";
import { describe, expect, it } from "vitest";
import { folderGroups } from "./groups";

const module = (path: string, is_test = false): GraphNode => ({
  examples: null,
  id: path,
  path,
  is_test,
  kind: "module",
  name: path,
  qualname: path,
  parent_id: null,
  start_line: 1,
  end_line: 1,
});
const folder = (name: string, count: number) =>
  Array.from({ length: count }, (_, i) => module(`${name}/${String(i)}.py`));

describe("folder groups", () => {
  it("separates Boltons folders, leaves docs alone, and puts all tests together", () => {
    const groups = folderGroups([
      ...folder("boltons", 30),
      ...folder("misc", 3),
      module("docs/conf.py"),
      module("tests/unit/a.py", true),
      module("checks/b.py", true),
    ]);
    expect(groups.map((g) => [g.label, g.panel, g.moduleIds.length])).toEqual([
      ["boltons", true, 30],
      ["docs", false, 1],
      ["misc", true, 3],
      ["tests", true, 2],
    ]);
    expect(groups[1]?.moduleIds).toEqual(["docs/conf.py"]);
  });
  it("leaves the flat shop demo and all-root repositories ungrouped", () => {
    expect(folderGroups([module("shop/__init__.py"), ...folder("shop", 5)])).toEqual([]);
    expect(folderGroups([module("a.py"), module("b.py")])).toEqual([]);
  });
  it("keeps root files in a panel-free group beside folders", () => {
    expect(
      folderGroups([module("a.py"), module("b.py"), ...folder("src", 2)]).map((g) => [
        g.label,
        g.panel,
        g.moduleIds,
      ]),
    ).toEqual([
      ["", false, ["a.py", "b.py"]],
      ["src", true, ["src/0.py", "src/1.py"]],
    ]);
  });
  it("looks below a shared folder for the first splitting level", () => {
    expect(
      folderGroups([...folder("src/lib", 2), ...folder("src/tools", 2)]).map((g) => g.label),
    ).toEqual(["src/lib", "src/tools"]);
  });
  it("accepts named groups only for flat folders over twelve modules", () => {
    const named = new Map([
      [
        "lib",
        [
          { label: "Text", moduleIds: ["lib/0.py", "lib/1.py"] },
          {
            label: "Files",
            moduleIds: Array.from({ length: 11 }, (_, i) => `lib/${String(i + 2)}.py`),
          },
        ],
      ],
    ]);
    expect(
      folderGroups([...folder("lib", 13), module("docs/conf.py")], named).map((g) => g.label),
    ).toEqual(["docs", "Text", "Files"]);
    expect(
      folderGroups([...folder("lib", 12), module("docs/conf.py")], named).map((g) => g.label),
    ).toEqual(["docs", "lib"]);
    expect(
      folderGroups(
        [...folder("lib/sub", 13), module("lib/a.py"), module("docs/conf.py")],
        named,
      ).some((g) => g.label === "Text"),
    ).toBe(false);
  });
  it("ignores non-module nodes and does not mutate inputs", () => {
    const nodes = [...folder("a", 2), ...folder("b", 2)];
    const before = structuredClone(nodes);
    expect(folderGroups([...nodes, { ...module("else.py"), kind: "function" }])).toEqual(
      folderGroups(nodes),
    );
    expect(nodes).toEqual(before);
  });
});

it("keeps the first folder level when tests already split a shared library", () => {
  const groups = folderGroups([
    ...folder("lib/core", 2),
    ...folder("lib/io", 2),
    module("tests/unit/a.py", true),
  ]);
  expect(groups.map((g) => g.label)).toEqual(["lib", "tests"]);
});
