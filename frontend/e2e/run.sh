#!/usr/bin/env bash
# `make e2e`: builds the static replay site (`vite build --mode replay`, the site judges open) into
# frontend/dist/replay, then runs Playwright over it. Playwright serves the site with `vite preview`
# on a free port and stops that server when the run ends, whatever the outcome. No backend starts.
# The Makefile puts this script under a time bound and a 6 GiB memory cap.
set -eu -o pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$root/frontend"

# The site first, so a build that fails stops here and a stale one is never tested.
./node_modules/.bin/vite build --mode replay --outDir dist/replay --emptyOutDir --configLoader runner

port=$(node -e 'const s = require("node:net").createServer().listen(0, "127.0.0.1", () => { console.log(s.address().port); s.close(); });')
OVERSIGHT_E2E_PORT=$port exec ./node_modules/.bin/playwright test --config e2e/playwright.config.ts
