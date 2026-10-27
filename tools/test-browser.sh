#!/usr/bin/env bash
# One outer timeout/memory scope from make covers both the launchers and Chromium tests.
set -eu -o pipefail
uv run --frozen pytest tools/browser/test_launchers.py
pnpm -C frontend test:browser
