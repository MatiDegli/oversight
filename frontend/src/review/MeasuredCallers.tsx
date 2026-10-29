import type { MeasuredCaller } from "@contracts";
import type { State } from "../state";
import { selectMeasuredCallers } from "./measuredCallers";

const focusRing =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";

function CallerEvidence({ caller }: { caller: MeasuredCaller }) {
  return (
    <>
      <p>
        <span className="font-mono">{caller.compared_count}</span> invocations compared ·{" "}
        <span className="font-mono">{caller.differing_count}</span> differing
      </p>
      <p>
        Unmatched before: <span className="font-mono">{caller.unmatched_before}</span> · Unmatched
        after: <span className="font-mono">{caller.unmatched_after}</span>
      </p>
      {caller.not_compared.map((reason, index) => (
        <p key={index}>Not compared: {reason}</p>
      ))}
      {caller.problems.map((reason, index) => (
        <p key={index}>Observation problem: {reason}</p>
      ))}
      {caller.not_probed && <p>Not probed: {caller.not_probed}</p>}
      {caller.rows.length > 0 && (
        <details>
          <summary className={`cursor-pointer rounded-label text-text ${focusRing}`}>
            Observed samples
          </summary>
          <ul className="flex flex-col gap-(--spacing-sm)">
            {caller.rows.map((sample, index) => (
              <li key={index}>
                {sample.unstable && <p className="text-text">Unstable observation</p>}
                <dl>
                  {(["Inputs", "Before", "After"] as const).map((label) => (
                    <div key={label}>
                      <dt>{label}</dt>
                      <dd className="whitespace-pre-wrap break-all font-mono text-text">
                        {
                          sample[
                            label === "Inputs" ? "inputs" : label === "Before" ? "before" : "after"
                          ]
                        }
                      </dd>
                    </div>
                  ))}
                </dl>
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

/** Fits review context or an inbox card; the parent owns width, spacing and scrolling. */
export function MeasuredCallers({ state, changeId }: { state: State; changeId: string }) {
  const view = selectMeasuredCallers(state, changeId);
  if (!view) return null;
  return (
    <section
      aria-label="Measured callers · Possibly affected"
      className="flex min-w-0 flex-col gap-(--spacing-sm) wrap-anywhere text-(length:--data-size) text-muted"
    >
      <h3 className="text-(length:--title-size) font-bold text-text">
        Measured callers · Possibly affected
      </h3>
      <p>
        Static cone bounds: depth <span className="font-mono">{view.maxDepth}</span> ·{" "}
        <span className="font-mono">{view.maxNodes}</span> nodes
      </p>
      {view.recording === "absent" && <p>Caller measurements not recorded for this revision</p>}
      {view.recording === "empty" && <p>No caller measurements in this cone</p>}
      {view.recording === "recorded" && (
        <ul className="flex flex-col gap-(--spacing-lg)">
          {view.rows.map((row) => (
            <li key={row.nodeId}>
              <p className="text-text">
                <code className="font-mono">{row.name}</code> · Distance{" "}
                <span className="font-mono">{row.distance}</span>
              </p>
              <p className="font-semibold text-text">{row.label}</p>
              {row.availability && <p>{row.availability}</p>}
              {row.evidence && <CallerEvidence caller={row.evidence} />}
            </li>
          ))}
        </ul>
      )}
      <p>Measurements cover observed invocations only; other callers may be affected.</p>
    </section>
  );
}
