#!/usr/bin/env bash
# One outer timeout/memory scope from make covers both the launchers and Chromium tests.
set -eu -o pipefail

vitest_args=()
if [[ ${BROWSER_SHARD+x} ]]; then
  case "$BROWSER_SHARD" in
    1/2|2/2) vitest_args=(--shard "$BROWSER_SHARD") ;;
    *)
      printf 'BROWSER_SHARD must be 1/2 or 2/2 (got: %s)\n' "$BROWSER_SHARD" >&2
      exit 64
      ;;
  esac
fi

if [[ ${BROWSER_SHARD:-1/2} == 1/2 ]]; then
  uv run --frozen pytest tools/browser/test_launchers.py
fi
pnpm -C frontend test:browser "${vitest_args[@]}"
