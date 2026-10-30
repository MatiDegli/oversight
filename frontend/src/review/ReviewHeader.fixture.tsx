// UI fixtures from committed event logs; never a recorded model run.
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import inbox from "../../../fixtures/events/handwritten-inbox-grants.jsonl?raw";
import ordinary from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { GraphView } from "../graph";
import { applyEvent, createInitialState, stateStore } from "../state";
import { parseEvent } from "../transport";
import { availableFacts, measuredFacts, withMeasuredFacts } from "./measuredCallers.fixture";
import type { ChangeFacts } from "@contracts";
import { ReviewPanel } from "./ReviewPanel";

export const headerCases = {
  a: { log: inbox, end: Infinity, changeId: "change-3" },
  b: { log: ordinary, end: 20, changeId: "change-1" },
  c: { log: ordinary, end: 20, changeId: "change-1" },
  d2: { log: ordinary, end: 20, changeId: "change-1" },
  d2NoReason: { log: ordinary, end: 20, changeId: "change-1" },
  d: { log: ordinary, end: 20, changeId: "change-1" },
  merged: { log: inbox, end: Infinity, changeId: "change-1" },
  approvedCone: { log: ordinary, end: Infinity, changeId: "change-1" },
};
export function headerState(name: keyof typeof headerCases) {
  if (name === "c") {
    const partial = structuredClone(measuredFacts[1] as ChangeFacts);
    const budget = partial.measured_cone?.[2];
    if (budget) budget.not_probed = "budget exhausted";
    partial.behavior_diff = [
      {
        node_id: "shop.pricing:compute_price",
        compared_count: 2,
        differing_count: 0,
        rows: [
          { inputs: "unit_price=int:100", before: "int:500", after: "int:700", unstable: true },
        ],
        not_probed: null,
      },
    ];
    return withMeasuredFacts(partial);
  }
  if (name === "d2" || name === "d2NoReason") {
    const facts: ChangeFacts = {
      ...availableFacts,
      measured_cone: [],
      behavior_diff: [
        {
          node_id: "shop.pricing:compute_price",
          compared_count: 0,
          differing_count: 0,
          rows: [],
          not_probed: name === "d2" ? "compute_price could not run outside its test setup" : null,
        },
      ],
    };
    return withMeasuredFacts(facts);
  }
  if (name === "b") {
    const callers = availableFacts.measured_cone?.slice(0, 3) ?? [];
    const facts: ChangeFacts = {
      ...availableFacts,
      complete: true,
      problems: [],
      measured_cone: callers,
      behavior_diff: [
        {
          node_id: "shop.pricing:compute_price",
          compared_count: 10,
          differing_count: 9,
          rows: [],
          not_probed: null,
        },
      ],
    };
    return withMeasuredFacts(facts);
  }
  const { log, end } = headerCases[name];
  const state = log
    .trim()
    .split("\n")
    .map(parseEvent)
    .filter((event) => event.seq <= end)
    .reduce(applyEvent, createInitialState());
  if (name === "a") {
    const first = state.changeFacts["change-3"]?.at(-1)?.review_cards?.[0];
    if (first) {
      first.answer = "yes";
      first.answered_by = "person";
    }
  }
  return state;
}

export function mountHeader(name: keyof typeof headerCases, state = headerState(name)) {
  stateStore.setState({ state });
  const host = document.createElement("div");
  host.style.cssText = "display:flex;width:880px;height:940px;margin:92px 0 0 56px";
  document.body.replaceChildren(host);
  const root = createRoot(host);
  const update = (state: ReturnType<typeof headerState>) => {
    flushSync(() => {
      root.render(
        <>
          <ReviewPanel
            state={state}
            changeId={headerCases[name].changeId}
            onClose={() => undefined}
            onDecide={() => undefined}
          />
          <div
            style={{
              position: "fixed",
              left: "960px",
              top: "92px",
              width: "900px",
              height: "940px",
            }}
          >
            <GraphView />
          </div>
        </>,
      );
    });
  };
  update(state);
  return update;
}
