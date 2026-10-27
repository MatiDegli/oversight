# Plan: Request changes, Stop, and the person's edits in review, level 2

Revision 2, plan only, for an independent review before any code. It answers review 1 (one
blocking, seven should-fix, one nit); each answer is marked R1 to R9 where it lands. Sources:
AGENTS.md, docs/proposal.md (4.4, 4.5, 4.7, 4.9, 5), `contracts/` 1.1.0, docs/plans/changes.md
revision 8, `changes/` at 0657c5a (main 352bc85 changed only the frontend), `agents/loop.py`, and
`api/wiring` 56c28ee (`app.py`, `api/commands.py`). No new module, dependency or contract field.

## 1. Request changes (`changes/_manager.py`)

After the stage split, `_finish_pending` is `_delivery.finish_pending` and `_recover` is `_recovery.recover`.

```python
@dataclass(frozen=True)
class Revision:                  # what the agent resumes with
    comment: str
    note: str | None             # set when the base moved: main merged in, conflicting paths
REVISABLE = frozenset({"ready", "conflict"}); STOPPABLE = REJECTABLE | {"working"}
def request_changes(self, change_id, comment: str) -> asyncio.Task[Revision | None]: ...
def stop(self, agent_id, run: asyncio.Task[None] | None) -> asyncio.Task[None]: ...
async def save_for_review(self, agent_id, path, content, base_hash) -> str: ...   # the new hash
def run_ended(self, agent_id, end: RunEnd | None, *, steps: int) -> None: ...      # `steps` is new
async def _whole(self, work: Coroutine[Any, Any, T]) -> T: ...
# _Change gains: requested (an unsent changes_requested), proposed_now, requests, summary, taken
```

- **`_whole(work)`** runs `work` as a tracked task (`_spawn`, so `aclose` waits for it), waits for
  it through every cancel, then re-raises the first cancel. Only for a publish and the bookkeeping
  that must match it.
- **From `ready` or `conflict`**, through `_take` (no `await` before the flip). A `halted` agent
  that proposed is `ready` (4.5). Every other state answers `WrongState`; a second click finds
  `deciding`.
- **Phase A, cancellable**, under main's lock, then the agent's: from `conflict` only, move the
  base (below). A failure sends an `error`, leaves `conflict` and returns `None`.
- **Phase B, one `_whole` step (R2, R8)**: `rec.requested` = the resolution (`changes_requested`,
  the comment, `revision` the last proposed, `merge`, `merge_commit` and `conflict` null); up to
  `CLOSE_OUT_ATTEMPTS` tries through `_send_event`, whose receipt clears `rec.requested` once the
  store took it; then `working`, `requests += 1`, `proposed_now = False`, return the `Revision`. A
  cancel before phase B leaves the change as it was (`_restore`, or R3); a cancel inside it waits
  for its end, so the resolution and `working` always go together.
- **R2: a resolution that failed twice stays pending**, like a close-out event: `rec.requested`
  stays set, the record joins `_pending`, an `error` names it, and the revision starts. Retried,
  never dropped: `proposed()` sends it first, under the agent's lock, and fails (fatal `error`,
  `unproposed`) if it still cannot, so revision n + 1 never precedes its request in the log;
  `_unsent` lists it before a Reject's or Stop's events; `_finish_pending` retries it without
  closing the change; `aclose` tries once more, then names it in its `error` or log line.
