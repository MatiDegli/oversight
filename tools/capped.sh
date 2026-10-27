#!/usr/bin/env bash
# capped.sh <memory-max> <command> [args...]: runs the command under a kernel memory limit of
# <memory-max> (systemd's MemoryMax, such as 10G), with no swap, when systemd can make the cap
# here, and as it is when it cannot (CI, sandboxes, no user manager). It then says so on stderr,
# in one line, so a log never suggests a cap that was not there. There is no `ulimit -v`
# fallback: Node and Chrome reserve far more address space than they use and fail under it.
# The hard layer of the memory caps: the kernel accounts memory per cgroup, a page two processes
# share once, and ends the biggest process of the scope alone, even when a thread of the command
# could not look in time. `systemd-run --scope` puts the command in a cgroup of its own and
# executes it in place, so the pid and the process group are unchanged, and everything the command
# starts is inside the cap. Used by `make check` for pytest (10G), and, through
# frontend/shots/capped.sh, by `make shots` for Chrome and the detector (6G; on 2026-10-26 a
# headless Chrome reached 13.7 GB).
set -eu -o pipefail

if [ "$#" -lt 2 ]; then
  echo "usage: capped.sh <memory-max> <command> [args...]" >&2
  exit 64
fi
limit=$1
shift

# The probe carries the same properties as the real call, so a manager that refuses them sends the
# command to the fallback instead of failing it.
scope=(systemd-run --user --scope -q -p "MemoryMax=$limit" -p MemorySwapMax=0)
if command -v systemd-run >/dev/null 2>&1 && "${scope[@]}" true >/dev/null 2>&1; then
  exec "${scope[@]}" -- "$@"
fi
echo "capped.sh: no memory cap of $limit: this machine has no user systemd, so the command runs without one" >&2
exec "$@"
