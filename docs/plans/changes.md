# Plan: the change manager, level 1 (front 15)

Revision 5, after the re-review: only the latest decision's task may end `deciding`, and a
cancel during a close-out publish waits for the store's answer (sections 1 and 4). Revision 4,
after the implementation review: a close-out stays pending until every event is sent, and a
cancelled decision settles its state (sections 4 and 5). Revision 3: Reject accepts
`broken`, and a halt on a lost worktree root proposes nothing (section 5). Sources: `contracts/`, docs/proposal.md (3, 4.4,
4.5, 4.7, 5), AGENTS.md. Builds on `workspace` and `graph` on main and on `agents/runtime` 64efac4
(`ChangeSink`, `AgentTask`, `RunEnd`, `Publish`), with section 5's change. Settled in round 1: empty
runs are proposed and approve as a merge of nothing; `change.resolved` precedes `graph.delta`;
starting agents stays in front 16.

## 1. Facade and callers

`oversight.changes` exports the class and errors below; internals are `_manager.py` (records,
states, decisions, cleanup), `_versions.py` (file sets, graphs, cone, main's deltas), `_diff.py`.

```python
class ChangeManager:                      # satisfies oversight.agents.ChangeSink structurally
    def __init__(self, workspace: Workspace, publish: Publish, *, main_graph: WorkspaceGraph,
                 main_snapshot: GraphSnapshot, clock: Clock = system_clock,
                 git_timeout_s: float = 30) -> None: ...
    snapshot: GraphSnapshot               # main's graph as last published
    async def open(self, task: AgentTask, model_id: str) -> Tree: ...
    async def file_edited(self, agent_id: AgentId, path: str) -> bool: ...         # ChangeSink
    async def proposed(self, agent_id: AgentId, summary: str | None) -> bool: ...  # ChangeSink
    def run_ended(self, agent_id: AgentId, end: RunEnd | None) -> None: ...
    def approve(self, change_id: ChangeId, comment: str | None) -> asyncio.Task[None]: ...
    def reject(self, change_id: ChangeId, comment: str | None) -> asyncio.Task[None]: ...
    async def aclose(self) -> None: ...   # waits for decisions in flight
class ChangeNotFound(LookupError): ...    # route answers not_found
class WrongState(Exception): ...          # route answers wrong_state
```

- **Wiring (front 16).** `app` scans main, publishes `graph.snapshot` and hands the manager that
  `WorkspaceGraph` and snapshot; from then on the manager owns every main-graph update. The starter
  calls `tree = await open(task, profile.id)`, runs `AgentRun(..., tree=tree, sink=manager)`, and
  calls `run_ended` in a `finally` with the `RunEnd`, or `None` if the run raised. The runtime
  calls `file_edited` after each edit and `proposed` at the end; neither raises (section 5).
- **Routes.** `approve` and `reject` check and flip the state with no `await` before the flip, so a
  second click fails at once; the work runs in a tracked task and the route answers 202.
- **States.** `working` until `run_ended`, then `ready` (proposed) or `unproposed`. Approve needs
  `ready`; reject needs `ready`, `conflict`, `unproposed` or `broken`; both enter `deciding`, which
  always ends, decided from git facts, in `closing` (the decision is a fact), `conflict`, `ready`
  (main untouched) or `broken` (main in an unknown state). `closing` becomes `closed` once the
  close-out is all sent. A decision cancelled before its first step never runs its own code, so a
  done-callback on the task, which runs before anyone awaiting it resumes, puts a change still
  `deciding` back where the decision took it from, or in `broken` once the merge was issued.
  Each decision taken gets the next number, and the callback acts only if its number is still the
  latest: a failed Approve that went back to `ready` while its `error` is going out may be retried,
  and its end must leave the retry `deciding`, or a Reject would race the retry's merge.

## 2. Live diff

- **`open`** calls `workspace.create_worktree` (main's and the agent's locks), then under the
  agent's lock reads every UTF-8 file of the fresh worktree, exactly `base_commit`, into
  `base_files`, and parses the Python ones with `parse_version`, cached by commit.
- **`file_edited(agent, path)`**, under the agent's lock: add `path` to `touched`; read it (missing
  means deleted); `original = base_files.get(path)` (missing means added); update `head_files`;
  `head = parse_version(head_files, base_commit, previous=head)`, re-parsing only that file;
  `graph = change_graph(base, head)`. `difflib.SequenceMatcher` counts lines for every event;
  `node_ids` are the graph's nodes in `path`; totals count files that differ from the base. It
  publishes `change.updated(author="agent")`, keeps the `modified` it sent in `published[path]`
  and returns `True`, so the event precedes the edit's focus and step (4.5). Pending nodes are that
  whole `ChangeGraph`; main's graph is untouched, so the overlay lives in events alone.

