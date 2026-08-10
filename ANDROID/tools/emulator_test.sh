#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Boot a real Android 16 (API 36) emulator, install the app, open it, and prove
# it is still alive and drawing -- the "it must not crash when you open it"
# requirement, checked on an actual Android runtime rather than by inspection.
#
#   ANDROID/tools/emulator_test.sh boot      start the AVD (headless)
#   ANDROID/tools/emulator_test.sh run       install + launch + screenshot + logcat
#   ANDROID/tools/emulator_test.sh tap X Y   tap a point, then screenshot
#   ANDROID/tools/emulator_test.sh stop      shut the emulator down
#   ANDROID/tools/emulator_test.sh all       boot, run, stop
#
# The APK it installs is the x86_64 build from the `emulator` buildozer profile
# (the shipped APK is arm64-v8a and cannot run on an x86_64 emulator); both are
# produced from identical Python sources.
# ---------------------------------------------------------------------------
set -euo pipefail

ANDROID_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SDK="$HOME/.buildozer/android/platform/android-sdk"
export ANDROID_SDK_ROOT="$SDK"
export ANDROID_AVD_HOME="$HOME/.desqueeze-toolchain/avd"
ADB="$SDK/platform-tools/adb"
AVD=desqueeze-api36
PACKAGE=com.kingdeusx.desqueeze
ACTIVITY="$PACKAGE/org.kivy.android.PythonActivity"
SHOTS="$ANDROID_DIR/logs/shots"
mkdir -p "$SHOTS"

boot() {
    if "$ADB" devices | grep -q emulator; then
        echo "emulator already running"
        return
    fi
    echo "booting $AVD (headless, swiftshader)"
    # -sysdir is explicit because avdmanager writes the image path relative to
    # a standard SDK layout, and buildozer's SDK is not in a standard place --
    # without it the emulator looks under <sdk>/android-sdk/system-images/... .
    nohup "$SDK/emulator/emulator" -avd "$AVD" \
        -no-window -no-audio -no-boot-anim -no-snapshot \
        -gpu swiftshader_indirect -memory 2048 \
        -sysdir "$SDK/system-images/android-36/google_apis/x86_64" \
        > "$ANDROID_DIR/logs/emulator.log" 2>&1 &
    "$ADB" wait-for-device
    echo -n "waiting for boot"
    for _ in $(seq 1 120); do
        if [ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; then
            echo " ok"
            "$ADB" shell input keyevent 82 >/dev/null 2>&1 || true   # dismiss lock
            return
        fi
        echo -n .
        sleep 5
    done
    echo; echo "emulator did not finish booting" >&2
    exit 1
}

run() {
    APK="$(ls -t "$ANDROID_DIR"/bin/*x86_64*.apk 2>/dev/null | head -1 || true)"
    [ -n "$APK" ] || { echo "no x86_64 APK; run: run_buildozer.py --profile emulator android debug" >&2; exit 1; }
    echo "installing $(basename "$APK")"
    "$ADB" install -r -g "$APK" | tail -1

    "$ADB" logcat -c || true
    echo "launching $ACTIVITY"
    "$ADB" shell am start -W -n "$ACTIVITY" | grep -E "Status|Error|Warning" || true

    # A python-for-android app unpacks its assets on first launch, so give the
    # interpreter time to come up before deciding whether it survived.
    sleep 25

    PID="$("$ADB" shell pidof "$PACKAGE" | tr -d '\r')"
    "$ADB" exec-out screencap -p > "$SHOTS/emulator-launch.png"

    echo
    echo "=== process ==="
    if [ -n "$PID" ]; then
        echo "ok   app is running (pid $PID)"
    else
        echo "FAIL app is not running -- it crashed or exited"
    fi

    echo "=== crash markers in logcat ==="
    if "$ADB" logcat -d | grep -aE "FATAL EXCEPTION|AndroidRuntime.*FATAL|Traceback \(most recent|E python.*Error" \
            | head -20 | grep -a . ; then
        echo "FAIL crash markers found above"
    else
        echo "ok   no fatal exceptions or python tracebacks"
    fi

    echo "=== app's own startup log ==="
    "$ADB" logcat -d | grep -aE "python  :|I python" | tail -12 || true

    "$ADB" logcat -d > "$ANDROID_DIR/logs/emulator-logcat.txt" || true
    echo
    echo "screenshot: $SHOTS/emulator-launch.png"
    [ -n "$PID" ]
}

tap() {
    "$ADB" shell input tap "$1" "$2"
    sleep 6
    "$ADB" exec-out screencap -p > "$SHOTS/emulator-tap-$1-$2.png"
    PID="$("$ADB" shell pidof "$PACKAGE" | tr -d '\r')"
    if [ -n "$PID" ]; then
        echo "ok   app still running after tapping $1,$2 (pid $PID)"
    else
        echo "FAIL app died after tapping $1,$2"
        return 1
    fi
    echo "screenshot: $SHOTS/emulator-tap-$1-$2.png"
}

stop() {
    "$ADB" emu kill 2>/dev/null || true
    echo "emulator stopped"
}

case "${1:-all}" in
    boot) boot ;;
    run) run ;;
    tap) tap "$2" "$3" ;;
    stop) stop ;;
    all) boot; run; stop ;;
    *) echo "usage: $0 {boot|run|tap X Y|stop|all}" >&2; exit 2 ;;
esac
