#!/usr/bin/env bash
# Drive the real Kivy app twice under a virtual X server: once to process a
# batch, and once more against the same state directory to prove the folder and
# preset choices survive a restart (Instruction.md Phase 5's acceptance test).
set -euo pipefail

ANDROID_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHOTS="$ANDROID_DIR/logs/shots"
STATE="$(mktemp -d)"
SOURCE="$(mktemp -d)/A001_CLIP"
OUTDIR="$(mktemp -d)/RENDERS"
mkdir -p "$SHOTS"

smoke() {   # smoke [EXTRA=VALUE ...]
    env DESQUEEZE_STATE_DIR="$STATE" DESQUEEZE_SOURCE_DIR="$SOURCE" \
        DESQUEEZE_OUT_DIR="$OUTDIR" "$@" \
        xvfb-run -a -s "-screen 0 1400x900x24" \
        python "$ANDROID_DIR/tests/smoke_app.py" "$SHOTS"
}

echo "--- first launch"
smoke
echo "--- second launch (settings must be restored)"
smoke DESQUEEZE_EXPECT_RESTORE=1
