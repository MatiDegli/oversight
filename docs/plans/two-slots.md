# Plan: two agents in parallel, slots A and B, level 2

Revision 2, after the review of 8805b4d (two blocking, four should-fix, one nit), plan only, for
an independent review before any code. Sources: AGENTS.md, docs/proposal.md (the slot row, 4.7's
`POST /agents`), `contracts/` 1.1.0, main 9872baa, docs/plans/api.md, docs/plans/changes.md,
docs/plans/request-changes.md at 806b495. No new module, dependency or contract field; risks K1
to K20 are in section 9.

## 1. What main has, and what is missing

- `app.py` runs one slot: `SLOT = "A"`, `_check` refuses any other, one `_slot`, one `profile`,
  `_metered` and `_pricing`, and `run.started.slots` is `[A]`. `_runs` (a dict) and `_stop_runs`
  already handle several runs; `_change_ids` is already per agent.
- The manager keeps records per agent, creates worktrees and decides under main's lock, then the
  agent's: approvals serialize, one delta each; a textual clash is `Conflict(kind="merge")`. **Not
  on main**: the combined tests when main moved, R5 of the request-changes plan (`changes/request`).
  Until it lands, approving B after A merges a clean B with no suite run on the combination, so B
  stays closed until it does (section 7, K15).

## 2. Slot state in the app (`app.py`)

```python
SLOTS: tuple[Slot, ...] = ("A", "B")
@dataclass(frozen=True)
class _Agent:           # kept for the server's life: a late tick or error always finds it
    slot: Slot; change_id: str; profile: ModelProfile; metered: bool   # provider == "meta"
OFFERED_SLOTS: tuple[Slot, ...] = ("A",)        # module constant: B opens after R5 (section 7)
self._slot_profiles: dict[Slot, ModelProfile]   # one per OFFERED_SLOTS entry, in order
self._slots: dict[Slot, str | None]             # None, "starting", or the holder's agent id
self._agents: dict[str, _Agent]                 # replaces _change_ids
```

- **`_check(request, manager) -> tuple[Slot, ModelProfile]`**, side-effect-free. An explicit
  `request.slot` is honored: not offered is `invalid_request`, taken is `slot_busy` ("slot B is
  taken (agent-2)"), braked is `spend_brake`. Omitted, the pick is the first *free* offered slot,
  A before B, **whose start checks pass** (model, key, brake, in today's order, each on that slot's
  profile): so with A braked and B unmetered, a start with no slot goes to B. If no free slot
  passes, the first free slot's error is raised; if none is free, `slot_busy` "both slots are
  taken (...)". The task box always sends its slot (section 6); this rule is for API callers and
  the box's preselection, which share it (K19).
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
- `__init__` checks each offered slot's model against `PROFILES` (A's `ValueError` today, naming
  the variable). A fake slot loads its own script; a fake B without one is not offered (closed
  below, K7).
- The request's `model`, per slot: omitted is the slot's default; given, it must be that default
  or an offered profile whose provider is not `fake`, and not in `UNVERIFIED_PROFILES`. A fake
  profile belongs to the slot that owns its script, so no other slot can take it: with A on Meta
  and B on fake with script B, "fake on A" is `invalid_request` ("slot A has no fake script"),
  "fake on B" and "muse-spark on B" pass. The key check follows the provider (K20).
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
- Order: the loop records into the shared `UsageLedger` synchronously; `_persist` starts `record`
  at once, so the file's FIFO lock queues the writes in tick order, and after its wait nothing
  suspends before the store's unbounded queue takes the tick: `spent_usd` never decreases along
  `seq` (K6).
