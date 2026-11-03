import type { GraphSnapshot, LeadStatus, ServerEvent } from "@contracts";
import { applyEvent, createInitialState } from "../state";
import { parseEvent } from "../transport";
import { event, plan, proposal, resolved, seed, status } from "./chatAgents.fixture";

export const boltonsPin = "d31d821d56c8f7756af9c30a864077f2e5855321";
export const railScenes = [
  "chat-busy",
  "chat-collapsed-selected",
  "chat-leadless",
  "agents-folded-overflow",
] as const;
export type RailScene = (typeof railScenes)[number];
export const observedPath =
  "boltons/observed_without_graph_data/long_segment_for_wrapping/iterutils.py";
function lead(id: string) {
  return event("lead.status", {
    agent_id: id,
    revision: 1,
    task: "Keep iterator transforms predictable and cover boundary cases.",
    model: "muse-spark",
    state: "idle",
    step: 4,
    detail: "Waiting for member work.",
    limits: null,
    requested_plan_id: null,
  } satisfies LeadStatus);
}
function members(id: string, owner: string | null, ids: string[], observed = ids.length) {
  const source = plan(id, owner);
  if (source.type !== "plan.updated") throw new Error("Missing handwritten plan");
  return [
    {
      ...source,
      payload: {
        ...source.payload,
        state: "running" as const,
        task: "Improve iterator boundary handling",
        subtasks: ids.map((agent_id) => ({
          agent_id,
          task: "Cover iterator boundaries",
          zone: { paths: ["boltons/iterutils.py"], node_ids: [] },
          depends_on: [],
          state: "running" as const,
        })),
      },
    },
    ...ids.slice(0, observed).map((id, i) =>
      status(id, {
        plan_id: source.payload.plan_id,
        task: "Cover iterator boundaries in boltons/iterutils.py",
        state: i ? "revising" : "working",
      }),
    ),
  ];
}
/** Handwritten envelopes, never a recording. Graph is inserted separately, before the prefix. */
export function railSource(scene: RailScene): ServerEvent[] {
  const opening = seed[0];
  if (opening?.type !== "run.started") throw new Error("Missing handwritten opening");
  const events = [
    {
      ...opening,
      payload: {
        ...opening.payload,
        run_id: "handwritten-team-rail",
        workspace: "handwritten team rail",
      },
    },
    lead("lead-a"),
    ...(scene === "chat-leadless" ? [] : [lead("lead-b")]),
    ...members("plan-a1", "lead-a", ["a1", "a2"], 1),
    ...members("plan-a2", "lead-a", ["a3", "a4"], 1),
    ...members("plan-b", scene === "chat-leadless" ? null : "lead-b", ["b1", "b2"]),
    status("lone-c", { task: "Document iterator edge cases", state: "working" }),
    event("agent.focus", {
      agent_id: "a1",
      mode: "edit",
      node_ids: [],
      ranges: [{ path: observedPath, start_line: 12, end_line: 28 }],
    }),
    event("agent.focus", {
      agent_id: "lone-c",
      mode: "read",
      node_ids: [],
      ranges: [{ path: "boltons/iterutils.py", start_line: 30, end_line: 30 }],
    }),
    event("agent.message", { agent_id: "a1", text: "Please keep the boundary checks focused." }),
    event("agent.step", {
      agent_id: "a1",
      step: 4,
      tool: "read_file",
      summary: "Read iterator boundaries",
      result: "ok",
      duration_ms: 1,
      error: null,
      error_kind: null,
      tests: null,
    }),
  ];
  if (scene === "agents-folded-overflow")
    events.push(
      ...Array.from({ length: 6 }, (_, i) =>
        status(`extra-${String(i)}`, {
          state: i % 2 ? "ready_for_review" : "halted",
          task: "Handwritten overflow member",
        }),
      ),
    );
  return events.map((envelope, i) => parseEvent(JSON.stringify({ ...envelope, seq: i + 1 })));
}
export function railEvents(scene: RailScene, graph?: GraphSnapshot) {
  const source = railSource(scene);
  const opening = source[0];
  if (!opening) throw new Error("Missing opening");
  const fallback = seed[1];
  const snapshot = graph ?? (fallback?.type === "graph.snapshot" ? fallback.payload : undefined);
  if (!snapshot) throw new Error("Missing interaction graph");
  return [
    opening,
    { type: "graph.snapshot" as const, seq: 2, ts: opening.ts, payload: snapshot },
    ...source.slice(1).map((envelope) => ({ ...envelope, seq: envelope.seq + 1 })),
  ];
}
export function railState(scene: RailScene, graph?: GraphSnapshot) {
  const state = railEvents(scene, graph).reduce(applyEvent, createInitialState());
  if (state.gap) throw new Error("Handwritten team rail prefix has a gap");
  return state;
}
export function railConflict(id: string, kind: "merge" | "tests"): ServerEvent[] {
  const resolution = resolved(id, true);
  if (resolution.type !== "change.resolved") throw new Error("Missing conflict");
  return [
    proposal(id),
    {
      ...resolution,
      payload: {
        ...resolution.payload,
        conflict:
          kind === "merge"
            ? { kind: "merge" as const, paths: [observedPath], tests: null }
            : {
                kind: "tests" as const,
                paths: [],
                tests: {
                  outcome: "failed",
                  passed: 0,
                  failed: 1,
                  skipped: 0,
                  duration_ms: 1,
                  failed_tests: ["test_handwritten"],
                  cases: null,
                  cases_complete: null,
                },
              },
      },
    },
  ];
}
