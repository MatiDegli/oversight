# Sourced by run.sh and its tests: stops a process only when it is provably the one this script
# launched. A PID alone proves nothing: once the process ends and its parent reaps it, the kernel
# may hand the same number to an unrelated process, and a `kill` would then hit that one. A process
# is identified by its PID and its start time together (field 22 of /proc/PID/stat, in clock ticks
# since boot), which no two processes sharing a PID can have in common.

# proc_start PID: prints the start time of PID; fails when there is no such process. The second
# field of the stat line, the command name, may hold spaces and ')', so the fields are counted from
# the last ') ': what follows begins at field 3, which makes field 22 its 20th word.
proc_start() {
  local stat rest
  IFS= read -r stat <"/proc/$1/stat" 2>/dev/null || return 1
  rest=${stat##*) }
  # shellcheck disable=SC2086
  set -- $rest
  [ -n "${20:-}" ] || return 1
  printf '%s\n' "${20}"
}

# kill_if_started PID START: sends PID the TERM signal only while its start time is still START.
# It fails, and signals nothing, for a START that is empty, a PID that is gone, or a PID that now
# belongs to another process.
kill_if_started() {
  local now
  [ -n "$2" ] || return 1
  now=$(proc_start "$1") || return 1
  [ "$now" = "$2" ] || return 1
  kill "$1" 2>/dev/null
}
