# Opening the person's own repository

Moved from AGENTS.md on 2026-11-01 with its authority: change these rules only with the maintainer's approval.

The maintainer decided this on 2026-10-31. Build only after the cut line, and only if time
remains, after a plan that gets the double review, because it touches git and integrity.
The demo path (`demo-repo/`, `make demo`, `make demo-check`) does not change.

- Oversight never copies the person's repository. It creates a session branch
  (`oversight/session-...`) at their last commit, checked out in a worktree under `.runtime/`,
  and that branch is Oversight's main. Agents branch from the session's tip; their worktrees
  are worktrees of the person's repository under `.runtime/worktrees/`.
- When the person commits on their own branch, the session takes it in with the follow-main logic.
- The graph shows the session plus the person's uncommitted edits as a separate "your edits"
  layer, drawn as its sketch decides: color stays reserved for agents and states. Approval
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

## Native platforms after opening a repository

Moved from AGENTS.md on 2026-11-01 with its authority: change these rules only with the maintainer's approval.

After level 2, once the cut line is drawn and only if time remains, build opening the
person's own repository first. After the hackathon, the Tauri shell comes next, with
the backend inside WSL; the native Windows and macOS backend ports follow it.
Item 20, the minimal native Windows port, therefore moves after the hackathon and
after the Tauri shell: a per-OS layer in `backend/src/oversight/workspace/`
(process-tree cleanup, file locks, no-follow file opens) that runs in the existing
mode without isolation (network open; rules, Revert and added-test probes disabled).
Windows isolation also remains after the hackathon.
