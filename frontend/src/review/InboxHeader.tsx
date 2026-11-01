import type { selectInboxSummary } from "./inboxSelectors";

type InboxView = "decisions" | "grants";
export function InboxViews({
  count,
  grants,
  view = "decisions",
  onView,
  inert,
}: {
  count: number;
  grants: number;
  view?: InboxView;
  onView?: (view: InboxView) => void;
  inert?: boolean;
}) {
  return (
    <nav aria-label="Inbox views" className="inbox-views">
      <button
        type="button"
        disabled={inert}
        aria-pressed={view === "decisions"}
        onClick={() => {
          onView?.("decisions");
        }}
      >
        Decisions <span>{count}</span>
      </button>
      <button
        type="button"
        disabled={inert}
        aria-pressed={view === "grants"}
        onClick={() => {
          onView?.("grants");
        }}
      >
        Standing grants <span>{grants}</span>
      </button>
    </nav>
  );
}

export function InboxHeading({
  count,
  agents,
  steps,
  minutes,
}: {
  count: number;
} & ReturnType<typeof selectInboxSummary>) {
  return (
    <header className="inbox-heading">
      <span className="inbox-count">{count}</span>
      <div>
        <h2>{count ? "Decisions waiting for you" : "No decisions waiting"}</h2>
        <p className="decision-muted">
          {agents} {agents === 1 ? "agent" : "agents"} took {steps} {steps === 1 ? "step" : "steps"}
          {minutes === null ? "" : ` in ${String(minutes)} min`}.{" "}
          {count ? `These ${String(count)} need a person.` : "Nothing needs your decision now."}
        </p>
      </div>
    </header>
  );
}
