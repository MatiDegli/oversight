# Lead wake implementation evidence

The implementation follows approved plan revision 3, items 2–5, and packet a-1102c
(2026-11-02). The mixed human/digest interpretation is recorded in DECISIONS.md under
“A lead wakes on its plan's events”. Contracts, generated contracts and frontend are unchanged.

## Base and prerequisite audit

Base: `1562f016859b6bce8e9d6c406ee5439edc65533d`. Contracts 1.17.0 landed in `1562f016`;
the configurable lead snapshot landed in `b1a372f5`; plan-size policy landed in `66a1516e`.
The event facade exports `is_lead_wake_trigger`, and every watcher candidate uses that facade.

One supplied prerequisite remains unresolved: the dispatch reference validator accepts a later
live reply with both correlation fields null. A direct fake-only live-store probe committed
two `LeadReply` payloads with `answers_seq=None, woken_by=None`, at seqs 1 and 2. The existing
`test_historical_later_null_replies_keep_validating` intentionally accepts historical examples,
but the live path also accepts them. `events/references.py:66` validates whichever correlation
is present without requiring one on later replies. This implementation emits exactly one on
every later successful turn, but does not change the shared validator or contract. That
prerequisite needs resolution before claiming review readiness.

The supplied export citations were re-found by content. Current locations after this change:

| Subject | Current location | Movement from supplied export |
| --- | --- | --- |
| Contract version / reply fields | `contracts/src/oversight_contracts/__init__.py:12`, `contracts/src/oversight_contracts/events.py:555` | Version line unchanged; fields now include `woken_by:562` |
| Reply reference validation / facade | `backend/src/oversight/events/references.py:66`, `backend/src/oversight/events/__init__.py:24,133` | Expanded validation and new predicate export |
| Defaults and configurable lead prompt | `backend/src/oversight/agents/_lead_policy.py:7,41` | Moved out of `_lead.py:36` / `_prompts.py:52` by configurable policy work |
| Lead ownership / launch / status / close | `backend/src/oversight/runtime_leads.py:46,140,156,304` | Previously `42,123,142,263` |
| Human commitment and launch / wake admission | `backend/src/oversight/runtime_lead_admission.py:55,78` | Previously publication at 47; digest take now follows open checks |
| Turn accounting / serialized finalization | `backend/src/oversight/runtime_lead_turns.py:86,118` | Previously `84,108` |
| Loop input / model messages / reply | `backend/src/oversight/agents/_lead.py:99,145,193` | Previously `97,135,183` |
| Distinct history / notice instructions / hashing | `backend/src/oversight/agents/_lead_history.py:12,45`, `backend/src/oversight/agents/_prompts.py:54,69` | History and prompt expanded; hashing entry moved from 75 |
| Frozen settings / selected configuration file | `backend/src/oversight/settings.py:49,186` | Previously `36,135` |
| Committed subscription | `backend/src/oversight/events/store.py:378` | Unchanged |
| Horizon from status reads | `backend/src/oversight/agents/_lead_tools.py:184` | Previously `169–174` |
| Suite / agent links / stable subtask ids | `contracts/src/oversight_contracts/events.py:118,494`, `contracts/src/oversight_contracts/plans.py:26,72` | Unchanged |
| Frontend retention / hidden row | `frontend/src/agents/conversation/selectLeadConversation.ts:23`, `frontend/src/agents/conversation/LeadThreadRows.tsx:22` | Same behavior; no status-notice row or TS attribution added |
| Budget and restart decisions | `DECISIONS.md:3717,3728` | Unchanged |

## Observable acceptance coverage

All tests use scripted clients or controlled publication barriers. No real coalescing sleeps
or provider calls are used. New lifecycle tests live in their own module to keep the existing
483-line lifecycle module below 500 lines.

