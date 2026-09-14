#!/usr/bin/env bash
# Run the headless checks (importer + listener). Override BLENDER to point at another binary.
set -euo pipefail
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
cd "$(dirname "$0")/.."
FIXTURE="${1:-tests/fixtures/pascal-sample.glb}"
status=0
for script in tests/run_tests.py tests/run_listener_tests.py; do
  echo "== $script"
  if ! "$BLENDER" --background --factory-startup --python "$script" -- "$FIXTURE" 2>&1 \
    | grep -E "^(SUMMARY|OK|Traceback|AssertionError|Error)|Error:|  File " | tee /dev/stderr | grep -q "^OK"; then
    status=1
  fi
done
exit $status
