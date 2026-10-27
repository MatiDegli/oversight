import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { hostname } from "node:os";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import recorded from "../../../fixtures/events/recorded-demo.jsonl?raw";
import provenance from "../../../fixtures/events/recorded-demo.jsonl.provenance.json";
import { createInitialState, applyEvent, selectAgentCard } from "../state";
import { replaySource } from "./index";
import { parseEvent } from "./events";

const secretScanArgs = [
  "stdin",
  "--config",
  fileURLToPath(new URL("../../../.gitleaks.toml", import.meta.url)),
  "--redact",
  "--no-banner",
  "--log-level",
  "error",
];

function expectPublishable(text: string) {
  // Include the exact bundled text and decoded strings: JSON escapes must not hide a leak.
  const decoded: string[] = [];
  for (const line of text.trim().split("\n")) {
    JSON.parse(line, (_key, value: unknown) => {
      if (typeof value === "string") decoded.push(value);
      return value;
    });
  }
  const scan = [text, ...decoded].join("\n");
  expect(scan).not.toMatch(
    /(?<![\w.~/\\)\]}<*])\/(?=[\w.~@$%+-])|(?<![\w.:/\\])(?:\/{2,}|\\{2,})(?=[\w.~@$%+-])|~\/|\bfile:\/\/\/|(?<!\w)[A-Za-z]:[\\/]/,
  );
  expect(scan).not.toMatch(/\b(?:DESKTOP|LAPTOP|WIN|WSL)-[\w-]+\b/i);
  expect(scan).not.toContain(hostname());
  const result = spawnSync("gitleaks", secretScanArgs, {
    input: scan,
    encoding: "utf8",
    timeout: 30_000,
  });
  expect(result.error).toBeUndefined();
  expect(result.status).toBe(0);
}

describe("the public replay recording", () => {
  it("defaults to the exact exported real run and its receipt", () => {
    expect(replaySource.text).toBe(recorded);
    expect(replaySource.kind).toBe("real recorded run");
    expect(replaySource.label).toBe("Recorded run · Oct 27, 2026");
    expect(createHash("sha256").update(recorded, "utf8").digest("hex")).toBe(provenance.sha256);
    const events = recorded.trim().split("\n").map(parseEvent);
    expect(events).toHaveLength(provenance.event_count);
    expect(events[0]).toMatchObject({
      type: "run.started",
      payload: { run_id: provenance.run_id },
    });
  });

  it("records the real model id on every usage tick for both agents", () => {
    const ticks = recorded
      .trim()
      .split("\n")
      .map(parseEvent)
      .filter((event) => event.type === "usage.tick");
    const models = {
      "agent-1": "muse-spark-1.3-contributor",
      "agent-2": "meta-models/muse-spark-open-1026",
    };
    expect(new Set(ticks.map((event) => event.payload.agent_id))).toEqual(
      new Set(Object.keys(models)),
    );
    for (const { payload } of ticks)
      expect(payload.model).toBe(models[payload.agent_id as keyof typeof models]);
  });

  it("scans the exact bundled log for absolute paths, credentials and machine names", () => {
    expectPublishable(recorded);
  });

  it.each([
    "/home/private/workspace",
    "C:\\Users\\private\\workspace",
    "\\\\private-host\\share",
    "file:///private/workspace",
    "DESKTOP-PRIVATE",
    hostname(),
    "ghp_" + "0123456789abcdef".repeat(3),
  ])("the leak gate rejects an injected private value (%#)", (leak) => {
    // Put the leak inside JSON, just as file contents and tool summaries are stored in the log.
    const contaminated = recorded + JSON.stringify({ summary: leak }) + "\n";
    expect(() => {
      expectPublishable(contaminated);
    }).toThrow();
  });

  it("replays both approved merges and shows the recorded model names", () => {
    const events = recorded.trim().split("\n").map(parseEvent);
    const state = events.reduce(applyEvent, createInitialState());
    expect(Object.keys(state.agents)).toHaveLength(2);
    expect(selectAgentCard(state, "agent-1")?.modelLabel).toBe("Muse Spark");
    expect(selectAgentCard(state, "agent-2")?.modelLabel).toBe("Muse Spark Open");
    const decisions = events.filter((event) => event.type === "change.resolved");
    expect(decisions).toHaveLength(2);
    expect(
      decisions.every(
        (event) => event.payload.decision === "approved" && event.payload.merge === "merged",
      ),
    ).toBe(true);
    expect(state.lastSeq).toBe(provenance.event_count);
    expect(state.gap).toBe(false);
  });
});
