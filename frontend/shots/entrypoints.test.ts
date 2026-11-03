import assert from "node:assert/strict";
import { test } from "node:test";

// These scripts run directly in Node, whose module resolution differs from Vite's.
void test("the capture entry point loads in native Node without starting a capture", async () => {
  const { writeSceneMetrics } = await import("./capture.ts");
  assert.equal(typeof writeSceneMetrics, "function");
});

void test("review-finding scenes load in native Node for capture planning", async () => {
  const { findingScenes } = await import("./reviewFindings.ts");
  assert.ok(findingScenes.length > 0);
  assert.ok(findingScenes.every((scene) => typeof scene.prepare === "function"));
});