## 3. Propose

`proposed(agent, summary)`, under the agent's lock; the runtime sends its last status after it:

1. `git add -A`, `status --porcelain`, and if dirty `commit` through `Workspace.git_ok` as the
   agent, dated from the injected clock, so fake runs repeat their hashes. `head_commit` is HEAD:
   `base_commit` when nothing changed.
2. Files from `git diff --name-status --no-renames -z base head`, the authority on what merges;
   `head_files` and `head` are refreshed for them from the clean worktree, which equals head.
3. **Republish**: each path there or in `touched` whose head content differs from
   `published[path]`, or was never sent, gets a full `change.updated(author="agent")`, so replay
   holds both versions of every proposed file whatever failed during the run.
4. `workspace.run_tests(tree, timeout_s=limits.test_timeout_s)`, demo venv; a timeout is a result.
5. `change_cone(graph, head.graph, max_depth=..., max_nodes=...)` from the task's limits.
6. Publish `change.proposed`, revision 1 (`summary` is `None` after a halt); return `True`.

## 4. Approve and reject

**Approve** holds main's lock, then the agent's, from the first git call to the last cleanup step:

1. The worktree is clean at `head_commit`, committed at propose: approve merges what was tested.
2. `pre = rev-parse HEAD` on main; `merge --no-ff --no-commit <head_commit>`; then, if `MERGE_HEAD`
   exists, `commit -m "Merge change-1 (agent-1)"`; as `user_author()`, clock-dated. `--no-ff` is
   on every merge: each level-1 head descends from main, and `--no-commit` alone fast-forwards,
   leaving HEAD moved and nothing for `merge --abort` to undo (checked in a scratch repository).
3. Merged means `merge-base --is-ancestor <head_commit> HEAD`, main clean, no `MERGE_HEAD`. An
   empty change is "Already up to date": merged, `merge_commit = pre`.
4. `new = main_graph.scan(post)` in a thread re-parses only files whose content hash changed.
5. Close-out (`approved`, `merged`, `merge_commit`, comment), then cleanup with `merged=True`
   once `change.resolved` is out.

**Versions.** `version` hashes the path and bytes of every importable Python file (`assemble` in
`graph/_version.py`): it moves exactly when a merge (at level 2 also a save on main or a rescan)
changes a Python file or their set; `commit` moves with every merge commit. `graph_delta` answers
`None` when nodes, edges and parse errors are equal, even if the version moved: `return 2` to
`return 3` keeps every span (checked). `applyEvent.ts` refuses a delta whose `from_version` is not
its graph's, and resuming replays the same events, so one skipped delta stalls the graph for good.
So every merge publishes exactly one `graph.delta`: `graph_delta`'s, else an empty one from
`old.version` to `new.version` with `commit=post` (equal after an empty merge; the reducer checks
only `from_version`). `snapshot` changes only once the delta from it is out; a scan that raises
sends an `error` and keeps it, so the next delta starts from what the UI holds.

**Close-out** is the only way into `closed`: `change.resolved`, the delta (approve only), then
`agent.status` `done` from `open`'s task and `RunEnd.steps`. Each is marked on the record when sent,
so nothing goes twice. Each publish runs in its own tracked task: a cancel of the decision waits
for the store's answer, marks what was committed (and moves `snapshot` after a delta), then goes
on, since the store may commit an event whose caller was cancelled; a second cancel stops waiting. It runs as soon as the decision is a fact and before cleanup, so no cleanup
failure can hold back or repeat a resolution. A decision tries the missing events twice, in order;
if one still fails, an `error` says so and the change stays `closing` and pending, with its
worktree and branch: cleanup runs only once `change.resolved` is out. The retry runs, under main's
lock and the change's, before the next decision's work and in the next `open`, each trying twice
again; approve and reject answer `WrongState` on a `closing` change. A pending delta that a later
merge's delta overtakes is dropped, not sent: the later one starts from the graph the UI holds,
`snapshot`, which the unsent delta never moved, so it covers both merges, and the older one would
start from a graph the UI no longer holds.

**Reject** holds both locks and closes out at once: `rejected`, the comment, `revision` 0 if never
proposed; no rescan and no delta, as main did not change. Then cleanup with `merged=False`, once
`change.resolved` is out.

