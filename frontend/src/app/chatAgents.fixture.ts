import type { AgentStatus, Plan, ServerEvent } from "@contracts";
import log from "../../../fixtures/events/handwritten-level1.jsonl?raw";
import { applyEvent, createInitialState } from "../state";

// Handwritten C2 data, using schema-shaped envelopes and the existing handwritten graph.
const source = log
  .trim()
  .split("\n")
  .map((line) => JSON.parse(line) as ServerEvent);
export function required<T>(value: T | null | undefined): T {
  if (value == null) throw new Error("Missing handwritten C2 evidence");
  return value;
}
export const seed = source.slice(0, 2);
export function event<T extends ServerEvent["type"]>(
  type: T,
  payload: Extract<ServerEvent, { type: T }>["payload"],
): ServerEvent {
  return { type, payload, seq: 0, ts: "2026-11-01T18:52:00Z" } as ServerEvent;
}
export function status(id = "agent-a", overrides: Partial<AgentStatus> = {}): ServerEvent {
  return event("agent.status", {
    agent_id: id,
    slot: "A",
    change_id: `change-${id}`,
    task: "compute_price in shop/pricing.py accepts a negative unit_price or quantity and returns a negative price. Make it raise ValueError for negative input.",
    model: "muse-spark",
    state: "working",
    step: 4,
    max_steps: 30,
    detail: null,
    plan_id: null,
    zone: null,
    started_by: null,
    queued_task_id: null,
    base_commit: null,
    examples: null,
    answering: null,
    ...overrides,
  });
}
export function plan(id: string, started_by: string | null = null, revision = 1): ServerEvent {
  return event("plan.updated", {
    plan_id: id,
    started_by,
    revision,
    task: "Handwritten team fixture",
    graph_version: "fixture",
    state: "planning",
    subtasks: null,
    detail: null,
    queued_task_id: null,
  } satisfies Plan);
}
export function step(id: string, tool = "edit_file", number = 4) {
  return event("agent.step", {
    agent_id: id,
    step: number,
    tool,
    summary:
      tool === "read_file"
        ? "Read inventory.py"
        : id === "agent-b"
          ? "Edited reserve in inventory.py"
          : "Edited compute_price in pricing.py",
    result: "ok",
    duration_ms: 5,
    error: null,
    error_kind: null,
    tests: null,
  });
}
export function usage(id: string, cost: number | null) {
  return event("usage.tick", {
    agent_id: id,
    model: "muse-spark",
    input_tokens: 10,
    output_tokens: 10,
    cached_tokens: null,
    role: "agent",
    change_id: null,
    plan_id: null,
    cost_usd: cost,
    spent_usd: cost ?? 0,
    budget_usd: 50,
    brake: "ok",
  });
}
export function proposal(id: string) {
  const template = required(source.find((e) => e.type === "change.proposed"));
  return event("change.proposed", { ...template.payload, change_id: `change-${id}`, agent_id: id });
}
export function resolved(id: string, conflict = false) {
  return event("change.resolved", {
    change_id: `change-${id}`,
    agent_id: id,
    revision: 1,
    decision: "approved",
    reason: null,
    comment: null,
    merge: conflict ? "conflict" : "merged",
    merge_commit: conflict ? null : "9e1f3a7",
    conflict: conflict ? { kind: "merge", paths: ["shop/pricing.py"], tests: null } : null,
    decided_by: "rules",
    review_seq: null,
    requested_by: null,
    head_commit: null,
    reasons: [],
    assessment: null,
    answers: null,
  });
}
export const fold = (events: ServerEvent[]) =>
  events.reduce((state, e, i) => applyEvent(state, { ...e, seq: i + 1 }), createInitialState());
export function scenario(name = "working"): ServerEvent[] {
  if (name === "empty") return seed;
  const working = [
    ...seed,
    status(),
    status("agent-b", {
      slot: "B",
      model: "muse-spark-open",
      task: "Reject zero and negative reserve quantities.",
      step: 2,
    }),
    step("agent-a"),
    step("agent-b", "read_file", 2),
    usage("agent-a", 0.03),
    usage("agent-b", 0.01),
  ];
  if (name === "working") return working;
  const proposed = [
    ...working,
    status("agent-a", { state: "ready_for_review", step: 7 }),
    proposal("agent-a"),
    step("agent-b", "edit_file", 4),
  ];
  return name === "merged"
    ? [
        ...proposed,
        proposal("agent-b"),
        resolved("agent-b"),
        status("agent-b", {
          slot: "B",
          task: "Reject zero and negative reserve quantities.",
          model: "muse-spark-open",
          state: "done",
          step: 6,
        }),
      ]
    : proposed;
}
