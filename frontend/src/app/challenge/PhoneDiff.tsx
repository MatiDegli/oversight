import { CornerDownRight, ChevronRight, ChevronDown } from "lucide-react";
import "./phoneDiff.css";
import { useLayoutEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { calledHelper, changedNode, diffRows, wrapSource } from "./phoneEvidence";
import type { PracticeCase, SourceRow } from "./phoneEvidence";
import type { Practice } from "./manifest";

export function Disclosure({
  label,
  children,
  count,
  variant,
  preview,
}: {
  label: string;
  children: ReactNode;
  count?: ReactNode;
  variant?: "decision";
  preview?: string;
}) {
  return (
    <details className={`pp-disclosure${variant === "decision" ? " pp-decision-row" : ""}`}>
      <summary>
        {variant === "decision" ? (
          <span className="pp-row-text">
            <span className="pp-row-label">{label}</span> · {preview}
          </span>
        ) : (
          <span>{label}</span>
        )}
        <span className="pp-disclosure-end">
          {count}
          {variant === "decision" ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </summary>
      {children}
    </details>
  );
}
function SourceLine({ row, capacity }: { row: SourceRow; capacity: number }) {
  if (row.kind === "fold") return <div className="pp-unchanged">Unchanged lines</div>;
  const parts = wrapSource(row.text, capacity);
  return (
    <div className="pp-source" data-source={row.text}>
      {parts.map((part, index) => (
        <div
          className={`pp-code-line ${row.kind}`}
          key={index}
          data-continuation={index > 0 || undefined}
        >
          <span className="pp-line-number" aria-hidden="true">
            {index ? <CornerDownRight size={13} /> : row.line}
          </span>
          <span className="pp-sign" aria-hidden="true">
            {index ? "" : row.kind === "added" ? "+" : row.kind === "removed" ? "−" : ""}
          </span>
          <code style={{ paddingLeft: `${String(part.indent)}ch` }}>{part.text || " "}</code>
        </div>
      ))}
    </div>
  );
}
function Code({ rows }: { rows: SourceRow[] }) {
  const host = useRef<HTMLDivElement>(null);
  const [capacity, setCapacity] = useState(28);
  useLayoutEffect(() => {
    const element = host.current;
    if (!element) return;
    const measure = () => {
      const canvas = document.createElement("canvas");
      const context = canvas.getContext("2d");
      if (!context) return;
      context.font = `400 15px ${getComputedStyle(element).getPropertyValue("--code-font")}`;
      setCapacity(
        Math.max(8, Math.floor((element.clientWidth - 55) / context.measureText("M").width)),
      );
    };
    void document.fonts.ready.then(measure);
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => {
      observer.disconnect();
    };
  }, []);
  return (
    <div ref={host} className="pp-code">
      {rows.map((row, index) => (
        <SourceLine key={index} row={row} capacity={capacity} />
      ))}
    </div>
  );
}
function FileDiff({
  practice,
  entry,
  path,
}: {
  practice: Practice;
  entry: PracticeCase;
  path: string;
}) {
  const file = practice.cut.changes[entry.changeId]?.files[path]?.file;
  const facts = entry.proposal.files.find((item) => item.path === path);
  if (!file) return null;
  const trim =
    changedNode(practice, entry)?.kind === "method" && !path.startsWith("tests/") ? 4 : 0;
  const rows = diffRows(file.original ?? "", file.modified ?? "").map((row) => ({
    ...row,
    text: row.text.slice(Math.min(trim, row.text.match(/^ */)?.[0].length ?? 0)),
  }));
  return (
    <section className="pp-file" aria-label={`Recorded diff, ${path}`}>
      <div className="pp-file-heading">
        <code>{path}</code>
        <span>
          <span className="pp-positive">+{facts?.lines_added}</span>{" "}
          <span className="pp-negative">−{facts?.lines_removed}</span>
        </span>
      </div>
      <Code rows={rows} />
    </section>
  );
}
export function PhoneDiff({ practice, entry }: { practice: Practice; entry: PracticeCase }) {
  const helper = calledHelper(practice, entry);
  const production = entry.proposal.files.filter((file) => !file.path.startsWith("tests/"));
  const tests = entry.proposal.files.filter((file) => file.path.startsWith("tests/"));
  const helperFile = helper && practice.cut.changes[entry.changeId]?.files[helper.path]?.file;
  const helperRows =
    helper &&
    helperFile?.original
      ?.split("\n")
      .slice(helper.start_line - 1, helper.end_line)
      .map((text, index) => ({ kind: "context" as const, line: helper.start_line + index, text }));
  return (
    <div id="phone-recorded-diff">
      {production.map((file) => (
        <FileDiff key={file.path} {...{ practice, entry }} path={file.path} />
      ))}
      <Disclosure
        label="Tests added"
        count={
          <span className="pp-positive">
            +{tests.reduce((sum, file) => sum + file.lines_added, 0)}
          </span>
        }
      >
        {tests.map((file) => (
          <FileDiff key={file.path} {...{ practice, entry }} path={file.path} />
        ))}
      </Disclosure>
      {helper && helperRows && (
        <Disclosure label={`Called helper · ${helper.name}`}>
          <Code rows={helperRows} />
        </Disclosure>
      )}
    </div>
  );
}
