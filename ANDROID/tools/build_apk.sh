#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# One command, clean checkout -> installable APK in ANDROID/.
#
#   ANDROID/tools/build_apk.sh [debug|release]     (default: release)
#
# Steps: bootstrap toolchain -> regenerate assets -> run the engine parity tests
# and the headless app smoke test -> buildozer -> verify the APK -> copy it next
# to this project.  Any failing step aborts the build.
# ---------------------------------------------------------------------------
set -euo pipefail

MODE="${1:-release}"
ANDROID_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$(dirname "$ANDROID_DIR")"
TC="${TC:-$HOME/.desqueeze-toolchain}"
LOGS="$ANDROID_DIR/logs"
mkdir -p "$LOGS"

step() { echo; echo "=== $* ==="; }

step "toolchain"
bash "$ANDROID_DIR/tools/bootstrap_toolchain.sh"
# shellcheck disable=SC1091
source "$TC/env.sh"

step "assets"
python "$ANDROID_DIR/tools/prepare_assets.py"

step "engine parity tests (vs real exiftool)"
python -m unittest discover -s "$ANDROID_DIR/tests" -t "$ANDROID_DIR/tests"

step "headless app smoke test"
bash "$ANDROID_DIR/tools/run_smoke.sh"

step "buildozer android $MODE"
cd "$ANDROID_DIR"
if [ "$MODE" = "release" ]; then
    # A release APK must be signed or Android refuses to install it.
    KEYSTORE="$TC/desqueeze-release.keystore"
    if [ ! -f "$KEYSTORE" ]; then
        echo "generating a release keystore at $KEYSTORE"
        keytool -genkeypair -v -keystore "$KEYSTORE" \
            -alias desqueeze -keyalg RSA -keysize 4096 -validity 10950 \
            -storepass desqueeze -keypass desqueeze \
            -dname "CN=Desqueeze, OU=Simple Cine Desqueezer, O=KINGdeusX, C=US"
    fi
    export P4A_RELEASE_KEYSTORE="$KEYSTORE"
    export P4A_RELEASE_KEYSTORE_PASSWD=desqueeze
    export P4A_RELEASE_KEYALIAS=desqueeze
    export P4A_RELEASE_KEYALIAS_PASSWD=desqueeze
fi
python "$ANDROID_DIR/tools/run_buildozer.py" android "$MODE" 2>&1 \
    | tee "$LOGS/buildozer-$MODE.log"

step "collect + verify"
APK="$(ls -t "$ANDROID_DIR"/bin/*.apk 2>/dev/null | head -1 || true)"
if [ -z "$APK" ]; then
    echo "BUILD FAILED: no APK produced (see $LOGS/buildozer-$MODE.log)" >&2
    exit 1
fi
cp -f "$APK" "$ANDROID_DIR/$(basename "$APK")"
python "$ANDROID_DIR/tools/verify_apk.py" "$ANDROID_DIR/$(basename "$APK")"

echo
echo "APK: $ANDROID_DIR/$(basename "$APK")"