- **A paid call is never dropped by a cancel.** `LedgerFile.record` only protects its write: a
  cancel while it waits for the lock A holds raises before anything is written, and B's answer is
  lost with the brake still open. `_persist` therefore runs `record` as its own task and waits
  with `asyncio.wait`, which never cancels it, looping until the task is done (one pass per
  cancel; no other loop). Afterwards, a failed write closes the brake as today
  (`_ledger_gone`), and a cancel that came meanwhile is re-raised, so the run still stops; the
  tick is not published, as for a cancel during the write today, and the file holds the spend
  (K16). Shutdown waits for these writes: each run's task awaits its own.
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
  once, so both changes end before `aclose` and the store closes last (K13). A run task cancelled
  before its first step never reaches its `finally`: `_stop_runs` ends those changes itself
  (section 7, K17).

## 6. Frontend (front `frontend/two-slots`, after the backend; `make shots`)

- Already slot-aware, tests only: `startBlock` reads `run.slots`; cards, rail and graph marks
  (`selectors.ts`, `graph/styles.ts`) take the `agent-b*` theme tokens; `view.ts` orders pending
  changes by slot.
- **Task box: a slot choice, shown only when `run.slots` has two entries.** Two buttons under the
  field, "A  Muse Spark" and "B  Muse Spark Open": the letter in the slot's text token and the
  model's `label` from `run.models`, so each slot's model is on screen and color is never alone.
  The chosen slot is sent as `StartAgent.slot` (the contract has it). The preselection is
  `nextSlot(state)`: the first slot not held (`holdsSlot`) whose profile is not braked, i.e. not
  `meta` while `selectSpend(state).brake === "blocked"`, the server's rule from section 2. The
  person's click overrides it until that slot is taken. A held slot's button is disabled and
  names its holder; a braked one stays selectable and the server's `spend_brake` message shows.
  With one slot offered the box is as today. So the person reaches B by the preselection when A
  is braked, or by clicking it.
- **Meter fix** (`applyEvent.ts`, `usage.tick`, `spend.ts`). Every tick, from any agent, carries
  the shared ledger's `spent_usd` and `brake`, and the latest one is the freshest: if A's Meta
  spend reached the file but A was cancelled before publishing its tick, a later Baseten or fake
  tick is the only one that tells the meter the new total and the brake, and the slot
  preselection (`nextSlot`) reads that brake. So `usage` takes every tick, as today, and the
  total and brake come from it whoever sent it. What changes is when the total reads "unknown":
  `selectSpend` showed it whenever the latest tick had `cost_usd` null, which is right for a Meta
  call without usage and wrong for a Baseten or fake tick, whose per-call cost is always null and
  says nothing about the total. A new state field, `spendUnknown`, is set by each tick of an agent
  that is not known to run a non-`meta` profile (its latest `agent.status.model`, looked up in
  `run.models`; an agent not yet in state counts as `meta`, so an odd order shows "unknown",
  never a wrong total) to `cost_usd === null`; a tick of a non-`meta` agent leaves it as it was.
  `selectSpend` reads the total and brake from `usage` and `spentUsd` is null only when
  `spendUnknown` is set. Replay uses the same function. Per-agent usage of unmetered agents waits
  for level 3's usage panel.

## 7. How this lines up with the request-changes plan in `app.py`

- That plan adds `request_changes`, `stop`, `_revise`, `_decisions`, the snapshot and drain in
  `_stop_runs`, `run_ended(..., steps=)` in `_run`'s `finally`, and keeps each `AgentRun` and its
  client until the change is rejected or merged. `_runs` and `_stop_runs` are on main already.
- This plan edits the slot constant, `__init__`'s fields, `startup`, `_publish`, `_ledger_gone`,
  `_default_client`, `_check`, `start`, `_run`'s `pricing=` line and `_halt_for_ledger`'s body.
  Only `__init__` overlaps: `pricing=` is 16 lines above the `finally`; `_stop_runs` is
  touched once, below.
