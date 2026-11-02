# Network-isolated test runner (C1)

C1 provides `IsolatedRunner`; C3 selects it automatically for sessions born with C.
The caller policy and startup notice are described below. Git leases, pins and merge commit
checks are C2a; metadata snapshots/restoration are C2b.
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

## Suite boundaries (C2b)

Every `Workspace.run_tests` call, including selected-case base/head probes and the kernel probe,
captures metadata before capture's first Git. The snapshot retains common config/config.worktree,
info, hooks and configured hooksPath directories, the linked-tree pointer and admin directories,
HEAD, branch and replacement refs, packed-refs, objects/info (including alternates), shallow and
all real indexes, including absence. Main and the other admin directories in the common repository
are covered too. Object data is excluded; capture's temporary index lives separately in tmp.

Reads use descriptor-relative O_NOFOLLOW on every directory and file, reject nonregular files,
hardlinks and symlinks, and enforce 10-second, 16,384-entry and 128-MiB aggregate budgets. A file
is limited to 4 MiB, or 64 MiB for an index. Config includes and unsupported hooksPath syntax
fail closed rather than allowing Git to discover more inputs. Admin roots are pinned by device
and inode. There is no filesystem sandbox: concurrent external writers remain a residual.

After runner cleanup, while retaining main/worktree locks and the Git lease, C2b compares and
restores changed metadata once. It rewrites files atomically and rereads the complete snapshot;
refs and pseudorefs use validated names/contents and an exclusive-lock compare-and-swap. The
real index also requires its exclusive lock. Existing locks are never removed, and no reset is
used. Unconfirmed namespace death skips comparison/restoration entirely. Unknown locks, changed
admin identities, unreadable metadata, failed CAS or failed readback latch `main_suspect` and
block all server Git and Approve until restart, without retrying an effect.

Successful restoration still refuses the run with `integrity_invalid`. Every metadata discrepancy
latches `workspace.integrity.rules_suspended` until a new server session. C3 consumes this latch;
it is independent of runner availability, so verified restoration permits further isolated suites.
Ordinary working-file changes, including ignored out.json/.coverage artifacts, add an
`integrity_invalid` problem to that execution's evidence (cases_complete becomes false), without
suspending session authority. They do not restore working files or relax C2a's commit barriers.
Before abort, the previous main and exact merge markers must still verify; uncertain recovery
keeps main suspect rather than resetting it from an untrusted ORIG_HEAD.

Availability policy, execution discrimination and CI integration belong to C3. The attack
tests retain pointer, config, fsmonitor, hooks, alternates, shallow, replacement refs, branch
refs, index, MERGE_HEAD and abort cases. C2b's matrix runs real tampering pytest suites on main
and linked trees, then verifies restitution or main_suspect and the persistent authority latch.
It also covers lock/CAS races, readback failure, cancellation, index absence and local artifact
vetoes. Removing restoration locally makes the later server `add` execute the filter sentinel.
The C2a regression executes a real agent suite leaving a malicious nested
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

## Session availability and caller policy (C3)

At server startup, after the runtime lock and before reset, one automatic check exercises
C1's trusted launcher, authenticated helper confirmation and bounded cleanup. A localhost
listener confirms the outside connection and the helper must confirm its denied inside
connection. It executes only a constant `pass` with the server interpreter, never pytest,
never the demo venv and never a provider API. This is separate from local delivery acceptance.
`workspace.isolation` is a read-only snapshot: checked, born_with_c, invalidated,
rules_suspended, reason, available and rules_available. C2b's suspension remains independent
of C availability: restored Git tampering suspends rules but permits isolated suites.

| Caller | Policy | With C | Born without C |
| --- | --- | --- | --- |
| Agent run_tests | best_effort (default) | Isolated | Network open |
| Proposal suite | best_effort (default) | Isolated | Network open |
| Human combined suite, Ask me | best_effort (default) | Isolated | Network open |
| Rules combined suite and Revert | required | Isolated | Refused with reason |
| Measured-cone base/head suites | required | Isolated | Evidence unavailable |
| Added-test base/head probes | required | Isolated | Skipped; facts unknown |

Required isolation is passed by `changes/_probes.py:_run`, `_merge.py:combined_tests_pass`
(for rules), `_measured.py:_execute` and `_revert.py`. Unavailable added-test probes publish no
suite execution and retain unknown facts with an unavailable diagnostic. Required refuses before
suite capture or execution; it never falls back. `changes/_mode.py:start_capability` and
`_revert_port.py:RevertPort.revert` consult rules_available before admitting rules or Revert.
Rules activation is open (`RULES_ACTIVATION = True`). Each fresh run starts in Ask me; explicit
rules selection still requires this session's C and all other start conditions. Later evidence
and transaction guards remain mandatory; clean rules merges offer person-only Revert.

