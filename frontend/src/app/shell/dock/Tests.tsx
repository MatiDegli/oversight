import { Check, X } from "lucide-react";
import type { State } from "../../../state";
import { agentLetter, duration, selectTestRuns } from "./selectors";

export function Tests({ state }: { state: State }) {
  const runs = selectTestRuns(state);
  if (!runs.length) return <p className="dock-empty">Test results will appear here.</p>;
  return (
    <div className="dock-tests">
      <p className="dock-note">Latest test run per change or agent</p>
      <ul aria-label="Test runs">
        {runs.map((run) => {
          const suite = run.suite;
          if (!suite)
            return (
              <li key={run.id} className="dock-test-run">
                <div className="dock-facts">
                  <span
                    className="dock-letter"
                    data-slot={agentLetter(state, run.agentId)}
                    aria-label={`Agent ${agentLetter(state, run.agentId)}`}
                  >
                    {agentLetter(state, run.agentId)}
                  </span>
                  <span className="dock-code">{run.label}</span>
                  <span className="dock-note">no test result</span>
                </div>
              </li>
            );
          const passed = suite.outcome === "passed";
          const Icon = passed ? Check : X;
          const letter = agentLetter(state, run.agentId);
          return (
            <li key={run.id} className="dock-test-run">
              <div className="dock-facts">
                <span className="dock-letter" data-slot={letter} aria-label={`Agent ${letter}`}>
                  {letter}
                </span>
                <span className="dock-code">{run.label}</span>
                <span className="dock-result" data-outcome={passed ? "passed" : "failed"}>
                  <Icon aria-hidden="true" /> {suite.outcome}
                </span>
                <span className="dock-fact">
                  <Check aria-hidden="true" />
                  <span className="dock-number">{suite.passed}</span> passed
                </span>
                <span className="dock-fact">
                  <X aria-hidden="true" />
                  <span className="dock-number">{suite.failed}</span> failed
                </span>
                <span className="dock-number">{duration(suite.duration_ms)}</span>
              </div>
              {suite.failed_tests.length > 0 && (
                <ul aria-label={`Failing tests for ${run.id}`} className="dock-failures">
                  {suite.failed_tests.map((name) => (
                    <li key={name}>
                      <X aria-label="Failed" className="dock-failed-icon" />
                      <code>{name}</code>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
