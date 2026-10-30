import { selectDecisionInbox } from "../state";
import type { ChangeState, DecisionInboxEntry, State } from "../state";
import type { ChangeFacts } from "@contracts";
import { possibleCallerNodes } from "../graph";
import { factsForRevision } from "./ReviewFacts";

export function selectPossibleCallersWait(state: State, changeId: string) {
  const change = state.changes[changeId];
  const p = change?.proposal;
  if (!p || change.resolution?.merge === "merged" || change.status === "rejected") return null;
  const event = state.decisionEvents.findLast(
    (e) =>
      e.type === "change.assessed" &&
      e.payload.change_id === changeId &&
      e.payload.revision === p.revision &&
      e.payload.head_commit === p.head_commit,
  );
  const assessment = event?.type === "change.assessed" ? event.payload : null;
  const rule = assessment?.rules.find((r) => r.rule === "small_cone");
  const o = rule?.observed;
  if (
    rule?.result !== "unknown" ||
    !rule.evidence.length ||
    !o ||
    typeof o.nodes !== "number" ||
    !Number.isInteger(o.nodes) ||
    o.nodes < 0 ||
    o.nodes > 5 ||
    !["omitted_by_depth", "omitted_by_limit", "parse_errors", "unresolved_removed"].every(
      (k) => o[k] === 0,
    )
  )
    return null;
  const callers = possibleCallerNodes(state, change, p.impact);
  const modules = state.graph?.nodes.filter(
    (n) => n.kind === "module" && n.path === p.files[0]?.path,
  );
  const module = p.files.length === 1 && modules?.length === 1 ? modules[0] : undefined;
  return callers?.length ? { callers, module } : null;
}

export type ChangeCardState = {
  kind: "merged" | "choice" | "unavailable";
  change: ChangeState;
  facts: ChangeFacts | undefined;
};
/** Only current-candidate evidence defines a card. Local answers never alter this projection. */
export function selectChangeCard(
  state: State,
  changeId: string,
  inboxEntry?: DecisionInboxEntry,
): ChangeCardState | null {
  const change = state.changes[changeId];
  if (!change?.proposal) return null;
  const facts = factsForRevision(state, change.proposal);
  if (change.resolution?.decided_by === "rules" && change.resolution.merge === "merged")
    return { kind: "merged", change, facts };
  if (change.status !== "ready_for_review" && change.status !== "conflict") return null;
  const entry = inboxEntry ?? selectDecisionInbox(state).find((e) => e.changeId === changeId);
  if (entry?.decisions.some((d) => d.kind === "follow_main")) return null;
  if (facts?.review_cards?.length) return { kind: "choice", change, facts };
  if (
    facts &&
    entry?.decisions.some((decision) => decision.kind === "checks_unavailable") &&
    !selectPossibleCallersWait(state, changeId)
  )
    return { kind: "unavailable", change, facts };
  return null;
}

export function selectInboxSummary(state: State) {
  const agents = Object.keys(state.agents).length;
  const steps = Object.values(state.steps).reduce((n, s) => n + s.length, 0);
  const minutes =
    state.clock && state.runStartedAt
      ? Math.max(0, Math.round((Date.parse(state.clock) - Date.parse(state.runStartedAt)) / 60000))
      : null;
  return { agents, steps, minutes };
}

/** A square represents a graph function; only captured comparisons can mark it unchanged. */
export function selectFunctionComparisons(state: State, change: ChangeState, facts?: ChangeFacts) {
  const observations = new Map(
    [...(facts?.behavior_diff ?? []), ...(facts?.measured_cone ?? [])].map((b) => [b.node_id, b]),
  );
  const changed = new Set([
    ...change.graph.nodes_modified,
    ...change.graph.nodes_added.map((node) => node.id),
    ...change.graph.nodes_removed,
  ]);
  const nodes = new Map(
    [...(state.graph?.nodes ?? []), ...change.graph.nodes_added].map((node) => [node.id, node]),
  );
  const functions = [...nodes.values()]
    .filter((node) => !node.is_test && (node.kind === "function" || node.kind === "method"))
    .map((node) => {
      const b = observations.get(node.id);
      const complete =
        !!b &&
        !b.not_probed &&
        !b.rows.some((row) => row.unstable) &&
        (!("complete" in b) || b.complete);
      return {
        node,
        changed: changed.has(node.id),
        compared: b?.compared_count ?? 0,
        differing: b?.differing_count ?? 0,
        unchanged: !!b?.compared_count && !b.differing_count && complete,
        questions: (facts?.review_cards ?? []).flatMap((q, i) =>
          q.node_id === node.id ? [i + 1] : [],
        ),
      };
    });
  const modules = [...new Set(functions.map((f) => f.node.path))]
    .map((path) => ({
      path,
      changed: functions.some((f) => f.node.path === path && f.changed),
      functions: functions.filter((f) => f.node.path === path),
    }))
    .sort((a, b) => Number(b.changed) - Number(a.changed) || a.path.localeCompare(b.path));
  const cone = change.proposal?.impact;
  const possible = cone ? (possibleCallerNodes(state, change, cone) ?? []) : [];
  const callers = [
    ...(cone?.entries.flatMap((entry) => {
      const node = nodes.get(entry.node_id);
      return node ? [{ node, followed: true }] : [];
    }) ?? []),
    ...possible.map((node) => ({ node, followed: false })),
  ];
  return {
    modules,
    callers,
    comparedFunctions: functions.filter((f) => f.compared > 0).length,
    unchangedFunctions: functions.filter((f) => f.unchanged).length,
  };
}

/** A bounded claim: every captured difference must belong to a changed, questioned function. */
export function selectComparisonSummary(change: ChangeState, facts?: ChangeFacts) {
  const observations = [
    ...new Map(
      [...(facts?.behavior_diff ?? []), ...(facts?.measured_cone ?? [])].map((b) => [b.node_id, b]),
    ).values(),
  ];
  const compared = observations.filter((b) => b.compared_count > 0);
  const changed = new Set([
    ...change.graph.nodes_modified,
    ...change.graph.nodes_added.map((node) => node.id),
    ...change.graph.nodes_removed,
  ]);
  const questioned = new Set(facts?.review_cards?.map((q) => q.node_id));
  const incomplete =
    !facts?.complete ||
    !compared.length ||
    observations.some(
      (b) =>
        !b.compared_count ||
        b.not_probed ||
        b.rows.some((row) => row.unstable) ||
        ("complete" in b && !b.complete),
    );
  return {
    functionCount: compared.length,
    inputCount: compared.reduce((n, b) => n + b.compared_count, 0),
    incomplete,
    noOtherDifference:
      !incomplete &&
      observations.every(
        (b) => !b.differing_count || (changed.has(b.node_id) && questioned.has(b.node_id)),
      ),
  };
}
