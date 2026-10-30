import type { GrantCreated, GrantScope } from "@contracts";
import type { State } from "./types";

export const grantReasons: Record<GrantCreated["reason"], string> = {
  possible_callers: "Possible callers, not followed",
  protected_path: "Protected area changed",
};
const scopes: Record<GrantScope["kind"], string> = {
  module: "This module",
  run: "This run",
  path: "This path",
  agent: "This agent",
};
export const grantScopeWords = (scope: GrantScope): string =>
  `${scopes[scope.kind]}${scope.kind === "run" ? "" : ` · ${scope.value}`}`;

/** Historical receipts survive revocation; a change using a grant counts once. */
export function selectGrantUses(state: State, grantId: string) {
  return state.decisionEvents
    .filter(
      (event) =>
        event.type === "change.resolved" &&
        event.payload.decision === "approved" &&
        event.payload.merge === "merged" &&
        event.payload.decided_by === "rules" &&
        event.payload.grants?.some((use) => use.grant_id === grantId),
    )
    .map((event) => event.payload)
    .filter(
      (p, i, all) =>
        "change_id" in p &&
        all.findIndex((other) => "change_id" in other && other.change_id === p.change_id) === i,
    );
}

/** v1.7 has no run-end event: run.started resets evidence; never infer expiry from agent states. */
export function selectActiveGrants(state: State) {
  const revoked = new Set(
    state.grantEvents.flatMap((event) =>
      event.type === "grant.revoked" ? [event.payload.grant_id] : [],
    ),
  );
  return state.grantEvents.flatMap((event) =>
    event.type === "grant.created" &&
    event.payload.run_id === state.run?.run_id &&
    !revoked.has(event.payload.grant_id)
      ? [
          {
            ...event.payload,
            ts: event.ts,
            uses: selectGrantUses(state, event.payload.grant_id).length,
          },
        ]
      : [],
  );
}
