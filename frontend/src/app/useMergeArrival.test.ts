import { readFileSync } from "node:fs";
import type { ServerEvent } from "@contracts";
import { expect, it } from "vitest";
import { applyEvent, createInitialState } from "../state";
import { graphElements } from "../graph";
import { mergeArrivals, mergingChanges } from "./useMergeArrival";
const events = readFileSync(
  new URL("../../../fixtures/events/handwritten-level1.jsonl", import.meta.url),
  "utf8",
)
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
const state = events.filter((event) => event.seq <= 21).reduce(applyEvent, createInitialState());
it("holds changed and dashed pending marks until the shared 250 + 1450 ms arrival", () => {
  const starts = mergeArrivals(state, new Map(), 1000);
  const before = mergingChanges(starts, 2699, false);
  expect(before).toEqual(["change-1"]);
  const row = graphElements(state, false, null, null, false, null, before).find(
    (node) => node.data.id === "shop.pricing:compute_price",
  );
  expect(row?.data.marks?.word).toBe("changed");
  expect(row?.classes).toContain("pending-A");
  const after = mergingChanges(starts, 2700, false);
  expect(after).toEqual([]);
  expect(
    graphElements(state, false, null, null, false, null, after).find(
      (node) => node.data.id === row?.data.id,
    )?.classes,
  ).not.toContain("pending-A");
  expect(mergingChanges(starts, 1000, true)).toEqual([]);
  expect(mergingChanges(mergeArrivals(state, new Map(), 1000, true), 1000, false)).toEqual([]);
  expect(mergeArrivals(createInitialState(), starts, 3000).size).toBe(0);
});
