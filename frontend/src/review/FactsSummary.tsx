import type { ChangeFacts } from "@contracts";

/** Server-written lines, kept apart from the agent's own report. */
export function FactsSummary({ facts }: { facts: ChangeFacts | undefined }) {
  if (!facts?.summary) return null;
  return (
    <section
      aria-label="Recorded facts summary"
      className="min-w-0 shrink-0 border-b border-page-border px-4.5 py-2.5 font-sans text-(length:--body-size) text-text"
    >
      <h3 className="mb-1.5 text-sm font-semibold text-muted">Recorded facts summary</h3>
      {facts.summary.map((line, index) => (
        <p key={index} className="wrap-anywhere whitespace-pre-wrap">
          {line}
        </p>
      ))}
    </section>
  );
}
