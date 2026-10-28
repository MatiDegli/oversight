# Network-isolated test runner (C1)

C1 is opt-in: `Workspace.run_tests(..., spawn=IsolatedRunner(runtime_root))`.
Existing callers keep their current runner. Availability policy and UI notices belong to C3;
Git leases and pins to C2a; metadata snapshots and the final integrity barrier to C2b.
There are no contract changes.

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
