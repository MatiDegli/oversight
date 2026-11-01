import { ArrowRight, Eye, GitFork, ListChecks, MessageSquare } from "lucide-react";
import type { State } from "../../state";
import { costText } from "../../state";
import { agentSlotStyle } from "../../theme";
import { ChangeFactsRow } from "../../review";
// eslint-disable-next-line no-restricted-imports -- Reuse the built timeline timestamp without another projection.
import { EventTime } from "../../agents/conversation/rows";
import { selectChatChange } from "../chatReviewSelectors";

export function GraphLink({ onClick }: { onClick?: () => void }) {
  return onClick ? (
    <button type="button" className="chat-graph-link" onClick={onClick}>
      <GitFork aria-hidden="true" size={14} /> See on graph
    </button>
  ) : (
    <span className="text-muted">Graph navigation unavailable.</span>
  );
}

export function ChatChangeCard({
  state,
  changeId,
  seq,
  reviewing,
  onReview,
  onGraph,
}: {
  state: State;
  changeId: string;
  seq: number;
  reviewing: boolean;
  onReview?: () => void;
  onGraph?: () => void;
}) {
  const card = selectChatChange(state, changeId, seq);
  if (!card) return null;
  const { proposal, callers } = card;
  const slot = state.agents[proposal.agent_id]?.slot;
  return (
    <div className="chat-proposal" style={agentSlotStyle(slot)}>
      <div className="chat-proposal-note">
        <div>
          <MessageSquare aria-hidden="true" size={14} /> Agent {slot ?? proposal.agent_id} says{" "}
          <EventTime ts={card.ts} />
        </div>
        <p>{proposal.summary ?? "No finish summary was recorded."}</p>
      </div>
      <article
        aria-label="Proposed change"
        className={`chat-change-card ${reviewing ? "is-reviewing" : ""}`}
      >
        {reviewing ? (
          <div className="chat-change-echo">
            <strong>Proposed change</strong>
            <span>Cost {costText(card.cost)}</span>
            <span className="chat-echo-caption">Reviewing in the panel</span>
            <span className="chat-reviewing">
              <Eye aria-hidden="true" size={14} />
              Reviewing
            </span>
          </div>
        ) : (
          <>
            <header>
              <strong>Proposed change</strong>
              <span>Checked by Oversight</span>
              <span className="chat-change-cost">Cost {costText(card.cost)}</span>
            </header>
            <div className="chat-change-chips">
              <ChangeFactsRow state={state} changeId={changeId} />
            </div>
            <section className="chat-change-comparison" aria-label="Impact summary">
              <div className="chat-comparison-lead">
                <strong>
                  {callers.differing.length
                    ? `${String(callers.differing.length)} caller${callers.differing.length === 1 ? " behaves" : "s behave"} differently`
                    : "No stable caller difference recorded"}
                </strong>
                <span>
                  {callers.measured} of {callers.total} callers measured
                </span>
              </div>
              {card.examples.map((caller) => (
                <div key={caller.nodeId}>
                  <div className="chat-caller">
                    <code>{caller.name}</code>
                    <span>
                      {caller.evidence?.differing_count} of {caller.evidence?.compared_count}{" "}
                      recorded calls changed
                    </span>
                  </div>
                  {caller.example && (
                    <>
                      <p className="chat-example-input">
                        <code>{caller.example.inputs}</code>
                      </p>
                      <div className="chat-example-pair">
                        <div>
                          <span>Before · main</span>
                          <code>{caller.example.before}</code>
                        </div>
                        <ArrowRight aria-hidden="true" size={14} />
                        <div>
                          <span>After · Agent {slot}</span>
                          <code>{caller.example.after}</code>
                        </div>
                      </div>
                    </>
                  )}
                </div>
              ))}
              <p>
                {callers.same.join(" ")} <GraphLink onClick={onGraph} />
              </p>
              {card.partial.map(({ nodeId, line }) => (
                <p key={nodeId}>{line}</p>
              ))}
              <p
                className={
                  proposal.impact.omitted_by_depth || proposal.impact.omitted_by_limit
                    ? undefined
                    : "sr-only"
                }
              >
                {proposal.impact.entries.length} possibly affected · depth bound{" "}
                {proposal.impact.max_depth}; {proposal.impact.omitted_by_depth} omitted by depth,{" "}
                {proposal.impact.omitted_by_limit} by limit.
              </p>
            </section>
            <div className="chat-change-evidence">
              <ListChecks aria-hidden="true" size={16} />
              <span>
                Evidence {card.passes} of 6
                {card.waiting ? " · decision waiting" : " · decision recorded"}
              </span>
              <ul aria-label="Six recorded rule results">
                {card.rules.map((rule) => (
                  <li
                    key={rule.rule}
                    data-result={rule.result}
                    title={`${rule.label}: ${rule.result}`}
                  >
                    <span className="sr-only">
                      {rule.label}: {rule.result}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
            <footer>
              {onReview ? (
                <button type="button" onClick={onReview}>
                  <Eye aria-hidden="true" size={16} />
                  Review change
                </button>
              ) : (
                <span>Review is unavailable for this candidate.</span>
              )}
              <span>{card.waiting ? "Waiting for a decision." : "Decision recorded."}</span>
            </footer>
          </>
        )}
      </article>
    </div>
  );
}
