# Plan: Request changes, Stop, and the person's edits in review, level 2

Revision 1, plan only, for an independent review before any code: it changes an agent's lifecycle
and the change manager's states. Sources: AGENTS.md (Pending changes, approval and conflicts; Agent
tools; Events; Commands; Robustness; Level 2), docs/proposal.md (4.4, 4.5, 4.7, 4.9, 5),
`contracts/` 1.1.0, docs/plans/changes.md revision 8 and `changes/` at main 0657c5a,
`agents/loop.py` and `agents/tools.py`, and the API front in review (`api/wiring` 4b2ebac:
docs/plans/api.md, `app.py`, `api/commands.py`). No new module, dependency or contract field.

## 1. Request changes (`changes/_manager.py`)

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
```

- **From `ready` or `conflict`**, through `_take` (no `await` before the flip). A `halted` agent
  that proposed is `ready`, so it qualifies (4.5: a revision with a new step budget). `unproposed`
  (nothing to revise, or a lost root), `broken`, `working`, `deciding`, `closing` and `closed`
  answer `WrongState`; a second click finds `deciding`.
- **The task**, under main's lock, then the agent's: (1) from `conflict` only, move the base
  (below); a failure sends an `error`, leaves the change `conflict` and returns `None`: no
  revision, nothing else sent. (2) Publish `change.resolved`: `changes_requested`, the comment,
  `revision` = the last proposed, `reason`, `merge`, `merge_commit`, `conflict` null (the validator
  allows it). Two tries; if both fail, an `error` names it and the revision still starts: the card
  follows `agent.status`, and the next `change.proposed` resets the change in the reducer. (3) State
  `working`, `requests += 1`, `proposed_now = False`; return the `Revision`. Nothing is discarded:
  worktree, branch, `base_commit`, `base_files`, `touched`, `published` stay. It is not a close-out:
  no `done`, no cleanup, not in `_pending`. Cancelled before it settles, `_restore` takes it back.
- **The next proposal** is today's `proposed()`: it commits on revision n's head as the agent,
  diffs `base_commit..head`, republishes what differs from `published`, runs the tests in the
  worktree at the new head, takes `change_cone(graph, head.graph)` on the agent's version, and
  sends `change.proposed` with revision n + 1 and the same `change_id`. `run_ended` reads
  `proposed_now` (set by a successful `proposed()`), not `head_commit`: a revision that fails to
  propose is `unproposed`, never `ready` with revision n's head under a worktree that moved on.
- **`done` and `stopped`** carry `step` from `run_ended(steps=)`, which the app passes from
  `AgentRun.steps` even for a cancelled run, and `max_steps = limits.max_steps * (requests + 1)`,
  the runtime's rule (section 2).
- **Main moved.** From `ready` the base stays: the person reviews the same diff, and Approve applies
  AGENTS.md's rule (combined tests when main moved since `base_commit`, a failure is
  `Conflict(kind="tests")`), the two-agent front's change to `_merge` (changes.md §6). From
  `conflict` ("redo it on the new base") the base moves first: `main_head = rev-parse HEAD`, main
  clean (`status --porcelain` empty, else the move fails); in the worktree `merge --no-ff
  --no-commit main_head`; exit 0, `commit` as the agent if `MERGE_HEAD`; exit 1 with unmerged paths,
  `add -A` and `commit` as the agent, so the conflict markers are committed and the agent, which has
  no git, sees them with read_file; anything else, `merge --abort` if `MERGE_HEAD`, and fail. Then
  `base_commit = main_head`, `base_files` and `base` from `_bases` or `_read_base(workspace.main,
  ...)`, head refreshed for the new diff and `touched`, each of those files republished
  (`author="agent"`) as its original changed, and `tree.base_commit` updated. The note says main was
  merged in, with the paths holding markers or, for a test conflict, the failed tests (`_conflict`
  keeps the `Conflict` on the record). The next proposal's base is main's HEAD, its tests are the
  combined result's, and its head descends from main, so Approve's `--no-ff` and abort still hold.

## 2. The runtime (`agents/loop.py`)

- `messages`, the step budget and the turn bound become attributes that outlive `run()`. New
  `revise(comment, note) -> RunEnd` (strings: `agents` does not import `changes`): (1) answer with
  "Not run." every tool call in the history that has no result: a run cut by its time limit
  mid-call leaves one, and Chat Completions refuses such a history; `_loop` also answers the calls
  after `finish` in its reply, as a halt already does; (2) append one user message,
  `REVISION_PROMPT` with the comment and note, which also says to read a file again before
  editing it; (3) grow `max_steps` by `limits.max_steps` and the turn bound with it, reset
  `_failures` and `_seen` (a re-read after review is no stall), send `agent.status` `revising`,
  `refresh_graph()`, then the body `run` has from there: a new `asyncio.timeout`, the same halts,
  `proposed`, the last status. The status stays `revising` through the revision (4.5).
- `run` and `revise` share one `_drive(first_status)`; `revise` while a run is under way raises
  `RuntimeError`. `AgentRun.steps` is read-only. `PROMPT_VERSION` hashes `REVISION_PROMPT` too.
- Stop needs nothing here: a cancel already sends the interrupted step, no status, no `proposed`.

## 3. The person's edits in review

- **Write** (`save_for_review`, called by the files front's `PUT /files` with `agent_id`): the
  change must be `ready` or `conflict` (idle: no run task exists then), checked before and again
  under the agent's lock, since an Approve or Stop may have flipped it while the save waited:
  `WrongState` (`wrong_state`). Then `tree.write_text(path, content, expected_hash=base_hash)`
  (`StaleFile`, `stale_file`), `add -- path`, `commit` as the user, `head_commit` = HEAD, and
  `_update(rec, path, author="user")`: `_send_file` takes the author, and `published[path]` keeps
  the next proposal from resending it as the agent's. Approve merges that head as the person saw
  it; the tests and cone shown stay revision n's (open question 2).
- **The loop learns it** from `Toolbox._read_hashes`, the hash of each file as the agent last read
  or wrote it: `edit_file` compares it with the file on disk, so after the save its next edit of
  that file fails with "changed since you last read it; read it again" until a `read_file`. Nothing
  new there; `revise` refreshes the toolbox's graph, and a test pins the behaviour (section 7).

## 4. Stop

- `stop(agent_id, run)`: `ChangeNotFound` for an unknown agent; `_take(change, STOPPABLE)`: a run
  or revision under way (`working`), `ready`, `conflict`, `unproposed`, `broken`; `deciding`,
  `closing` and `closed` answer `WrongState`. In the same synchronous call, `run.cancel()` if the
  app passed a live task: the run stops at its current `await`, as at shutdown, and no sink call can
  then find the change `deciding` but the one the cancel interrupts.
- **The task**: `asyncio.wait([run])`; the run's `finally` calls `run_ended`, which records `steps`
  and, when a Stop holds the change, sets its `fallback` to `ready` or `unproposed`. Then
  `_finish_pending()` and, under main's lock and the agent's, the resolution `rejected`,
  `reason="stopped"`, no comment, `revision` the last proposed (0 if none), and the close-out with a
  stop flag: `_unsent` yields `agent.status` `stopped` (detail "Stopped by the user.") then
  `change.resolved`, with no delta and no `done` (4.5: `stopped`, the last status, comes first).
  `EVENT_TYPES` gains "stopped"; retries, the drain and `aclose` go through `_unsent` unchanged.
- **Discard is the Reject path**, with its reason and first event; no second path. Proposed or not,
  the change goes, and cleanup (`discard_worktree`, `branch -D`) follows the resolution.
- **Slot.** The app's `_publish` frees it on `change.resolved` `rejected`, a stop's included;
  `changes_requested` keeps it held (`app.py`'s condition already; a test pins it).
- **Races.** Stop and Approve both `_take`: the first wins, the second answers 409 at once. A merged
  Approve leaves `closing`/`closed`; a conflict or failure leaves `conflict`/`ready`, where Stop
  then works. A second Stop is 409 and changes nothing. During Request changes' task Stop is 409;
  once the resolution is out, the revision task is the run and Stop cancels it.

## 5. Routes and errors (`api/commands.py`, `app.py`, on top of `api/wiring`)

| Route | Body | Success | Errors |
| --- | --- | --- | --- |
| `POST /changes/{id}/request-changes` | `RequestChanges {comment}` | 202, empty | `invalid_request` 422 (no or empty comment), `not_found` 404, `wrong_state` 409 |
| `POST /agents/{id}/stop` | none | 202, empty | `not_found` 404, `wrong_state` 409 |

- `Commands` gains `request_changes(change_id, comment)` and `stop(agent_id)`, synchronous like
  `approve`, so the flip precedes the 202. Both leave `NOT_BUILT` for `LIVE_ONLY` (501 in replay).
- **App `request_changes`**: `wrong_state` while main is suspect (approve's message) or the brake
  refuses new runs (its message; section 6); `manager.request_changes`; then, with no `await`,
  `self._runs[agent] = create_task(self._revise(...))`, which waits for the decision with
  `asyncio.wait` and on a `Revision` runs `revise`, its `finally` calling `run_ended(...,
  steps=...)` once the change went `working`. The app keeps each `AgentRun` and its client until
  the change is rejected or merged.
- **App `stop`**: an unknown or malformed id is `not_found`; `manager.stop(agent, self._runs.get(
  agent))`; allowed while main is suspect, like Reject. Shutdown: `_stop_runs` cancels revision
  tasks too, and `aclose` waits for a stop's task.
- **Front 17 (interface notes only).** Request changes needs a comment (trimmed, disabled while
  blank); offered on `ready_for_review` and `conflict` ("Redo on the new base" there); Approve stays
  a click, never reachable from the comment box by keyboard. Buttons stay disabled until
  `change.resolved`, a 4xx or an `error` naming the change; show a 409's message. After
  `changes_requested` the change is `revising` (the reducer does it), the comment shown; revision
  n + 1 brings it back. The card's "Stop and discard" is destructive, for every state before `done`
  or `stopped`; label a stopped change from `reason`. The new side is editable only on
  `ready_for_review` or `conflict`; `author: "user"` reads "edited by you", and the tests as from
  before the edit. No reducer change; a hand-written, schema-checked fixture lets it start now.

## 6. Contracts v1.1

Enough: `RequestChanges {comment}`, stop with no body, 202, `not_found`/`wrong_state`; `revising`,
`stopped`, a growing `max_steps`; `changes_requested` with `comment`; `reason="stopped"`;
`author="user"`; `revision` and a per-proposal `base_commit`. One proposal, not blocking: list
`spend_brake` (403) among request-changes' errors in 4.7, since a revision spends credits like a
new run; until then the brake answers `wrong_state` with its message.

## 7. Order of work, and tests

1. `changes/request` (manager), two commits, about 250 lines of code and 350 of tests: Request
   changes with the base move, `run_ended(steps=)`, `proposed_now`, `save_for_review`; then Stop.
2. `agents/revise` (loop), about 100 and 200, in parallel with 1.
3. `api/request` (routes, app), about 120 and 250, after 1, 2 and `api/wiring` merge. The files
   front's `PUT /files` with `agent_id` follows 1. Front 17's follow-up runs in parallel now.

Tests as changes.md §7: `asyncio.timeout(60)`, waits on events, every envelope validated, the delta
chain checked, the fake model only. * marks those shown failing with their change reverted.

- `changes/test_request_changes.py`: *`test_revision_keeps_worktree_and_base` (resolution only;
  worktree, branch, base unchanged; an edit and `proposed` give revision 2, same id, head's parent
  revision 1's head, `twice` in the cone; approve merges); `test_request_changes_states`;
  *`test_failed_revision_is_unproposed` (Reject closes revision 1); *`test_redo_on_the_new_base`
  (a real conflict: HEAD's parents include main's, markers committed, the note names the path,
  main's version as original; revision 2's base is main's HEAD; approve merges);
  *`test_base_move_failure_keeps_the_conflict` (no `MERGE_HEAD`, no resolution, `None`).
- `changes/test_user_save.py`: *`test_save_commits_as_the_user` (author, `change.updated` "user",
  not resent, merged by approve; `StaleFile` writes nothing); *`test_save_rechecks_under_the_lock`.
- `changes/test_stop.py`: *`test_stop_a_running_agent` (a model blocked on an event: interrupted
  step, `stopped` with the step count, resolution with `stopped`, no `done`, worktree and branch
  gone); *`test_stop_idle` for `ready`, `conflict`, `unproposed`, `broken`;
  `test_stop_mid_revision`; *`test_stop_races` (Approve and Stop either way, Stop twice);
  `test_stop_close_out_retried`.
- `agents/test_revise.py`: *`test_revise_keeps_the_history` (last history plus one message,
  `revising`, `max_steps` doubled, steps go on); *`test_revise_answers_unanswered_calls` (after
  `finish`, after a time limit mid-call); *`test_revise_resets_stop_rules`;
  `test_edit_after_a_save_needs_a_read`, a guard that dropping the hash check fails.
- `api/test_revise_stop.py`: the event order of a revision; *`test_request_changes_keeps_the_slot`;
  422, 404, 409 (deciding, main suspect), 501 in replay; stop 202, 409 twice, 404, slot freed;
  *`test_stop_right_after_request_changes` (409 while deciding, then no step after `stopped`).

## Level 3, kept out

Pause and resume (`paused`) and messages to a running agent stay 501. Request changes is taken only
while the agent is idle; while it runs, Stop is its only command.

## Open, for the maintainer

1. Redo on the new base: merge main into the agent's branch with markers committed (proposed), a
   fresh worktree from main (breaks "discards nothing"), or Reject only on a merge conflict.
2. The person's edits in review: Approve merges them untested (proposed for level 2, said in the
   panel), or Approve re-runs the tests when the head moved since the proposal (`Conflict(tests)`).
3. Request changes from `ready` after main moved: keep the base (proposed) or move it too.
4. `spend_brake` on request-changes (section 6), and whether Stop asks to confirm (front 17).
