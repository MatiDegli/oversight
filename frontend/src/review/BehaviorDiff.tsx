import type { State } from "../state";
import { selectBehaviorEvidence } from "./behaviorEvidence";

/** Portable evidence block: its parent owns width, spacing and scrolling. */
export function BehaviorDiff({ state, changeId }: { state: State; changeId: string }) {
  const nodes = selectBehaviorEvidence(state, changeId);
  if (nodes === null) return null;
  return (
    <section
      aria-label="Behavior diff"
      className="flex min-w-0 flex-col gap-2.5 text-sm text-muted"
    >
      <h3 className="text-(length:--title-size) font-bold text-text">Behavior diff</h3>
      {nodes.length === 0 ? (
        <p>No behavior probes were recorded for changed nodes.</p>
      ) : (
        <>
          <p>Up to eight samples per node; counts cover all compared inputs.</p>
          <p>These bounded inputs do not establish equivalence.</p>
          <ul className="flex min-w-0 flex-col gap-3.5">
            {nodes.map((node, index) => (
              <li key={`${node.node_id}:${String(index)}`} className="flex min-w-0 flex-col gap-2">
                <p className="wrap-anywhere text-text">
                  <code className="font-mono">{node.label}</code> · {node.status}
                </p>
                <div className="flex flex-wrap gap-x-3.5 gap-y-1">
                  <p>
                    Inputs compared: <span className="font-mono">{node.compared_count}</span>
                  </p>
                  <p>
                    Differences measured: <span className="font-mono">{node.differing_count}</span>
                  </p>
                </div>
                {node.limitation !== null && (
                  <p className="whitespace-pre-wrap wrap-anywhere">{node.limitation}</p>
                )}
                {node.noDifference && <p>No difference measured on these inputs</p>}
                {node.compared_count === 0 && node.limitation === null && (
                  <p>No input comparisons were recorded.</p>
                )}
                {node.rows.length > 0 && (
                  <details>
                    <summary className="cursor-pointer rounded-label focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text">
                      Samples ({node.rows.length})
                      {node.rows.some((row) => row.unstable)
                        ? " · Results varied between runs"
                        : ""}
                    </summary>
                    <ol className="mt-2 flex min-w-0 flex-col gap-2.5">
                      {node.rows.map((row, rowIndex) => (
                        <li
                          key={rowIndex}
                          className="flex min-w-0 flex-col gap-1 border-t border-page-border pt-2"
                        >
                          {row.unstable && (
                            <p>
                              Results varied between runs; this is not an established difference.
                            </p>
                          )}
                          <dl className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)] gap-x-2.5 gap-y-1">
                            {(
                              [
                                ["Input", row.inputs],
                                ["Before", row.before],
                                ["After", row.after],
                              ] as const
                            ).map(([label, value]) => (
                              <div key={label} className="contents">
                                <dt>{label}</dt>
                                <dd className="whitespace-pre-wrap wrap-anywhere font-mono text-text">
                                  {value}
                                </dd>
                              </div>
                            ))}
                          </dl>
                        </li>
                      ))}
                    </ol>
                  </details>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
