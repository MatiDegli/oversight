import { describe, expect, it } from "vitest";
import { applyEvent, createInitialState } from "../state";
import { selectMeasuredCallers } from "./measuredCallers";
import {
  availableFacts,
  firstCaller,
  measuredEvents,
  measuredFacts,
  measuredState,
  withMeasuredFacts,
} from "./measuredCallers.fixture";

const select = (state = withMeasuredFacts()) => selectMeasuredCallers(state, "change-1");

describe("measured callers projection", () => {
  it("joins by id in retained distance order, without mutating facts or using graph edges", () => {
    const facts = {
      ...availableFacts,
      measured_cone: availableFacts.measured_cone?.toReversed() ?? [],
    };
    const state = withMeasuredFacts(facts);
    const before = JSON.stringify(state);
    const view = select(state);
    expect(view?.rows.map((row) => row.nodeId)).toEqual(
      state.changes["change-1"]?.proposal?.impact.entries.map((entry) => entry.node_id),
    );
    expect(view?.rows[0]).toMatchObject({
      name: "Cart.quote_add",
      distance: 1,
      label: "Result change measured",
    });
    expect(view?.rows[1]?.label).toBe("No difference measured on these inputs");
    expect(view?.rows[0]?.evidence).toBe(firstCaller);
    expect(JSON.stringify(state)).toBe(before);
    expect(select({ ...state, graph: null })?.rows[0]?.name).toBe(firstCaller.node_id);
    expect(select({ ...state, graph: state.graph && { ...state.graph, edges: [] } })).toEqual(view);
  });

  it("retains stable differences separately from partial availability and unmatched calls", () => {
    const facts = measuredFacts[1];
    if (!facts) throw new Error("Missing partial fixture");
    const rows = select(withMeasuredFacts(facts))?.rows;
    expect(rows?.[0]).toMatchObject({
      label: "Result change measured",
      availability: "Partial measurements",
    });
    expect(rows?.[0]?.evidence).toMatchObject({
      compared_count: 12,
      differing_count: 3,
      unmatched_before: 2,
      unmatched_after: 1,
    });
    expect(rows?.[1]).toMatchObject({
      label: "Possible, no change measured",
      availability: "Partial measurements",
    });
    expect(rows?.[1]?.evidence?.rows[0]?.unstable).toBe(true);
    expect(rows?.[2]?.availability).toBe("Measurements unavailable");
    expect(rows?.[3]).toMatchObject({ label: "Possible, no change measured", evidence: null });
  });

  it("keeps published reasons, excludes out-of-cone evidence, and ignores overall completeness", () => {
    const facts = measuredFacts[2];
    if (!facts) throw new Error("Missing unavailable fixture");
    const view = select(withMeasuredFacts(facts));
    expect(view?.rows[0]?.evidence?.problems).toEqual(facts.measured_cone?.[0]?.problems);
    expect(view?.rows[0]?.availability).toBe("Measurements unavailable");
    const state = withMeasuredFacts({
      ...availableFacts,
      complete: false,
      measured_cone: [firstCaller, { ...firstCaller, node_id: "outside" }],
    });
    expect(select(state)?.rows[0]?.availability).toBe("Comparisons available");
    expect(select(state)?.rows.some((row) => row.nodeId === "outside")).toBe(false);
  });

  it("distinguishes missing facts, null and legacy absence from an explicit empty array", () => {
    const legacy: Partial<typeof availableFacts> = { ...availableFacts };
    delete legacy.measured_cone;
    for (const state of [
      measuredState,
      withMeasuredFacts({ ...availableFacts, measured_cone: null }),
      withMeasuredFacts(legacy as typeof availableFacts),
    ]) {
      expect(select(state)?.recording).toBe("absent");
      expect(select(state)?.rows.every((row) => row.label === "Possible, no change measured")).toBe(
        true,
      );
    }
    expect(select(withMeasuredFacts({ ...availableFacts, measured_cone: [] }))?.recording).toBe(
      "empty",
    );
    expect(selectMeasuredCallers(measuredState, "missing")).toBeNull();
  });

  it.each([{ revision: 2 }, { head_commit: "stale-head" }, { change_id: "other-change" }])(
    "rejects stale or foreign facts: %j",
    (patch) => {
      expect(select(withMeasuredFacts({ ...availableFacts, ...patch }))?.recording).toBe("absent");
    },
  );

  it("uses the last matching publication without falling back through a null publication", () => {
    const state = withMeasuredFacts();
    expect(
      select({
        ...state,
        changeFacts: {
          "change-1": [
            availableFacts,
            { ...availableFacts, measured_cone: null },
            { ...availableFacts, head_commit: "old" },
          ],
        },
      })?.recording,
    ).toBe("absent");
  });

  it("produces the same model for streamed events and replay at every evidence cut", () => {
    let streamed = measuredState;
    for (const event of measuredEvents.filter((event) => event.seq > 20)) {
      streamed = applyEvent(streamed, event);
      const replay = measuredEvents
        .filter((item) => item.seq <= event.seq)
        .reduce(applyEvent, createInitialState());
      expect(select(streamed)).toEqual(select(replay));
    }
  });
});
