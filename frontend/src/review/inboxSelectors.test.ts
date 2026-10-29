import { expect, it } from "vitest";
import log from "../../../fixtures/events/handwritten-inbox-grants.jsonl?raw";
import { parseEvent } from "../transport";
import {
  applyEvent,
  createInitialState,
  grantScopeWords,
  selectActiveGrants,
  selectDecisionInbox,
  selectGrantUses,
} from "../state";
import type { State } from "../state";
import { possibleCallers } from "../graph";
import {
  selectChangeCard,
  selectInboxSummary,
  selectPossibleCallersWait,
  selectUnchanged,
} from "./inboxSelectors";

export const inboxEvents = log.trim().split("\n").map(parseEvent);
export const inboxState = (end = inboxEvents.length) =>
  inboxEvents.slice(0, end).reduce(applyEvent, createInitialState());
const state = inboxState();

it("rebuilds identical run-local evidence in live batches and serialized replay without mutation", () => {
  let live = createInitialState();
  for (const event of inboxEvents) {
    Object.freeze(live);
    live = applyEvent(live, event);
    expect(live.gap).toBe(false);
  }
  expect(live).toEqual(state);
  const copy = JSON.parse(JSON.stringify(state)) as State;
  expect(selectActiveGrants(copy)).toEqual(selectActiveGrants(state));
  expect(selectDecisionInbox(copy)).toEqual(selectDecisionInbox(state));
  expect(selectInboxSummary(copy)).toEqual({ agents: 2, steps: 8, minutes: 7 });
});

it("lists only unrevoked current-run grants and retains uses after revocation", () => {
  expect(selectActiveGrants(state)).toMatchObject([
    {
      grant_id: "grant-pricing",
      uses: 1,
      ts: state.grantEvents.find((e) => e.type === "grant.created")?.ts,
    },
  ]);
  expect(selectGrantUses(state, "grant-pricing")).toHaveLength(1);
  expect(selectGrantUses(state, "grant-revoked")).toEqual([]);
  const revoked = applyEvent(state, {
    type: "grant.revoked",
    payload: { grant_id: "grant-pricing" },
    seq: state.lastSeq + 1,
    ts: state.clock ?? "",
  });
  expect(selectActiveGrants(revoked)).toEqual([]);
  expect(selectGrantUses(revoked, "grant-pricing")).toHaveLength(1);
  expect(selectActiveGrants({ ...state, run: null })).toEqual([]);
  expect(
    selectActiveGrants({
      ...state,
      grantEvents: state.grantEvents.map((e) =>
        e.type === "grant.created"
          ? { ...e, payload: { ...e.payload, scope: { ...e.payload.scope, expires: "session" } } }
          : e,
      ),
    }),
  ).toEqual([]);
  const run = inboxEvents[0];
  if (run?.type !== "run.started") throw new Error("Missing run");
  const reset = applyEvent(state, {
    ...run,
    payload: { ...run.payload, run_id: "run-20261029-190000" },
  });
  expect(reset.grantEvents).toEqual([]);
  expect(selectActiveGrants(reset)).toEqual([]);
  expect(selectGrantUses(reset, "grant-pricing")).toEqual([]);
});

it("names every contract scope and counts one change once even with repeated receipts", () => {
  for (const kind of ["module", "path", "agent", "run"] as const)
    expect(grantScopeWords({ kind, value: "scope", expires: "run" })).toContain(
      kind === "run" ? "This run" : "scope",
    );
  const receipt = state.decisionEvents.find((e) => e.type === "change.resolved");
  if (!receipt) throw new Error("Missing receipt");
  expect(
    selectGrantUses(
      { ...state, decisionEvents: [...state.decisionEvents, receipt] },
      "grant-pricing",
    ),
  ).toHaveLength(1);
});

it("lists precisely the existing possible-callers count, with no tests or exact callers", () => {
  const wait = selectPossibleCallersWait(state, "change-2");
  expect(wait?.module).toBe("shop/pricing.py");
  const change = state.changes["change-2"];
  if (!change?.proposal || !wait) throw new Error("Missing change");
  expect(wait.callers).toHaveLength(possibleCallers(state, change, change.proposal.impact) ?? -1);
  expect(wait.callers.every((node) => !node.is_test && node.qualname.length > 0)).toBe(true);
  const graph = state.graph;
  if (!graph) throw new Error("Missing graph");
  const first = wait.callers[0];
  const edge = graph.edges.find(
    (e) => e.src === first?.id && e.dst === "shop.pricing:compute_price",
  );
  if (!edge) throw new Error("Missing caller");
  const withExact = {
    ...state,
    graph: {
      ...graph,
      edges: [...graph.edges, { ...edge, id: "exact-twin", confidence: "exact" as const }],
    },
  };
  expect(selectPossibleCallersWait(withExact, "change-2")?.callers).toHaveLength(
    wait.callers.length - 1,
  );
  expect(selectPossibleCallersWait({ ...state, graph: null }, "change-2")).toBeNull();
  expect(selectPossibleCallersWait(state, "change-1")).toBeNull();
});

it.each([
  null,
  { nodes: 6, omitted_by_depth: 0, omitted_by_limit: 0, parse_errors: 0, unresolved_removed: 0 },
  { nodes: 0 },
  { nodes: 0, omitted_by_depth: 0, omitted_by_limit: 0, parse_errors: 1, unresolved_removed: 0 },
])("refuses a grant offer for an incomplete or exceeded cone: %j", (observed) => {
  const next: State = {
    ...state,
    decisionEvents: state.decisionEvents.map((e) =>
      e.type === "change.assessed"
        ? {
            ...e,
            payload: {
              ...e.payload,
              rules: e.payload.rules.map((r) => (r.rule === "small_cone" ? { ...r, observed } : r)),
            },
          }
        : e,
    ),
  };
  expect(selectPossibleCallersWait(next, "change-2")).toBeNull();
});

it("projects the three states from current evidence, with no inherited questions on a new head", () => {
  expect(selectChangeCard(state, "change-1")?.kind).toBe("merged");
  expect(selectChangeCard(state, "change-3")?.facts?.review_cards).toHaveLength(2);
  expect(selectChangeCard(state, "change-3")?.kind).toBe("choice");
  expect(selectChangeCard(state, "change-4")?.kind).toBe("unavailable");
  expect(selectChangeCard(state, "unknown")).toBeNull();
  const change = state.changes["change-3"];
  if (!change?.proposal) throw new Error("Missing proposal");
  const newer: State = {
    ...state,
    changes: {
      ...state.changes,
      "change-3": { ...change, proposal: { ...change.proposal, head_commit: "new-head" } },
    },
  };
  expect(selectChangeCard(newer, "change-3")).toBeNull();
  expect(selectDecisionInbox(state)).toHaveLength(3);
});

it("counts compared inputs rather than claiming all behavior is unchanged", () => {
  const facts = selectChangeCard(state, "change-3")?.facts;
  expect(selectUnchanged(facts)).toEqual({ compared: 24, unchanged: 22, incomplete: false });
  expect(selectUnchanged(undefined)).toEqual({ compared: 0, unchanged: 0, incomplete: true });
});

it("does not turn a follow-main conflict into an unavailable check", () => {
  const next: State = {
    ...state,
    decisionEvents: state.decisionEvents.map((e) =>
      e.type === "error"
        ? {
            ...e,
            payload: {
              ...e.payload,
              message: e.payload.message.replace("code=review_timeout", "code=main_moved"),
            },
          }
        : e,
    ),
  };
  expect(selectChangeCard(next, "change-4")).toBeNull();
});
