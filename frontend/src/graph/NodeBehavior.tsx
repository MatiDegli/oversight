// eslint-disable-next-line no-restricted-imports -- B2n reuses line A's selector; review exports are outside this front.
import { selectBehaviorEvidence, type BehaviorNodeView } from "../review/behaviorEvidence";
import type { State } from "../state";
import { agentLook } from "../theme";
import { selectGraphBehavior } from "./behavior";
import type { GraphBehaviorEntry } from "./behavior";

function Samples({ node }: { node: BehaviorNodeView }) {
  const rows = node.rows.slice(0, 8);
  if (rows.length === 0) return null;
  return (
    <details>
      <summary className="cursor-pointer rounded-label focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text">
        Samples ({rows.length})
        {node.rows.some((row) => row.unstable) ? " · Results varied between runs" : ""}
      </summary>
      <ol className="mt-2 flex min-w-0 flex-col gap-2.5">
        {rows.map((row, index) => (
          <li key={index} className="flex min-w-0 flex-col gap-1 border-t border-page-border pt-2">
            {row.unstable && (
              <p>Results varied between runs; this is not an established difference.</p>
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
                  <dd className="whitespace-pre-wrap wrap-anywhere font-mono text-text">{value}</dd>
                </div>
              ))}
            </dl>
          </li>
        ))}
      </ol>
    </details>
  );
}

function ChangeBehavior({ state, entry }: { state: State; entry: GraphBehaviorEntry }) {
  const node = selectBehaviorEvidence(state, entry.changeId)?.find(
    (node) => node.node_id === entry.nodeId,
  );
  if (!node) return null;
  const slot = state.agents[entry.agentId]?.slot;
  return (
    <li className="flex min-w-0 flex-col gap-2">
      <p className="wrap-anywhere text-text">
        <span
          aria-label={`Agent ${slot ?? entry.agentId}`}
          className={slot ? agentLook[slot].textClass : undefined}
        >
          {slot ?? entry.agentId}
        </span>{" "}
        · {node.status}
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
      <Samples node={node} />
    </li>
  );
}

/** Standalone evidence for one node; the eventual focus-panel host owns its placement. */
export function NodeBehavior({ state, nodeId }: { state: State; nodeId: string }) {
  const entries = selectGraphBehavior(state).filter((entry) => entry.nodeId === nodeId);
  if (entries.length === 0) return null;
  return (
    <section
      aria-label="Behavior diff"
      className="flex min-w-0 flex-col gap-2.5 text-sm text-muted"
    >
      <h3 className="text-(length:--title-size) font-bold text-text">Behavior diff</h3>
      <p>Up to eight samples per node; counts cover all compared inputs.</p>
      <p>These bounded inputs do not establish equivalence.</p>
      <ul className="flex min-w-0 flex-col gap-3.5">
        {entries.map((entry) => (
          <ChangeBehavior key={entry.changeId} state={state} entry={entry} />
        ))}
      </ul>
    </section>
  );
}
