import { useState } from "react";
import { replayDecisionWords, useDecisions } from "./DecisionContext";
import { commandFailure } from "../transport";
import { ShieldCheck } from "lucide-react";
import type { State } from "../state";
import { grantReasons, grantScopeWords, selectActiveGrants } from "../state";
import { eventTime } from "./ChangeCard";
import "./inbox.css";

function Revoke({ id }: { id: string }) {
  const { revokeGrant, replayOf } = useDecisions();
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState("");
  return (
    <div>
      <button
        type="button"
        className="decision-reject"
        disabled={!revokeGrant || pending}
        onClick={() => {
          if (!revokeGrant || pending) return;
          setPending(true);
          setFailure("");
          void revokeGrant(id).catch((error: unknown) => {
            setPending(false);
            setFailure(commandFailure("Revoke", error));
          });
        }}
      >
        {pending ? "Revoking…" : "Revoke"}
      </button>
      {!revokeGrant && <p className="decision-muted">{replayDecisionWords(replayOf)}</p>}
      {failure && <p role="alert">{failure}</p>}
    </div>
  );
}
export function StandingGrants({ state }: { state: State }) {
  const grants = selectActiveGrants(state);
  return (
    <section aria-label="Standing grants" className="grants-content">
      <h2>Standing grants</h2>
      <p className="decision-muted">
        Choices you let through without asking again. Grants end with their recorded run or session.
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
            <Revoke id={grant.grant_id} />
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
    </section>
  );
}