**Cleanup** is idempotent by facts, each step skipped once done: (a) if `worktree list --porcelain
-z` lists the agent's path and the directory exists, `discard_worktree`, or after a merge
`cleanup_merged_worktree`, with `delete_branch=False`; listed but gone, `worktree prune`; (b) if
`rev-parse --verify --quiet refs/heads/<agent>` finds the branch, `branch -D`, or `-d` after a
merge so an unmerged branch is never deleted. A failure sends an `error` and keeps the change pending; the next decision or `open`
reruns cleanup first. A workspace guard's refusal is reported,
never forced, and the next start's `reset` clears what is left.

## 5. Failure paths

Every git call is `Workspace.git`: asyncio, with a timeout that kills the process group. Every
`error` carries the ids and text passed through `workspace.redact`.

- **Conflict** (impossible at level 1; kept for level 2): the merge exits 1, paths from `git diff
  --name-only --diff-filter=U -z`; `merge --abort`; main clean at `pre`; `change.resolved`
  approved, `merge="conflict"`, `Conflict(kind="merge", paths)`; state `conflict`.
- **A merge or commit timeout, another failure, or a bug** in the decision task: `merge --abort`
  if `MERGE_HEAD` exists, then the facts. Step 3 holds: close out as merged. Main clean at `pre`:
  `error` ("main is unchanged; approve again or reject"), back to `ready`. Otherwise, or if the
  facts cannot be read: `error`, `broken`; approve answers `WrongState` until the next start,
  and Reject still works: `broken` is among the states it accepts, so it closes out as usual
  (`rejected`, then `done`) and discards the worktree and branch, leaving main to the next start's
  `reset`, even when an unconfirmed merge landed. No `reset --hard`, no lock-file removal.
- **An approval cancelled** inside the locks recovers the same way before the cancellation goes
  on: still under both locks, `merge --abort` if `MERGE_HEAD` exists, then the facts. A landed
  merge closes out as merged and leaves its cleanup pending; main clean at `pre` is `ready`;
  otherwise `broken`. Each sends an `error` saying the approval was cancelled.
- **Propose fails** at step 1, 2, 3 or 5: an `error` with `fatal` true, no `change.proposed`,
  `False`; the runtime halts, `run_ended` makes the change `unproposed`, and Reject closes it with
  revision 0. Tests that cannot start give `outcome="error"` and an `error`; the proposal goes.
- **`file_edited` fails**: an `error` (`fatal` false), the path joins `unpublished`, `False`. The
  next successful `file_edited` republishes `unpublished` first; propose step 3 catches the rest.

**Runtime change, for front 12's next round** (`agents/sink.py`, `agents/loop.py`): `file_edited`
and `proposed` return `bool`, `False` meaning the event was not sent and an `error` was. On `False`
from `file_edited`, `_report` sends that edit's `agent.focus` with its ranges and `node_ids=[]`,
since the edit's new nodes never reached the UI. On `False` from `proposed`, `run` publishes
`halted` with detail "Its change could not be proposed." instead of `ready_for_review`, and returns
`RunEnd("halted", detail, steps, None)`. Any other exception from the loop, save cancellation, is a
halt with a fatal `error`, so every run that is not stopped sends a last status and calls
`proposed`, with one exception: when the first scan finds the worktree's root lost (the runtime's
`_RootLost`, a root swapped for a symlink), the run halts without calling `proposed`, since the
sink would read a tree that is no longer the agent's. `run_ended` then makes the change
`unproposed` and Reject closes it with revision 0; cleanup meets the workspace's guard, which
refuses the swapped root, and reports it. The reducer needs no change: the agent card and review mode (fronts 13, 17) offer Reject
when the agent is `halted` and its change has no proposal.

## 6. Level 2, kept out but not blocked

- **Main moved** (`pre != base_commit`): the combined tests run in main between step 2's calls; a
  failure is `merge --abort` and `Conflict(kind="tests")`. A head redone on the new base descends
  from main, and only `--no-ff` lets that abort restore `pre`.
- **Request changes** from `ready` or `conflict` keeps worktree, base and `head_files`; the run
  resumes with the comment and proposes revision 2. **User edits** on the new side are
  `change.updated(author="user")` under the agent's lock; saves on main and rescans follow the
  version rule. **Two agents**: records per agent, deltas serialized under main's lock. **Stop**
  cancels the run and reuses reject with `stopped`.

## 7. Tests: `uv run pytest backend/tests/changes`

A small template (`value`, a `twice` that calls it, one test), a real `EventStore` in `tmp_path`,
`demo_python = sys.executable`, `ScriptedModel`, never Meta's API. Each test runs under
`asyncio.timeout(60)`, waits on events or the decision task, never sleeps, validates every envelope
with `TypeAdapter(ServerEvent)`, with no temporary path, and checks that each delta's `from_version`
is the previous snapshot's or delta's `to_version`. A `Workspace` subclass whose `git`, once armed,
fails or times out one subcommand in one tree drives sections 4 and 5. Tests marked * are shown
failing with their change reverted.

