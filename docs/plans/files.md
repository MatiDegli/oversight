# Plan: file editing on main, level 2 (GET /files, PUT /files, POST /graph/rescan, the file view)

Revision 3 (revision 1 by Muse Wonder), plan only, for an independent review before any code. It
answers plan review 1 (B1 to B4, S1 to S7) and re-review 1 (F1 to F3), each marked where it lands. Sources:
docs/proposal.md 4.3 to 4.9, `contracts/` 1.1.0, docs/plans/changes.md rev. 8, docs/plans/api.md,
docs/plans/request-changes.md rev. 2 (806b495, sections 1, 3, 7), and the code at main 75a837e. No
module, dependency or contract change; section 4 holds two contract proposals, neither settled. This
front builds `PUT` on main, `GET` for both targets and rescan; agent saves stay with request-changes.

**Builds on `changes/request` step 1** (request-changes section 7): `_whole`, R5's combined tests,
and the test doubles `held_git(after=...)` and `held_publish(...)`. Saves on main are not enabled
before R5 merges: without it an approval after a save merges onto a moved main untested.

## 1. Backend

### 1.1 Routes and states (`api/commands.py`, `app.py`)

`Commands` gains `get_file`, `save_file`, `rescan`; `LiveRun` delegates to the `ChangeManager`, sole
owner of main's graph, `snapshot` and lock order.

| Route | Live | Replay | Errors |
| --- | --- | --- | --- |
| `GET /files?path=&agent=` | 200 `FileContent` | 501 | `invalid_path` 422, `not_found` 404 |
| `PUT /files`, no `agent_id` | 200 `FileSaved` | 501 | `invalid_path`, `stale_file` 409, `wrong_state` 409 |
| `PUT /files`, `agent_id` set | 501, nothing read or written | 501 | until request-changes step 2 |
| `POST /graph/rescan` | 202, empty | 501 | none of its own (S7, section 4 P1) |

- As every route, also `invalid_request` (live validation; replay's no-body 501 answers first) and
  `internal`. `GET …&agent=` with a malformed id or no live worktree is `not_found`.
- The lifespan awaits `startup` before serving, so `manager is None` is unreachable; the guard
  answers `wrong_state` for `PUT` (as `approve`), `internal` for the others (no state code).

### 1.2 GET /files (`ChangeManager.read_file(agent_id, path) -> FileContent`)

1. **S2: main is read under main's lock.** Every mutation of main (a merge with R5's tests, a
   save, a rescan) holds it from its first git call to its last publish and ends clean at a commit
   or suspect, so a read sees a whole commit, never a `merge --no-commit` tree waiting on R5's tests
   nor conflict markers before `merge --abort`. `hash` is the SHA-256 of the bytes read. A GET may
   wait behind an approval's tests (seconds). A suspect main is read as it stands (GET has no
   `wrong_state`); saving it answers `wrong_state`.
2. **Agent target**: `Tree.read_text` under that worktree's lock (today's behaviour), any state
   with a live worktree; the hash may go stale, which is what `stale_file` is for.
3. **Errors**: `InvalidPath` (absolute, `..`, `.git`, a symlink component, not a regular file) and
   non-UTF-8 → `invalid_path`; `FileNotFoundError` → `not_found`; messages through
   `workspace.redact`, never an absolute path.

### 1.3 Mechanisms shared by saves, rescans and merges

After the stage split, `_finish_pending` and `_record_merge` are `_delivery.finish_pending` and `_delivery.record_merge`; `_recover` is `_recovery.recover`.

- **M1, one whole step (B3, S1, the cancel half of B2).** `save_main` and `rescan` run their body
  as one `_whole(...)` step; the spawned task takes main's lock itself (locks are reentrant per
  task; the request task holds none). A cancel of the request waits for the save to end, then
  re-raises; `aclose` waits for it as for any tracked task. No cancel falls between write and
  commit, commit and publish, or the store taking a delta and `snapshot` moving; bounded by the git
  timeout and one scan. Wider than request-changes' "only for a publish and the bookkeeping that
  must match it": for a save the commit is that bookkeeping.
- **M2, `_scan(commit)` (S1).** `asyncio.to_thread(main_graph.scan, commit)` that, on a cancel,
  waits for the thread to return before re-raising, so no lock is released while `WorkspaceGraph`
  mutates. Used by save, rescan and `_record_merge`, where today a cancelled approval's `_recover`
  releases both locks while its scan thread still runs.