A session born without C never enables C later, rules, their probes or Revert. Ask me needs
no acceptance step: startup emits a log warning and an existing nonfatal `error` event from
`tests`, explaining that tests retain network access and rules/probes/Revert stay disabled.
Settings shows the server's refusal verbatim. A session born with C never selects the
open runner, including after invalidation. Only failed isolation effect/entry/cleanup evidence
invalidates C; ordinary suite failures, timeouts and artifact vetoes do not. Git metadata
changes use C2b's rules_suspended latch, without invalidating C after verified restoration.
No stable cleanup/snapshot guarantee is claimed for the open path.

CI runs `uv run --frozen python -m oversight isolation-probe` in one additional step and
writes the JSON verdict to the job summary. `confirmed` and `unavailable` succeed;
`failed` (or an unrecognized verdict or command error) fails the step. No action, permission
or secret is added. The optional full probe above remains the local acceptance command;
it does not establish or upgrade a running server's capability.

The maintainer accepted the filesystem residual: C removes the network; it does not isolate
the filesystem; it closes known Git routes and vetoes what it cannot attest. Read-only mounts
are outside this package. C3 changes no contracts.

### W1 native Windows startup boundary (2026-11-01)

W1 introduces an injectable stdlib Windows seam: seek-zero byte locking, suspended launch into
kill-on-close Jobs, descendant cleanup before return, and a process-lifetime server Job whose
handle is retained until process teardown. A nested-job probe precedes reset. Unknown cleanup
refuses evidence while the existing suite plumbing retains partial reports. Windows retains
OPEN_NOTICE: no network isolation, rules, Revert, behavior or measured evidence.

Startup requires short, private, local nonsynced NTFS placements; rejects known sync roots,
reparse/offline/recall ancestors, UNC/device/ADS/alias inputs, SUBST and mapped/network drives;
and warns outside USERPROFILE. Checks do not establish a filesystem sandbox or harden inherited
ACLs. Demo-only acceptance covers destination races and inherited ACLs; it does not authorize
own-repository use. An unregistered sync client is not reliably discoverable from filesystem
attributes: the operator must choose nonsynced folders. Linux-injected checks are not native
proof. Non-elevated Windows acceptance of Jobs, nested jobs, Ctrl+C shutdown logging, Defender
sharing/retries, Git localization and three restarts remains required. W2a and W2b must land
before native tree/files, agent read/edit/run_tests, proposal or Approve acceptance.

### W3 advisory Windows supervision (2026-11-01)

W3 adds console-attached tool supervision through W1's public Job primitives, plus retained
logs and one-byte runtime-lock probes. Outer driver and per-server kill-on-close Jobs cover
launcher and Git descendants even if their console groups differ. Empty-job evidence precedes
scratch removal; forced termination cannot certify graceful shutdown. The native driver-death
proof holds process handles only, never an external handle to the driver's outer Job.

Linux-injected tests establish ordering, failure cleanup, cancellation, polling and verdict
policy. They establish no Windows kernel or console acceptance. Actual SIGBREAK code/log,
nested Jobs, cold uv/backend death, driver death, read-only and locked-directory cleanup remain
native checks. The candidate `0xC000013A` status is unverified until the console test passes;
console/environment skips must remain visible. Full driver acceptance also awaits W2a + W2b.

Windows still has OPEN_NOTICE, open network mode and no filesystem sandbox or memory cap;
`tools/memwatch.py` returns zero without `/proc`. Demo-only destination-race/inherited-ACL
acceptance above is unchanged. W3 grants no own-repository permission. W4 qualification and
complete two-machine/six-run evidence remain pending. Advisory failures are not swallowed;
30-minute workflow and three-minute smoke bounds supplement the driver's phase/overall clocks.

### Copied own repositories: approved residual scope (2026-11-01)

The maintainer's acceptance now includes copied own repositories: concurrent path replacement
between check and open, and inherited folder ACLs on copies/logs. This supersedes the demo-only
scope above (see DECISIONS.md). It adds no isolation or ACL hardening. Tests/plugins execute
trusted code as the user, with network and potential writes outside runtime; child interpreters
may import original source beyond the future guard. Public trust disclosure remains W4c2 work.
W4a1 provides internal selection only; every public NT repository path still refuses.
