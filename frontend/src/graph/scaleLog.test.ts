import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import type { GraphSnapshot, ServerEvent } from "@contracts";
import { expect, it } from "vitest";

import { scaleLog } from "../../tools/scale-log.ts";

const snapshot: GraphSnapshot = {
  version: "tiny-v1",
  commit: "tiny-base",
  nodes: [
    {
      id: "tiny",
      kind: "module",
      name: "tiny",
      qualname: "tiny",
      path: "tiny.py",
      start_line: 1,
      end_line: 2,
      parent_id: null,
      is_test: false,
      examples: null,
    },
  ],
  edges: [],
  parse_errors: [],
  positions: {},
};

it("writes two typed, schema-valid hand-written events with and without a preset", () => {
  const root = resolve(import.meta.dirname, "../../..");
  const scratch = resolve(root, ".shots/scale");
  mkdirSync(scratch, { recursive: true });
  const directory = mkdtempSync(resolve(scratch, "scale-log-test-"));
  try {
    const plain = scaleLog(snapshot);
    const preset = scaleLog(snapshot, { tiny: { x: 12, y: 34 } });
    const records = [plain, preset].flatMap((jsonl, index) => {
      expect(jsonl.endsWith("\n")).toBe(true);
      const events = jsonl
        .trim()
        .split("\n")
        .map((line) => JSON.parse(line) as ServerEvent);
      expect(events.map(({ seq, type }) => ({ seq, type }))).toEqual([
        { seq: 1, type: "run.started" },
        { seq: 2, type: "graph.snapshot" },
      ]);
      expect(events[0]).toMatchObject({ payload: { models: [{ provider: "fake" }] } });
      expect(events[1]).toMatchObject({ payload: { nodes: snapshot.nodes } });
      return events.map((event) => ({ source: `inline-${String(index)}`, event }));
    });
    const input = resolve(directory, "records.json");
    writeFileSync(input, JSON.stringify(records));
    expect(
      execFileSync("node", ["contracts/tests/validate_serialized.cjs", input], {
        cwd: root,
        encoding: "utf8",
      }),
    ).toContain("Validated 4 recorded events");
    expect(JSON.parse(plain.trim().split("\n")[1] ?? "")).toMatchObject({ payload: snapshot });
    expect(preset).toContain('"x":12.0,"y":34.0');
    expect(snapshot.positions).toEqual({});
    expect(scaleLog({ ...snapshot, positions: { tiny: { x: 1, y: 2 } } })).toContain('"x":1.0');
    expect(scaleLog(snapshot, {})).toBe(plain);
    expect(scaleLog({ ...snapshot, positions: { tiny: { x: 1, y: 2 } } }, {})).toBe(plain);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

it("derives the run id from the event timestamp in UTC", () => {
  const [started] = scaleLog(snapshot)
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as ServerEvent);
  expect(started?.type).toBe("run.started");
  if (started?.type !== "run.started") throw new Error("Missing run.started");
  expect(started.payload.run_id).toBe("run-20261028-150000");
  const [, date, time] = started.payload.run_id.split("-");
  if (!date || !time) throw new Error("Invalid run id");
  expect(
    `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6)}T${time.slice(0, 2)}:${time.slice(2, 4)}:${time.slice(4)}.000Z`,
  ).toBe(started.ts);
});

it("refuses incomplete presets rather than silently selecting the wrong camera", () => {
  expect(() => scaleLog(snapshot, { other: { x: 0, y: 0 } })).toThrow(
    "Missing preset position: tiny",
  );
});

it("reads a snapshot and layout output through the CLI", () => {
  const root = resolve(import.meta.dirname, "../../..");
  mkdirSync(resolve(root, ".shots/scale"), { recursive: true });
  const directory = mkdtempSync(resolve(root, ".shots/scale/scale-log-cli-"));
  try {
    const input = resolve(directory, "snapshot.json");
    const positions = resolve(directory, "positions.json");
    const output = resolve(directory, "replay.jsonl");
    writeFileSync(input, JSON.stringify(snapshot));
    writeFileSync(positions, JSON.stringify({ tiny: { x: 2, y: 3 } }));
    execFileSync(
      "node",
      [
        "frontend/tools/scale-log.ts",
        "--input",
        input,
        "--positions",
        positions,
        "--output",
        output,
      ],
      { cwd: root },
    );
    expect(readFileSync(output, "utf8")).toBe(scaleLog(snapshot, { tiny: { x: 2, y: 3 } }));
    execFileSync("node", ["frontend/tools/scale-log.ts", "--input", input, "--output", output], {
      cwd: root,
    });
    expect(readFileSync(output, "utf8")).toBe(scaleLog(snapshot));
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
