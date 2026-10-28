import type { Practice } from "./manifest";

export type Answer = "Approve" | "Reject";
export type Attempt = {
  index: number;
  started: boolean;
  revealed: boolean;
  answers: Partial<Record<string, Answer>>;
};
export const freshAttempt = (): Attempt => ({
  index: 0,
  started: false,
  revealed: false,
  answers: {},
});
export function answerOnce(attempt: Attempt, id: string, answer: Answer): Attempt {
  return attempt.answers[id]
    ? attempt
    : { ...attempt, answers: { ...attempt.answers, [id]: answer } };
}
export const complete = (practice: Practice, attempt: Attempt) =>
  practice.cases.every((item) => attempt.answers[item.changeId]);
export const score = (practice: Practice, attempt: Attempt) =>
  practice.cases.filter((item) => attempt.answers[item.changeId] === item.reference).length;
const control =
  "rounded-control border border-control-edge px-4 py-2 text-sm enabled:hover:bg-raised disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";

interface PanelProps {
  practice: Practice;
  attempt: Attempt;
  onAnswer: (answer: Answer) => void;
  onStart: () => void;
  onNext: () => void;
  onReveal: () => void;
  onRestart: () => void;
  onExit: () => void;
}

function Findings({ practice, attempt, onRestart }: PanelProps) {
  return (
    <>
      <p role="status">Matches with recorded decisions: {score(practice, attempt)}/2.</p>
      {practice.cases.map((entry) => (
        <section
          key={entry.changeId}
          aria-label={`Findings for ${entry.changeId}`}
          className="border-t border-divider pt-4"
        >
          <h2 className="font-semibold">
            {entry.changeId === "change-2" ? "Inventory" : "Pricing"}
          </h2>
          <p>Your practice answer: {attempt.answers[entry.changeId]}</p>
          <p>
            Recorded decision: {entry.reference} ·{" "}
            {entry.resolution.merge === "merged" ? "merged into main" : "not merged"} ·{" "}
            <span className="font-mono">{entry.resolution.merge_commit?.slice(0, 7)}</span>
          </p>
          <p>
            {entry.proposal.tests.passed} passing tests · {entry.proposal.impact.entries.length}{" "}
            possibly affected entries
          </p>
          <p>
            +{entry.proposal.impact.omitted_by_depth} omitted by depth · +
            {entry.proposal.impact.omitted_by_limit} omitted by limit
          </p>
        </section>
      ))}
      <p>
        Detailed review facts, cross-review and behavior evidence were not recorded. Their absence
        does not establish clean reviews, behavior changes, measurement or safety.
      </p>
      <p>The graph retains the selected decision-time cone; recorded merges are shown here only.</p>
      <button className={control} onClick={onRestart}>
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
        {item.changeId === "change-2" ? "Inventory · Agent B" : "Pricing · Agent A"}
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
        {proposal.files.length} files · +{proposal.lines_added} −{proposal.lines_removed} lines ·{" "}
        {proposal.tests.passed} passing tests · Revision {proposal.revision}
      </p>
      <p className="text-sm text-muted">{proposal.files.map((file) => file.path).join(" · ")}</p>
      <p className="font-mono text-sm break-all">Head {proposal.head_commit}</p>
      <p>
        {proposal.impact.entries.length} possibly affected entries · +
        {proposal.impact.omitted_by_depth} omitted by depth · +{proposal.impact.omitted_by_limit}{" "}
        omitted by limit
      </p>
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
  const { attempt, onStart, onExit } = props;
  return (
    <section aria-label="Two-change practice" className="h-full overflow-auto p-6 text-text">
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
              Two-change practice · Two proposals from a real recorded run, frozen before either
              decision.
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
    </section>
  );
}
