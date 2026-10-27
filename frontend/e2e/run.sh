#!/usr/bin/env bash
# `make e2e`: builds the static replay site (`vite build --mode replay`, the site judges open, with
# the log named in log.ts) into frontend/dist/replay, serves it with `vite preview` on a free port,
# and runs Playwright over it. No backend starts.
# This script owns the server, not Playwright's webServer: Playwright starts that one in a process
# group of its own, so when `timeout` ends a run it stays behind (it did, on 2026-10-27). Here the
# server stays in this script's process group, which `timeout` signals, and an EXIT trap stops it by
# PID on success, failure, TERM and INT. Under SIGKILL no trap runs; the group kill still reaches it.
# The Makefile puts this script under a time bound and a 6 GiB memory cap.
set -eu -o pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root/frontend"

# The site first, so a build that fails stops here and a stale one is never tested.
./node_modules/.bin/vite build --config e2e/vite.config.ts --mode replay --outDir dist/replay --emptyOutDir --configLoader runner

port=$(node -e 'const s = require("node:net").createServer().listen(0, "127.0.0.1", () => { console.log(s.address().port); s.close(); });')

server=
stop_server() {
  if [ -n "$server" ]; then
    kill "$server" 2>/dev/null || true
    wait "$server" 2>/dev/null || true
    server=
  fi
}
trap stop_server EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# node directly, not the .bin shim: the PID kept here is the server's own.
node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port "$port" --strictPort --outDir dist/replay --configLoader runner &
server=$!

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
OVERSIGHT_E2E_PORT=$port ./node_modules/.bin/playwright test --config e2e/playwright.config.ts || status=$?
exit "$status"
