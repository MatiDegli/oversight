#!/usr/bin/env bash
# bounded.sh <seconds> <command> [args...]: runs the command under an outer time bound of <seconds>
# and says so when the bound is what ended it. After <seconds> `timeout` ends the command's process
# group with TERM, and BOUND_GRACE_S seconds later (30 by default) with KILL. The command's own exit
# status comes through; when the bound ended it, one line on stderr names the bound, so a CI log and
# a local run both say what happened instead of a bare 124 or 137.
# The bound of the whole pytest run of `make check`, setup and teardown included, which
# pytest-timeout leaves open (tools/capped.sh is the memory side of the same run).
set -eu -o pipefail

if [ "$#" -lt 2 ] || ! [[ $1 =~ ^[1-9][0-9]*$ ]]; then
  echo "usage: bounded.sh <whole seconds> <command> [args...]" >&2
  exit 64
fi
bound=$1
shift
grace=${BOUND_GRACE_S:-30}

status=0
timeout --kill-after="$grace" "$bound" "$@" || status=$?

# timeout answers 124 when it ended the command, and 137 when it had to kill it. Both statuses can
# also come from the command itself (or from the kernel ending it for memory), so the line needs
# the whole bound to have passed as well. SECONDS counts from the start of this script.
if { [ "$status" -eq 124 ] || [ "$status" -eq 137 ]; } && [ "$SECONDS" -ge "$bound" ]; then
  echo "TIME BOUND: the run passed its outer bound of ${bound} s and was ended (exit ${status}); RUN_TIMEOUT sets it" >&2
fi
exit "$status"
