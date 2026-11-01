# Contracts 1.4.0 delivery A review notes

> Status: done. Delivered in `304a96c`; historical review notes for that delivery only.
> Later producers and routes have landed; see api.md and additions.md for current status.

Delivery A changes the existing decision path and reserves the later level 3 wire shapes.
It does not enable tree traversal, safe creation, automated reviews, facts producers, queue
execution, plans, leads or Revert. SaveFile retains its required string base_hash.

App constructs the observed guard from its callback's render state and passes that guard to
the decision sender. Frame keeps its existing callback signature. Frame.tsx and GraphView.tsx
are owned by another front and have no changes here; README.md and DEVLOG.md also stay untouched.

## Proposal text for maintainer review

Only the command table in section 4.7 and the exception in section 4.9 change:

- The command table adds the optional start zone, required revision/head decision guards and
  the reserved agent/change views, review, Revert, history, queue, lead and runtime-tree routes.
  The tree row records the required nullable hash, joint cap and 501 reservation.
- Section 4.9 records the bounded enum/guard exception at 1.4.0 and the later required nullable
  SaveFile hash together with safe creation, zones and inventory, taking the next free version
  after its approval. Version strings
  cannot identify multiple schemas.

## Review signals for size

These are review signals, not new gates. Generated data and the immutable baseline intentionally
retain the required complete artifacts. Existing scenario suites keep their related assertions
and fake setup together; this migration adds fields and guards to those scenarios.

| File above 500 lines | Reason it remains together |
| --- | --- |
| `backend/src/oversight/app.py` | Existing composition root owns server configuration, registration, ledger and command dispatch; this change adds only the required wiring. |
| `backend/tests/api/test_brake.py` | Existing spend/ledger refusal scenarios require valid guarded decision requests for cleanup. |
| `backend/tests/changes/test_main_edit.py` | Existing main-edit transaction and recovery scenarios now send observed guards. |
| `backend/tests/changes/test_request_changes.py` | Existing cancellation, base-move and recovery matrix retains its scenario helpers and assertions. |
| `contracts/baselines/1.3.0.schema.json` | Required byte-exact immutable schema from the approved Git object. It was committed in three append-only parts to respect the size hook; the final bytes match the pinned digest. |
| `contracts/generated/contracts.ts` | Complete output of the repository generator, with unchanged tool pins. |
| `contracts/generated/oversight.schema.json` | Complete generated schema containing the reserved contracts. |
| `contracts/src/oversight_contracts/events.py` | The approved public home of SuiteResult, SuiteExecution, RevertResult, LeadStatus and all event envelopes; splitting their public homes would depart from the approved layout. |
| `docs/proposal.md` | Existing proposal; edits are confined to its command table and section 4.9 for maintainer review. |
| `frontend/src/graph/graph.test.ts` | Existing graph scenario suite receives the added nullable status fields; GraphView.tsx is unchanged. |
| `frontend/src/state/state.test.ts` | Existing reducer/replay scenario suite receives recursive defaults and retains its continuity assertions. |
| `frontend/src/transport/transport.test.ts` | Existing transport suite additionally checks complete guarded JSON and admission of all six reserved event types. |
| `tools/tests/test_demo_check.py` | Existing phase/deadline tests now verify exact guard bodies and seeded proposal heads. |

`backend/tests/e2e/test_two_agents.py:test_a_then_b_with_a_failing_combination` is 84 lines.
The added observed guards keep one complete two-agent integration scenario together, including
the rejected combined result and cleanup. No product function crosses 80 lines.

## Validation boundaries

The writer freezes serialized data and revalidates each candidate with live context only when
configured live. A semantic refusal consumes no seq and reaches only its publisher. Accepted
neighbors, including earlier accepted references in the same batch, commit together. An unexpected
writer failure closes admission and resolves waiting publishers with an error.

Producer-owned preflight checks capture authority before Git effects. The original immutable
capture survives a moved base or recovery. A post-effect EventRejected is terminal for that run;
Git facts and the unsent payload remain available, and the worktree and occupied slot are retained.
The future owners validate full cross-review, facts, queue, lead and Revert evidence when their
producers are built. Replay configuration and fake-provider authorization are independent.

The array gate inspects changed restrictions generically against the pinned baseline. Its negative
fixtures are freshly generated using the repository generator; legacy, generic-max, generic-min
and existing-min each must report its concrete tuple field with exit 1. The current schema must
pass with exit 0, and the plain-array TypeScript consumer compiles without casts.

M6 adds too_large (HTTP 413) to unshipped 1.4.0; the API guard front adds the size limits later.
