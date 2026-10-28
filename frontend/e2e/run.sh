#!/usr/bin/env bash
# `make e2e`: builds the static replay site (`vite build --mode replay`, the site judges open, with
# the log named in log.ts) into frontend/dist/replay, serves it with `vite preview` on a free port,
# and runs Playwright over it. No backend starts.
# This script owns the server, not Playwright's webServer: Playwright starts that one in a process
# group of its own, so when `timeout` ends a run it stays behind (it did, on 2026-10-27). Here the
# server stays in this script's process group, which `timeout` signals, and an EXIT trap stops it by
# PID on success, failure, TERM and INT. The trap kills only a process whose /proc start time is
# the one recorded at launch (procs.sh), so a PID that Vite's exit freed and the kernel reused is
# never killed. The server and Playwright both run under `setpriv --pdeathsig KILL`, so if this
# script alone is SIGKILLed (no trap, no group kill) the kernel kills them with it. Under a group
# SIGKILL all die together. Without setpriv (util-linux) the script refuses to start.
# The Makefile puts this script under a time bound and a 6 GiB memory cap.
set -eu -o pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../.." && pwd)
cd "$root/frontend"

# A process is told apart from a stranger with the same PID by its /proc start time.
if [ ! -r /proc/self/stat ]; then
  echo "e2e: needs /proc (Linux, WSL): it cannot tell its server from another process otherwise" >&2
  exit 1
fi
# Nothing starts before this: without setpriv a SIGKILL of this script alone orphans the server and
# Playwright, and orphaned node processes have taken this machine's memory before.
if ! command -v setpriv >/dev/null 2>&1; then
  echo "e2e: needs setpriv (util-linux): without it, killing this script alone leaves its server and Playwright running" >&2
  exit 1
fi
# shellcheck source=procs.sh
. "$here/procs.sh"

# The site first, so a build that fails stops here and a stale one is never tested.
./node_modules/.bin/vite build --config e2e/vite.config.ts --mode replay --outDir dist/replay --emptyOutDir --configLoader runner

# The real static build under a nested deployment path; the same preview serves both sites.
./node_modules/.bin/vite build --mode replay --base /oversight/ --outDir dist/replay/oversight --emptyOutDir --configLoader runner

port=$(node -e 'const s = require("node:net").createServer().listen(0, "127.0.0.1", () => { console.log(s.address().port); s.close(); });')

server=
server_start=
stop_server() {
  if [ -n "$server" ]; then
    # Only the process launched here: a PID reused after vite exited belongs to someone else.
    kill_if_started "$server" "$server_start" || true
    wait "$server" 2>/dev/null || true
    server=
    server_start=
  fi
}
trap stop_server EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# node directly, not the .bin shim: the PID kept here is the server's own. setpriv makes the kernel
# send the server KILL when this script dies, even by SIGKILL alone, and then execs node, so the PID
# is still the server's.
setpriv --pdeathsig KILL node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port "$port" --strictPort --outDir dist/replay --configLoader runner &
server=$!
server_start=$(proc_start "$server" || true)

# Up within 60 s, or it is an error: the server exited, or never listened.
ready=
for _ in $(seq 1 600); do
  if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
    ready=yes
    break
  fi
  kill -0 "$server" 2>/dev/null || { echo "e2e: vite preview exited before it listened on $port" >&2; exit 1; }
  sleep 0.1
done
if [ -z "$ready" ]; then
  echo "e2e: vite preview did not listen on $port within 60 s" >&2
  exit 1
fi

status=0
# The same tie for Playwright: its runner (the .bin shim execs node) dies with this script.
OVERSIGHT_E2E_PORT=$port setpriv --pdeathsig KILL ./node_modules/.bin/playwright test --config e2e/playwright.config.ts "$@" || status=$?
exit "$status"
