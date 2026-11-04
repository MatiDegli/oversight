// Handwritten stage evidence, stretched uniformly for long-duration layout coverage.
import { useState } from "react";
import { createRoot } from "react-dom/client";
import type { ChangeFacts, ServerEvent } from "@contracts";
import log from "../../../fixtures/events/stage-clocks.jsonl?raw";
import { applyEvent, createInitialState } from "../state";
import { parseEvent } from "../transport";
import { ReviewHeader } from "../review";
import { ChatChangeCard } from "./shell/ChatChangeCard";
import "./shell/chatReview.css";

function exampleFacts(nodeId: string): ChangeFacts["measured_cone"] {
  return [
    {
      node_id: nodeId,
      compared_count: 12,
      differing_count: 3,
      not_probed: null,
      outcome: "changed",
      unmatched_before: 0,
      unmatched_after: 0,
      not_compared: [],
      complete: true,
      problems: [],
      rows: [
        {
          inputs: "quantity=int:-2",
          before: "int:-1800",
          after: "raises ValueError: quantity must not be negative",
          unstable: false,
        },
      ],
    },
  ];
}

export function stageClocksState() {
  const events = log.trim().split("\n").map(parseEvent);
  const epoch = Date.parse(events[0]?.ts ?? "");
  const proposal = events.findLast(
    (event) => event.type === "change.proposed" && event.payload.change_id === "change-1",
  );
  if (proposal?.type !== "change.proposed") throw new Error("Missing proposal");
  const stretched = events.map((event): ServerEvent => {
    const next = {
      ...event,
      ts: new Date(epoch + (Date.parse(event.ts) - epoch) * 101).toISOString(),
    };
    if (next.type === "tests.executed") {
      return {
        ...next,
        payload: {
          ...next.payload,
          suite: { ...next.payload.suite, duration_ms: next.payload.suite.duration_ms * 101 },
        },
      };
    }
    if (
      next.type === "change.resolved" &&
      next.payload.change_id === "change-1" &&
      next.payload.decision === "approved"
    ) {
      return {
        ...next,
        payload: {
          ...next.payload,
          merge: "conflict",
          merge_commit: null,
          conflict: { kind: "tests", paths: [], tests: proposal.payload.tests },
        },
      };
    }
    if (next.type === "change.facts_recorded" && next.payload.change_id === "change-1") {
      return {
        ...next,
        payload: {
          ...next.payload,
          measured_cone: exampleFacts(
            proposal.payload.impact.entries[0]?.node_id ?? "shop.coupons:discount",
          ),
        },
      };
    }
    return next;
  });
  const state = stretched.reduce(applyEvent, createInitialState());
  return applyEvent(state, {
    seq: state.lastSeq + 1,
    ts: state.clock ?? "",
    type: "usage.tick",
    payload: {
      agent_id: "agent-1",
      model: null,
      input_tokens: null,
      output_tokens: null,
      cached_tokens: null,
      cost_usd: 0.03,
      spent_usd: 0.03,
      budget_usd: 150,
      brake: "ok",
      role: "agent",
      change_id: "change-1",
      plan_id: null,
    },
  });
}

function Harness() {
  const [reviewing, setReviewing] = useState(false);
  const state = stageClocksState();
  return (
    <main style={{ padding: 16, maxWidth: 1280, marginInline: "auto" }}>
      <p className="text-muted">Handwritten stage times · long-duration layout fixture</p>
      <section aria-label="Review header" style={{ marginTop: 16 }}>
        <ReviewHeader
          state={state}
          changeId="change-1"
          onClose={() => {
            setReviewing(false);
          }}
        />
      </section>
      <section aria-label="Chat card" style={{ marginTop: 16 }}>
        <ChatChangeCard
          state={state}
          changeId="change-1"
          seq={11}
          reviewing={reviewing}
          onReview={() => {
            setReviewing(true);
          }}
          onGraph={() => {}}
        />
      </section>
    </main>
  );
}

const host = document.getElementById("root");
if (host) createRoot(host).render(<Harness />);
