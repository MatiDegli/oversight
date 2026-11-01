// Hand-written sketch data layered on the existing hand-written shell, never a model run.
import type {
  BehaviorDiffRow1,
  ChangeFacts,
  ParameterExtensionDiff,
  ServerEvent,
} from "@contracts";
import log from "../../../fixtures/events/handwritten-behavior-review.jsonl?raw";
import { applyEvent, createInitialState } from "../state";

export const parameterStates = [
  "rejected",
  "values",
  "partial",
  "mixed",
  "unavailable",
  "none",
] as const;
export type ParameterState = (typeof parameterStates)[number];
export const nodeId = "shop.pricing:compute_price";
const row = (inputs: string, before: string, after: string): BehaviorDiffRow1 => ({
  inputs,
  before,
  after,
  unstable: false,
});
const rejection = "raises TypeError: got an unexpected keyword argument 'cap_percent'";
export const R1 = row(
  "compute_price(1000, 20, cap_percent=15, extra_percent=15)",
  rejection,
  "int:17000",
);
export const R2 = row("compute_price(1000, 60, cap_percent=10)", rejection, "int:54000");
export const V1 = row("compute_price(1000, 20, 15)", "int:15000", "int:18000");
export const V2 = row("compute_price(1000, 60, 10)", "int:42000", "int:54000");
const B1 = row("compute_price(1000, 20, extra_percent=40)", "int:10000", "int:14000");
const B2 = row("compute_price(1000, 60, extra_percent=40)", "int:24000", "int:42000");
const ordinary = [0, 1, 5, 10].map((quantity) =>
  row(
    `compute_price(1000, ${String(quantity)})`,
    `int:${String(quantity * 1000)}`,
    `int:${String(quantity * 1000)}`,
  ),
);
export function parameterState(name: ParameterState, empty = false) {
  const events = log
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as ServerEvent);
  const state = events.filter((event) => event.seq <= 21).reduce(applyEvent, createInitialState());
  const facts = state.changeFacts["change-1"]?.at(-1);
  if (!facts) throw new Error("Missing hand-written shell facts");
  const entry: ParameterExtensionDiff = {
    node_id: nodeId,
    parameters: ["cap_percent"],
    run_count: name === "partial" ? 48 : name === "mixed" ? 4 : name === "unavailable" ? 0 : 2,
    rejected_before: name === "values" || name === "unavailable" ? 0 : name === "partial" ? 48 : 2,
    rows:
      name === "unavailable"
        ? []
        : name === "values"
          ? [V1, V2]
          : name === "mixed"
            ? [R1, R2, V1, V2]
            : name === "partial"
              ? Array.from({ length: 8 }, (_, i) =>
                  row(
                    `compute_price(1000, 20, cap_percent=${String(i)})`,
                    rejection,
                    `int:${String(20000 - i * 200)}`,
                  ),
                )
              : [R1, R2],
    not_probed:
      name === "unavailable"
        ? "probe failed"
        : name === "partial"
          ? "recording budget reached"
          : null,
  };
  const current: ChangeFacts = {
    ...facts,
    measured_cone: name === "unavailable" ? null : [],
    behavior_diff:
      name === "unavailable"
        ? null
        : [
            {
              node_id: nodeId,
              compared_count: name === "mixed" ? 6 : 4,
              differing_count: name === "mixed" ? 2 : 0,
              rows: name === "mixed" ? [...ordinary, B1, B2] : ordinary,
              not_probed: null,
            },
          ],
    review_cards:
      name === "mixed"
        ? [
            {
              node_id: nodeId,
              question: "Should this input receive the capped discount?",
              evidence_seq: 21,
              example: B1,
              answer: null,
              answered_by: null,
            },
          ]
        : null,
    parameter_extensions: name === "none" ? (empty ? [] : null) : [entry],
  };
  state.changeFacts["change-1"] = [current];
  return state;
}
