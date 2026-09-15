#!/usr/bin/env bash
# Run the headless checks (importer + listener). Override BLENDER to point at another binary.
set -euo pipefail
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
cd "$(dirname "$0")/.."
FIXTURE="${1:-tests/fixtures/pascal-sample.glb}"
status=0
echo "== manifest"
manifest=$("$BLENDER" --command extension validate . 2>&1 | grep -E "^(Success|Error|FATAL)" || true)
echo "${manifest:-no output from extension validate}"
case "$manifest" in Success*) ;; *) status=1 ;; esac
for script in tests/run_tests.py tests/run_listener_tests.py; do
  echo "== $script"
  if ! "$BLENDER" --background --factory-startup --python "$script" -- "$FIXTURE" 2>&1 \
    | grep -E "^(SUMMARY|OK|Traceback|AssertionError|Error)|Error:|  File " | tee /dev/stderr | grep -q "^OK"; then
    status=1
  fi
done
exit $status
