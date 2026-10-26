#!/usr/bin/env bash
# capped.sh <command> [args...]: runs the command under a memory cap of 6 GiB, with no swap, when
# systemd can make the cap here, and as it is when it cannot (CI, sandboxes, no user manager).
# Chrome is the reason: on 2026-10-26 a headless Chrome reached 13.7 GB. `systemd-run --scope`
# puts the command in a cgroup of its own and executes it in place, so the pid is unchanged and
# everything the command starts, Chrome included, is inside the cap; the kernel ends the biggest
# process of that cgroup alone if it goes over, and the rest of the machine never feels it.
# The one helper of `make shots`: run.sh puts the captures under it and detect.sh the detector.
set -eu -o pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: capped.sh <command> [args...]" >&2
  exit 64
fi

# The probe carries the same properties as the real call, so a manager that refuses them sends the
# command to the fallback instead of failing it.
scope=(systemd-run --user --scope -q -p MemoryMax=6G -p MemorySwapMax=0)
if command -v systemd-run >/dev/null 2>&1 && "${scope[@]}" true >/dev/null 2>&1; then
  exec "${scope[@]}" -- "$@"
fi
exec "$@"