- `test_live_diff.py`: original, modified, counts and `nodes_modified` for an edit of `value`; an
  added function in `nodes_added`; `change.updated` precedes focus and step;
  *`test_failed_edit_republished`: invalid UTF-8 makes `file_edited` return `False`, the test then
  writes a valid edit with no call, and `proposed` sends that file in full before `change.proposed`.
- `test_propose.py`: head's parent is base; tests pass, then fail after a breaking edit; `twice` at
  distance 1; `change.proposed` is committed before `proposed` returns, so it precedes the
  runtime's `ready_for_review`; a halt proposes too.
- `test_approve.py`: main's HEAD has parents `pre` and head; resolved, delta, `done`, in order;
  worktree and branch gone; early, repeated or unknown approves fail; *`test_body_only_edit_chains`:
  `return 2` to `return 3` sends one empty delta to the new version, and agent-2's approval chains.
- `test_reject.py`: worktree and branch gone, main unchanged, resolved with the comment, no delta;
  *`test_reject_after_a_halt_with_no_proposal`: the root swapped for a symlink and `run_ended`
  with a halt and no `proposed`; approve answers `WrongState`, Reject closes revision 0 with
  `done`, and the guard's refusal is an `error`.
- `test_failures.py`: a real conflict from a commit on main, main clean after; a faked merge
  timeout leaves main unchanged and a retry merges; a merge reported as a timeout counts as merged;
  *`test_commit_failure_aborts_to_pre`: `commit` fails after the no-commit merge, main is clean at
  `pre` without `MERGE_HEAD`, the change is `ready`, a retry merges (without `--no-ff`, HEAD has
  fast-forwarded and it fails); *`test_cleanup_failure_still_closes`, for reject and approve:
  `branch` fails once, resolution and `done` go once with an `error`, a second decision answers
  `WrongState`, agent-2's `open` deletes the branch; *`test_failed_propose`: a failed commit
  returns `False` with a fatal `error` and no `change.proposed`, and Reject closes with revision 0; *`test_reject_from_broken`: the merge times out and `status`
  fails, so the facts cannot be read; approve answers `WrongState`, and Reject closes with the
  comment, `done` last, worktree and branch gone, main clean at `pre`;
  `test_reject_from_broken_after_an_unconfirmed_merge`: the merge commit lands but reports a
  timeout and `status` fails; Reject leaves main at that commit, sends no delta and closes;
  *`test_decision_cancelled_before_it_starts`, for approve and reject: cancelled before its first
  step, the change is back in `ready` and Reject closes it; *`test_cancel_mid_merge_aborts_to_pre`:
  cancelled after `merge --no-commit`, main is clean at `pre`, an `error` says so, a retry merges;
  *`test_cancel_after_the_merge_commit_records_it`: the landed merge closes out, then an `error`,
  and the next `open` cleans up; *`test_stale_decision_leaves_a_retry_deciding`: Approve fails and is
  retried while its `error` is held; once the first task ends, Reject answers `WrongState` and the
  retry merges.
- `test_close_out.py`: a delta cut short once goes on the second try;
  *`test_cancel_during_a_publish_records_what_went_out`: the approval is cancelled after the store
  commits the delta and before its receipt; the next `open` sends only `done`, the chain holds; *`test_close_out_stays_pending_until_sent`:
  both tries of a reject's resolution fail, so worktree and branch stay and nothing is sent; the
  next `open` sends the resolution and `done`, then cleans up; *`test_pending_delta_is_retried_or_superseded`:
  agent-1's delta fails twice, then agent-2's approval sends it first, or, failing there too,
  agent-2's delta covers both merges and agent-1's is dropped, its `done` sent at the next `open`;
  a rescan that raises keeps the snapshot; tests that cannot start still propose; cleanup prunes a
  worktree directory already gone.

About 450 lines of code and 550 of tests, in two commits: live diff and propose, then decisions.

## Open

1. Section 5's runtime change, for front 12's next round; until then these tests call the sink
   directly, and `ChangeManager`, whose sink methods return `bool`, matches `ChangeSink` only once
   it lands. Halting departs from 4.5's "proposed as it stands" only when no proposal can be built.
2. The Reject rule for a `halted` agent without a proposal, for fronts 13 and 17.
3. No contract change: an empty `graph.delta`, with equal versions or not, is valid.
