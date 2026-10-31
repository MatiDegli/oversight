import { Check } from "lucide-react";
import type { ChangeFacts } from "@contracts";
import type { ChangeState, State } from "../state";
import { selectFunctionComparisons, selectComparisonSummary } from "./inboxSelectors";
import "./comparison.css";

export function ComparisonMap({
  state,
  change,
  facts,
}: {
  state: State;
  change: ChangeState;
  facts?: ChangeFacts;
}) {
  const summary = selectComparisonSummary(change, facts);
  const map = selectFunctionComparisons(state, change, facts);
  return (
    <section className="unchanged-facts" aria-label="Compared functions and callers">
      <h3>
        {summary.noOtherDifference
          ? "No other difference in what was compared"
          : "What was compared"}
        {" · "}
        {summary.functionCount} {summary.functionCount === 1 ? "function" : "functions"},{" "}
        {summary.inputCount} {summary.inputCount === 1 ? "input" : "inputs"}
      </h3>
      <p className="decision-muted">
        The squares distinguish changed, unchanged and unverified functions.
        {summary.incomplete && " Comparison coverage is incomplete."}
      </p>
      <div className="comparison-modules">
        {map.modules.map((module) => (
          <div key={module.path}>
            <p>
              <code>{module.path}</code>
              {module.changed && " · changed module"}
            </p>
            <div className="function-squares" aria-label={module.path}>
              {module.functions.map((f) => (
                <span
                  key={f.node.id}
                  className={
                    !f.compared
                      ? ""
                      : f.changed
                        ? "function-changed"
                        : f.unchanged
                          ? "function-same"
                          : ""
                  }
                  title={`${f.node.qualname} · ${f.compared ? `${String(f.compared)} inputs compared` : "not compared"}`}
                  aria-label={`${f.node.qualname}: ${f.changed ? "changed; " : ""}${f.unchanged ? "unchanged" : `${String(f.compared)} inputs compared`}${f.questions.length ? `; questions ${f.questions.join(", ")}` : ""}`}
                >
                  {!f.compared ? (
                    "?"
                  ) : f.questions.length ? (
                    f.questions.join(",")
                  ) : f.unchanged ? (
                    <Check size={12} aria-hidden="true" />
                  ) : f.changed || f.differing ? (
                    "Δ"
                  ) : (
                    "?"
                  )}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
      <p className="decision-muted">
        Numbers link changed functions to questions. ✓ unchanged · Δ changed · ? not compared.
      </p>
      {!!map.callers.length && (
        <p className="comparison-callers">
          Callers:{" "}
          {map.callers.map(({ node, followed }) => (
            <span key={node.id}>
              <code>{node.qualname}</code> · {followed ? "possibly affected" : "not followed"}
            </span>
          ))}
        </p>
      )}
      <p className="decision-muted">
        Only captured inputs are covered; other functions and inputs remain unverified.
      </p>
    </section>
  );
}
