import type { State } from "../state";
import { AgentBadge } from "../teams/badge";
import { presentationFor, presentationStyle } from "../teams/presentation";
import { coneOverlap, type ConeLimitation } from "./coneOverlap";

const limits: Record<ConeLimitation, string> = {
  "parse-errors": "Some files have parse errors; the graph retains their last good parses.",
  "sequence-gap": "An event sequence or graph version gap limits the available graph.",
  "cone-omissions": "The cone's depth or node limit omits reachable nodes.",
  "missing-seeds":
    "Some nodes added or modified by this pending change are absent from the graph used for this cone.",
  "removed-only":
    "This pending change only removes nodes; removed nodes do not seed its current cone.",
};

export function ConeOverlap({ state, changeId }: { state: State; changeId: string }) {
  const peers = coneOverlap(state, changeId);
  if (!peers.length) return null;
  return (
    <section aria-label="Other pending changes" className="cone-overlap">
      <h3>Other pending changes</h3>
      <p>Based on the current graph and its limits; other intersections may be missing.</p>
      {peers.map((peer) => {
        const identity = presentationFor(state, peer.agentId);
        const name = identity ? `Agent ${identity.label}` : `Agent ${peer.agentId}`;
        return (
          <div key={peer.changeId} data-agent-id={peer.agentId} style={presentationStyle(identity)}>
            <p className="cone-peer">
              <AgentBadge identity={identity} />
              {!identity && <span>{peer.agentId}</span>}
            </p>
            {peer.unavailable ? (
              <p>
                Cannot check {name}’s current impact cone: {peer.unavailable} unavailable.
              </p>
            ) : peer.nodes.length ? (
              <ul>
                {peer.nodes.map((node) => (
                  <li key={node.id}>
                    <code>{node.name}</code> is in {name}’s current impact cone.
                  </li>
                ))}
              </ul>
            ) : (
              <p>No edited nodes appear in {name}’s currently computed cone.</p>
            )}
            {peer.limitations.map((limit) => (
              <p key={limit}>{limits[limit]}</p>
            ))}
          </div>
        );
      })}
    </section>
  );
}