- **M3, one delta per commit on main (S3).** `_versions.main_delta(old, new, commit, cause)` gains
  the cause. Every commit on main, a merge or a save, publishes exactly one `graph.delta`:
  `graph_delta`'s, else an empty one from `old.version` to `new.version` with `commit=head`, equal
  versions included (changes.md §4 already sends that for an empty merge). A `pyproject.toml` save
  therefore moves `snapshot.commit`, `GET /graph` and the top bar's commit.
- **M4, pending deltas (B1).** A save and a rescan open as `_approve` does: `_finish_pending()`
  before main's lock (a pending close-out usually sends its delta A→B here, moving `snapshot` to
  B), then under the lock `_drain` every pending publish. When it builds its delta from
  `self.snapshot`, it drops every pending unsent delta: `_record_merge`'s loop, factored into
  `_supersede_pending()` and shared. The new delta starts from the graph the UI holds and covers
  the merge; the dropped A→B is never sent, so no later retry breaks the chain.
- **M5, authority rechecked inside main's lock (B4).** `_approve`, `open`, `save_main` and
  `rescan` read `main_suspect` again after taking main's lock. Approve: no git call, back to its
  fallback (`ready`), one `error` (`server`, not fatal) "main became suspect before this approval
  ran; reject the change or restart". `open` holds main's lock from that check through
  `create_worktree` (reentrant) and raises `WrongState`, which `start` maps to `wrong_state`. The
  route-level checks stay as fast paths. **F1, dependency on the request-changes front (not a
  note):** its phase A must do the same recheck. Phase A takes main's lock before it reads main
  or merges it into a worktree, so it re-reads `main_suspect` there, before phase B; if set, it
  aborts like a failed phase A: no base move, no phase B, no revision started, the change stays
  `ready` or `conflict`, one `error`, the task returns `None`. The app-level suspect check before
  the task spawns stays only a fast path, exactly as here. Request changes is not enabled
  alongside main saves until that front lands this recheck and its test (3.1).

### 1.4 PUT /files on main (`ChangeManager.save_main(path, content, base_hash) -> FileSaved`)

Inside M1's step, after M4's opening, under main's lock:

1. M5's check; then the `check_main` facts: `MERGE_HEAD` or a dirty status → `wrong_state`.
   `pre = rev-parse HEAD`.
2. Read the current bytes (`original`, kept for a restore). Missing or a bad path answers
   `stale_file` or `invalid_path` with nothing written: never an implicit create (question 3).
3. `Tree.write_text(path, content, expected_hash=base_hash)`: compared again at swap time; that
   check decides. `StaleFile` → `stale_file`, disk untouched.
4. `new_hash == base_hash`: answer 200 at once; no commit, delta or event (request-changes R9).
5. `git add -- <path>`, then `commit -q -m "Edit <path>"` through `_git(..., commit_as="user")`,
   clock-dated. Main was clean, so the commit holds this file only.
6. A failed or timed-out `add` or `commit` goes to the recovery below (B2).
7. `head = rev-parse HEAD`; `new = await _scan(head)` (M2); `delta = main_delta(snapshot, new,
   head, "user_edit")` (M3); `_supersede_pending()` (M4); publish; once the store took it,
   `snapshot = with_positions(new, snapshot)`. All in M1's step, so the two never part (B3).
8. Answer 200 `FileSaved{path, hash}`. The commit is the save; step 7 failing never un-saves it.

**B2: recovery decides from git facts first, then restores.** After a failed `add` or `commit`,
still under the lock, read `rev-parse HEAD`, `MERGE_HEAD`, `status --porcelain` and the file's hash
on disk, and only then act:

- **Landed**: `HEAD != pre`, `rev-parse HEAD^ == pre`, `diff --name-only pre HEAD` is exactly
  `path`, status clean, disk hash `new_hash`. The commit is a fact: go on at step 7, answer 200.
- **Not landed**: `HEAD == pre`, no `MERGE_HEAD`. Write `original` back (`expected_hash=new_hash`),
  `git add -- <path>` so the index matches HEAD, then confirm status clean. Confirmed: one `error`
  (`git`, not fatal, no ids) and `internal` 500; main stays trusted.
- **Anything else**, or a fact unreadable, or the restore failing: `_suspect = True` (no change
  record exists to break), one fatal `error`, `internal` 500. Saves, approvals and starts refuse
  until restart (M5); Reject still closes changes. No `reset --hard`, no lock-file removal.

A save emits no `change.*` or `agent.status`, runs no tests (R5 tests the combination at the next
approval), and touches no worktree, base, `head_files` or `ChangeGraph`.

