# Plan: two agents in parallel, slots A and B, level 2

Revision 1, plan only, for an independent review before any code. Sources: AGENTS.md,
docs/proposal.md (the slot row, 4.7's `POST /agents`), `contracts/` 1.1.0, main 9872baa,
docs/plans/api.md, docs/plans/changes.md, docs/plans/request-changes.md at 806b495. No new
module, dependency or contract field; risks K1 to K14 are in section 9.

## 1. What main has, and what is missing

- `app.py` runs one slot: `SLOT = "A"`, `_check` refuses any other, one `_slot`, one `profile`,
  `_metered` and `_pricing`, and `run.started.slots` is `[A]`. `_runs` (a dict) and `_stop_runs`
  already handle several runs; `_change_ids` is already per agent.
- The manager keeps records per agent, creates worktrees and decides under main's lock, then the
  agent's: approvals serialize, one delta each; a textual clash is `Conflict(kind="merge")`. **Not
  on main**: the combined tests when main moved, R5 of the request-changes plan (`changes/request`).
  Until it lands, approving B after A merges a clean B with no suite run on the combination (K12).

## 2. Slot state in the app (`app.py`)

```python
SLOTS: tuple[Slot, ...] = ("A", "B")
@dataclass(frozen=True)
class _Agent:           # kept for the server's life: a late tick or error always finds it
    slot: Slot; change_id: str; profile: ModelProfile; metered: bool   # provider == "meta"
self._slot_profiles: dict[Slot, ModelProfile]   # the offered slots, in order
self._slots: dict[Slot, str | None]             # None, "starting", or the holder's agent id
self._agents: dict[str, _Agent]                 # replaces _change_ids
```

- **`_check(request, manager) -> tuple[Slot, ModelProfile]`**, side-effect-free: the slot is
  `request.slot`, else the first free offered one (A before B), else A; not offered is
  `invalid_request`. Today's checks follow in today's order on that slot's profile or the requested
  model, then `slot_busy`: "slot B is taken (agent-2)", or "both slots are taken (...)".
- **Claim**: `start` sets `_slots[slot] = "starting"` with no `await` after `_check`; a failure or
  cancel resets that slot only. After `open`, with no `await` up to the task's creation:
  `_slots[slot] = agent_id`, `_agents[agent_id]`, the run task. `AgentTask.slot` and
  `AgentCreated.slot` are the resolved slot; agent ids keep one counter across slots.
- **Release** in `_publish`, once the store took it: a `change.resolved` for a slot's holder,
  `rejected` (a Stop's included) or `merge == "merged"`, frees that slot. `changes_requested` and an
  approval with `merge == "conflict"` keep it, as today.

## 3. Profiles, settings and clients

- `Settings` gains `agent_model_b` (`OVERSIGHT_AGENT_MODEL_B`, unset: A's model) and
  `fake_script_b` (`OVERSIGHT_FAKE_SCRIPT_B`), both in `.env.example`. B runs Muse Spark now; once
  the Baseten probe adds Muse Spark Open to `PROFILES`, the variable alone moves B.
- `__init__` checks each slot's model against `PROFILES` (A's `ValueError` today, naming the
  variable). A fake slot loads its own script; a fake B without one is not offered (question 4).
- The request's `model` must be an offered profile (`run.started.models`) and not in
  `UNVERIFIED_PROFILES`; the key check follows that profile's provider.
- One client per start: `ClientFactory` becomes `(profile, slot) -> ModelClient`, and the default
  reads a provider table: meta, `META_BASE_URL` and Meta's key; baseten, `BASETEN_BASE_URL` and
  Baseten's key; fake, a new `ScriptedModel` on that slot's script. Test factories take the slot.
- The card's model comes from the client's profile (`loop.py`), unchanged; `_halt_for_ledger`
  reads `_agents[agent].profile.id`, not `self.profile.id`.

## 4. Spend: one ledger, one total, the brake per metered profile

- One `LedgerFile` and one `UsageLedger` per process, as today; `braked` if any offered slot is
  metered. Each tick's `spent_usd` is the shared total: the meter reads one number whoever sent it.
- Metered means the agent's profile is `meta`, the credits the brake guards. `_check` asks
  `brake_reason()` only for a metered profile, so a fake slot is never refused. `_publish` writes a
  tick to the ledger file when `_agents[tick.agent_id].metered` (set before the run task exists),
  not on a server-wide flag; `_run` passes `pricing` to a metered agent only.
- Order: the loop records into the shared `UsageLedger` synchronously; `_persist` waits on the
  file's FIFO lock, and after its release nothing suspends before the store's unbounded queue
  takes the tick, so `spent_usd` never decreases along `seq` (K6).
- A lost ledger write closes the brake for metered starts and halts the agent whose tick failed;
  the other metered agent halts at its next tick, an unmetered one runs on. The brake still
  checks only at start (AGENTS.md), with two agents spending up to twice as fast.

## 5. `run.started`, interleaving, shutdown

- `models`: the offered profiles, deduplicated in slot order (`[muse-spark]` when both run it);
  `slots`: one `SlotInfo(slot, default_model)` per offered slot. Limits per agent, budget shared.
- Both runs publish through the store's single writer: `seq` stays strictly increasing and
  contiguous, and each agent's events keep their order (one task per run). Git on main is under
  main's lock and each worktree under its own; both runs' `run_tests` use the one demo venv.
- Approvals clicked back to back: the second waits on main's lock and its delta starts at the
  first's `to_version`. Same lines edited: the second gets `Conflict(kind="merge")`, keeps its slot.
- Shutdown: `_stop_runs` cancels and gathers every run; each `_run`'s `finally` calls `run_ended`
  once, so both changes end before `aclose` and the store closes last. No change; K13 pins it.

## 6. Frontend (front `frontend/two-slots`, after the backend; `make shots`)

- Already slot-aware: `startBlock` reads `run.slots`; cards, rail and graph marks (`selectors.ts`,
  `graph/styles.ts`) take the `agent-b*` theme tokens; `view.ts` orders pending changes by slot;
  the meter reads the latest tick's shared total. Only tests are added there.
- Task box: it still sends only the task, and the server picks the slot. One new line under it
  names that slot, "Starts agent B", the letter in B's text token, from `nextSlot(state)` beside
  `startBlock`, sharing `holdsSlot`. No slot picker (question 2).

## 7. How this lines up with the request-changes plan in `app.py`

- That plan adds `request_changes`, `stop`, `_revise`, `_decisions`, the snapshot and drain in
  `_stop_runs`, `run_ended(..., steps=)` in `_run`'s `finally`, and keeps each `AgentRun` and its
  client until the change is rejected or merged. `_runs` and `_stop_runs` are on main already.
- This plan edits the slot constant, `__init__`'s fields, `startup`, `_publish`, `_ledger_gone`,
  `_default_client`, `_check`, `start`, `_run`'s `pricing=` line and `_halt_for_ledger`'s body.
  Only `__init__` overlaps: `pricing=` is 16 lines above the `finally`; `_stop_runs` is untouched.
- Seams: its Request changes brake check asks `_agents[agent].metered` first; its kept `AgentRun`
  gets its own dict (`_Agent` stays frozen); `test_request_changes_keeps_the_slot` reads `_slots`.
- **This front lands first.** It needs only main, while `api/request` waits on three manager and
  loop fronts (request plan §7, step 4) and then branches from main with this merged: no app.py
  merge. If it branched earlier, its fields go at the end of `__init__` and `_slot` becomes
  `_slots`. "Approve A, then B" at level 2 needs R5 from `changes/request`; this adds none.

## 8. Contracts

No change: `StartAgent`, `AgentCreated`, `SlotInfo`, `run.started`, `agent.status.slot`,
`usage.tick` and `slot_busy` cover it. One proposal, not blocking: `StartAgent.slot`'s docstring
could say "the first free slot, A before B", the rule the task box's line relies on.

## 9. Risks, each with the test that catches it (* shown failing with the change reverted)

- K1 two starts claim one slot: *`test_concurrent_starts_take_a_then_b_then_busy` (`_venv` an
  unresolved future, three starts in flight: A, B, then 409; the future resolves, two runs).
- K2 a failed or cancelled start frees the wrong slot: *`test_a_failed_open_in_b_frees_only_b`.
- K3 a resolution frees the wrong slot, or on a conflict: *`test_a_resolution_frees_only_its_slot`.
- K4 slot rules: `test_slot_rules` (omitted with A held: B; B not offered: 422; both held: 409).
- K5 the brake on the wrong profile: *`test_the_brake_refuses_the_metered_slot_only` (A fake, B
  Muse Spark through the factory, ledger at 136 of 150: B 403, A 201).
- K6 ticks persisted by a server flag, or two totals: *`test_only_metered_ticks_reach_the_ledger`,
  *`test_two_agents_share_one_growing_total` (non-decreasing along `seq`, last = start + both).
- K7 *`test_run_started_lists_the_offered_slots` (`[A, B]`; a fake B with no script: `[A]`).
- K8 B's halt names A's model or change: *`test_a_lost_ledger_halts_b_with_its_own_profile`.
- K9 one client for two agents: *`test_each_start_builds_its_own_client` (two scripts, two edits).
- K10 endpoints mixed up: *`test_the_client_endpoint_follows_the_provider` (`test-key`, no call).
- K11 interleaving: `test_two_agents_interleave_in_one_log` (gated models released in turn on
  `agent.step`: `seq` contiguous from 1, each agent's steps in order, envelopes validated).
- K12 `test_back_to_back_approvals_chain_their_deltas`; the combination before R5: the request
  plan's `test_combined_tests_after_main_moved`.
- K13 `test_shutdown_mid_two_runs_ends_both` (e2e: both gated mid-call; both ended, none alive).
- K14 frontend: *`nextSlot` in `taskBox.test.ts` (A held: B; both held: none; A merged: A);
  two interleaved agents' cards and marks (`state.test.ts`), B's token classes, the latest total.

## 10. Order of work

1. `agents/two-slots` (backend): `app.py` about 130 lines, `settings.py` 15, `.env.example` 4, a
   fake script for B on another demo-repo function 40, tests 450; two commits, tests as api.md §7.
   Tests that move: `_slot` to `_slots["A"]`; "slot B is 422" becomes 201; the race needs a third.
2. `frontend/two-slots`, after 1: about 40 lines and 100 of tests, then `make shots`.

## 11. Open questions, for the maintainer

1. Muse Spark Open on Baseten, metered against Meta's ledger? Proposed: no; the brake guards
   Meta's credits, Baseten ticks carry `cost_usd` null and never write the file, and its own limit
   waits for its quota. The meter (unknown on a null cost) then skips them, with B's move.
2. Who picks the slot. Proposed: the server (A before B), with the box naming it; a picker adds a
   control for a choice that changes only the color.
3. `StartAgent.model`. Proposed: any offered profile, so a start through the API can put B on
   Muse Spark without a restart (the UI sends none); or only the slot's default.
4. A fake B with no `OVERSIGHT_FAKE_SCRIPT_B`. Proposed: B not offered, so today's fake configs
   keep one agent; or B reuses A's script, whose second approval then conflicts.
