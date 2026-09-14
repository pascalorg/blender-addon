#!/usr/bin/env bash
# Run the headless importer checks. Override BLENDER to point at another binary.
set -euo pipefail
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
cd "$(dirname "$0")/.."
"$BLENDER" --background --factory-startup --python tests/run_tests.py -- "${1:-tests/fixtures/pascal-sample.glb}" 2>&1 \
  | grep -E "^(SUMMARY|OK|Traceback|AssertionError|Error)|Error:|  File " || true