### 1.5 POST /graph/rescan (`ChangeManager.rescan()`, the route awaits it, then 202)

Inside M1's step, after M4's opening, under main's lock:

1. M5's check, then the `check_main` facts. Suspect, mid-merge or dirty main is refused: an
   uncommitted tree is not main's graph at any commit. **Until P1 is decided** (section 4) the
   refusal is the 202 with one `error` (`graph`, not fatal, "Main has uncommitted edits or a merge
   in progress; nothing was rescanned."), nothing published; with P1 it becomes `wrong_state` 409.
2. `head = rev-parse HEAD`; `new = await _scan(head)`; the digest cache re-parses changed files only.
3. Publish one delta, `cause="rescan"`, when nodes, edges, parse errors, the version or the commit
   moved (M3's empty delta covers the last two); then move `snapshot`. All equal: nothing.
4. A raising scan or a refused publish: one `error`, `snapshot` stays, the route answers `internal`.

What a rescan picks up: a commit made on main behind the server's back, and the commit of a save or
merge whose scan raised. An uncommitted on-disk edit is refused by step 1, not scanned.

### 1.6 Races: main's lock decides, M1 keeps each operation whole

- **Save vs Approve.** Save first: R5 tests the combined tree and the merge commit has the save as
  parent. Merge first: step 3 rechecks `base_hash`; the loser gets `stale_file`, never an overwrite.
  A save never lands inside R5's tests, which run under the approval's locks. A pending merge delta
  is M4; main turning suspect is M5.
- **Save vs a running agent whose base main left**: nothing is owed. Worktree, branch, base and
  `ChangeGraph` are untouched; divergence is settled at approval (R5, or a conflict to redo).
- **Save vs an agent save in review**: other locks and trees; request-changes R1 settles Approve.

### 1.7 Events

Pre-commit refusals are the HTTP error alone. A committed save sends one `graph.delta`
(`user_edit`), or one `error` if its scan or publish fails (`snapshot` stays; the next merge, save
or rescan covers it); B2's failures send one `error`. A rescan sends at most one delta (`rescan`) or
one `error`; GET nothing. A store refusing an `error` is logged by the module's logger, as `aclose`.

### 1.8 Facade and order of work

```python
async def read_file(self, agent_id: AgentId | None, path: str) -> FileContent: ...
async def save_main(self, path: str, content: str, base_hash: str) -> FileSaved: ...
async def rescan(self) -> None: ...   # refusal: `error` event now, WrongState once P1 is approved
```

`Commands` maps `InvalidPath`, `StaleFile`, an unknown agent and `WrongState` to `invalid_path`,
`stale_file`, `not_found` and `wrong_state`, as for approve. Three commits with their tests: (a)
the manager (M2 to M5, the three methods, `_record_merge`), about 250 lines and 500 of tests; (b)
routes and `LiveRun`, 60 and 150; (c) the file view, 200 and 250. `make contracts` shows no diff.

## 2. Frontend

### 2.1 One panel, three modes (`app/view.ts`, `app/Frame.tsx`, `app/App.tsx`)

```ts
export interface FileTarget { path: string; agentId: string | null; startLine: number; endLine: number }
export interface View { selected: string | null; diff: DiffTarget | null; review: string | null; file: FileTarget | null }
```

- `panelOpen` is any of the three; `closePanel` clears all three, keeping `selected`. `Frame`
  renders one panel in the same slot: review, else live diff, else file view; the agents rail
  folds while any is open. Only the person's actions change `View`, never an event.
- `clickNode` keeps today's order for a node a pending change touches (review once proposed, else
  live diff). Any other node opens `file` at its lines from `state.graph`; a module node opens its
  file whole; a removed node opens nothing.
- Replay has no command client: `App` passes no file callbacks; node clicks keep today's behaviour.

### 2.2 The file view (`editor/FileView.tsx`, exported from `editor/index.ts`)

Only `editor/` imports Monaco: `monaco.ts` (local package, bundled worker), `createEditorTheme(
getActiveTheme())`, and `EditorDiff`'s font gate. Colors from theme tokens only.

- **Model**: one per opened file (`python` for `.py`, plaintext otherwise); on a target change the
  new model attaches before the old one is disposed (`showDiff`'s order). Its value is replaced
  only on load, Reload, Discard or a clean refresh (2.4).
- **State** (in the component; Monaco objects never enter a store): `loading`, `clean`, `dirty`,
  `saving`, `stale`, `failed`. Dirty means `model.getValue() !== loaded.content`; the header shows
  the path and "Unsaved edits" while dirty; Save is enabled only when dirty and not saving.
- **Save**: the button and Ctrl+S/Cmd+S send `{path, content, base_hash}`. The shortcut is a Monaco
  command plus a `keydown` on the panel element, both removed on unmount. It cannot fire in review:
  `FileView` is unmounted while `View.review` is set. Approve keeps its pointer-only rule.
- **Answers**: `FileSaved` adopts the returned hash and the sent text as loaded (edits typed in
  flight stay dirty against it). The PUT itself is App's, not the component's (F2, 2.3): an
  unmount settles nothing and drops nothing. `stale_file` shows "This file changed on the server since you
  started editing. Your edits are still here." with **Reload** (refetch, replace the model, drop
  the draft); no "save anyway". Other errors show the server's message in `role="alert"`, keep the
  edits, and leave Save retryable.
- **Agent targets** render read-only, no Save or shortcut, until the request-changes front.
- **Focus**: never `editor.focus()`; opening reveals `startLine` without taking DOM focus. Escape
  closes through the app's existing rule, which yields when Monaco took the key.

### 2.3 Drafts: switching never destroys edits (S4)

- `App` keeps `drafts: Map<string, {content, baseHash}>` in a ref, keyed by path (main only; agent
  targets are read-only), outside the event store: it is the person's state, never an event, and
  replay never sees it. It lives for the page; a browser reload is out of scope.
- `FileView` writes its draft whenever the model turns dirty and on unmount, and deletes it once
  clean, so switching file, opening a review, closing the panel or Escape keeps the text. Opening a
  path with a draft shows the draft with its own `baseHash`; if the fetched hash differs, the
  `stale_file` banner shows at once, before any save.
- **Discard edits** (a labeled header button, while dirty, destructive token) restores the fetched
  content and deletes the draft, as a successful save or Reload does. No confirm dialog: nothing is
  lost without that explicit click.
- **F2, an in-flight save outlives the view.** `App` also keeps `inFlightSaves: Map<string,
  Promise<FileSaved>>` in a ref, one per path (Save is disabled while one runs). `FileView`
  registers the PUT there synchronously when it sends, and `App`, not the component, owns its
  settlement: on the 200 it reconciles that path's draft, mounted or not. If the draft's content
  is the sent text, the draft is deleted (clean); if the person typed past it, the draft keeps
  that text and its `baseHash` becomes the returned hash, so on return the draft is dirty against
  the just-saved content, not stuck on `stale_file`. Any error leaves the draft untouched (the
  `stale_file` banner or a retry on return). Reopening a path with a save still in flight shows
  `saving`, sends no second PUT, and adopts the same settlement when it lands.

### 2.4 Refreshes never overwrite typing (S5)

- **F3, the GET guard.** Each GET records, when issued: its target, the model instance itself
  (identity, not just its path: A → B → A creates a new one), a request generation (a counter
  `App`/`FileView` bumps on every GET issued and on every model change, so only the latest GET
  for a target is current), and `model.getVersionId()` (it grows on every edit). Its answer
  is **current** only if its target, its model instance and its generation all still hold. A
  response that is not current is discarded entirely: no text, no server hash, no banner, so an
  old GET of A can never raise a false `stale_file`. A current response replaces the model's text
  only if, besides, the version id is the recorded one and the state is clean; otherwise the text
  is kept and only the server hash is noted: if it differs from the base, the stale banner shows.
  `getVersionId` alone is not trusted: a recreated model can repeat it, so an old GET of A that
  passes version and clean still fails identity or generation and is discarded. A slow GET for
  file A never writes file B's model or hash; an unmount drops all, and invalidates the
  generation.
- A clean open file refetches when a `graph.delta` arrives whose `commit` differs from the last one
  it saw; M3 makes every main commit send one, a non-Python save or a body-only merge included.

### 2.5 The graph after a save, and replay (S6)

`applyEvent` already folds a delta of any cause: nodes keep stable ids, so the selection ring
survives a moved function; the camera does not move; a node a merge removed leaves the file open
with `selected` matching nothing; `GET /graph` and the top bar's commit follow the delta's.

**Replay guarantee, stated exactly**: a recorded save is one `graph.delta` (`user_edit`) with its
commit, versions and node changes, not the saved text; replay has no file view, so it cannot be
rebuilt from a recording. Section 4 P2 proposes the contract support; nothing depends on it. Rescan
gets no button in this front.

## 3. Tests

As changes.md §7 and request-changes §7: `asyncio.timeout(60)`, waits on events and gates, never
sleeps, every envelope through `TypeAdapter(ServerEvent)`, each delta's `from_version` checked against
the previous `to_version`, real git in `tmp_path`, the fake model only. Doubles: `held_git(after=)`,
`held_publish(...)`, `FaultyWorkspace` (a git call fails, or runs and reports a timeout); scan gates
are a `threading.Event`. * = shown failing with its change reverted.

### 3.1 Backend (`tests/changes/test_main_edit.py`, `tests/api/test_files.py`, one e2e)

- Basics: *test_save_commits_as_the_user* (author `User`, message, one file, HEAD + 1);
  *test_save_rechecks_the_hash_under_the_lock*; *test_identical_save_is_a_no_op*; *test_body_only_save_chains*.
- S3: *test_non_python_save_moves_the_commit*: one delta, equal versions, `commit` the new HEAD.
- B1: *test_save_supersedes_a_failed_merge_delta*: agent-1's delta fails four times (the approval's
  two tries, the save's `_finish_pending` two); the save's delta starts from A and covers both, A→B
  is never sent, the next `open` sends only `done`. *test_save_sends_a_pending_merge_delta_first*:
  two failures; A→B goes, then B→C.
- B2: *test_commit_landing_as_a_timeout_counts_as_saved* (200, one delta, the saved bytes, main
  clean); *test_commit_failure_restores_the_file* (original bytes, HEAD `pre`, trusted, `internal`);
  *test_restore_failure_makes_main_suspect* (Reject still closes).
- B3: *test_cancel_during_the_save_publish_moves_the_snapshot*: `held_publish` commits the delta and
  waits; the request is cancelled; on release `snapshot` is its `to_version`, the request raises
  `CancelledError`, the next save chains. *test_cancel_during_a_held_commit_still_saves*.
- B4: *test_approve_queued_behind_a_failing_save_does_not_merge*: the save is held at a commit
  armed to fail with its restore; Approve queues; on release main is suspect, nothing merges, the
  change is `ready` with one `error`. *test_open_queued_behind_a_failing_save_refuses*.
- F1 (owned by the request-changes front, in its `test_request_changes.py`):
  *test_request_changes_queued_behind_a_failing_save_starts_no_revision*: the save is held at a
  commit armed to fail as above; Request changes passes its app-level check and queues on main's
  lock; on release main is suspect, phase A aborts, the change stays `ready` or `conflict`, no
  revision task runs, one `error`. Shown failing without phase A's recheck.
- S1: *test_cancelled_approval_scan_holds_main_until_its_thread_ends*: the approval's scan waits on
  a gate, the approval is cancelled, a save queues; its first git call follows the thread's end.
- S2: *test_get_main_waits_for_a_held_merge*: a conflicting merge held after `merge`; the GET
  answers after `merge --abort` with the pre-merge bytes, no markers.
- Races and rescans: *test_save_waits_for_a_held_merge_then_saves_on_the_merge_commit*;
  *test_save_on_main_leaves_a_working_agents_change_untouched*; R5's
  *test_combined_tests_after_main_moved* with a save as the move;
  *test_scan_failure_after_commit_still_answers_saved_then_rescan_covers_it*;
  *test_rescan_after_a_commit_behind_the_server*; *test_rescan_with_no_change_publishes_nothing*;
  *test_rescan_refuses_a_dirty_or_merging_main* (one `error`, nothing published).
- API: *test_get_and_save_a_file_on_main_round_trips*; *test_get_file_for_both_targets*;
  *test_get_file_for_a_closed_agent_is_not_found*; *test_agent_save_answers_501_until_the_review_front*
  (worktree, main, log untouched). Error table: `../x`, `/etc/passwd`, `a/.git/config`, a symlinked
  directory, a directory → `invalid_path` with no runtime path; missing → 404; wrong hash → 409;
  replay 501s all three with a malformed body unread.
- E2E: the fake agent proposes, a main file is saved over HTTP, the approval runs R5 and merges on
  the save's commit, both deltas chain.

### 3.2 Frontend (vitest, plus one browser test)

- `app/view.test.ts`: an untouched node opens `file` at its lines; a pending node still opens the
  live diff, then review; `closePanel` and `panelOpen` count `file`.
- `editor/FileView.test.tsx` (Monaco doubled as in `LiveDiff.test.tsx`): load, dirty, Save sends the
  base hash and adopts the new one; `stale_file` and Reload; other errors keep the edits; agent
  target read-only; never focuses. S4: *a draft survives a switch, a review and Escape* (remount on
  the same map: text and `baseHash` back); *a draft over a moved file shows the banner*; *Discard
  deletes the draft*. S5: *a current refresh answering after typing keeps the text, notes the new
  hash and shows the banner*; *a clean file refetches on a delta with a new commit*. F2: *save v1, type v2, open a review before the 200, then return*
  (the PUT held on a deferred promise: on release the unmounted draft's `baseHash` is the saved
  hash, v2 is still the text, and saving v2 answers 200, not `stale_file`); *reopening while the
  PUT is still in flight sends no second PUT*. F3: *A → B → A with the two GETs of A answering out
  of order* (the Monaco double repeats `getVersionId` on the recreated model; the two answers
  carry different hashes and the older one's differs from the base: the newer answer lands first,
  the older one is discarded entirely, so the model keeps the newer text, the stored server hash
  is the newer one and no banner shows); *a GET of A answering after a switch to B* leaves B's
  text, hash and banner untouched.
- `app/fileView.browser.test.ts` (`review.browser.fixture` pattern): click an untouched node → file
  view at its lines, graph unmoved; Ctrl+S → one save; `stale_file` → banner, Reload; edit, open a
  review, Ctrl+S sends nothing, close it → the edits are back.
- Then `make check`, `pnpm -C frontend test:browser`, `pnpm -C frontend build`, replay `make e2e`.

## 4. Contract proposals for the maintainer (not settled; proposal 4.9's process)

- **P1, rescan's refusal (S7).** `POST /graph/rescan` lists `wrong_state` (409) when main is
  suspect, mid-merge or dirty, so a rescan that cannot run says so in its answer. Affects 4.7's
  table, `api`, a route test; no model changes and the code exists, but it alters a frozen error
  set. Until decided the refusal is the 202 plus an `error` (1.5). **Recommended: approve.**
- **P2, saved content in the log (S6).** A 1.x event `file.saved {path, commit, hash, original,
  modified}`, carrying a save as `change.updated` carries an agent's edit, so replay can show it.
  Affects contracts, `changes`, the reducer's exhaustive switch, a replay view, fixtures; a new
  event type is compatible. **Recommended: defer**: replay has no file view, and the demo's story
  is the agents' changes, which carry both versions. Adopt it if the demo records a save on main.

## 5. Decided by the maintainer (2026-10-27)

The maintainer approved the recommended answer to each question below. On question 3 he asked why,
and accepted the reason: creating a file needs a contract change (a null `base_hash` meaning "create,
and fail if the file exists"), a "New file" action, and a new module placed in the preset layout
without moving the rest. It is a separate small front right after editing works, with that contract
change proposed in the contracts 1.4.0 PR.


1. Agent saves answer 501 until request-changes step 2 lands `save_for_review`? **Recommended:
   yes**: one implementation of its re-proposal and Approve race.
2. GET and PUT on any UTF-8 text? **Recommended: yes**; M3 makes a non-Python save visible as a
   commit, and `.py`-only would forbid fixing `pyproject.toml`.
3. May PUT create a missing file? **Recommended: no**: no "must not exist" sentinel in the
   contract; a missing file is `stale_file`. Creation later, by proposal.
4. P1 and P2 (section 4). **Recommended: approve P1** (202 plus `error` until then), **defer P2**.
5. Saves enabled only once R5 (`changes/request` step 1) merges, commit message `Edit <path>`, no
   tests per save? **Recommended: yes** to all three; R5 tests every combination at approval.

## Contracts 1.4.0 tree reservation

`GET /tree?agent=` is reserved and returns 501 in live and replay. Omitted agent selects main;
a supplied agent will select its active worktree. It accepts neither body nor path selector.
The future `TreeResponse` contains sorted unique directory paths and `TreeFile` entries, with a
joint cap of 2,000 and explicit truncation. Hash is a required nullable lowercase SHA-256 of
exact bytes. Null means hashing was incomplete; it does not mean the file disappeared.
A future implementation shares workspace path eligibility, holds the appropriate tree lock,
and spends at most 8 MiB hashing retained files, without partial digests or extra reads.
No listing, hashing, exclusions or graph-version calculation is built in delivery A.

`SaveFile` remains the 1.3 shape: base_hash is a required string for both main and review.
Null and omitted hashes fail validation before any file handler runs. Safe creation, nullable
base_hash, zones and inventory arrive together under 1.5.0.