- `_stop_runs` is touched once: after the gather, a loop `for agent_id in runs:
  manager.run_ended(agent_id, None)` over the snapshot taken before the cancels (K17). It is the
  fallback the request plan's R8 already describes ("if the drained change is then `working`,
  `_stop_runs` itself calls `run_ended(agent, None, steps=)`"), written once: the first to land
  writes the loop, the other widens it to the kept `AgentRun`s and adds `steps=`. `run_ended` is a
  no-op unless the change is `working`, so it never overrides a run's own end.
- Seams: its Request changes brake check asks `_agents[agent].metered` first; its kept `AgentRun`
  gets its own dict (`_Agent` stays frozen); `test_request_changes_keeps_the_slot` reads `_slots`.
- **Order, and the gate on B.** The slot, profile, spend and shutdown work lands first: it needs
  only main, while `api/request` waits on three manager and loop fronts (request plan §7, step 4)
  and then branches from main with this merged: no app.py merge. B stays closed until R5: the
  constant `OFFERED_SLOTS = ("A",)` makes `run.started.slots` `[A]`, `slot: "B"` `invalid_request`
  (as on main today) and the box show no choice. Tests that need B set it with a fixture
  (`monkeypatch.setattr(app, "OFFERED_SLOTS", ("A", "B"))`). `test_slot_b_is_closed_until_r5`
  pins the closed state (K15). The opening commit, on a branch cut from main after
  `changes/request` merged, sets `("A", "B")`, drops the fixture and replaces that test with the
  e2e `test_a_then_b_with_a_failing_combination` (K15): two runs on a temporary workspace whose
  one test calls both edited functions, each edit green alone; approve A, then B: `Conflict(
  kind="tests")`, main unchanged, B keeps its slot. It fails without R5, so the opening commit
  cannot be green on a branch that lacks it. Until then no run can have two agents.

## 8. Contracts

No change: `StartAgent`, `AgentCreated`, `SlotInfo`, `run.started`, `agent.status.slot`,
`usage.tick` and `slot_busy` cover it. One proposal, not blocking: `StartAgent.slot`'s docstring
could say "the first free slot that can start, A before B", the rule the task box preselects by.

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
- K16 a cancel in the ledger lock's wait loses a paid call:
  *`test_a_cancel_waiting_for_the_ledger_lock_keeps_the_spend`, parametrized on B's write ok or
  failing. Two metered agents; the ledger's lock is a test subclass of `asyncio.Lock` that sets an
  event when `acquire` finds it held; A's write is held on a `threading.Event`. B's tick reaches
  the lock (the event), B's run task is cancelled, A's write is released, both runs are awaited.
  Ok: the file's `spent_usd` is start + A + B. Failing (the second `write` raises): the brake
  reads closed (`brake_reason()` not None), B ends halted with its own profile, and a fatal
  `error` went out. Without the task around `record`, B's cost is missing in the first case and
  the brake open in the second.
- K7 *`test_run_started_lists_the_offered_slots` (with the fixture `[A, B]`; a fake B with no
  script: `[A]`).
