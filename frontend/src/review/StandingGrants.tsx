import { ShieldCheck } from "lucide-react";
import type { State } from "../state";
import { grantReasons, grantScopeWords, selectActiveGrants } from "../state";
import { eventTime } from "./ChangeCard";
import "./inbox.css";

export function StandingGrants({ state }: { state: State }) {
  const grants = selectActiveGrants(state);
  return (
    <section aria-label="Standing grants" className="grants-content">
      <h2>Standing grants</h2>
      <p className="decision-muted">
        Choices you let through without asking again. Every grant ends with its run.
      </p>
      {grants.length ? (
        grants.map((grant) => (
          <article className="grant-row" key={grant.grant_id}>
            <div>
              <h3>{grantReasons[grant.reason]}</h3>
              <p>{grantScopeWords(grant.scope)}</p>
              <p className="decision-muted">
                Given by you at {eventTime(grant.ts)} · {grant.uses}{" "}
                {grant.uses === 1 ? "change" : "changes"} let through
              </p>
            </div>
            <button
              type="button"
              className="decision-reject"
              aria-disabled="true"
              title="Preview: revoke is not connected yet"
            >
              Revoke
            </button>
          </article>
        ))
      ) : (
        <div className="grants-empty">
          <ShieldCheck size={28} aria-hidden="true" />
          <h3>No active grants</h3>
          <p>
            When possible callers need you, Approve with “Don't ask again” and a scope creates a
            grant here.
          </p>
          <p className="decision-muted">
            Revoked grants and grants from earlier runs are not active.
          </p>
        </div>
      )}
      <p className="decision-muted grants-note">Preview · Revoke is not connected in this stage.</p>
    </section>
  );
}
