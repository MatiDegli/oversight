import type { LeadStatus, ServerEvent } from "@contracts";
import { mkdirSync, writeFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import handwritten from "../../../../fixtures/events/handwritten-lead-thread.jsonl?raw";
import plansLog from "../../../../fixtures/events/handwritten-lead-plans.jsonl?raw";
import {
  applyEvent,
  createInitialState,
  createStateStore,
  selectDecisionInboxCount,
  stateStore,
} from "../../state";
import { selectInboxSummary } from "../../review";
import { chooseReplaySource, parseEvent, startReplay } from "../../transport";
import { selectLeadConversation } from "./selectLeadConversation";
import LeadThreadRows from "./LeadThreadRows";
import { Composer } from "./Composer";
import { ConversationPanelHeader } from "./ConversationPanelHeader";
import { beginSend, draftKey, draftStore, setDraft } from "./drafts";

// Server rendering otherwise reads Zustand's initial snapshot; exercise current live drafts.
vi.mock("zustand", async (original) => ({
  ...(await original<typeof import("zustand")>()),
  useStore: <T, U>(store: { getState(): T }, select: (state: T) => U) => select(store.getState()),
}));

const compatibility = handwritten.trim().split("\n").map(parseEvent);
const first = compatibility[0];
if (first?.type !== "run.started") throw new Error("Missing run");
const run = first;
const planEvents = plansLog.trim().split("\n").map(parseEvent);
const template = planEvents.find((event) => event.type === "plan.updated");
const ts = "2026-11-02T12:00:00Z";
const base: LeadStatus = {
  agent_id: "lead/one",
  revision: 1,
  task: "Inspect the repository",
  model: "muse-spark",
  state: "starting",
  step: 0,
  detail: null,
  limits: null,
  requested_plan_id: null,
};
function thread(turns = 3, withPlans = false) {
  const events: ServerEvent[] = [run];
  let revision = 0;
  const status = (state: LeadStatus["state"], step: number) =>
    events.push({
      seq: events.length + 1,
      ts,
      type: "lead.status",
      payload: { ...base, revision: ++revision, state, step },
    });
  status("starting", 0);
  for (let turn = 0; turn < turns; turn++) {
    let answer: number | null = null;
    if (turn) {
      answer = events.length + 1;
      events.push({
        seq: answer,
        ts,
        type: "agent.message",
        payload: { agent_id: base.agent_id, text: `Person turn ${String(turn)}` },
      });
    }
    status("working", turn * 2);
    if (withPlans && [0, 20, 40].includes(turn) && template?.type === "plan.updated") {
      const planId = `thread-plan-${String(turn)}`;
      events.push({
        ...template,
        seq: events.length + 1,
        ts,
        payload: { ...template.payload, plan_id: planId, started_by: base.agent_id },
      });
      events.push({
        seq: events.length + 1,
        ts,
        type: "lead.status",
        payload: {
          ...base,
          revision: ++revision,
          state: "working",
          step: turn * 2 + 1,
          requested_plan_id: planId,
        },
      });
    }
    events.push({
      seq: events.length + 1,
      ts,
      type: "lead.reply",
      payload: {
        agent_id: base.agent_id,
        answers_seq: answer,
        woken_by: null,
        text: `Lead turn ${String(turn + 1)}`,
        through_seq: turn ? events.length : 0,
        cut: null,
      },
    });
    status(turn === 49 ? "halted" : "idle", (turn + 1) * 2);
  }
  return events;
}
const fold = (events: ServerEvent[]) => events.reduce(applyEvent, createInitialState());
const project = (events: ServerEvent[]) => {
  const conversation = selectLeadConversation(fold(events), base.agent_id);
  if (!conversation) throw new Error("Missing lead");
  return conversation;
};

it("retains unknown lead limits as null in the conversation header", () => {
  expect(project(thread(1)).header.limits).toBeNull();
});

it("loads the lead selector only through the lazy conversation boundary", async () => {
  vi.resetModules();
  const loaded = vi.fn();
  vi.doMock("./selectLeadConversation", async (original) => {
    loaded();
    return original<typeof import("./selectLeadConversation")>();
  });
  try {
    const { loadConversationPanel } = await import("../index");
    expect(loaded).not.toHaveBeenCalled();
    const panel = await loadConversationPanel();
    expect(loaded).toHaveBeenCalledOnce();
    expect(panel.selectLeadConversation(fold(thread(1)), base.agent_id)?.header).toMatchObject({
      kind: "lead",
      state: "idle",
      step: 2,
    });
  } finally {
    vi.doUnmock("./selectLeadConversation");
    vi.resetModules();
  }
});

it("retains opening and two person turns in envelope order with exact answer association", () => {
  const events = thread();
  const conversation = project(events);
  expect(conversation.header).toMatchObject({ kind: "lead", state: "idle", step: 6 });
  expect(conversation.header).not.toHaveProperty("slot");
  expect(conversation.entries.filter((e) => e.kind === "task")).toHaveLength(1);
  const messages = conversation.entries.filter((e) => e.kind === "message");
  expect(
    conversation.entries
      .filter((e) => e.kind === "lead-reply")
      .map((e) => e.event.payload.answers_seq),
  ).toEqual([null, ...messages.map((e) => e.seq)]);
  expect(conversation.entries.map((e) => e.seq)).toEqual(
    conversation.entries.map((e) => e.seq).sort((a, b) => a - b),
  );
  const state = fold(events);
  const last = events.at(-1);
  if (!last) throw new Error("Missing final status");
  expect(applyEvent(state, last).leadReplies).toBe(state.leadReplies);
  expect(applyEvent(state, { ...last, seq: state.lastSeq + 2 }).gap).toBe(true);
  expect(
    selectLeadConversation(
      applyEvent(state, { ...run, payload: { ...run.payload, run_id: "next" } }),
      base.agent_id,
    ),
  ).toBeUndefined();
});

it("uses only increasing lead revisions and retains distinct replacement identities", () => {
  const events = thread(1);
  const terminal: ServerEvent = {
    seq: events.length + 1,
    ts,
    type: "lead.status",
    payload: { ...base, revision: 100, state: "stopped", step: 2 },
  };
  events.push(terminal, {
    ...terminal,
    seq: events.length + 2,
    payload: { ...terminal.payload, revision: 2, state: "working" },
  });
  events.push({ ...terminal, seq: events.length + 1, payload: { ...base, agent_id: "lead/two" } });
  expect(project(events).header.state).toBe("stopped");
  expect(project(events).entries.filter((e) => e.kind === "lead-status")).toHaveLength(4);
  expect(selectLeadConversation(fold(events), "lead/two")?.header.state).toBe("starting");
  expect(selectLeadConversation(fold(events), "absent")).toBeUndefined();
});

it.each([null, "tool_calls", "model_calls", "time", "spend_brake"] as const)(
  "presents text/withholding and separate %s notices, with exact horizons",
  (cut) => {
    for (const text of ["<script>bad()</script>\nhttps://example.test/plan-9 **plain**", null]) {
      for (const through_seq of [null, 0, 2]) {
        const events = thread(1).map((event): ServerEvent =>
          event.type === "lead.reply"
            ? { ...event, payload: { ...event.payload, text, cut, through_seq } }
            : event,
        );
        const html = renderToStaticMarkup(
          createElement(LeadThreadRows, { conversation: project(events) }),
        );
        expect(html).not.toContain("<script>");
        expect(html).not.toContain("<a ");
        expect(html.includes("Reply withheld")).toBe(text === null);
        expect(html.includes("Turn ended:")).toBe(cut !== null);
        expect(html.includes("As of event")).toBe(through_seq !== null);
        if (through_seq !== null) expect(html).toContain(`As of event ${String(through_seq)}`);
      }
    }
  },
);

it.each(["starting", "working", "idle", "halted", "stopped", "done"] as const)(
  "supports shared lead header/composer for %s without coding-agent controls",
  (state) => {
    const events = thread(1);
    events.push({
      seq: events.length + 1,
      ts,
      type: "lead.status",
      payload: { ...base, revision: 99, state, step: 12 },
    });
    const conversation = project(events);
    stateStore.setState({ state: fold(events) });
    const html = renderToStaticMarkup(
      createElement(Composer, { header: conversation.header, onSend: () => Promise.resolve() }),
    );
    expect(html).toContain('aria-label="Message the lead"');
    expect(html.includes('readOnly=""')).toBe(state !== "idle");
    expect(html).not.toContain("Pause");
    expect(html).not.toContain("Stop and discard");
    expect(
      renderToStaticMarkup(createElement(ConversationPanelHeader, { header: conversation.header })),
    ).not.toContain(" of ");
    expect(
      renderToStaticMarkup(createElement(Composer, { header: conversation.header })),
    ).toContain("Replay: lead messages and stop are off.");
  },
);

it("retains three explicit attachments across nulls, rejects foreign plans and stale lead revisions", () => {
  const events = planEvents;
  const state = fold(events);
  const attachments = selectLeadConversation(state, "agent-1")?.entries.filter(
    (e) => e.kind === "lead-plan",
  );
  expect(attachments?.map((e) => e.planId)).toEqual(["plan-1", "plan-2", "plan-3"]);
  const foreign = events.map((e): ServerEvent =>
    e.type === "plan.updated" ? { ...e, payload: { ...e.payload, started_by: "other" } } : e,
  );
  expect(
    selectLeadConversation(fold(foreign), "agent-1")?.entries.filter((e) => e.kind === "lead-plan"),
  ).toEqual([]);
  const noLinks = events.map((e): ServerEvent =>
    e.type === "lead.status"
      ? { ...e, payload: { ...e.payload, requested_plan_id: null, detail: "plan-1 plan-2 plan-3" } }
      : e,
  );
  expect(
    selectLeadConversation(fold(noLinks), "agent-1")?.entries.filter((e) => e.kind === "lead-plan"),
  ).toEqual([]);
});

it("keeps the handwritten source read-only and projects all compatibility replies", () => {
  const state = fold(compatibility);
  const conversation = selectLeadConversation(state, "agent-1");
  expect(conversation?.entries.filter((e) => e.kind === "lead-reply")).toHaveLength(
    compatibility.filter((e) => e.type === "lead.reply").length,
  );
});

it.each(
  ["", "  ", "x".repeat(4096), "x".repeat(4097)].map((text) => ({ text, length: text.length })),
)("bounds lead send eligibility for a draft of $length characters", ({ text }) => {
  const state = fold(thread(1));
  stateStore.setState({ state });
  draftStore.setState({}, true);
  const key = draftKey(state.run?.run_id ?? null, base.agent_id);
  setDraft(key, text);
  const header = project(thread(1)).header;
  const render = () =>
    renderToStaticMarkup(createElement(Composer, { header, onSend: () => Promise.resolve() }));
  const send = render().match(/<button[^>]*aria-label="Send message to lead"[^>]*>/)?.[0];
  expect(send).toContain(`aria-disabled="${String(!text.trim() || text.length > 4096)}"`);
  beginSend(key);
  expect(render().match(/<button[^>]*aria-label="Send message to lead"[^>]*>/)?.[0]).toContain(
    'aria-disabled="true"',
  );
  expect(draftStore.getState()[key]?.text).toBe(text);
  draftStore.setState({}, true);
});

it("explicitly replays the handwritten source through the public replay API with an honest label", async () => {
  const source = chooseReplaySource(null, handwritten);
  expect(source.label).toBe("Replay · hand-written demo fixture");
  const store = createStateStore();
  const replay = startReplay({ store, source: { text: source.text }, speed: 0 });
  await replay.ready;
  expect(store.getState().state).toEqual(fold(compatibility));
  replay.stop();
});

it("folds 50 realistic turns, ends reply 50 with halted, and measures the existing count revision loop", () => {
  const events = thread(50, true);
  const samples = [];
  const idle = events.filter(
    (event) => event.type === "lead.status" && event.payload.state === "idle",
  );
  const prefixes = [idle[0]?.seq, idle[24]?.seq, events.length];
  for (const [index, end] of prefixes.entries()) {
    if (end === undefined) throw new Error("Missing turn boundary");
    const count = index + 1;
    const state = fold(events.slice(0, end));
    const start = performance.now();
    const counts = Array.from({ length: 100 }, () => selectDecisionInboxCount(state));
    samples.push({ events: end, count100ms: performance.now() - start });
    expect(new Set(counts)).toEqual(new Set([count]));
    expect(selectInboxSummary(state).steps).toBe(
      selectLeadConversation(state, base.agent_id)?.header.step,
    );
  }
  console.info("Lead count selector cost (100 calls; includes lifecycle revision loop):", samples);
  mkdirSync("../.shots/chat-lead", { recursive: true });
  writeFileSync("../.shots/chat-lead/count-cost.json", JSON.stringify(samples, null, 2));
  const conversation = project(events);
  expect(conversation.header).toMatchObject({ state: "halted", step: 100 });
  expect(conversation.entries.filter((e) => e.kind === "lead-reply")).toHaveLength(50);
  expect(events.at(-2)?.type).toBe("lead.reply");
  expect(events.at(-1)).toMatchObject({ type: "lead.status", payload: { state: "halted" } });
  expect(
    conversation.entries
      .filter((entry) => entry.kind === "lead-plan")
      .map((entry) => entry.answersSeq),
  ).toEqual([
    null,
    ...events
      .filter((event) => event.type === "agent.message")
      .filter(
        (event) =>
          event.payload.text === "Person turn 20" || event.payload.text === "Person turn 40",
      )
      .map((event) => event.seq),
  ]);
});
