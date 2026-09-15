#!/usr/bin/env bash
# Build the extension zip with Blender's own tool. Override BLENDER to point at another binary.
set -euo pipefail
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
cd "$(dirname "$0")/.."
OUT="${1:-dist}"
rm -rf "$OUT"
mkdir -p "$OUT"
"$BLENDER" --command extension build --source-dir . --output-dir "$OUT"
"$BLENDER" --command extension validate "$OUT"/*.zip
echo "== contents"
unzip -l "$OUT"/*.zip
