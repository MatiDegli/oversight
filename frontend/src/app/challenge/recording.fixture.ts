import { readFileSync } from "node:fs";
import type { RecordingProvenance } from "../../transport";
export const recording = {
  text: readFileSync(
    new URL("../../../../fixtures/events/recorded-demo.jsonl", import.meta.url),
    "utf8",
  ),
  provenance: JSON.parse(
    readFileSync(
      new URL("../../../../fixtures/events/recorded-demo.jsonl.provenance.json", import.meta.url),
      "utf8",
    ),
  ) as RecordingProvenance,
};
