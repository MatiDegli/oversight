// Hand-written scale fixtures, never model recordings.
// node frontend/tools/scale-log.ts --input snapshot.json --output replay.jsonl [--positions positions.json]
import { readFile, writeFile } from "node:fs/promises";
import { parseArgs } from "node:util";

import type { GraphSnapshot, ServerEvent } from "../../contracts/generated/contracts.ts";

import { updateReplay } from "./layout.ts";

/** Build a deterministic, explicitly fake run accepted by the SSE replay player. */
export function scaleLog(snapshot: GraphSnapshot, positions?: GraphSnapshot["positions"]): string {
  const ts = "2026-10-28T15:00:00.000Z";
  const events: ServerEvent[] = [
    {
      seq: 1,
      ts,
      type: "run.started",
      payload: {
        run_id: `run-${ts.slice(0, 10).replaceAll("-", "")}-${ts.slice(11, 19).replaceAll(":", "")}`,
        contract_version: "1.5.0",
        prompt_version: "hand-written-scale-fixture",
        workspace: "hand-written scale fixture",
        base_commit: snapshot.commit,
        merge_mode: null,
        models: [
          {
            id: "fake",
            label: "Hand-written fixture",
            provider: "fake",
            model: "fake",
            reasoning: null,
          },
        ],
        slots: [{ slot: "A", default_model: "fake" }],
        limits: {
          max_steps: 30,
          max_output_tokens: 4096,
          max_wall_clock_s: 600,
          test_timeout_s: 60,
          cone_max_depth: 3,
          cone_max_nodes: 40,
        },
        budget: { budget_usd: 50, spent_usd: 0, warn_at: [0.5, 0.8], block_at: 0.9 },
      },
    },
    {
      seq: 2,
      ts,
      type: "graph.snapshot",
      payload: { ...snapshot, positions: positions ?? snapshot.positions },
    },
  ];
  // Reuse the layout tool's float serialization so positions round-trip through Python's store.
  const jsonl = events.map((event) => JSON.stringify(event)).join("\n") + "\n";
  const preset = positions ?? snapshot.positions;
  return Object.keys(preset).length ? updateReplay(jsonl, preset) : jsonl;
}

if (import.meta.main) {
  const { values } = parseArgs({
    options: {
      input: { type: "string" },
      output: { type: "string" },
      positions: { type: "string" },
    },
  });
  if (!values.input || !values.output)
    throw new Error(
      "Usage: scale-log.ts --input snapshot.json --output replay.jsonl [--positions positions.json]",
    );
  const snapshot = JSON.parse(await readFile(values.input, "utf8")) as GraphSnapshot;
  const positions = values.positions
    ? (JSON.parse(await readFile(values.positions, "utf8")) as GraphSnapshot["positions"])
    : undefined;
  await writeFile(values.output, scaleLog(snapshot, positions));
}