- **Nothing is discarded**: worktree, branch, base and files stay; no `done`, no cleanup.
- **The next proposal** is today's `proposed()` split around `_proposal(rec, head, files,
  author)`: republish what differs from `published`, run the tests in the worktree, take
  `change_cone(graph, head.graph)` on the agent's version, then **R7**: publish `change.proposed`
  (revision n + 1, same `change_id`) and set `revision`, `head_commit`, `proposed_now` and
  `summary` in one `_whole` step, so a Stop or shutdown that cancels the run during the publish
  finds the bookkeeping matching what the store took. `run_ended` reads `proposed_now`.
- **`done` and `stopped`** carry `step` from `run_ended(steps=)` (the app passes `AgentRun.steps`,
  even for a cancelled run) and `max_steps = limits.max_steps * (requests + 1)`.
- **R5: combined tests, owned by this plan**, since main moves at level 2 with one slot too (saves
  on main, an earlier approval). In `_merge`, when `pre != rec.base_commit`, after `merge --no-ff
  --no-commit` exits 0, `Workspace.run_tests(main)` runs before `commit`. A failure or timeout is
  `merge --abort`, the `unchanged(pre)` check, and `change.resolved` approved with
  `Conflict(kind="tests", tests=suite)`, as `_conflict` does for paths; the change goes
  `conflict`. A cancel during the tests is `_recover`'s (it aborts on `MERGE_HEAD`). The two-agent
  front reuses this and adds nothing to `_merge`.
- **Main moved, from `ready`**: the base stays; Approve runs R5. **From `conflict`** ("redo it on
  the new base") phase A moves it: `main_head = rev-parse HEAD`, main clean; in the worktree `merge
  --no-ff --no-commit main_head`; exit 0, `commit` as the agent if `MERGE_HEAD`; exit 1 with
  unmerged paths, `add -A` and `commit`, so the agent (no git) reads the markers; else fail. The
  note names the paths with markers or the failed tests.
- **R4: the head comes from the merged tree.** After the commit, `base_files, base` =
  `_read_base(main, main_head)`, and `head_files, head` = every file of the worktree at the new
  head (the same reader, not cached in `_bases`), never only the paths in the new diff; then
  `graph = change_graph(base, head)`, `touched` = the files in `main_head..head`, and each path in
  `published` or `touched` whose original or modified changed is republished as the agent's. A
  caller that main added stays in the cone and never shows as removed.
- **R3: recovery from facts.** `_restore`, a done-callback, cannot run git, so phase A catches
  `CancelledError` and `Exception` as `_approve` does and, still under both locks, reads the
  worktree: `MERGE_HEAD` present, `merge --abort`. HEAD the old head and `status --porcelain`
  empty: unchanged, `conflict`, an `error` says so. HEAD a merge of the old head and `main_head`,
  clean: the move landed, R4's bookkeeping completes from those facts, the change stays `conflict`
  with no resolution, and a second request finds main merged in. Anything else, or git failing:
  `broken`, which leaves `main_suspect` false since main was only read (open question 5); Reject
  discards it. A cancel re-raises after recovering.

## 2. The runtime (`agents/loop.py`)

- `messages`, the step budget and the turn bound outlive `run()`. New `revise(comment, note) ->
  RunEnd` (strings: `agents` does not import `changes`): answer "Not run." to every tool call left
  without a result (a time limit mid-call, calls after `finish`); append one user message,
  `REVISION_PROMPT` with the comment and note, which says to read a file again before editing it;
  grow `max_steps` and the turn bound by `limits.max_steps`; reset `_failures` and `_seen`; send
  `revising`, `refresh_graph()`, then `run`'s body. The status stays `revising` (4.5).
- `run` and `revise` share `_drive(first_status)`; `revise` during a run raises `RuntimeError`.
  `AgentRun.steps` is read-only; `PROMPT_VERSION` hashes `REVISION_PROMPT` too. Stop needs nothing
  here: a cancel already sends the interrupted step, no status, no `proposed`.

## 3. The person's edits in review

- **Only from `ready`**, checked before and again under the agent's lock (`WrongState`): AGENTS.md
  gives a conflict two choices, redo or reject. The save holds the agent's lock throughout.
- **Steps**: `tree.write_text(path, content, expected_hash=base_hash)` (`StaleFile`); `add -A`;
  `commit` as the user only if `status --porcelain` shows something. **R9**: identical content
  stages nothing, so no commit; with HEAD still `head_commit` the save returns the hash and sends
  nothing. Otherwise `change.updated(author="user")` (`_send_file` takes the author; `published`
  keeps it from being resent as the agent's), then `_proposal(rec, head, files, author="user")`.
- **R6: a save re-proposes.** `change.proposed` revision n + 1 carries the cone recomputed on the
  saved head and the tests run on it; the reducer replaces `proposal.impact` and the tests on each
  `change.proposed`, so a save that touches another function lights its callers. `summary` stays
  the agent's. Approve never merges an untested edit.
- **R1: Approve decides from git facts, never from a stale record.** Approve's `_take` records
  `taken = revision`. `_approve`, under both locks, before `_merge`: (a) `revision != taken` (a
  save proposed after the click): no merge, `ready`, an `error` "revision n + 1 arrived after you
  clicked; review it and approve again"; (b) the worktree's HEAD is not `head_commit`, or `status
  --porcelain` is not empty (a save whose commit landed but whose proposal did not, by a cancel or
  a failed git call): no merge; `_proposal(rec, author="user")` re-proposes what the worktree
  holds, committing leftovers as the user, then `ready` and an `error` naming revision n + 1.
  `head_commit` moves only inside R7's `_whole` step, so (b) catches every save cut short.
- **The loop learns it** from `Toolbox._read_hashes`: `edit_file` compares the hash the agent last
  read or wrote with the disk, so after a save its next edit of that file fails until a
  `read_file`. Nothing new; a test pins it.

## 4. Stop

- `stop(agent_id, run)`: `ChangeNotFound` for an unknown agent; `_take(change, STOPPABLE)`;
  `deciding`, `closing` and `closed` answer `WrongState`. In the same synchronous call,
  `run.cancel()` if the app passed a live task.
- **The task**: `asyncio.wait([run])`; the run's `finally` calls `run_ended`, which records `steps`
  and sets `fallback` to `ready` or `unproposed`. **R7**: a run cancelled inside the
  `change.proposed` publish finishes that `_whole` step first, so when the wait returns, `revision`
  and `proposed_now` match the store. Then `_finish_pending()` and, under both locks, the
  resolution `rejected`, `reason="stopped"`, `revision` the last proposed (0 if none), and the
  close-out with a stop flag: `_unsent` yields a pending `changes_requested` (R2), `agent.status`
  `stopped` ("Stopped by the user."), then `change.resolved`; no delta, no `done`. `EVENT_TYPES`
  gains "requested" and "stopped". **Discard is the Reject path**; cleanup follows the resolution.
- **Slot.** Freed on `change.resolved` `rejected`, a stop's included; `changes_requested` keeps it
  (`app.py`'s condition already; a test pins it).
- **Races.** Stop and Approve both `_take`: the first wins, the second is 409. During Request
  changes' task Stop is 409; once `working`, the revision task is the run and Stop cancels it. A
  Stop during a save waits for the agent's lock and closes the revision it finds.

## 5. Routes and errors (`api/commands.py`, `app.py`, on top of `api/wiring`)

| Route | Body | Success | Errors |
| --- | --- | --- | --- |
| `POST /changes/{id}/request-changes` | `RequestChanges {comment}` | 202, empty | `invalid_request` 422 (no or empty comment), `not_found` 404, `wrong_state` 409 |
| `POST /agents/{id}/stop` | none | 202, empty | `not_found` 404, `wrong_state` 409 |

- `Commands` gains `request_changes(change_id, comment)` and `stop(agent_id)`, synchronous like
  `approve`, so the flip precedes the 202; both move to `LIVE_ONLY` (501 in replay).
- **App `request_changes`**: `wrong_state` while main is suspect or the brake refuses new runs;
  `decision = manager.request_changes(...)`; then, with no `await`, `self._decisions[agent] =
  decision` and `self._runs[agent] = create_task(self._revise(decision, ...))`: it waits for the
  decision and on a `Revision` runs `revise`, its `finally` calling `run_ended(..., steps=...)`
  once the change is `working`; `_revise` does `self._decisions.pop(agent, None)` once it has
  awaited the decision. The app keeps each `AgentRun` and its client until the change is
  rejected or merged.
- **R8: shutdown owns the decision too.** `_stop_runs` does not rely on `_revise`'s cancel
  handler, which a task cancelled before its first step never runs. It first snapshots
  `decisions = list(self._decisions.items())`, then cancels the run/`_revise` tasks, then
  cancels each decision in the snapshot and drains it (awaits it, swallowing `CancelledError`)
  before `manager.aclose()` and the store close. A `_revise` waking as its decision ends pops
  during that drain: the snapshot is unaffected, and `pop(agent, None)` tolerates the entry
  already gone. That settles it by section 1: before phase B, phase A's recovery and
  `_decide`'s `_restore` return `ready`/`conflict` with nothing sent; inside phase B, `_whole`
  finishes the resolution and `working` together despite the cancel. After draining,
  `_stop_runs` awaits the cancelled `_revise` tasks (swallowing `CancelledError`) before its
  own `run_ended`, so `_revise`'s `finally` `run_ended` finishes first and the two cannot
  interleave. If the drained change is then `working`, `_stop_runs` itself calls
  `run_ended(agent, None, steps=)` with the kept run's steps: `unproposed`, as for any run a
  shutdown cuts (the state guard makes it a no-op if `_revise`'s `finally` already ran). No
  `deciding` change, and no unended `working` one, reaches `aclose`.
- **App `stop`**: an unknown or malformed id is `not_found`; `manager.stop(agent,
  self._runs.get(agent))`; allowed while main is suspect, like Reject.
- **Front 17 (interface notes only).** Request changes needs a trimmed comment, on
  `ready_for_review` and `conflict` ("Redo on the new base"); Approve stays a click. Buttons stay
  disabled until `change.resolved`, a 4xx or an `error` naming the change. "Stop and discard" is
  destructive. The new side is editable on `ready_for_review` only; a save brings revision n + 1
  with its own tests and cone, "edited by you" from `author`. The reducer replaces
  `proposal.impact` on each `change.proposed` (a vitest pins it).

## 6. Contracts v1.1

Enough: `RequestChanges {comment}`, stop with no body, 202, `not_found`/`wrong_state`; `revising`,
`stopped`, a growing `max_steps`; `changes_requested` with `comment`; `reason="stopped"`;
`author="user"`; `revision`, a per-proposal `base_commit`, `Conflict(kind="tests")`. One proposal,
not blocking: list `spend_brake` (403) among request-changes' errors in 4.7.

## 7. Order of work, and tests

1. `changes/request` (manager): `_whole` and R7, R5, Request changes with the base move, R2 to R4;
   about 300 lines of code and 450 of tests, three commits.
2. `changes/save-stop`, after 1: `save_for_review` with R1, R6, R9, then Stop; about 150 and 300.
3. `agents/revise` (loop), about 100 and 200, in parallel with 1.
4. `api/request` (routes, app, R8), about 120 and 250, after 1 to 3 and `api/wiring`. The files
   front's `PUT /files` with `agent_id` follows 2; front 17's follow-up runs in parallel now.

Tests as changes.md §7: `asyncio.timeout(60)`, waits on events, every envelope validated, the fake
model only. Two doubles hold a step: `held_git(after=...)`, a Workspace whose `git` awaits a gate
once the named call in the named tree returns; `held_publish(...)`, a publish that for one event
type fails n times, waits on a gate before the store, or commits to the store and then waits (the
store took it, the caller has not heard). * marks those shown failing with their change reverted.

- `test_request_changes.py`: *`test_revision_keeps_worktree_and_base`,
  `test_request_changes_states`, *`test_failed_revision_is_unproposed`,
  *`test_redo_on_the_new_base`, *`test_base_move_failure_keeps_the_conflict`, and:
  - *`test_requested_kept_until_sent` (R2): `held_publish` fails `changes_requested` twice; the
    revision runs; the store holds one, with the comment, below revision 2's `change.proposed`. A
    third failure: `unproposed`, and Reject sends it before `rejected`.
  - *`test_cancel_after_worktree_merge` (R3): `held_git(after="merge")` in the worktree; the test
    cancels the decision, then opens the gate: no `MERGE_HEAD`, clean, HEAD the old head,
    `conflict`, no resolution. `after="commit"`: base moved, `conflict`, a second request revises.
    `merge --abort` failing: `broken`, `main_suspect` false, Reject cleans up.
  - *`test_redo_keeps_mains_callers` (R4): main adds a caller of the changed function in a file
    the agent never touched; revision 2's `impact` has it, `nodes_removed` does not.
  - *`test_combined_tests_after_main_moved` (R5): a commit on main breaks a test with the agent's
    change; approve gives `Conflict(kind="tests")`, main at `pre`, no `MERGE_HEAD`, clean; a
    passing variant merges and leaves `status --porcelain` empty.
- `test_user_save.py`: *`test_save_commits_as_the_user`, *`test_save_rechecks_under_the_lock`, and:
  - *`test_cut_save_is_never_approved_stale` (R1): `held_git(after="commit")` in the worktree; the
    test cancels the save, opens the gate, approves: main unchanged, revision n + 1 at the
    worktree's HEAD, an `error`, `ready`; approving again puts the person's text on main. A
    `rev-parse` failing after the commit gives the same.
  - *`test_approve_wants_the_revision_it_saw` (R1): `held_publish` holds the save's
    `change.proposed` before the store; Approve is clicked, the gate opens: no merge, `ready`.
  - *`test_save_reproposes_with_a_new_cone` (R6): a save edits another function; revision n + 1
    lists its callers and fresh tests. *`test_identical_save_is_a_no_op` (R9): the hash, no
    commit, no event.
- `test_stop.py`: *`test_stop_a_running_agent`, *`test_stop_idle`, `test_stop_mid_revision`,
  *`test_stop_races`, `test_stop_close_out_retried`, and:
  - *`test_stop_during_the_proposal_publish` (R7): `held_publish` commits `change.proposed` rev 1
    and waits; Stop cancels the run; the gate opens: the stop's resolution names revision 1 and
    `stopped` follows the proposal. A blocked model would never reach this window.
- `agents/test_revise.py`: *`test_revise_keeps_the_history`,
  *`test_revise_answers_unanswered_calls`, *`test_revise_resets_stop_rules`,
  `test_edit_after_a_save_needs_a_read`.
- `api/test_revise_stop.py`: a revision's event order, *`test_request_changes_keeps_the_slot`, 422,
  404, 409, 501 in replay, stop's 202, 409 and 404, *`test_stop_right_after_request_changes`, and:
  - *`test_shutdown_during_revise` (R8): `held_publish` holds `changes_requested` before the store;
    the test cancels `_revise` as `_stop_runs` does, then opens the gate: one resolution, the
    change `unproposed`, nothing after it, no task alive. With main's lock held by the test
    instead: no resolution, `ready`.
  - *`test_shutdown_before_revise_starts` (R8): `held_publish` holds `changes_requested` before
    the store and signals when held. The test drives the decision to that hold, registered in
    `_decisions` as `request_changes` registers it, and only then creates the `_revise` task,
    cancelling it with no `await` between `create_task` and `cancel()`, so its body and handler
    provably never run (a counting `revise` stays at zero). It then starts `_stop_runs`, opens
    the gate and awaits it: one resolution, `working` settled to `unproposed` by `_stop_runs`'
    own `run_ended`, nothing after it, no task alive.
  - *`test_shutdown_decision_pops_during_drain` (R8): `held_publish` holds `changes_requested`
    before the store for two changes, each on its own gate. The test first swaps `_decisions`
    for a `dict` subclass whose `pop` sets a per-agent event (test-only, no production hook),
    registers both decisions there, and starts each `_revise`, which signals it is awaiting
    its decision. It then starts `_stop_runs`, opens only the first gate, and waits for that
    agent's `pop` event, so the first entry is gone while the drain is still between the two
    decisions; only then does it open the second gate: no `RuntimeError`, every decision in
    the snapshot drained, one resolution each, no task alive. Without the snapshot the
    drain's next step raises `RuntimeError` and the second decision never drains.

## Level 3, kept out

Pause, resume and messages to a running agent stay 501; a running agent takes only Stop.

## Decided by the maintainer (2026-10-27)

The maintainer approved the proposed answer to each open question:

1. Redo on the new base merges main into the agent's branch and commits the conflict markers; the
   agent resolves them in its kept worktree. No fresh worktree, so "discards nothing" holds.
2. A save in review re-proposes the change and runs the tests (seconds) before Approve can take it.
3. Request changes from `ready` after main moved keeps the agent's base; the combined tests at
   Approve cover the move.
4. Request changes answers `spend_brake` (403) when the brake refuses new runs, since a revision
   spends credits: listed in 4.7's errors with the contracts 1.2 proposal. Stop asks to confirm
   inline (a second click on the same button, no modal, no focus change), since it discards the
   agent's work.
5. A worktree-only failure marks the change `broken` without setting `main_suspect`.
6. The new side is editable on `ready_for_review` only, not on `conflict`.
