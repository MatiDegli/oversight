import { expect, it } from "vitest";
// eslint-disable-next-line no-restricted-imports -- Shared test fixture, never a product dependency.
import { sameNodeMoment, priceId } from "../state/overlap.fixture";
import { graphElements } from "./elements";

it("retains pending-both and both agent badges for the shared edit", () => {
  const node = graphElements(sameNodeMoment()).find((element) => element.data.id === priceId);
  expect(node?.classes.split(" ")).toContain("pending-both");
  expect(node?.data.marks?.badges).toEqual([
    { slot: "A", filled: true },
    { slot: "B", filled: true },
  ]);
});
