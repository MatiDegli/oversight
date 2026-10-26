# Sourced by run.sh: the Impeccable scan, as a function so a test can run it without servers.
#
# run_detect <impeccable> <url> <out dir> <seconds>
#
# The detector lives outside git, and the scan is optional: this function always returns 0, saying
# on one line what happened. Exit 0 and 2 from the detector both mean the scan completed (2: it has
# findings). Anything else, including the deadline, leaves no detect.json, which would read as "no
# findings". A URL scan needs a Chromium-based browser: unless the caller names one, use the one
# Playwright installed for the captures.
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
  timeout --kill-after=10 "$seconds" \
    "$impeccable" detect --json --viewport 1920x1080 "$url" >"$out/detect.json" 2>"$out/detect.err" ||
    detect=$?
  case $detect in
    0 | 2) echo "shots: detect scanned $url (exit $detect), saved $out/detect.json" ;;
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
