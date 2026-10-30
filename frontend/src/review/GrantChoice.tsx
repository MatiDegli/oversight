import { useId } from "react";
import { ChevronDown } from "lucide-react";
import type { State } from "../state";
import { candidateKey, currentCards, answeredCards, useAnswers } from "./answers";
import { useDecisions } from "./DecisionContext";
import { decisionView } from "./decisions";
import { DecisionFeedback } from "./CardDecisions";
import { selectPossibleCallersWait } from "./inboxSelectors";

export function GrantChoice({
  state,
  changeId,
  scopeOnly = false,
}: {
  state: State;
  changeId: string;
  scopeOnly?: boolean;
}) {
  const scopeId = useId();
  const candidate = candidateKey(state, changeId);
  const grant = useAnswers((s) => s.grants[candidate]);
  const scope = grant?.scope.kind;
  const { attempts, onDecide } = useDecisions();
  const change = state.changes[changeId];
  const module = selectPossibleCallersWait(state, changeId)?.module;
  if (!change) return null;
  const view = decisionView(change, state.errors, attempts?.[changeId], !onDecide);
  const enabled = !!onDecide && view.kind === "open";
  const allYes =
    currentCards(state, changeId).length === answeredCards(state, changeId).length &&
    answeredCards(state, changeId).every((card) => card.answer === "yes");
  return (
    <div className="grant-choice">
      {!scopeOnly && (
        <button
          className="decision-primary"
          disabled={!enabled || !allYes}
          onClick={(event) => {
            if (event.detail > 0) onDecide?.(changeId, "approve", "");
          }}
        >
          Approve
        </button>
      )}
      <details
        onToggle={(event) => {
          if (!event.currentTarget.open) useAnswers.getState().grant(candidate);
        }}
      >
        <summary>
          Don't ask again <ChevronDown size={14} aria-hidden="true" />
        </summary>
        <fieldset disabled={!enabled}>
          <legend>Allow possible callers, not followed</legend>
          <label>
            <input
              type="radio"
              name={scopeId}
              checked={scope === "module"}
              disabled={!module}
              onChange={() => {
                chooseScope(state, changeId, "module");
              }}
            />
            This module{" "}
            <code>{module?.qualname ?? "Module scope requires exactly one module's file"}</code>
          </label>
          <label>
            <input
              type="radio"
              name={scopeId}
              checked={scope === "run"}
              disabled={!state.run}
              onChange={() => {
                chooseScope(state, changeId, "run");
              }}
            />
            This run
          </label>
          <p>Ends with this run. Created only when you Approve.</p>
        </fieldset>
      </details>
      {!scopeOnly && <DecisionFeedback view={view} />}
    </div>
  );
}

function chooseScope(state: State, changeId: string, kind: "module" | "run") {
  const value =
    kind === "module" ? selectPossibleCallersWait(state, changeId)?.module?.id : state.run?.run_id;
  if (value)
    useAnswers.getState().grant(candidateKey(state, changeId), {
      reason: "possible_callers",
      scope: { kind, value, expires: "run" },
    });
}
