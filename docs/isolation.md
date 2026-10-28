# Network-isolated test runner (C1)

C1 is opt-in: `Workspace.run_tests(..., spawn=IsolatedRunner(runtime_root))`.
Existing callers keep their current runner. Availability policy and UI notices belong to C3;
Git leases, pins and merge commit checks belong to C2a; metadata snapshots/restoration to C2b.
There are no contract changes.

## Pre-Git integrity (C2a)

All server Git shares a task-reentrant lease with test-tree capture, suite execution and
cancellation cleanup. Tests acquire main before the worktree; tree locks precede the lease.
The public workspace lease also supports the future temporary Revert checkout: acquire
main, temporary tree, then lease. No mode lock may be acquired while retaining the lease.

Git's directory, common directory and working tree are pinned to the server-created
registration, not discovered from a mutable `.git` pointer or `core.worktree`. Capture uses
the same environment and changes only `GIT_INDEX_FILE`. Replacement objects, hooks and
fsmonitor are disabled. `diff.ignoreSubmodules=all` and `--ignore-submodules=all` on status
and diff are a second layer; they do not protect add, diff-files or capture.

Before each tree-reading Git, a filesystem walk checks all entries, including ignored ones,
without following symlinks. Names are compared casefolded; only the registered root's literal
non-symlink `.git` entry is exempt, with its registered kind and exact linked-tree pointer.
Reset drops stale registrations and keeps only main. Capture walks once before its first Git.
Each walk and lease wait uses the caller's Git deadline; an unreadable directory or an expired scan cannot
launch Git. Ordinary symlinks are not traversed. Concurrent filesystem writers outside the
suite remain the filesystem-isolation residual described below.

The scan exemption allowlist is `rev-parse`, `rev-list`, `merge-base`, `write-tree`, exact
`ls-files -s -z`, and three-argument `cat-file -p/-e/-t/-s OBJECT`. All other invocations are
scanned, including unknown commands, `cat-file --filters`, `diff-index` and `merge --abort`.
`worktree remove` scans its registered target, since that is the tree Git inspects.
No broad `cat-file` or `worktree` exemption is safe. Every Git result, including capture and
raw yes/no queries, refuses truncated output without retrying effects.

Proposal commits inspect every NUL-delimited index stage for mode 160000 before add and
again before commit. Tracked, untracked and ignored `.gitmodules` entries are also rejected.
Capture applies the veto to its temporary index before add. Refusals use
`code=integrity_invalid; reason=...` with a bounded, JSON-escaped path (semicolons escaped too).
Scan I/O errors use `integrity_invalid`; deadline exhaustion and incomplete index queries use
`git_failed`. An uncertain approval recovery uses `main_suspect`. In particular, a nested
repository left on main blocks `merge --abort` as well: main stays suspect until restart,
with its pending merge intact. Unknown index, HEAD and main-ref locks are never removed.

C2a captures the candidate with `write-tree` before a combined suite and fingerprints the real
index. After a passing suite, and again after publishing its evidence immediately before commit,
it verifies that fingerprint and the merge markers before Git can refresh anything, then runs
`write-tree` before status: index = tested tree = candidate. The gitlink/module veto runs again;
HEAD must still point to the captured main, ORIG_HEAD must name it, and MERGE_HEAD must contain
exactly the proposed head plus one newline. Working-tree, cached-index and untracked queries
must agree. Optional index refreshes are disabled for server Git (`GIT_OPTIONAL_LOCKS=0`).
Without a main advance the existing proposal-tree path remains, checked against the exact
agent commit tree, without claiming a new combined suite.

C2a does not snapshot or restore the full Git metadata or real index. The fingerprint is a
tamper tripwire, not a restorable snapshot. Before abort, the captured previous main and exact
merge markers must still be verifiable; mismatches and unknown locks leave main suspect rather
than resetting it from an untrusted ORIG_HEAD. Snapshot-dependent metadata comparison and
verified restoration remain C2b.
Availability policy, execution discrimination and CI integration belong to C3. The attack
tests retain pointer, config, fsmonitor, hooks, alternates, shallow, replacement refs, branch
refs, index, MERGE_HEAD and abort cases; the metadata matrix asserts the pre-Git refusal,
not C2b restoration. The regression executes a real agent suite leaving a malicious nested
repository, and checks a filter sentinel after every server Git through proposal capture,
as-clicked approval, preparation and the combined suite. Removing the scan and veto locally
must make capture execute the filter and fail that regression.

Run the optional kernel probe from the repository root:

```sh
uv run --frozen python -m oversight isolation-probe
```

It uses `.runtime/isolation-probe/.runtime`, its own runtime lock and demo venv. It resets only
that probe workspace. It runs the demo suite through the real isolated runner, with a localhost
listener ready before entry. JSON reports the outside connection, the denied inside connection,
the authenticated entry identities, the suite, kernel and util-linux versions. `connect_errno`
is ECONNREFUSED (111), or ENETUNREACH (101) when the new namespace's loopback is down. Missing or
blocked namespaces report `status: unavailable`; a failing suite reports `status: failed`.
Neither result runs the suite with network access. No provider API is called.

`make check` uses injected launch/confirmation paths and does not require user namespaces.
Optional real-kernel lifecycle tests can also be run explicitly:

```sh
OVERSIGHT_ISOLATION_KERNEL_TESTS=1 uv run --frozen pytest backend/tests/workspace/test_isolation_kernel.py
```

These cover normal/failing exit, timeout, cancellation, server SIGKILL, and a detached child
holding stdout/stderr open. A pidfd pins namespace init until it exits; Linux tears down its
namespace members before that exit. Cleanup signals only the exact supervisor PID and retains
the runner's 2 + 5 + 1 second bounds. Entry evidence travels over private pipes, never stdout or
JUnit, and both descriptors close before pytest starts. The helper validates its visible private
proc mount and that the server's cmdline, cwd, fd, maps and environ paths are absent.

An orphan signature found under the runtime lock prevents startup before reset. Its diagnostic
names the exact PID and command for the operator; the server does not kill it automatically.
Launch programs are constant isolated-mode Python strings, never runtime files. The launcher
sets parent-death SIGKILL, checks its parent and refuses setuid/setgid or file capabilities on
unshare. C1 removes network access; it does not isolate the filesystem or protect the interpreter,
stdlib, venv, backend or other writable files against a deliberate filesystem attack.
