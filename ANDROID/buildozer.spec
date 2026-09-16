[app]

title = Desqueeze
package.name = desqueeze
package.domain = com.kingdeusx

source.dir = .
source.include_exts = py,kv,png,jpg,ttf,json
# Development-only trees must not be shipped inside the APK.
source.exclude_dirs = tests, tools, logs, bin, java, .buildozer, __pycache__
source.exclude_patterns = *.apk, *.aab, *.log, buildozer.spec, README.md

version = 1.2

# The raw and MP4 tag writers are pure Python; pillow physically resizes the
# rendered formats (PNG/JPEG/TIFF).  pyjnius drives the Storage Access Framework
# and the Java transcoder.
requirements = python3,kivy,pyjnius,pillow

orientation = portrait, landscape
fullscreen = 0

icon.filename = %(source.dir)s/icon.png
icon.adaptive_foreground.filename = %(source.dir)s/icon_foreground.png
icon.adaptive_background.color = #141414

presplash.filename = %(source.dir)s/presplash.png
android.presplash_color = #141414

# Deliberately empty: SAF's persistable tree grant is the storage permission,
# and asking for anything broader would only add a prompt before first use.
android.permissions =

# Android 16.  Bumping to Android 17 (API 37) is this one line plus a rebuild,
# once python-for-android confirms support -- see ANDROID/README.md.
android.api = 36
android.minapi = 29
android.ndk_api = 29
android.archs = arm64-v8a

# The HEVC transcoder: MediaCodec, EGL and MediaMuxer are driven from Java,
# because doing it from Python would mean thousands of JNI calls per frame.
android.add_src = java

android.accept_sdk_license = True
android.allow_backup = True
android.enable_androidx = True
android.release_artifact = apk
android.debug_artifact = apk

# NDK version is left to p4a's RECOMMENDED_NDK_VERSION (r28c at time of build),
# which is what gives 16 KB-page-aligned native libraries -- required for
# Android 15+ devices that boot with 16 KB pages.
p4a.bootstrap = sdl2
p4a.branch = develop

[buildozer]

log_level = 2
warn_on_root = 1

[app@emulator]
# x86_64 variant built ONLY for the automated emulator smoke test
# (tools/emulator_test.sh).  The shipped APK stays arm64-v8a; nothing about the
# release artifact is affected by this profile.
android.archs = x86_64
