import { impactText, omittedText, sentenceList } from "./evidenceCopy";
import { RecordedEvidence, absenceNote } from "./RecordedEvidence";
import type { EvidencePractice as Practice } from "./caseEvidence";
import { usePhonePractice } from "./usePhonePractice";
import { PhonePractice } from "./PhonePractice";

export { answerOnce, complete, freshAttempt, score } from "./attempt";
export type { Answer, Attempt } from "./attempt";
import { complete, score } from "./attempt";
import type { Answer, Attempt } from "./attempt";
const control =
  "rounded-control border border-control-edge px-4 py-2 text-sm enabled:hover:bg-raised disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";
const primary =
  "inline-flex h-(--button-primary-height) items-center justify-center rounded-control bg-text px-4 text-(length:--label-size) font-semibold text-page hover:bg-text-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";

export interface PanelProps {
  practice: Practice;
  attempt: Attempt;
  onAnswer: (answer: Answer) => void;
  onStart: () => void;
  onNext: () => void;
  onReveal: () => void;
  onRestart: () => void;
  onFinish?: () => void;
  onExit: () => void;
}

export const caseTitle = (task: string) => task.trim().split(/\s+/)[0] ?? "Change";

function Findings({ practice, attempt, onRestart }: PanelProps) {
  return (
    <>
      <p role="status">
        {score(practice, attempt)
          ? `Matches with recorded decisions: ${String(score(practice, attempt))}/${String(practice.cases.length)}.`
          : "None of your decisions matched the recording."}
      </p>
      {practice.cases.map((entry) => (
        <section
          key={entry.changeId}
          aria-label={`Findings for ${entry.changeId}`}
          className="border-t border-divider pt-4"
        >
          <h2 className="font-semibold">{caseTitle(entry.proposal.task)}</h2>
          <p>Your practice answer: {attempt.answers[entry.changeId]}</p>
          <p>
            Recorded decision: {entry.reference},{" "}
            {entry.resolution.merge === "merged" ? "merged into main" : "not merged"}
            {entry.resolution.merge_commit && (
              <>
                {" "}
                at <span className="font-mono">{entry.resolution.merge_commit.slice(0, 7)}</span>
              </>
            )}
            .
          </p>
          <p>
            {entry.proposal.tests.passed
              ? `${String(entry.proposal.tests.passed)} passing tests. `
              : "No passing tests were recorded. "}
            {impactText(entry.proposal.impact.entries.length)}.
          </p>
          {omittedText(entry.proposal.impact) && <p>{omittedText(entry.proposal.impact)}</p>}
          <RecordedEvidence practice={practice} entry={entry} />
        </section>
      ))}
      <p>{absenceNote}</p>
      <p>The graph retains the selected decision-time cone; recorded merges are shown here only.</p>
      <button className={primary} onClick={onRestart}>
        Try again
      </button>
    </>
  );
}

function Decision({ practice, attempt, onAnswer, onNext, onReveal }: PanelProps) {
  const item = practice.cases[attempt.index];
  if (!item) return null;
  const proposal = item.proposal;
  const answer = attempt.answers[item.changeId];
  return (
    <>
      <h2 className="font-semibold">
        {caseTitle(proposal.task)}, Agent{" "}
        {practice.cut.agents[proposal.agent_id]?.slot ?? "unavailable"}
      </h2>
      <section aria-label="Task">
        <h3 className="mb-2 font-semibold">Task</h3>
        <p>{proposal.task}</p>
      </section>
      <section aria-label="Agent summary">
        <h3 className="mb-2 font-semibold">Agent summary</h3>
        <p>{proposal.summary}</p>
      </section>
      <p>
        {sentenceList([
          ...(proposal.files.length ? [`${String(proposal.files.length)} files`] : []),
          ...(proposal.lines_added ? [`${String(proposal.lines_added)} lines added`] : []),
          ...(proposal.lines_removed ? [`${String(proposal.lines_removed)} lines removed`] : []),
          ...(proposal.tests.passed ? [`${String(proposal.tests.passed)} passing tests`] : []),
          `revision ${String(proposal.revision)}`,
        ])}
        .
      </p>
      <p className="text-sm text-muted">{proposal.files.map((file) => file.path).join(", ")}</p>
      <p className="text-sm break-all">
        Head <span className="font-mono">{proposal.head_commit}</span>
      </p>
      <p>{impactText(proposal.impact.entries.length)}.</p>
      {omittedText(proposal.impact) && <p>{omittedText(proposal.impact)}</p>}
      <p id="practice-only">
        These buttons save a practice answer only. Approve requires a pointer click.
      </p>
      <div className="flex gap-3">
        <button
          className={control}
          disabled={!!answer}
          aria-describedby="practice-only"
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") event.preventDefault();
          }}
          onClick={(event) => {
            if (event.detail !== 0) onAnswer("Approve");
          }}
        >
          Approve
        </button>
        <button
          className={control}
          disabled={!!answer}
          aria-describedby="practice-only"
          onClick={() => {
            onAnswer("Reject");
          }}
        >
          Reject
        </button>
      </div>
      {answer && <p role="status">Practice answer saved: {answer}</p>}
      {attempt.index === 0 && (
        <button className={control} disabled={!answer} onClick={onNext}>
          Next
        </button>
      )}
      <button className={control} disabled={!complete(practice, attempt)} onClick={onReveal}>
        Show findings
      </button>
    </>
  );
}

export function PracticePanel(props: PanelProps) {
  const phone = usePhonePractice(true);
  if (phone) return <PhonePractice {...props} />;
  const { attempt, onStart, onExit } = props;
  return (
    <section
      aria-label="Two-change practice"
      className="practice-panel h-full overflow-auto p-6 text-text"
    >
      <div className="mx-auto flex max-w-[70ch] flex-col gap-5">
        <h1 className="text-xl font-semibold">
          {attempt.revealed
            ? "Recorded findings"
            : attempt.started
              ? `Decision ${String(attempt.index + 1)} of 2`
              : "Would you approve these changes?"}
        </h1>
        {!attempt.started ? (
          <>
            <p>
              This two-change practice uses two proposals from a real recorded run, frozen before
              either decision.
            </p>
            <p>
              Choose Approve or Reject for each change. Answers stay in this attempt only. After
              both answers, compare with the recorded decisions.
            </p>
            <p>
              This edition has no planted bugs. Agreement with the recording is not a measure of
              safety or bug detection.
            </p>
            <button className={control} onClick={onStart}>
              Start practice
            </button>
          </>
        ) : attempt.revealed ? (
          <Findings {...props} />
        ) : (
          <Decision {...props} />
        )}
        <button className={`${control} self-start`} onClick={onExit}>
          Return to ordinary replay
        </button>
      </div>
      <p className="practice-phone-note mt-5 text-sm text-muted">
        Read-only inspection tabs are not shown in the phone layout. Use a wider window to inspect
        files and diffs.
      </p>
    </section>
  );
}
