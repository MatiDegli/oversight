# Sourced by run.sh: the Impeccable scan, as a function so a test can run it without servers.

# stop_group <pgid>: TERM to the group, up to 2 s for it to empty, then KILL and up to 2 s more.
# Says so on stderr if something is still alive after that; returns 0 either way.
stop_group() {
  local pgid=$1 tries
  kill -TERM -- "-$pgid" 2>/dev/null || return 0
  for ((tries = 0; tries < 20; tries++)); do
    kill -0 -- "-$pgid" 2>/dev/null || return 0
    sleep 0.1
  done
  kill -KILL -- "-$pgid" 2>/dev/null || return 0
  for ((tries = 0; tries < 20; tries++)); do
    kill -0 -- "-$pgid" 2>/dev/null || return 0
    sleep 0.1
  done
  echo "shots: processes of the detector (group $pgid) outlived SIGKILL" >&2
}

# run_detect <impeccable> <url> <out dir> <seconds>
#
# The detector lives outside git, and the scan is optional: this function always returns 0, saying
# on one line what happened. Exit 0 and 2 from the detector both mean the scan completed (2: it has
# findings). Anything else, including the deadline, leaves no detect.json, which would read as "no
# findings". A URL scan needs a Chromium-based browser: unless the caller names one, use the one
# Playwright installed for the captures.
#
# The detector runs in its own process group, which holds the headless Chrome it starts. `timeout`
# alone is not enough: when the detector dies on SIGTERM while a child ignores it, `timeout` returns
# 124 without escalating. So whatever the way out (finished, deadline, or a signal to this shell),
# stop_group signals the whole group, TERM first and KILL after a short deadline, and checks that
# nothing in it is alive. A process that leaves the group by itself (setsid, setpgid) is out of reach.
#
# The detector's Chrome is under a memory cap of 6 GiB where systemd can make one (capped.sh); it
# execs in place, so `timeout` and the group see the same process as before.
run_detect() {
  local impeccable=$1 url=$2 out=$3 seconds=$4 detect=0
  if [ ! -x "$impeccable" ]; then
    echo "shots: $impeccable is missing or not executable, detect skipped"
    return 0
  fi
  if [ -z "${IMPECCABLE_BROWSER:-}" ]; then
    IMPECCABLE_BROWSER=$(cd frontend && node --input-type=module -e \
      'import { chromium } from "@playwright/test"; console.log(chromium.executablePath());') ||
      IMPECCABLE_BROWSER=
    export IMPECCABLE_BROWSER
  fi
  # The traps come first, so no signal finds the job without a handler: a signal to this shell stops
  # the group before the shell goes, then the caller's own traps come back. setsid makes the job a
  # session and group leader whose pid is the group id (job control is off in a script, so setsid
  # does not fork), and `timeout` is the job's first process.
  local saved_traps gid=
  saved_traps=$(trap -p INT TERM HUP)
  trap 'stop_group "$gid"; exit 130' INT
  trap 'stop_group "$gid"; exit 143' TERM HUP
  setsid timeout --kill-after=10 "$seconds" bash frontend/shots/capped.sh \
    "$impeccable" detect --json --viewport 1920x1080 "$url" >"$out/detect.json" 2>"$out/detect.err" &
  gid=$!
  wait "$gid" || detect=$?
  stop_group "$gid"
  trap - INT TERM HUP
  eval "$saved_traps"
  case $detect in
    0 | 2)
      echo "shots: detect scanned $url (exit $detect), saved $out/detect.json"
      # The findings themselves, in the log. A report that cannot be read is said, not hidden.
      node frontend/shots/findings.ts "$out/detect.json" ||
        echo "shots: could not read the findings in $out/detect.json" >&2
      ;;
    124 | 137)
      rm -f "$out/detect.json"
      echo "shots: detect did not finish within $seconds s, skipped (see $out/detect.err)" >&2
      ;;
    *)
      rm -f "$out/detect.json"
      echo "shots: detect could not scan $url (exit $detect), see $out/detect.err" >&2
      ;;
  esac
}
