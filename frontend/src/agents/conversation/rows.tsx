import type { SuiteResult } from "@contracts";
import {
  Check,
  Circle,
  Eye,
  Flag,
  FlaskConical,
  List,
  MessageSquare,
  Pencil,
  Search,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import type { ConversationEntry } from "./selectConversation";

export const focusRing =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text";
const icons: Record<string, LucideIcon> = {
  list_files: List,
  search: Search,
  read_file: Eye,
  edit_file: Pencil,
  run_tests: FlaskConical,
};

export function EventTime({ ts }: { ts?: string }) {
  return ts ? (
    <time dateTime={ts} className="shrink-0 font-mono text-[13px] whitespace-nowrap text-faint">
      {new Date(ts).toLocaleTimeString("en-GB", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      })}
    </time>
  ) : null;
}

function Row({
  icon: Icon,
  title,
  aside,
  children,
}: {
  icon: LucideIcon;
  title: ReactNode;
  aside?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="flex gap-3 px-[22px] py-[9px]">
      <span className="w-[22px] shrink-0 pt-px text-faint">
        <Icon aria-hidden="true" className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-3 text-[15px]">
          <div className="min-w-0 wrap-anywhere">{title}</div>
          {aside}
        </div>
        {children}
      </div>
    </div>
  );
}

export function Divider({ children }: { children: ReactNode }) {
  return (
    <div className="mx-[22px] my-[10px] flex items-center gap-3 text-center text-[13px] font-semibold text-faint">
      <span className="h-px min-w-3 flex-1 bg-divider" />
      <span>{children}</span>
      <span className="h-px min-w-3 flex-1 bg-divider" />
    </div>
  );
}

function MessageRow({
  task = false,
  ts,
  text,
}: {
  task?: boolean;
  ts: string;
  text: string | null;
}) {
  return (
    <Row
      icon={task ? Flag : MessageSquare}
      title={task ? "Task from you" : "From you"}
      aside={<EventTime ts={ts} />}
    >
      <p className="mt-[6px] rounded-[10px] border border-border bg-raised px-[14px] py-[10px] text-[15px] leading-[1.55] whitespace-pre-wrap wrap-anywhere">
        {text ?? "Message sent. Its text is not in this recording."}
      </p>
    </Row>
  );
}

function TestOutput({ tests }: { tests: SuiteResult }) {
  const files = new Map<string, Map<string, number>>();
  for (const item of tests.cases ?? []) {
    const path = item.test_id.split("::")[0] ?? item.test_id;
    const counts = files.get(path) ?? new Map<string, number>();
    counts.set(item.outcome, (counts.get(item.outcome) ?? 0) + 1);
    files.set(path, counts);
  }
  const failedIds = new Set([
    ...tests.failed_tests,
    ...(tests.cases ?? [])
      .filter((item) => item.outcome === "failed" || item.outcome === "error")
      .map((item) => item.test_id),
  ]);
  return (
    <details className="mt-[6px] text-[14px] text-muted">
      <summary className={`w-fit cursor-pointer rounded-control ${focusRing}`}>Test output</summary>
      <div className="mt-[6px] rounded-[8px] border border-divider bg-panel px-3 py-2 font-mono text-[13px] leading-5 whitespace-pre-wrap wrap-anywhere">
        <p>
          {tests.passed} passed in {(tests.duration_ms / 1000).toFixed(2)}s
          {tests.failed > 0 ? `, ${String(tests.failed)} failed` : ""}
          {tests.skipped > 0 ? `, ${String(tests.skipped)} skipped` : ""}
        </p>
        {[...failedIds].map((id) => (
          <p key={id}>
            {id}
            {tests.cases?.find((item) => item.test_id === id)?.detail
              ? ` · ${tests.cases.find((item) => item.test_id === id)?.detail ?? ""}`
              : ""}
          </p>
        ))}
        {[...files].map(([path, counts]) => (
          <p key={path}>
            {path} ·{" "}
            {[...counts].map(([outcome, count]) => `${String(count)} ${outcome}`).join(", ")}
          </p>
        ))}
      </div>
    </details>
  );
}

function StepRow({
  entry,
  changeId,
  onOpenDiff,
}: {
  entry: Extract<ConversationEntry, { kind: "step" }>;
  changeId: string;
  onOpenDiff?: (id: string) => void;
}) {
  const { step } = entry;
  return (
    <Row
      icon={icons[step.tool] ?? Circle}
      title={
        <>
          <code className="font-mono text-[14px]">{step.tool}</code> · {step.summary}
        </>
      }
      aside={
        <span
          className={`shrink-0 text-[13px] whitespace-nowrap ${step.result === "ok" ? "text-passed" : "text-failed-text"}`}
        >
          {step.result} <span className="font-mono">{step.duration_ms} ms</span>
        </span>
      }
    >
      {step.result === "error" && step.error && (
        <p className="text-[14px] text-muted wrap-anywhere">{step.error}</p>
      )}
      {step.tool === "edit_file" && step.result === "ok" && onOpenDiff && (
        <button
          type="button"
          className={`rounded-control text-[14px] text-muted underline underline-offset-2 hover:text-text ${focusRing}`}
          onClick={() => {
            onOpenDiff(changeId);
          }}
        >
          View in diff
        </button>
      )}
      {step.tool === "run_tests" && step.tests && <TestOutput tests={step.tests} />}
    </Row>
  );
}

export function TimelineEntry({
  entry,
  changeId,
  onOpenDiff,
}: {
  entry: ConversationEntry;
  changeId: string;
  onOpenDiff?: (id: string) => void;
}) {
  switch (entry.kind) {
    case "task":
      return <MessageRow task ts={entry.ts} text={entry.text} />;
    case "message":
      return <MessageRow ts={entry.ts} text={entry.text} />;
    case "step":
      return <StepRow entry={entry} changeId={changeId} onOpenDiff={onOpenDiff} />;
    case "proposed":
      return (
        <Row icon={Check} title="Proposed the change" aside={<EventTime ts={entry.ts} />}>
          {entry.summary !== null && (
            <p className="text-[14px] text-muted wrap-anywhere">
              The model: &quot;{entry.summary}&quot;
            </p>
          )}
        </Row>
      );
    case "cross-review":
      return (
        <>
          <Divider>
            Cross-review · {entry.label} ·{" "}
            {!entry.complete
              ? "incomplete"
              : `${String(entry.findingCount)} ${entry.findingCount === 1 ? "note" : "notes"}, ${entry.blockingCount ? `${String(entry.blockingCount)} blocking` : "nothing blocking"}`}
          </Divider>
          {entry.review.findings.map((finding, index) => (
            <Row key={index} icon={Eye} title={<>Note: {finding.text}</>}>
              {finding.path && (
                <p className="text-[14px] text-muted wrap-anywhere">
                  {finding.path}
                  {finding.line !== null ? `:${String(finding.line)}` : ""}
                </p>
              )}
            </Row>
          ))}
        </>
      );
    case "resolution":
      return entry.resolution.decision === "changes_requested" ? (
        <>
          <Divider>You requested changes · round {entry.round}</Divider>
          {entry.resolution.comment !== null && (
            <MessageRow ts={entry.ts} text={entry.resolution.comment} />
          )}
        </>
      ) : (
        <Divider>
          {entry.resolution.decision === "approved"
            ? "You approved the change"
            : "You rejected the change"}
        </Divider>
      );
  }
}
