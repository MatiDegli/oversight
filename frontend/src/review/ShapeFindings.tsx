import type { ChangeFacts, EvidenceRef, ShapeFact } from "@contracts";
import { shapeCompletion } from "./reviewFindings";

/** References are identifiers, not fabricated navigation to unavailable source detail. */
export function FindingReferences({ evidence }: { evidence: EvidenceRef[] }) {
  return evidence.length ? (
    <div className="finding-references">
      <p>References (detail unavailable here)</p>
      <ul>
        {evidence.map((ref, index) => (
          <li key={index}>
            {ref.event_seq != null && <span>Event #{ref.event_seq}</span>}
            {ref.git_object != null && (
              <span>
                Git object <code>{ref.git_object}</code>
              </span>
            )}
            {ref.path != null && (
              <span>
                Path <code>{ref.path}</code>
              </span>
            )}
            {ref.event_seq == null && ref.git_object == null && <span>Reference unavailable</span>}
          </li>
        ))}
      </ul>
    </div>
  ) : (
    <p className="finding-bound">No supporting references recorded.</p>
  );
}
const labels: Record<ShapeFact["kind"], string> = {
  function_length: "Function length",
  import_added: "Added import",
  import_cycle: "Import cycle",
  duplicate_logic: "Duplicate logic (structural observation)",
};
export function ShapeFindings({ facts }: { facts: ChangeFacts }) {
  return (
    <section aria-label="Structural observations" className="review-findings">
      <h3>Structural observations</h3>
      <p className="finding-bound">{shapeCompletion(facts.shape_complete)}</p>
      {facts.shape == null ? (
        <p>Structural analysis was not recorded.</p>
      ) : facts.shape.length === 0 ? (
        <p>No structural observations retained.</p>
      ) : (
        <ul className="finding-list">
          {facts.shape.map((fact, index) => (
            <li key={index}>
              <p className="finding-kind">{labels[fact.kind]}</p>
              <p>
                <code>{fact.path}</code>
              </p>
              {fact.node_id != null && (
                <p>
                  Node <code>{fact.node_id}</code>
                </p>
              )}
              <p>{fact.message}</p>
              {fact.kind === "duplicate_logic" && (
                <p className="finding-bound">
                  Structure alone does not establish the same behavior.
                </p>
              )}
              <FindingReferences evidence={fact.evidence} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
