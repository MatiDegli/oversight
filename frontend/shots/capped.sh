#!/usr/bin/env bash
# capped.sh <command> [args...]: runs the command under a memory cap of 6 GiB, with no swap, when
# systemd can make the cap here, and as it is when it cannot (CI, sandboxes, no user manager).
# Chrome is the reason: on 2026-10-26 a headless Chrome reached 13.7 GB. The logic is the shared
# helper's, tools/capped.sh, which `make check` also uses for pytest, with a limit of its own.
# The one helper of `make shots`: run.sh puts the captures under it and detect.sh the detector.
set -eu -o pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: capped.sh <command> [args...]" >&2
  exit 64
fi

# No dirname: the tests run this with a PATH of their own.
here=${BASH_SOURCE[0]%/*}
if [ "$here" = "${BASH_SOURCE[0]}" ]; then here=.; fi
exec "$BASH" "$here/../../tools/capped.sh" 6G "$@"
