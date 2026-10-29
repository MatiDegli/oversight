# Standing grants: plan

Proposed on 2026-10-29 for the maintainer's approval. Nothing here is built, and no contract has
changed. The decision itself is in DECISIONS.md ("Standing grants: Oversight remembers what the
person let go"), and the queue entry is in [additions.md](../additions.md#standing-grants).

## Why now

`small_cone` now answers `unknown` when a changed node or a node in its cone has callers linked
only by possible calls. Planning estimates for Boltons d31d821 put 446 of 898 single-function
changes, and 5 of the 16 drafted proof 2 tasks, in that situation. These are planning estimates,
not reproducible from this repository: the corpus measurement and draft task list are not checked
in, and the 16 are not `tools/eval/tasks.json`. They motivate the plan, not a measured proof 2
result. Many questions would repeat: the same module, the same kind of gap. A grant answers a
question once.

## Three layers, each in one direction

1. **Rules** are deterministic: each passes or makes the change wait, with evidence.
2. **Muse Spark Open's decision** can only add a wait: a `not_asked_for` or `cannot_tell`
   difference becomes a question (D6, item 9).
3. **The person's grants** can only remove a wait the person could accept themselves.

A grant never turns a rule's recorded result into `pass`. The assessment stays as measured, and
the rules approval records which grant removed which wait.

Both authorization gates must change: each accepts a rule with result `pass`, or `unknown` whose
entire wait reason is covered by an active grant; unsupported waits and any `fail` still block.
The recorded result stays `unknown` when a grant removes its wait. Existing exact-profile,
evidence, target, mode and integrity guards stay unchanged.

The `small_cone` discriminator is structured, never inferred from its evidence text: `observed`
is present with all five counts, `0 <= nodes <= 5`, `omitted_by_depth == 0`,
`omitted_by_limit == 0`, `parse_errors == 0` and `unresolved_removed == 0`. Only then can an
`unknown` be covered by `possible_callers`, with a measured positive possible-callers count from
the same tree, captured in the grant-use evidence below. `observed=None` always waits for the
person; a missing count, an exceeded limit or an unmeasured caller count also waits. This applies
at admission, candidate re-measure and both pure resolution checks; a module scope never waives
missing measurements or unresolved removals.

- Admission: `pending_policy` in `backend/src/oversight/changes/_rules.py` applies that check to
  every non-combined rule. `combined_tests` keeps its existing pending-unknown requirement until
  the combined suite runs; a grant never covers it.
- Definitive authorization: `merges_alone` in `backend/src/oversight/changes/_policy.py`, called
  by `backend/src/oversight/changes/_transaction.py` and the pure
  `validate_resolution_intent` check, applies the same grant-aware check to the final assessment,
  including the candidate rules re-measured by
  `backend/src/oversight/changes/_candidate.py`. Combined tests must now pass. A prior grant use
  cannot authorize a newly uncovered wait in the re-measured assessment.
  `ChangeResolved._rules_decision` must accept that same evidence when validating the event;
  both pure checks are part of the proposed change below.

## What a grant is

- **Made only by the person**, with Approve on an inbox entry: Yes plus "Don't ask again" and a
  scope they pick. No agent tool, lead plan, cross-review or relayed message creates one. A later
  free-text grant is turned into this same structure and confirmed before it exists.
- **Waivable reasons**, closed list: `possible_callers` (the only one `small_cone` can have when
  its exact limits pass, its parse errors and unresolved removals are zero, and possible callers
  remain) and `protected_path` (the paths rule). Never failing or erroring tests, conflicts,
  test integrity, cross-review, run-test integrity, or a decision question: each behavior
  difference is new.
- **Exact scope, one dimension:** `module` (every changed file is that module's file), `path`
  (every changed file is under that prefix), `agent` (that agent's changes), or `run` (any change
  in this run). Matching is set membership and string equality on server data; no model judges a
  match. A grant covers one reason kind.
- **It expires** with the run by default; `session` (until the server stops) is the only other
  choice in this proposal. The maintainer may choose otherwise through a separately approved
  expiry contract. Nothing persists past a restart, and nothing is global.
- **Visible and revocable:** a grants list with Revoke; a change merged under a grant says so on
  its card, with the grant and when it was given.
- **Counted:** proof 2 reports questions asked and grants applied separately.

## Contract change (proposed contracts 1.7.0)

- `GrantReason = Literal["possible_callers", "protected_path"]`; `GrantScope`: `kind`
  (`module`, `path`, `agent`, `run`), `value`, `expires` (`run`, `session`).

  The one-to-one reason-to-rule mapping is fixed, not inferred from text:

  | GrantReason | RuleId |
  | --- | --- |
  | `possible_callers` | `small_cone` |
  | `protected_path` | `protected_paths` |

  `possible_callers` covers only the unknown described above, never an exceeded cone limit,
  parse error or unresolved removal. `protected_path` covers only the protected-path wait, never
  another rule's finding on the same file. `GrantUse.rule` records the mapped RuleId.

- `ApproveChange.grant: GrantRequest | None` (`reason`, `scope`). Refused with
  `invalid_request` unless the change's current wait has that reason and the scope covers the
  change it is given from.
- Events: `grant.created` (`grant_id`, `reason`, `scope`, `change_id`, `revision`,
  `head_commit`, `run_id`, `session_id`, `granted_by: "person"`) and `grant.revoked` (`grant_id`).
  The creation event's `ts` is when it was given. `grant.expired` (`grant_id`, `reason`:
  `run_ended` or `session_ended`) records the applicable lifecycle boundary for replay.
- `ChangeResolved.grants: list[GrantUse] | None` on rules approvals: `grant_id`, `rule`, and what
  was waived (for `possible_callers`, the caller ids, at most 20, and their measured total count).
  Each `GrantUse.authorization` is an immutable server capture: reason, scope, creation event seq
  and timestamp, run/session ids, `checked_at`, `active_at_effect`, and the assessment target
  (change_id, agent_id, revision and head_commit). It binds the grant and caller measurement to
  the exact assessment; missing or mismatched evidence cannot cover an unknown.
- Relax `ChangeResolved._rules_decision` in `contracts/src/oversight_contracts/events.py` and
  `validate_resolution_intent` in `backend/src/oversight/changes/_resolution_validation.py`:
  accept `pass` or a fully covered `unknown`, using the discriminator above, never `fail`.
  The former reads `ChangeResolved.grants`; the latter reads the identical immutable
  `WriteEvidence.grants` capture passed to `merges_alone`. Both validate the reason/rule mapping,
  counts, exact target and scope, and activity at `checked_at`, without reading mutable grant
  state. The transaction refreshes that capture from current server state at every barrier and
  at the serialized effect boundary, where `active_at_effect` must be true before Git changes.
  For validation after effects, `validate_resolution_evidence` requires the event's grants to
  equal the captured grants before repeating both pure checks. Retries and replay use the
  original capture: later expiry or revocation cannot invalidate a historical receipt or
  authorize a new merge.
- `POST /grants/{grant_id}/revoke`: `not_found` for an unknown id, `wrong_state` once revoked or
  expired. The active list derives from events and filters expired or revoked grants using the
  replay event clock and the recorded lifecycle boundaries (run ended or session ended), not
  the replay host's session. A restart never restores an earlier live session's grants; a
  recording missing its closing boundary treats remaining grants as inactive at its end.
  Historical card lines and use counters remain visible after expiry, distinct from active grants.

## Smallest version that helps proof 2

Backend (Codex, size M): the contract; `possible_callers` only (`protected_path` refused as not
yet supported); `module` and `run` scopes; run expiry only; grant creation on Approve; rules
admission through `pending_policy` and definitive authorization through `merges_alone` use the
grant-aware checks above, including the merge-time re-measure on the candidate tree; revoke.
Frontend (line B, size M): the "Don't ask again" control on the inbox entry, the grants list with
Revoke in the existing Settings tab, and the card line. The inbox uses the existing decisions
chip; both surfaces follow DESIGN.md as located in the shots below. Report: two counters in the
proof 2 summary.

At admission, the candidate re-measure, and every revalidation barrier, re-check that each grant
used is active and still covers the current wait and scope. Read current server state, never
just the admission snapshot. A revoke, expiry or the run ending stops the in-flight rules merge
like `authority_suspended`: keep the change waiting for the person, without a merge or grant-use
receipt. Revoke and run end serialize with the final authority check at the merge effect boundary;
expiry is checked there too. A grant created later does not revive the stopped reservation.

`path`, `agent`, `session` and `protected_path` follow when proof 2 shows they are needed.

## Order and what it displaces

1. Maintainer approves this plan and the contract change.
2. The `small_cone` rule and candidate re-measure are already on main. The caller-listing inbox
   entry remains to be built and approved; the rule landing is not a pending prerequisite.
3. Contract 1.7.0 and backend in one front: about half a day with review. It shares rules
   admission with [item 9](../additions.md#9-a-change-card-with-three-states)'s remaining
   decision-veto wiring into rules admission (the open model can add a wait, never approve), so
   grants go first and that wiring follows.
4. Line B's front after the shots below are approved, folded into its card-answers front
   because both answer inbox entries.

Before the Saturday checkpoint this moves item 9's decision-veto wiring and subsequent review
follow-ups back by about half a day. Those follow-ups are not enumerated in this repository;
the coordinator must name their findings and affected additions items before scheduling them.
This is a proposed capacity allowance, not a verified list of displaced fronts. Nothing on the
checkpoint list is dropped.

## Shots the maintainer approves first

These stay inside DESIGN.md's existing shell: the decisions chip opens the inbox, where the
person creates a grant; the grants list lives in the Settings tab in the focus panel, alongside
the standing merge choices. The graph remains visible. No new navigation surface is proposed.

1. The inbox entry for a possible-callers wait, with the callers marked "not followed", Approve,
   and the "Don't ask again" control open with its two scopes (this module, this run).
2. The grants list with one active grant and Revoke, and its empty state.
3. A change card merged under a grant: the grant, its scope and when it was given.

Each in dark and day themes at desktop width; the static build's phone width is unaffected, since
the recorded run has no grants.

## Later, not before the freeze

The person's past answers as examples for the open model's decision. That needs its own bench arm,
with the threshold set from development examples, never from the 20 bench tasks.
