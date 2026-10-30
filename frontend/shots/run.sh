#!/usr/bin/env bash
# `make shots`: serves the fake event player and the Vite dev server on free ports, captures the
# scenes into .shots/ with capture.ts, then runs Impeccable's detector. It stops its servers by PID
# on every exit path, and every wait has a deadline.
#
# The detector does not scan the page the captures use. That page keeps the SSE stream /events open,
# and the detector's navigation only finishes when the page holds no connection, so it timed out
# after 30 s with "Navigation timeout of 30000 ms exceeded". It scans a second Vite server instead,
# in replay mode with the same log: the static site judges open, which opens no connection after
# load (scan.vite.config.ts, scan-log.ts).
#
# Environment: LOG (the event log, relative to the repo root), IMPECCABLE (the detector's path),
# DETECT_TIMEOUT (seconds the detector may take, 120 by default).
set -eu -o pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root"
log=${LOG:-fixtures/events/handwritten-level1.jsonl}
impeccable=${IMPECCABLE:-.agents/skills/impeccable/scripts/impeccable}
detect_timeout=${DETECT_TIMEOUT:-120}
out=.shots
mkdir -p "$out"
# shellcheck source=frontend/shots/detect.sh
source frontend/shots/detect.sh
# The player's event store goes under .shots/, not .runtime/: the player deletes its file for this
# run id when it starts, which would pull the file from under a `make replay` of the same log.
rm -rf "$out/runtime"

pids=()

# Asks each server to stop, waits up to 5 s for it, then kills what is left, waiting up to 5 s more.
stop_servers() {
  local pid
  trap - EXIT
  for pid in ${pids[@]+"${pids[@]}"}; do kill "$pid" 2>/dev/null || true; done
  for pid in ${pids[@]+"${pids[@]}"}; do
    local deadline=$((SECONDS + 5))
    while kill -0 "$pid" 2>/dev/null && ((SECONDS < deadline)); do sleep 0.1; done
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
      deadline=$((SECONDS + 5))
      while kill -0 "$pid" 2>/dev/null && ((SECONDS < deadline)); do sleep 0.1; done
    fi
    wait "$pid" 2>/dev/null || true
  done
}
trap stop_servers EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

free_port() {
  node -e 'const s = require("node:net").createServer().listen(0, "127.0.0.1", () => { console.log(s.address().port); s.close(); });'
}

# wait_for <url> <pid> <label> <seconds>: polls until the URL answers, the server dies, or time is up.
wait_for() {
  local url=$1 pid=$2 label=$3 deadline=$((SECONDS + $4))
  until curl -fsS --max-time 2 -o /dev/null "$url" 2>/dev/null; do
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "shots: $label exited before it answered; its log is $out/$label.log:" >&2
      tail -n 20 "$out/$label.log" >&2 || true
      return 1
    fi
    if ((SECONDS >= deadline)); then
      echo "shots: $label did not answer $url within $4 s; its log is $out/$label.log" >&2
      return 1
    fi
    sleep 0.2
  done
}

backend_port=$(free_port)
vite_port=$(free_port)
while [ "$vite_port" = "$backend_port" ]; do vite_port=$(free_port); done
export OVERSIGHT_PORT=$backend_port OVERSIGHT_VITE_PORT=$vite_port
url="http://127.0.0.1:$vite_port/"

# The fake player, with no pauses, on its own port: a running `make dev` keeps its ports. The
# environment is the caller's (the Makefile drops the API keys); OVERSIGHT_PORT moves the backend
# and Vite's proxy target together.
OVERSIGHT_PORT=$backend_port OVERSIGHT_RUNTIME_DIR="$out/runtime" \
  uv run --frozen python -m oversight replay "$log" --speed 0 \
  >"$out/backend.log" 2>&1 &
pids+=("$!")
# Vite keeps all its state under .shots/, so `make dev` shares none of it: its cache through
# OVERSIGHT_VITE_CACHE_DIR (read by vite.config.ts), and no temp file for the config, which the
# default loader writes to node_modules/.vite-temp, through --configLoader runner.
OVERSIGHT_PORT=$backend_port OVERSIGHT_VITE_CACHE_DIR="$root/$out/vite-cache" bash -c \
  'cd frontend && exec ./node_modules/.bin/vite --host 127.0.0.1 --port "$0" --strictPort --configLoader runner' \
  "$vite_port" >"$out/vite.log" 2>&1 &
pids+=("$!")

# The detector's server: replay mode, no backend, the log with every event at one time, so the
# replay has no pause and the page is complete at load. Its own port and cache.
scan_port=$(free_port)
while [ "$scan_port" = "$backend_port" ] || [ "$scan_port" = "$vite_port" ]; do scan_port=$(free_port); done
scan_url="http://127.0.0.1:$scan_port/"
node frontend/shots/scan-log.ts "$log" "$out/scan-log.events"
OVERSIGHT_SCAN_LOG="$root/$out/scan-log.events" OVERSIGHT_VITE_CACHE_DIR="$root/$out/scan-vite-cache" bash -c \
  'cd frontend && exec ./node_modules/.bin/vite --config shots/scan.vite.config.ts --mode replay --host 127.0.0.1 --port "$0" --strictPort --configLoader runner' \
  "$scan_port" >"$out/scan-vite.log" 2>&1 &
pids+=("$!")

wait_for "http://127.0.0.1:$backend_port/health" "${pids[0]}" backend 60
wait_for "${url}graph" "${pids[1]}" vite 60
wait_for "$scan_url" "${pids[2]}" scan-vite 60
echo "shots: replay of $log on :$backend_port, UI on $url, scan page on $scan_url"

# Node, not pnpm, so the deadline reaches the process that holds the browser. capped.sh puts it, and
# the Chrome it starts, under a memory cap of 6 GiB where systemd can make one.
timeout --kill-after=10 300 bash frontend/shots/capped.sh \
  node frontend/shots/capture.ts --url "$url" --log "$log" --out "$out"

run_detect "$impeccable" "$scan_url" "$out" "$detect_timeout"
