#!/usr/bin/env bash
# Reproduces OrcaSlicer CLI segfault: paint_color + 2 filaments
#
# Adjust ORCA to your OrcaSlicer path if needed.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ORCA="${ORCA:-/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer}"
OUT=$(mktemp -d)

echo "=== Test 1: paint_color + 2 filaments (CRASHES) ==="
set +e
"$ORCA" \
  --load-settings "$DIR/machine.json;$DIR/process.json" \
  --load-filaments "$DIR/filament1.json;$DIR/filament2.json" \
  --slice 0 --outputdir "$OUT" "$DIR/painted.3mf" 2>&1
echo "Exit code: $?"
set -e

echo ""
echo "=== Test 2: paint_color + 1 filament (OK) ==="
"$ORCA" \
  --load-settings "$DIR/machine.json;$DIR/process.json" \
  --load-filaments "$DIR/filament1.json" \
  --slice 0 --outputdir "$OUT" "$DIR/painted.3mf" 2>&1
echo "Exit code: $?"

echo ""
echo "=== Test 3: no paint_color + 2 filaments (OK) ==="
"$ORCA" \
  --load-settings "$DIR/machine.json;$DIR/process.json" \
  --load-filaments "$DIR/filament1.json;$DIR/filament2.json" \
  --slice 0 --outputdir "$OUT" "$DIR/plain.3mf" 2>&1
echo "Exit code: $?"

rm -rf "$OUT"
