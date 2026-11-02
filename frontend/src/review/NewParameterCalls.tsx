import { ArrowRight, ChevronRight, CircleAlert } from "lucide-react";
import { presentationStyle, type AgentPresentation } from "../teams/presentation";
import { unstableBehaviorSentence } from "./behaviorEvidence";
import { evidenceWords } from "./evidenceWords";
import type { parameterExtensions } from "./parameterExtensions";

const calls = (count: number) => `${String(count)} call${count === 1 ? "" : "s"}`;

export function NewParameterCalls({
  entries,
  identity,
}: {
  entries: ReturnType<typeof parameterExtensions>;
  identity?: AgentPresentation;
}) {
  if (!entries.length) return null;
  return (
    <section aria-label="New-parameter calls" className="parameter-calls">
      <h3>New-parameter calls</h3>
      {entries.map((entry, index) => {
        const limitation = evidenceWords(entry.not_probed);
        return (
          <section id={entry.target} tabIndex={-1} key={`${entry.target}-${String(index)}`}>
            <p>
              {entry.node_id.split(":").at(-1)}
              {entry.run_count > 0 && (
                <>
                  : {calls(entry.run_count)} run with <code>{entry.parameters.join(", ")}</code>.
                </>
              )}
            </p>
            {entry.rejected_before > 0 && <p>Base rejected {calls(entry.rejected_before)}.</p>}
            <p className="parameter-bound">
              These calls use the new parameter, so What was compared does not count them.
            </p>
            {entry.not_probed && (
              <p className="parameter-limitation">
                <CircleAlert aria-hidden="true" size={16} />
                <span>Some calls were not measured{limitation ? `: ${limitation}` : ""}.</span>
              </p>
            )}
            {entry.rows.length > 0 && (
              <details className="parameter-samples">
                <summary>
                  Samples ({entry.rows.length})<ChevronRight aria-hidden="true" size={14} />
                </summary>
                {entry.rows.length < entry.run_count && (
                  <p className="parameter-retained">
                    {entry.rows.length} sample{entry.rows.length === 1 ? "" : "s"} shown from{" "}
                    {calls(entry.run_count)} run.
                  </p>
                )}
                <ol>
                  {entry.rows.map((row, rowIndex) => (
                    <li key={rowIndex}>
                      <p className="parameter-input">
                        <span>Input</span>
                        <code>{row.inputs}</code>
                      </p>
                      <div className="parameter-pair">
                        <div>
                          <span>Before</span>
                          <code>{row.before}</code>
                        </div>
                        <ArrowRight aria-hidden="true" size={16} />
                        <div data-agent-identity={identity} style={presentationStyle(identity)}>
                          <span>After</span>
                          <code>{row.after}</code>
                        </div>
                      </div>
                      {row.unstable && (
                        <p className="parameter-retained">{unstableBehaviorSentence}</p>
                      )}
                    </li>
                  ))}
                </ol>
              </details>
            )}
          </section>
        );
      })}
    </section>
  );
}
