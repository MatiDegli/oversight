import { readFileSync } from "node:fs";

import type { GraphSnapshot } from "@contracts";
import { expect, it } from "vitest";

import { createInitialState } from "../state";
import { themes } from "../theme";
import { graphElements } from "./elements";
import { foldPositions } from "./foldPositions";

it("preserves small preset slots when opening any module, including stacked classes", () => {
  const root = new URL("../../../", import.meta.url);
  const graph = JSON.parse(
    readFileSync(new URL("fixtures/graph/demo-repo.json", root), "utf8"),
  ) as GraphSnapshot;
  graph.positions = JSON.parse(
    readFileSync(new URL("fixtures/layouts/demo-repo.json", root), "utf8"),
  ) as GraphSnapshot["positions"];
  const elements = graphElements({ ...createInitialState(), graph });
  const ids = elements.filter((e) => e.data.kind === "module").map((e) => e.data.id);
  const project = (open = new Set<string>()) =>
    foldPositions(elements, open, themes.dark, new Set(ids.filter((id) => !open.has(id))));
  const before = project();
  // Main's reserved slot below Cart: its folded class still occupies room inside the module.
  expect(before.positions.get("shop.inventory")).toEqual({ x: 609, y: 256 });
  for (const id of ids) {
    const opened = project(new Set([id]));
    for (const other of ids.filter((other) => other !== id))
      expect(opened.positions.get(other), `${id} must preserve ${other}`).toEqual(
        before.positions.get(other),
      );
    expect(project()).toEqual(before);
  }
});