- K8 B's halt names A's model or change: *`test_a_lost_ledger_halts_b_with_its_own_profile`.
- K9 one client for two agents: *`test_each_start_builds_its_own_client` (two scripts, two edits).
- K10 endpoints mixed up: *`test_the_client_endpoint_follows_the_provider` (`test-key`, no call).
- K11 interleaving: `test_two_agents_interleave_in_one_log` (gated models released in turn on
  `agent.step`: `seq` contiguous from 1, each agent's steps in order, envelopes validated).
- K12 `test_back_to_back_approvals_chain_their_deltas`; the combination's tests are K15's e2e.
- K13 `test_shutdown_mid_two_runs_ends_both` (e2e: both gated mid-call; both ended, none alive).
- K17 a run task cancelled before its first step skips `_run`'s `finally`:
  *`test_shutdown_before_a_run_starts_ends_the_change`: `start` returns with no suspension after
  `create_task`, so the test calls `shutdown` at once, the task never ran; the change is
  `unproposed` before `aclose`, none `working`, `_runs` empty. Without the loop it stays `working`.
- K14 frontend: *`nextSlot` in `taskBox.test.ts` (A held: B; both held: none; A merged: A; A
  Meta and blocked, B not Meta: B; both Meta and blocked: A); two interleaved agents' cards and
  marks (`state.test.ts`), B's token classes, the latest total.
- K15 B opens before R5: *`test_slot_b_is_closed_until_r5` (`run.started.slots` is `[A]`, `slot:
  "B"` is 422, with the real constant), then the e2e above in the opening commit.
- K18 the meter hides or loses Meta's total: *`selectSpend` in `spend.test.ts` and
  `state.test.ts`: a Meta tick (cost 0.02, spent 3.0), then a Baseten tick (cost null, spent
  3.0): 3.0, not unknown; a Meta tick skipped (never published), then a Baseten tick with spent
  3.5 and brake `blocked`: the total reads 3.5 and `selectSpend(...).brake` is `blocked`, so
  `nextSlot` leaves a Meta slot A and takes B; same through a fake tick; a Meta tick with cost
  null: unknown, and a later non-Meta tick keeps it unknown with its new total held; replay of
  the same events gives the same.
- K19 the automatic pick fails on a braked A: *`test_an_omitted_slot_skips_a_braked_one` (A and B
  Meta through the factory, ledger past the block: both braked, 403 with A's reason; with B fake:
  201 in B; explicit `slot="A"` while braked: 403, not rerouted). The box's side is K14's table.
- K20 slot and model combinations: *`test_slot_model_combinations` (A Meta, B fake with script B:
  fake on A 422, fake on B and muse-spark on B 201; A Meta, B Baseten: each takes the other's
  profile; an unverified profile 422; fake A alone: B 422).

## 10. Order of work

1. `agents/two-slots` (backend), three commits, B closed: (a) slots, profiles, clients, the
   pick and the combinations, `app.py` about 110 lines, `settings.py` 15, `.env.example` 4, tests
   300; (b) spend: the ledger task and the brake per profile, `app.py` about 40, tests 150; (c)
   `_stop_runs`' fallback, about 6 and 60. Tests that move: `_slot` to `_slots["A"]`; the race
   needs a third start; those needing B use the fixture. B's fake script (a demo-repo function
   other than A's, 40 lines) comes with (a).
2. `frontend/two-slots`, after 1 (it needs no gate: the choice shows only when `run.slots` has
   two entries): the box's choice and `nextSlot` about 70 lines, the meter's unknown mark 15,
   tests 220, then `make shots` (the box with one slot and with two, A braked).
3. `agents/two-slots-open`, cut from main once `changes/request` (R5) has merged: the constant,
   the fixture's removal, the e2e of section 7. About 20 lines and 120 of tests.

## 11. Open questions, for the maintainer

1. Muse Spark Open on Baseten, metered against Meta's ledger? Proposed: no, as the review agreed;
   the brake guards Meta's credits, Baseten ticks carry `cost_usd` null and never write the file,
   and its own limit waits for its quota. The meter keeps their total and brake, and only
   their null per-call cost is not read as unknown (section 6, K18).
2. Who picks the slot. Proposed: the person, with two buttons that name each slot's model,
   preselected by the server's rule (first free slot that can start); the API's omitted slot
   follows the same rule. The alternative, a server-only pick with a line under the box, cannot
   reach B when A is braked and the models differ.
3. `StartAgent.model`. Proposed: the slot's default, or an offered non-fake profile; fake stays
   with the slot that owns its script (section 3). The narrower option, only the slot's default,
   drops K20's cross cases; the UI sends none either way.
4. Opening B waits for R5 (section 7). Proposed: B closed by a constant until `changes/request`
   merges, then opened by its own small branch. The alternative is to hold this whole front back.

Closed, from the review: a fake B with no `OVERSIGHT_FAKE_SCRIPT_B` is not offered (section 3,
K7): reusing A's script would propose the same edit, and the second approval would conflict.
