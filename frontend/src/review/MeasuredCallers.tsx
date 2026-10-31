import { ChevronRight } from "lucide-react";
import type { MeasuredCaller } from "@contracts";
import type { State } from "../state";
import { smallestExample } from "./reviewBodyEvidence";
import { selectMeasuredCallers } from "./measuredCallers";

const focusRing =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";

function callerSamples(caller: MeasuredCaller, detailsOnly: boolean) {
  const example =
    detailsOnly && caller.outcome === "changed" && !caller.rows.some((r) => r.unstable)
      ? smallestExample(caller.rows)
      : undefined;
  return caller.rows.filter((row) => row !== example);
}

function CallerEvidence({ caller, detailsOnly }: { caller: MeasuredCaller; detailsOnly: boolean }) {
  const samples = callerSamples(caller, detailsOnly);
  return (
    <>
      {!detailsOnly && (
        <p>
          <span className="font-mono">{caller.compared_count}</span> invocations compared ·{" "}
          <span className="font-mono">{caller.differing_count}</span> differing
        </p>
      )}
      {!detailsOnly && (
        <p>
          Unmatched before: <span className="font-mono">{caller.unmatched_before}</span> · Unmatched
          after: <span className="font-mono">{caller.unmatched_after}</span>
        </p>
      )}
      {!detailsOnly &&
        caller.not_compared.map((reason, index) => <p key={index}>Not compared: {reason}</p>)}
      {!detailsOnly &&
        caller.problems.map((reason, index) => <p key={index}>Observation problem: {reason}</p>)}
      {!detailsOnly && caller.not_probed && <p>Not probed: {caller.not_probed}</p>}
      {samples.length > 0 && (
        <details className={detailsOnly ? "group" : undefined}>
          <summary
            className={`cursor-pointer rounded-label text-text ${focusRing}${detailsOnly ? " flex list-none items-center gap-1 [&::-webkit-details-marker]:hidden" : ""}`}
          >
            {detailsOnly && (
              <ChevronRight
                aria-hidden="true"
                size={14}
                className="shrink-0 text-muted group-open:rotate-90"
              />
            )}
            Observed samples
          </summary>
          <ul className="flex flex-col gap-(--spacing-sm)">
            {samples.map((sample, index) => (
              <li key={index}>
                {sample.unstable && (
                  <p className="text-text">
                    {detailsOnly
                      ? "Results varied between runs; this is not an established difference."
                      : "Unstable observation"}
                  </p>
                )}
                <dl
                  className={
                    detailsOnly
                      ? "grid min-w-0 grid-cols-[auto_minmax(0,1fr)] gap-x-2.5 gap-y-1"
                      : undefined
                  }
                >
                  {([detailsOnly ? "Input" : "Inputs", "Before", "After"] as const).map((label) => (
                    <div key={label} className={detailsOnly ? "contents" : undefined}>
                      <dt>{label}</dt>
                      <dd
                        className={`whitespace-pre-wrap ${detailsOnly ? "wrap-anywhere" : "break-all"} font-mono text-text`}
                      >
                        {
                          sample[
                            label === "Inputs" || label === "Input"
                              ? "inputs"
                              : label === "Before"
                                ? "before"
                                : "after"
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
export function MeasuredCallers({
  state,
  changeId,
  detailsOnly = false,
}: {
  state: State;
  changeId: string;
  detailsOnly?: boolean;
}) {
  const view = selectMeasuredCallers(state, changeId);
  if (!view) return null;
  const rows = detailsOnly
    ? view.rows.filter(
        ({ evidence: caller }) =>
          caller &&
          caller.compared_count > 0 &&
          (caller.outcome === "changed" || caller.rows.some((row) => row.unstable)) &&
          callerSamples(caller, true).length > 0,
      )
    : view.rows;
  if (detailsOnly && rows.length === 0) return null;
  const heading = detailsOnly ? "Measured callers" : "Measured callers · Possibly affected";
  return (
    <section
      aria-label={heading}
      className="flex min-w-0 flex-col gap-(--spacing-sm) wrap-anywhere text-(length:--data-size) text-muted"
    >
      <h3 className="text-(length:--title-size) font-bold text-text">{heading}</h3>
      {!detailsOnly && (
        <>
          <p>
            Static cone bounds: depth <span className="font-mono">{view.maxDepth}</span> ·{" "}
            <span className="font-mono">{view.maxNodes}</span> nodes
          </p>
          {view.recording === "absent" && <p>Caller measurements not recorded for this revision</p>}
          {view.recording === "empty" && <p>No caller measurements in this cone</p>}
        </>
      )}
      {view.recording === "recorded" && (
        <ul className="flex flex-col gap-(--spacing-lg)">
          {rows.map((row) => (
            <li key={row.nodeId}>
              <p className="text-text">
                <code className="font-mono">{row.name}</code>
                {!detailsOnly && (
                  <>
                    {" "}
                    · Distance <span className="font-mono">{row.distance}</span>
                  </>
                )}
              </p>
              {!detailsOnly && <p className="font-semibold text-text">{row.label}</p>}
              {!detailsOnly && row.availability && <p>{row.availability}</p>}
              {row.evidence && <CallerEvidence caller={row.evidence} detailsOnly={detailsOnly} />}
            </li>
          ))}
        </ul>
      )}
      {!detailsOnly && (
        <p>Measurements cover observed invocations only; other callers may be affected.</p>
      )}
    </section>
  );
}