| Requirement | Passing test names |
| --- | --- |
| Shared closed catalog, per-lead event-time ownership, completed summary, approval excluded | `test_watcher_uses_shared_catalog_and_isolates_leads`, `test_recorded_wake_fixture_matches_predicate_and_watcher` |
| Prefix order, replay without a gap or duplicate | `test_subscription_replay_and_human_sync_have_no_gap_or_duplicate`, `test_recorded_wake_fixture_matches_predicate_and_watcher` |
| Fixed five-second window, accumulation while working, committed-horizon repruning | `test_first_trigger_sets_fixed_five_second_window`, `test_running_turn_coalesces_then_reprunes_reply_horizon` |
| Human priority, post-commit digest take, mixed correlation, failure preservation | `test_waiting_human_wins_before_wake_claim`, `test_human_launch_takes_pending_only_after_message_commit`, `test_failed_human_publication_preserves_pending_triggers` |
| Running Send response and one claim | `test_send_during_wake_returns_409`, existing `test_two_concurrent_messages_have_exactly_one_claim` |
| Wake/turn counts, provider brake, cap, last human turn | `test_wake_counts_once_and_preserves_last_human_turn`, `test_refusal_decision_and_publish_hold_idle_lock` (brake/cap/last-turn) |
| Refusal decision and durable publication hold the idle lock; revision and unchanged state | `test_refusal_decision_and_publish_hold_idle_lock` |
| Open/idle recheck after lock acquisition; Stop/shutdown precedence | `test_refusal_rechecks_open_and_idle_under_lock`, `test_wake_stop_shutdown_races_preserve_terminal_ownership` (admission/reply/refusal × Stop/shutdown) |
| One refusal per reason until launch, stale Stop, retry after failed publication | `test_refusal_once_per_reason_until_next_turn`, `test_failed_refusal_publication_keeps_pending_and_retryable_notice` |
| Server notice distinct from speech, no human event or digest status event | `test_wake_digest_is_server_notice_without_agent_message`, `test_server_notices_trim_with_exchanges_without_becoming_human` |
| Masking, escaping, whole-line omission counts, exact 4,096-character bound, stable subtask labels | `test_digest_is_fenced_masked_bounded_and_counts_omissions`, `test_digest_labels_stable_subtasks_and_plan_wide_completion` |
| Sorted correlation includes omitted lines, real reference validation and no reuse | `test_wake_reply_reports_sorted_all_consumed_seqs` |
| Closed tools, propose-only authority, accounting role and reset per-turn limits | `test_wake_preserves_tools_accounting_and_per_turn_limits`, `test_wake_digest_is_server_notice_without_agent_message`, existing lead limits/lifecycle tests |
| Prompt-version movement and configured snapshot | `test_server_notice_paragraph_moves_prompt_versions`, `test_thread_builds_derived_policy_once_and_passes_its_snapshot` |
| Retained watcher ownership, terminal cancellation with store open, close before gathers, initialization race | `test_terminal_lead_cancels_open_watcher`, `test_close_cancels_watchers_before_gathers_with_store_open` |
| Wake client closed once, no idle resurrection, no subscription or timer left | `test_shutdown_during_wake_closes_client_once`, `test_wake_stop_shutdown_races_preserve_terminal_ownership` |
| Fresh restart without pending wakes or model call | `test_restart_discards_pending_wakes_and_calls_no_model` |
| Default 3, zero disabled, unlimited nonnegative setting, invalid values, environment precedence, one run snapshot | `test_wakes_default_zero_unbounded_and_invalid_values`, `test_wakes_resolved_once_with_run_policy`, `test_zero_disables_wake_execution_and_human_still_takes_digest` |

## Shared fake recording

`fixtures/events/recorded-lead-wakes.jsonl` is an unchanged SQLite export from a fake-only run.
Its explicitly marked expected results are `fixtures/events/lead-wakes.expected.json`, with
objects `{lead_id, trigger_seq}` for future TypeScript tests. Coverage includes two lead owners,
combined failed/error/timeout outcomes, noncombined and passed suites, standalone agents,
approval waiting, completed/failed plans and an agent reassignment after a suite event.
There is no TypeScript parity claim. The test's optional recording mode regenerates the run,
exports it through `export_run`, then tests the exported bytes; it never edits envelopes.

SHA-256:

- Recording: `07b8c83c07ce7e22623a790763ca2f6ab680bf8750881c587eb441b6f6b3f7c5`
- Expected: `aaba7265ebfd967595a54655095caf3f2c24310af9de7331e8a53f87dce2f2eb`

## Targeted validation

The bounded fake-only regression run passed **643 tests** with `-n 2`:

```sh
bash tools/bounded.sh 240 env -u META_API_KEY -u BASETEN_API_KEY uv run --frozen pytest -n 2 \
  backend/tests/api/test_lead_wakes.py backend/tests/api/test_lead_wake_lifecycle.py \
  backend/tests/api/test_lead_messages.py backend/tests/api/test_lead_races.py \
  backend/tests/api/test_lead_lifecycle.py backend/tests/api/test_leads.py \
  backend/tests/agents/test_lead_wakes.py backend/tests/agents/test_lead_history.py \
  backend/tests/agents/test_lead_loop.py backend/tests/agents/test_lead_limits.py \
  backend/tests/agents/test_lead_replies.py backend/tests/events/test_lead_wake_parity.py \
  backend/tests/events/test_lead_wake.py backend/tests/events/test_lead_reply.py \
  backend/tests/events/test_lead_status.py backend/tests/test_lead_wake_settings.py \
  backend/tests/test_lead_settings.py backend/tests/test_lead_policy_snapshot.py \
  contracts/tests/test_lead_reply.py --tb=short
```

Targeted `ruff check`, `ruff format --check`, and `pyright` passed for every changed Python
file. `pnpm -C frontend exec tsc --noEmit` passed. `git diff --check` passed. Commit hooks run
secret scanning, targeted Ruff and commit-message/size validation. The full `make check`,
repository-wide lint and frontend related Vitest/ESLint are not run: the requested scope is
targeted checks, and no frontend file changes in this implementation.

## Limits and remaining integration

The watcher retains the committed prefix required by the shared predicate per lead; this has
run-local memory and predicate-scan costs. History and model digests are bounded independently.
Frontend notice rendering and attribution remain future work. Full integration gates and an
independent review remain outside the targeted check run.

No changed Python file exceeds 500 lines and no changed Python function exceeds 80 lines.
The existing documentation files `ARCHITECTURE.md`, `DECISIONS.md` and `docs/additions.md`
remain above 500 lines: their existing lifecycle, decision log and inventory are updated in
place to keep the authoritative records together. These are the only size exceptions.
