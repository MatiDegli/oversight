import type { BehaviorDiffRow } from "@contracts";
import type { State } from "../state";
import { selectBehaviorEvidence } from "./behaviorEvidence";
import { factsForRevision } from "./factsForRevision";
import {
  selectChangeCard,
  selectComparisonSummary,
  selectFunctionComparisons,
} from "./inboxSelectors";
import { selectMeasuredCallers } from "./measuredCallers";

export const smallestExample = (rows: BehaviorDiffRow[]) =>
  rows
    .filter((row) => !row.unstable && row.before !== row.after)
    .toSorted((a, b) => a.inputs.length - b.inputs.length)[0];

/** Static membership alone never supplies a measurement or an established difference. */
export function selectCallerComparison(state: State, changeId: string) {
  const rows = selectMeasuredCallers(state, changeId)?.rows ?? [];
  const measured = rows.filter(({ evidence: e }) => e && e.compared_count > 0);
  const stable = measured.filter(
    ({ evidence: e }) => e && !e.not_probed && !e.rows.some((r) => r.unstable),
  );
  const differing = stable.filter(
    ({ evidence: e }) => e?.outcome === "changed" && e.differing_count > 0,
  );
  const same = stable.filter(
    ({ evidence: e }) => e?.complete && e.outcome === "unchanged" && !e.differing_count,
  );
  const counts = [...new Set(same.map((r) => r.evidence?.compared_count ?? 0))];
  return {
    rows,
    measured: measured.length,
    total: rows.length,
    differing,
    same: counts.map(
      (count) =>
        `${same
          .filter((r) => r.evidence?.compared_count === count)
          .map((r) => r.name)
          .join(
            " and ",
          )}: same result on ${String(count)} recorded calls${same.filter((r) => r.evidence?.compared_count === count).length > 1 ? " each" : ""}.`,
    ),
  };
}

export function selectReviewBody(state: State, changeId: string) {
  const change = state.changes[changeId];
  const facts = change?.proposal ? factsForRevision(state, change.proposal) : undefined;
  const card = selectChangeCard(state, changeId);
  const callers = selectCallerComparison(state, changeId);
  const behavior = selectBehaviorEvidence(state, changeId);
  const kind =
    card?.kind === "merged"
      ? "g"
      : card?.kind === "choice"
        ? "a"
        : card?.kind === "unavailable"
          ? "unavailable"
          : facts?.behavior_diff == null && facts?.measured_cone == null
            ? "d"
            : change && selectComparisonSummary(change, facts).incomplete
              ? "other"
              : callers.differing.length
                ? "b"
                : "other";
  const summary: string[] = [];
  if (change && ["a", "b", "g"].includes(kind)) {
    for (const b of behavior ?? [])
      if (b.compared_count && !b.rows.some((r) => r.unstable)) {
        const choices = facts?.review_cards?.filter((c) => c.node_id === b.node_id) ?? [];
        const examplesShown =
          kind === "a" &&
          choices.length > 0 &&
          choices.every((c) => c.example && c.example.before !== c.example.after);
        summary.push(
          `${b.label}: ${String(b.compared_count)} inputs compared, ${String(b.differing_count)} differ${examplesShown ? "; one example of each kind is shown above." : "."}`,
        );
      }
    const unmeasured = callers.total - callers.measured;
    if (unmeasured)
      summary.push(`${String(unmeasured)} possibly affected callers were not measured.`);
    const functions = selectFunctionComparisons(state, change, facts);
    if (
      !callers.total &&
      change.proposal &&
      !change.proposal.impact.omitted_by_depth &&
      !change.proposal.impact.omitted_by_limit
    ) {
      const possible = functions.callers.filter((c) => !c.followed);
      const detail =
        possible.length === 1
          ? `${possible[0]?.node.name ?? ""} may call it, not followed.`
          : `${String(possible.length)} possible callers, not followed.`;
      summary.push(`No exact callers${possible.length ? `; ${detail}` : "."}`);
    }
    if (functions.comparedFunctions)
      summary.push(
        `${String(functions.comparedFunctions)} of ${String(functions.modules.reduce((n, m) => n + m.functions.length, 0))} functions compared.`,
      );
  }
  return { kind, card, facts, callers, summary: summary.slice(0, 4) };
}
