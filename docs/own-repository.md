# Opening the person's own repository

Moved from AGENTS.md on 2026-11-01 with its authority: change these rules only with the maintainer's approval.

Native Windows own-repository COPY startup is implemented through W4c2. Changes to Git and integrity
still require a plan and double review. The demo path (`demo-repo/`, `make demo`, `make demo-check`)
does not change.

On native Windows, Oversight opens a validated disposable copy of the selected repository and never
writes changes back automatically. The session-branch, follow-main, personal-edit-layer and
final-integration workflow below is a later design, not current COPY behavior.

## Future session design

- Oversight creates a session branch
  (`oversight/session-...`) at their last commit, checked out in a worktree under `.runtime/`,
  and that branch is Oversight's main. Agents branch from the session's tip; their worktrees
  are worktrees of the person's repository under `.runtime/worktrees/`.
- When the person commits on their own branch, the session takes it in with the follow-main logic.
- The graph shows the session plus the person's uncommitted edits as a separate "your edits"
  layer, drawn as its sketch decides: color stays reserved for teams and states. Approval
  warns when an agent's change touches lines the person edited.
- A file watcher (watchfiles, MIT) only triggers the existing rescan path. It debounces saves,
  skips while a merge or another main write holds the git lease, and emits the usual
  `graph.delta` with cause `rescan` (no contract change). On the Linux filesystem it uses
  native events; under `/mnt/c` it polls more slowly and says so at start.
- The session reaches the person's branch in one final step: a click with no keyboard
  shortcut, or by rules under its own standing grant, off by default and revocable. Either
  way it runs only after the tests pass on the combined result, only as a clean merge or
  a fast-forward, and never when the person's uncommitted edits touch the same files.
  Oversight never stashes in the person's repository; a conflict goes to "needs you".
- Opening a repository shows a trust prompt once per repository: its tests and probes run
  its code as the person, with no filesystem sandbox, and one line names the providers that
  receive the folder's code (today Meta Model API and Baseten). It informs and does not block.
- Tests run with the repository's own `.venv` Python when there is one; otherwise they show
  as unavailable.
- Agent commits keep the repository's git hooks disabled.

## Current COPY test limits

Set `OVERSIGHT_TEST_TIMEOUT_S` before starting an own-repository run (seconds, default 60;
no policy maximum, with a nine-significant-digit parsing bound). Demo and Boltons keep 60 seconds;
public native Windows COPY selection is enabled; native qualification remains pending. The agent's
time limit per round is
`max(600, 3*T + 300)` seconds. Both stay fixed for the run; the model cannot change them.
The console says `Test limit: {T} s. Agent time limit: {W} s per round. These settings stay fixed for this run.`
For a nondefault setting on demo/Boltons it also says
`OVERSIGHT_TEST_TIMEOUT_S applies only to your own repositories; this run keeps 60 s.`
A long limit holds main's lock and the Git lease while the suite runs: other agents' tests and
Approve wait. Stop ends the suite. A timeout reports incomplete results with an unknown cause;
set a different limit when starting the next run.

## Native platforms after opening a repository

Moved from AGENTS.md on 2026-11-01 with its authority: change these rules only with the maintainer's approval.

The minimal native Windows backend precedes Tauri; validated own-repository COPY startup is enabled.
Native qualification remains pending. Tauri, broader platform ports, macOS and native Windows
isolation remain later work.

## Current Windows delivery (2026-11-02)

DECISIONS.md's copied-own residual approval supersedes the older session-only scope above.
Public native Windows COPY startup copies raw bytes, protects prior runtimes and uses only the
validated original venv, with imports mapped to the tested copy and guarded result admission.
Neither Windows machine has qualified this workflow; advisory CI remains unrequired.
See [trust, limits and the fake template](workspace-source.md#chosen-repository-template).
