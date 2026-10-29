import { GitBranch } from "lucide-react";
import type { Practice } from "./manifest";
import { calledHelper, caseSlot, countAt, nodeName } from "./phoneEvidence";
import type { PracticeCase } from "./phoneEvidence";

export function AgentLetter({ slot }: { slot: string }) {
  return (
    <span className="pp-letter" data-slot={slot}>
      {slot}
    </span>
  );
}
export function AgentLine({ practice, entry }: { practice: Practice; entry: PracticeCase }) {
  const slot = caseSlot(practice, entry);
  const agent = practice.cut.agents[entry.proposal.agent_id];
  const modelLabel =
    practice.cut.run?.models.find((model) => model.id === agent?.model)?.label ??
    agent?.model ??
    "Model unavailable";
  return (
    <div className="pp-agent" data-slot={slot}>
      <AgentLetter slot={slot} />
      <strong>Agent {slot}</strong>
      <span>· {modelLabel}</span>
    </div>
  );
}
function Node({
  practice,
  entry,
  merged,
  compact,
}: {
  practice: Practice;
  entry: PracticeCase;
  merged: boolean;
  compact: boolean;
}) {
  const slot = caseSlot(practice, entry);
  return (
    <div className={`pp-node ${merged ? "is-merged" : ""}`} data-slot={slot}>
      <span>{nodeName(practice, entry)}</span>
      {!compact && <small>{merged ? "Merged" : "Changed"}</small>}
      <AgentLetter slot={slot} />
    </div>
  );
}
function Piece({
  practice,
  entry,
  merged,
  compact,
}: {
  practice: Practice;
  entry: PracticeCase;
  merged: boolean;
  compact: boolean;
}) {
  const helper = calledHelper(practice, entry);
  return (
    <div
      className={`pp-piece ${helper ? "pp-call" : "pp-cone"} ${compact ? "is-compact" : ""}`}
      data-slot={caseSlot(practice, entry)}
    >
      <svg
        className={`pp-taxi ${helper && !merged ? "is-pending" : ""}`}
        viewBox={compact ? "0 0 172 138" : undefined}
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        {helper ? (
          <>
            <path
              d={
                compact
                  ? "M20 42V66Q20 74 28 74H136Q144 74 144 82V100"
                  : "M20 42V52Q20 60 28 60H276Q284 60 284 68V74"
              }
            />
            <path
              className="pp-arrow"
              d={compact ? "M140 98L144 104L148 98Z" : "M280 72L284 78L288 72Z"}
            />
          </>
        ) : (
          <>
            <path
              d={
                compact
                  ? "M85 54V46M85 104V92"
                  : "M198 67H270Q278 67 278 59V46M198 113H216Q224 113 224 105V75Q224 67 216 67H202"
              }
            />
            <path
              className="pp-arrow"
              d={
                compact
                  ? "M81 48L85 42L89 48ZM81 94L85 88L89 94Z"
                  : "M274 48L278 42L282 48ZM204 63L198 67L204 71Z"
              }
            />
          </>
        )}
      </svg>
      <Node {...{ practice, entry, merged, compact }} />
      {helper ? (
        <>
          <span className="pp-call-label">{merged ? "Exact call" : "New exact call"}</span>
          <div className="pp-callee">{helper.name}</div>
        </>
      ) : (
        [1, 2].map((distance) => (
          <div className={`pp-fold pp-distance-${String(distance)}`} key={distance}>
            <span>
              {countAt(entry, distance)}{" "}
              {distance === 1 ? "direct callers" : `at distance ${String(distance)}`}
            </span>
            <span className="pp-distance">{distance}</span>
          </div>
        ))
      )}
    </div>
  );
}
export function PhoneGraph({
  practice,
  entry,
  merged,
}: {
  practice: Practice;
  entry?: PracticeCase;
  merged: boolean;
}) {
  const pieces = entry ? [entry] : [...practice.cases].reverse();
  return (
    <aside
      className={`pp-graph ${entry ? "" : "pp-combined"}`}
      aria-label={merged ? "Recorded graph after both merges" : "Decision-time graph"}
    >
      <div className="pp-graph-heading">
        <span>
          <GitBranch size={13} /> {merged ? "Recorded outcome" : "Decision-time graph"}
        </span>
        <span>
          {entry
            ? `${String(entry.proposal.impact.entries.length)} possibly affected`
            : merged
              ? "Both merged"
              : `${String(practice.cases.length)} pending changes`}
        </span>
      </div>
      <div className={entry ? "" : "pp-pair"}>
        {pieces.map((item) => (
          <Piece
            key={item.changeId}
            practice={practice}
            entry={item}
            merged={merged}
            compact={!entry}
          />
        ))}
      </div>
      {!entry ? (
        <p className="pp-graph-foot">
          {pieces
            .map(
              (item) =>
                `${caseSlot(practice, item)} · ${String(item.proposal.impact.entries.length)} possibly affected`,
            )
            .join(" · ")}
        </p>
      ) : (
        !entry.proposal.impact.entries.length && (
          <p className="pp-graph-foot">No exact callers resolved. Impact may be missed.</p>
        )
      )}
    </aside>
  );
}
