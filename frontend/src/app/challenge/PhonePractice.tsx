import { ArrowRight, Check, Info, LockKeyhole, Play, X, ArrowLeftRight } from "lucide-react";
import "./phoneGraph.css";
import { AgentLetter, AgentLine, PhoneGraph } from "./PhoneGraph";
import { caseSlot, nodeName } from "./phoneEvidence";
import { PhoneDecision } from "./PhoneDecision";
import { RecordedEvidence, absenceNote } from "./RecordedEvidence";
import { score } from "./PracticePanel";
import type { PanelProps } from "./PracticePanel";

function Introduction({ practice, onStart }: PanelProps) {
  return (
    <>
      <main className="pp-main pp-intro">
        <h1>Would you approve it?</h1>
        <p className="pp-purpose">Review two changes, then compare decisions.</p>
        <div className="pp-previews">
          {practice.cases.map((entry) => (
            <div className="pp-preview" key={entry.changeId}>
              <AgentLetter slot={caseSlot(practice, entry)} />
              <div>
                <h2>{entry.title}</h2>
                <p>{nodeName(practice, entry)}</p>
              </div>
            </div>
          ))}
        </div>
        <p className="pp-intro-note">
          <LockKeyhole size={18} />
          <span>
            Your choices stay in this browser
            <br />
            and change nothing.
          </span>
        </p>
        <p className="pp-edition">This edition has no planted bugs.</p>
      </main>
      <footer className="pp-dock">
        <button className="pp-button pp-primary" onClick={onStart}>
          Start practice
          <ArrowRight size={18} />
        </button>
      </footer>
    </>
  );
}
function RecordedDecisions({ practice, attempt, onFinish }: PanelProps & { onFinish: () => void }) {
  return (
    <>
      <main className="pp-main pp-reveal">
        <h1>Recorded decisions</h1>
        <p className="pp-purpose">Compare your choices with the recording.</p>
        {practice.cases.map((entry) => {
          const same = attempt.answers[entry.changeId] === entry.reference;
          const Icon = same ? Check : ArrowLeftRight;
          return (
            <section
              className="pp-result"
              key={entry.changeId}
              aria-label={`Findings for ${entry.changeId}`}
            >
              <AgentLine practice={practice} entry={entry} />
              <h2>{entry.title}</h2>
              <div className="pp-comparison">
                <div>
                  <p>Your choice</p>
                  <strong>{attempt.answers[entry.changeId]}</strong>
                </div>
                <div>
                  <p>Recorded decision</p>
                  <strong>
                    {entry.reference}{" "}
                    <span>· {entry.resolution.merge === "merged" ? "Merged" : "Not merged"}</span>
                  </strong>
                </div>
              </div>
              <p className="pp-match">
                <Icon size={15} />
                {same ? "Same decision" : "Different decision"}
              </p>
              <p className="pp-result-facts">
                <span className="pp-positive">
                  <Check size={13} />
                  {entry.proposal.tests.passed} passed
                </span>
                <span>{entry.proposal.impact.entries.length} possibly affected</span>
              </p>
              <RecordedEvidence practice={practice} entry={entry} />
            </section>
          );
        })}
        <p className="pp-reveal-note">
          <Info size={18} />
          {absenceNote}
        </p>
      </main>
      <footer className="pp-dock">
        <button className="pp-button pp-primary" onClick={onFinish}>
          See your result
          <ArrowRight size={18} />
        </button>
      </footer>
    </>
  );
}
function Finish({ practice, attempt, onRestart, onExit }: PanelProps) {
  const approved = practice.cases.filter((entry) => entry.reference === "Approve").length;
  const rejected = practice.cases.length - approved;
  const all = practice.cases.length === 2 ? "Both" : "All";
  const recordedDecisions =
    rejected === 0
      ? `${all} were approved in the recording.`
      : approved === 0
        ? `${all} were rejected in the recording.`
        : `${String(approved)} approved and ${String(rejected)} rejected in the recording.`;
  return (
    <>
      <main className="pp-main pp-finish">
        <h1>You’ve reviewed both changes.</h1>
        <p className="pp-purpose">See how your decisions compare.</p>
        <p className="pp-score">
          <span>
            {score(practice, attempt)} of {practice.cases.length}
          </span>{" "}
          decisions matched
        </p>
        <p className="pp-score-description">{recordedDecisions}</p>
        <div className="pp-recap">
          {practice.cases.map((entry) => (
            <div key={entry.changeId}>
              <AgentLetter slot={caseSlot(practice, entry)} />
              <span>{nodeName(practice, entry)}</span>
              <span>
                {attempt.answers[entry.changeId] === entry.reference ? "Matched" : "Different"}
              </span>
            </div>
          ))}
        </div>
        <p className="pp-honesty">
          <Info size={18} />
          <span>
            Agreement is not proof
            <br />
            of correctness.
          </span>
        </p>
      </main>
      <footer className="pp-dock pp-finish-dock">
        <button className="pp-button pp-primary" onClick={onRestart}>
          Try again
        </button>
        <button className="pp-watch" onClick={onExit}>
          <Play size={18} />
          Watch the recording
        </button>
      </footer>
    </>
  );
}
export function PhonePractice(props: PanelProps) {
  const { practice, attempt, onExit } = props;
  const decision = attempt.started && !attempt.revealed;
  const entry = decision ? practice.cases[attempt.index] : undefined;
  // The recording stays frozen at the decision cut. Only this bounded illustration shows merges.
  return (
    <section className="phone-practice" aria-label="Two-change practice">
      <header className="pp-chrome">
        <div className="pp-topbar">
          <span>Oversight</span>
          <button onClick={onExit}>
            Leave practice
            <X size={14} />
          </button>
        </div>
        <div className="pp-status" aria-label="Status line" role="status">
          Replay of a real recorded run · Oct 27, 2026
        </div>
      </header>
      <PhoneGraph practice={practice} entry={entry} merged={attempt.revealed} />
      {!attempt.started ? (
        <Introduction {...props} />
      ) : decision ? (
        <PhoneDecision key={entry?.changeId} {...props} />
      ) : attempt.finished ? (
        <Finish {...props} />
      ) : (
        <RecordedDecisions
          {...props}
          onFinish={() => {
            props.onFinish?.();
          }}
        />
      )}
    </section>
  );
}
