#!/usr/bin/env bash
# One outer timeout/memory scope from make covers both the launchers and Chromium tests.
set -eu -o pipefail

vitest_args=()
if [[ ${BROWSER_SHARD+x} ]]; then
  case "$BROWSER_SHARD" in
    1/4|2/4|3/4|4/4) vitest_args=(--shard "$BROWSER_SHARD") ;;
    *)
      printf 'BROWSER_SHARD must be 1/4, 2/4, 3/4 or 4/4 (got: %s)\n' "$BROWSER_SHARD" >&2
      exit 64
      ;;
  esac
fi

if [[ ${BROWSER_SHARD:-1/4} == 1/4 ]]; then
  uv run --frozen pytest tools/browser/test_launchers.py
fi
pnpm -C frontend test:browser "${vitest_args[@]}"
