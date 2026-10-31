import type { BehaviorDiffRow } from "@contracts";
import type { State } from "../state";
import { selectBehaviorEvidence } from "./behaviorEvidence";
import { hasCompared } from "./coneWords";
import { factsForRevision } from "./factsForRevision";
import { recordingBudgetReached } from "./evidenceWords";
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

/** Panel copy only; diagnostics never become an unsupported causal claim. */
export function partialLines(state: State, changeId: string) {
  const callers = selectCallerComparison(state, changeId);
  const behavior = selectBehaviorEvidence(state, changeId) ?? [];
  return [
    ...callers.rows.flatMap((r) => (r.evidence ? [{ ...r.evidence, label: r.name }] : [])),
    ...behavior,
  ].flatMap((e) => {
    const caller = "complete" in e;
    const varied = e.rows.some((r) => r.unstable);
    const unmatched = caller ? e.unmatched_before + e.unmatched_after : 0;
    const line = !e.compared_count
      ? `not measured${recordingBudgetReached(e) ? ", recording budget reached" : ""}.`
      : varied
        ? caller
          ? "results varied between runs; no difference established."
          : `${String(e.compared_count)} inputs compared; results varied between runs.`
        : unmatched
          ? `${String(e.differing_count)} of ${String(e.compared_count)} matched calls changed; ${String(unmatched)} calls could not be matched.`
          : caller && e.outcome === "changed" && e.differing_count && !e.not_probed
            ? `${String(e.differing_count)} of ${String(e.compared_count)} recorded calls changed.`
            : e.not_probed || (caller && !e.complete)
              ? `${String(e.compared_count)} inputs compared.`
              : null;
    return line
      ? [
          {
            nodeId: e.node_id,
            line: `${e.label}: ${line}`,
            example:
              caller && !varied && !e.not_probed && e.outcome === "changed" && e.differing_count
                ? smallestExample(e.rows)
                : undefined,
          },
        ]
      : [];
  });
}

export function nothingComparedLine(state: State, changeId: string) {
  const reason = selectBehaviorEvidence(state, changeId)?.find((b) => b.words)?.words;
  return reason ? `Nothing was compared: ${reason}.` : "Nothing was compared.";
}

export function selectReviewBody(state: State, changeId: string) {
  const change = state.changes[changeId];
  const facts = change?.proposal ? factsForRevision(state, change.proposal) : undefined;
  const card = selectChangeCard(state, changeId);
  const callers = selectCallerComparison(state, changeId);
  const behavior = selectBehaviorEvidence(state, changeId);
  const compared = hasCompared(facts);
  const kind =
    card?.kind === "merged"
      ? "g"
      : card?.kind === "choice"
        ? "a"
        : card?.kind === "unavailable"
          ? "unavailable"
          : facts?.behavior_diff == null && facts?.measured_cone == null
            ? "d"
            : !compared
              ? "d2"
              : change && selectComparisonSummary(change, facts).incomplete
                ? "c"
                : callers.differing.length
                  ? "b"
                  : "other";
  const summary: string[] = [];
  if (change && ["a", "b", "c", "g", "other"].includes(kind)) {
    for (const b of behavior ?? [])
      if (kind !== "c" && b.compared_count && !b.not_probed && !b.rows.some((r) => r.unstable)) {
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
    if (kind === "c" && callers.total)
      summary.push(
        `${String(callers.measured)} of ${String(callers.total)} callers measured; ${String(unmeasured)} were not.`,
      );
    else if (unmeasured)
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
    if (functions.comparedFunctions || kind === "c")
      summary.push(
        `${String(functions.comparedFunctions)} of ${String(functions.modules.reduce((n, m) => n + m.functions.length, 0))} functions compared.`,
      );
  }
  return { kind, card, facts, callers, summary: summary.slice(0, 4) };
}

/** Phone preview uses the same eligible evidence as the comparison summary. */
export function comparisonPreview(state: State, changeId: string) {
  const { kind, callers, facts } = selectReviewBody(state, changeId);
  const change = state.changes[changeId];
  if (!change || !["a", "b", "c", "g", "other"].includes(kind)) return "";
  const first =
    kind === "c"
      ? undefined
      : selectBehaviorEvidence(state, changeId)?.find(
          (b) => b.compared_count && !b.not_probed && !b.rows.some((r) => r.unstable),
        );
  const parts: string[] = [];
  if (first)
    parts.push(`${String(first.compared_count)} inputs, ${String(first.differing_count)} differ`);
  const unmeasured = callers.total - callers.measured;
  if (kind === "c" && callers.total)
    parts.push(`${String(callers.measured)} of ${String(callers.total)} callers measured`);
  else if (unmeasured) parts.push(`${String(unmeasured)} callers not measured`);
  const functions = selectFunctionComparisons(state, change, facts);
  if (functions.comparedFunctions)
    parts.push(
      `${String(functions.comparedFunctions)} of ${String(functions.modules.reduce((n, m) => n + m.functions.length, 0))} functions`,
    );
  return parts.slice(0, 2).join(" · ");
}
