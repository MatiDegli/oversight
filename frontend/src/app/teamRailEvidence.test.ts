import { expect, it } from "vitest";
import { applyEvent, createInitialState } from "../state";
import { railEvents, railScenes, railSource, railState } from "./teamRail.fixture";
import {
  expectedFrames,
  sha256,
  validateManifest,
  type RailManifest,
} from "./teamRailEvidence.testSupport";

it("requires exactly the 20 named viewport/theme frames", () => {
  expect(expectedFrames).toHaveLength(20);
  expect(new Set(expectedFrames).size).toBe(20);
  expect(expectedFrames.filter((name) => name.startsWith("agents-folded"))).toHaveLength(2);
});
it.each(railScenes)(
  "parses and folds every inclusive %s prefix with production applyEvent",
  (scene) => {
    const events = railEvents(scene);
    let state = createInitialState();
    for (const [index, event] of events.entries()) {
      expect(event.seq).toBe(index + 1);
      state = applyEvent(state, event);
      expect(state.gap).toBe(false);
      expect(state).toEqual(events.slice(0, index + 1).reduce(applyEvent, createInitialState()));
    }
    expect(state).toEqual(railState(scene));
    expect(events).toHaveLength(railSource(scene).length + 1);
  },
);
const measure = { clientWidth: 390, scrollWidth: 390, x: 0, right: 390, width: 390, height: 844 };
function manifest(): RailManifest {
  return {
    name: "chat-busy-390-dark",
    provenance: "handwritten",
    sourceSha: "a".repeat(40),
    eventSource: "frontend/src/app/teamRail.fixture.ts",
    eventSourceHash: sha256("source"),
    eventHash: sha256("events"),
    fixtureCut: 20,
    foldedCut: 21,
    graph: {
      pin: "d31d821d56c8f7756af9c30a864077f2e5855321",
      snapshotHash: sha256("snapshot"),
      layout: "fixtures/layouts/boltons-d31d821.json",
      layoutHash: sha256("layout"),
    },
    viewport: { width: 390, height: 844 },
    theme: "dark",
    setupActions: ["Mount production Frame"],
    pngHash: sha256("png"),
    pageErrors: [],
    measurements: { document: [measure], rail: [measure], chips: [measure], thread: [measure] },
    lamps: null,
  };
}
it("accepts complete handwritten manifests and rejects false provenance, errors, overflow and omissions", () => {
  expect(() => {
    validateManifest(manifest());
  }).not.toThrow();
  for (const patch of [
    { provenance: "recorded" },
    { sourceSha: "short" },
    { pageErrors: ["broken"] },
    { eventSourceHash: "" },
    { fixtureCut: 19 },
    { measurements: { document: [] } },
    { measurements: { ...manifest().measurements, thread: [{ ...measure, scrollWidth: 500 }] } },
  ]) {
    expect(() => {
      validateManifest({ ...manifest(), ...patch } as RailManifest);
    }).toThrow();
  }
});
