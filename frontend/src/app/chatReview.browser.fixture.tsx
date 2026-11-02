// Hand-written C4 additions on retained recorded graph/run data; never a model run.
import type { ChangeFacts, ServerEvent } from "@contracts";
import { createRoot, type Root } from "react-dom/client";
import { flushSync } from "react-dom";
import { useReducer } from "react";
import { useStore } from "zustand";
import log from "../../../fixtures/events/recorded-demo.jsonl?raw";
import { applyEvent, createInitialState, stateStore } from "../state";
import { parseEvent } from "../transport";
import { App } from "./App";
import { Frame } from "./Frame";
import { InitialShellLayout } from "./shell/useShell";
import { conversationTab, navigateView, openReview, tabbedView, viewReducer } from "./view";
import { summaryRules } from "./chatReviewSelectors";
// eslint-disable-next-line no-restricted-imports -- Seed a draft written before the agent became ready.
import { draftKey, setDraft } from "../agents/conversation/drafts";

export function chatReviewState() {
  let state = log
    .trim()
    .split("\n")
    .map(parseEvent)
    .filter((e) => e.seq <= 63)
    .reduce(applyEvent, createInitialState());
  const proposal = state.changes["change-1"]?.proposal;
  if (!proposal) throw new Error("Missing recorded proposal");
  const sample = {
    inputs: "product=Product:W-1, quantity=int:-2",
    before: "int:-1800",
    after: "raises ValueError: quantity must not be negative",
    unstable: false,
  };
  const facts: ChangeFacts = {
    change_id: proposal.change_id,
    revision: proposal.revision,
    head_commit: proposal.head_commit,
    base_commit: proposal.base_commit,
    main_commit: proposal.base_commit,
    candidate_tree: "fixture-tree",
    declared: null,
    edited_paths: [],
    test_runs: [],
    integrity: [],
    added_tests: [],
    run_findings: [],
    zone: null,
    reach: null,
    complete: false,
    problems: [],
    summary: null,
    behavior_diff: null,
    parameter_extensions: null,
    shape: null,
    shape_complete: null,
    review_cards: null,
    measured_cone: proposal.impact.entries.slice(0, 3).map((entry, i) => ({
      node_id: entry.node_id,
      compared_count: 12,
      differing_count: i ? 0 : 3,
      rows: i ? [] : [sample],
      not_probed: null,
      outcome: i ? "unchanged" : "changed",
      unmatched_before: 0,
      unmatched_after: 0,
      not_compared: [],
      complete: true,
      problems: [],
    })),
  };
  const additions: ServerEvent[] = [
    { seq: 64, ts: "2026-11-01T22:40:00Z", type: "change.facts_recorded", payload: facts },
    {
      seq: 65,
      ts: "2026-11-01T22:40:01Z",
      type: "change.assessed",
      payload: {
        ...proposal,
        main_commit: proposal.base_commit,
        candidate_tree: "fixture-tree",
        policy_version: "fixture",
        mode: "ask_me",
        mode_revision: 0,
        rules: summaryRules.map((rule, i) => ({
          rule,
          result: i === 5 ? "unknown" : "pass",
          observed: null,
          threshold: {},
          evidence: [],
        })),
      },
    },
    {
      seq: 66,
      ts: "2026-11-01T22:40:02Z",
      type: "usage.tick",
      payload: {
        agent_id: "agent-1",
        model: null,
        input_tokens: null,
        output_tokens: null,
        cached_tokens: null,
        cost_usd: 0.03,
        spent_usd: 0.04,
        budget_usd: 150,
        brake: "ok",
        role: "agent",
        change_id: "change-1",
        plan_id: null,
      },
    },
  ];
  for (const event of additions) state = applyEvent(state, event);
  return state;
}
let root: Root | undefined;
function Harness() {
  const state = useStore(stateStore, (s) => s.state);
  const [view, dispatch] = useReducer(
    viewReducer,
    viewReducer(tabbedView, { type: "open", tab: conversationTab(tabbedView, state, "agent-1") }),
  );
  return (
    <Frame
      state={state}
      view={view}
      canOpenChange={() => false}
      onOpenChange={() => {}}
      onNodeTap={() => {}}
      onClosePanel={() => {
        dispatch({ type: "close", id: view.activeTab ?? "" });
      }}
      onTabAction={dispatch}
      onMessageAgent={() => Promise.resolve()}
      canReview={(id) => openReview(state, view, id) !== null}
      onReview={(id) => {
        const next = navigateView(view, openReview(state, view, id) ?? view, state);
        const tab = next.tabs?.find((t) => t.id === next.activeTab);
        if (tab) dispatch({ type: "open", tab });
      }}
      onDecide={() => {
        throw new Error("Use production App for decisions");
      }}
      replayLabel="Hand-written C4 controls · recorded graph/run data"
    />
  );
}
export const chatReviewTest = {
  draft(text: string) {
    setDraft(draftKey(stateStore.getState().state.run?.run_id ?? null, "agent-1"), text);
  },
  questions() {
    const s = stateStore.getState().state;
    const facts = s.changeFacts["change-1"]?.at(-1);
    if (!facts) throw new Error("Missing facts");
    this.emit({
      seq: 0,
      ts: s.clock ?? "2026-11-01T22:40:00Z",
      type: "change.facts_recorded",
      payload: {
        ...facts,
        review_cards: [
          {
            node_id: "shop.cart:Cart.quote_add",
            question: "Keep the changed caller result?",
            evidence_seq: s.lastSeq + 1,
            example: facts.measured_cone?.[0]?.rows[0] ?? null,
            answer: null,
            answered_by: null,
          },
        ],
      },
    });
  },
  mount(app = false) {
    root?.unmount();
    stateStore.setState({ state: chatReviewState() });
    const host = document.getElementById("root");
    if (!host) throw new Error("Missing root");
    root = createRoot(host);
    flushSync(() =>
      root?.render(
        <InitialShellLayout value="Chat">{app ? <App /> : <Harness />}</InitialShellLayout>,
      ),
    );
  },
  seed() {
    flushSync(() => {
      stateStore.setState({ state: chatReviewState() });
    });
  },
  state: () => stateStore.getState().state,
  emit(event: ServerEvent) {
    flushSync(() => {
      const state = stateStore.getState().state;
      const seq =
        event.type === "run.started" && event.payload.run_id !== state.run?.run_id
          ? 1
          : state.lastSeq + 1;
      stateStore.getState().applyEvents([{ ...event, seq }]);
    });
  },
  merged() {
    const p = stateStore.getState().state.changes["change-1"]?.proposal;
    if (!p) return;
    this.emit({
      seq: 0,
      ts: "2026-11-01T22:41:00Z",
      type: "change.resolved",
      payload: {
        change_id: p.change_id,
        agent_id: p.agent_id,
        revision: p.revision,
        head_commit: p.head_commit,
        decision: "approved",
        merge: "merged",
        merge_commit: "fixture-merge",
        reason: null,
        comment: null,
        conflict: null,
        decided_by: "rules",
        review_seq: null,
        requested_by: null,
        reasons: [],
        assessment: null,
        grants: [],
        answers: [],
      },
    });
  },
};
if (typeof window !== "undefined") Object.assign(window, { chatReviewTest });
declare global {
  interface Window {
    chatReviewTest: typeof chatReviewTest;
  }
}
