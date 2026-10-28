import { Check, X, ArrowRight, ChevronDown, Files } from "lucide-react";
import { useState } from "react";
import { AgentLine } from "./PhoneGraph";
import { caseSlot } from "./phoneEvidence";
import { Disclosure, PhoneDiff } from "./PhoneDiff";
import type { PanelProps } from "./PracticePanel";
import type { PracticeCase } from "./phoneEvidence";

function Impact({ entry }: { entry: PracticeCase }) {
  const impact = entry.proposal.impact;
  return (
    <Disclosure label="Possibly affected" count={impact.entries.length}>
      <div className="pp-impact">
        <p>
          Exact calls only · Depth limit {impact.max_depth} · Node limit {impact.max_nodes}
        </p>
        {impact.entries.map((node) => (
          <p key={node.node_id}>
            {node.distance} {node.node_id.split(":")[1]}
          </p>
        ))}
        <p>
          {impact.omitted_by_depth || impact.omitted_by_limit
            ? `+${String(impact.omitted_by_depth)} omitted by depth · +${String(impact.omitted_by_limit)} omitted by node limit.`
            : "No entries omitted by depth or node limit."}
        </p>
      </div>
    </Disclosure>
  );
}
function Facts({
  entry,
  open,
  onToggle,
}: {
  entry: PracticeCase;
  open: boolean;
  onToggle: () => void;
}) {
  const proposal = entry.proposal;
  return (
    <div className="pp-facts">
      <span className="pp-chip">
        <Files size={14} />
        {proposal.files.length} files
      </span>
      <button
        className="pp-chip pp-diff-toggle"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls="phone-recorded-diff"
      >
        <span>
          <span className="pp-positive">+{proposal.lines_added}</span>{" "}
          <span className="pp-negative">−{proposal.lines_removed}</span> lines
        </span>
        <span>
          {open ? "Hide diff" : "Show diff"} <ChevronDown size={14} />
        </span>
      </button>
      <span className="pp-chip pp-positive">
        <Check size={14} />
        {proposal.tests.passed} passed
      </span>
    </div>
  );
}
export function PhoneDecision({ practice, attempt, ...actions }: PanelProps) {
  const [open, setOpen] = useState(false);
  const entry = practice.cases[attempt.index];
  if (!entry) return null;
  const answer = attempt.answers[entry.changeId];
  return (
    <>
      <main className="pp-main">
        <div className="pp-progress">
          <span>
            Decision {attempt.index + 1} of {practice.cases.length}
          </span>
          <span className="pp-ticks" aria-hidden="true">
            {practice.cases.map((item, i) => (
              <span key={item.changeId} className={i <= attempt.index ? "is-active" : ""} />
            ))}
          </span>
        </div>
        <AgentLine {...{ practice, entry }} />
        <h1>{entry.title}</h1>
        <p className="pp-purpose">
          {answer
            ? "Your choice is saved. Continue when ready."
            : "Inspect the change, then make your call."}
        </p>
        <Facts
          entry={entry}
          open={open}
          onToggle={() => {
            setOpen(!open);
          }}
        />
        <section className="pp-summary" aria-label={`Agent ${caseSlot(practice, entry)}'s summary`}>
          <h2>Agent {caseSlot(practice, entry)}’s summary</h2>
          <p>{entry.proposal.summary}</p>
        </section>
        {open ? <PhoneDiff {...{ practice, entry }} /> : <div id="phone-recorded-diff" hidden />}
        <Disclosure label="Recorded task">
          <p className="pp-task">{entry.proposal.task}</p>
        </Disclosure>
        {!!entry.proposal.impact.entries.length && <Impact entry={entry} />}
      </main>
      <DecisionDock practice={practice} attempt={attempt} {...actions} />
    </>
  );
}

function DecisionDock({ practice, attempt, ...actions }: PanelProps) {
  const entry = practice.cases[attempt.index];
  const answer = entry && attempt.answers[entry.changeId];
  return (
    <footer className={`pp-dock ${answer ? "is-answered" : ""}`}>
      {answer ? (
        <>
          <div className="pp-saved" role="status">
            <Check size={20} />
            <div>
              <span>Your choice</span>
              <strong>{answer}</strong>
            </div>
          </div>
          <button
            className="pp-button pp-primary"
            onClick={attempt.index === 0 ? actions.onNext : actions.onReveal}
          >
            {attempt.index === 0 ? "Next" : "Show findings"}
            <ArrowRight size={18} />
          </button>
        </>
      ) : (
        <>
          <button
            className="pp-button pp-choice"
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") event.preventDefault();
            }}
            onClick={(event) => {
              if (event.detail !== 0) actions.onAnswer("Approve");
            }}
          >
            <Check size={18} />
            Approve
          </button>
          <button
            className="pp-button pp-choice"
            onClick={() => {
              actions.onAnswer("Reject");
            }}
          >
            <X size={18} />
            Reject
          </button>
        </>
      )}
    </footer>
  );
}
