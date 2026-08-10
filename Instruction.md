
# INSTRUCTION.md — Port "Simple Cine Desqueezer" to Android via Kivy + Buildozer

**Audience:** Claude (Opus), running as a coding agent in Claude Code, with
local Linux/macOS access (python-for-android's toolchain does not run on
Windows directly — WSL2 Ubuntu is fine if the dev machine is Windows).
**Source repo:** https://github.com/KINGdeusX/Simple-Cine-Desqueezer
**Source app:** `app.py` — a PyQt6 desktop tool that batch-writes the DNG
`DefaultScale` metadata tag via `exiftool`, to "desqueeze" anamorphic footage.
**Target:** A fully-Python Android `.apk` built with **Kivy + Buildozer**
(python-for-android), sideloadable, targeting Android 16/17 (API 36/37).
**Chosen direction:** Fully Python, custom-drawn UI (Kivy), not a native
Kotlin/Compose rewrite and not BeeWare/Toga.

Read this whole file before writing any code. Sections are ordered as build
phases — treat each "Acceptance criteria" block as a checkpoint before moving
on. Where you must make a judgment call, state the assumption and proceed;
the specific things that need the user's sign-off are listed at the end
under "Open decisions."

---

## 0. Framing — what this choice actually buys and costs

- **PyQt6 will not run on Android.** Kivy is the substitute UI toolkit —
  it's Python end-to-end, but it renders its own widgets via OpenGL/SDL2
  rather than using native Android views. That's fine for this app (it's a
  utility tool, not something that needs to look like stock Android UI),
  and it means the exact gold-on-black desktop theme is very achievable —
  Kivy's `.kv` styling is conceptually similar to the Qt stylesheet the
  desktop app already uses.
- **Be upfront with the user about startup cost.** A Kivy/python-for-android
  app boots a full CPython interpreter plus an SDL2/OpenGL context on every
  cold start. This is a fundamentally slower profile than a native Kotlin
  app, and the *very first* launch after install is slower still (p4a
  extracts bundled assets to internal storage once). This file's Phase 6
  is entirely about minimizing that cost within what the architecture
  allows — it will not fully disappear, and that's an expected tradeoff of
  the "fully Python" choice, not a bug to try to engineer away completely.
- **The exiftool/Perl problem doesn't change just because the host language
  changed.** Perl still doesn't run on Android. See Phase 2's engine
  decision — same tradeoff as it would be in any language.

---

## 1. Feature parity checklist (port these exactly)

Pull these directly from `app.py` — this is the product spec, not something
to re-derive:

**Lens presets** (label → squeeze X, squeeze Y) — reproduce verbatim as the
preset picker's options:

| Label | X | Y |
|---|---|---|
| 1.33x — Canon C70 / C300 / C500 / Sigma Cine / DJI | 1.33 | 1.0 |
| 1.5x — Kowa / Vintage Anamorphic / Iscorama 36 | 1.5 | 1.0 |
| 1.6x — Lomo Square Front / Some Vintage Glass | 1.6 | 1.0 |
| 1.8x — Panasonic GH5/GH6 Anamorphic Mode | 1.8 | 1.0 |
| 2.0x — Panavision / Hawk / Cooke / Classic Anamorphic | 2.0 | 1.0 |
| 2.0x — Lomo 35 OCT-19 / Russian Vintage | 2.0 | 1.0 |
| 1.25x — Sirui Sniper / Entry Anamorphic Lenses | 1.25 | 1.0 |
| 1.79x — RED DSMC Anamorphic Mode | 1.79 | 1.0 |
| 2.39x — Ultra Panavision 70 (Rare) | 2.39 | 1.0 |
| Custom… | user input | user input |

**Core workflow:**
1. Pick a source folder/tree containing `.dng`/`.DNG` files.
2. Pick (or auto-suggest, as `<source>_desqueezed`) a destination folder.
3. Pick a lens preset or enter custom X/Y.
4. Run → for every DNG in source, write the `DefaultScale` tag with the
   chosen X/Y, writing results into destination (originals untouched — the
   desktop app copies out, it doesn't edit in place).
5. Live progress: file count, running log, percentage bar.
6. Cancel mid-run.
7. Remember last-used source, destination, and preset across launches
   (desktop app does this via `state.json`; on Android, write a small JSON
   file into the app's private storage, or use `plyer`/plain `json` against
   `App.get_running_app().user_data_dir`).
8. Report engine status in the log on launch (desktop app auto-detects
   `exiftool.exe`; the Android version should log something like
   "Desqueeze engine ready" since the engine is bundled, not detected).

**Non-negotiable behavioral parity:** the bytes written into a DNG's
`DefaultScale` tag for a given X/Y must match what real `exiftool` would
write — the user may mix output from the desktop and Android versions on
the same project. Verify this in Phase 8 by comparing a file processed on
Android against the same file processed by desktop `exiftool`, using
`exiftool -DefaultScale <file>` on both.

---

## 2. Tech stack & the engine decision

- **Language/UI:** Python 3 + Kivy. Confirm the exact Python version against
  whatever python-for-android currently supports at build time — p4a's
  supported Python versions lag behind CPython's latest releases, so don't
  assume the newest interpreter version works; pin to whatever the current
  p4a/Buildozer release documents as supported.
- **Packaging:** Buildozer, which wraps python-for-android (p4a) and Gradle.
- **API targets:** set `android.api` (compile/target) to the highest level
  your installed Buildozer/p4a version reliably supports. Android 17 (API
  37) is very recent — if the installed p4a hasn't caught up yet, target
  API 36 (Android 16) now and note in the README that bumping to 37 is a
  one-line `buildozer.spec` change once p4a support is confirmed current.
  Don't guess; check p4a's own release notes/issue tracker at build time.
  `android.minapi`: 29 is a reasonable floor (clean scoped-storage baseline)
  unless the user needs older device support.
- **Metadata engine — build an abstraction, default to Option A:**
  - **Option A (default): pure-Python DNG tag writer.** Parse the DNG's
    TIFF/IFD0 structure directly in Python and write/replace the
    `DefaultScale` tag (TIFF tag `0xC61E` / decimal `50718`, type
    `RATIONAL`, count 2), byte-for-byte compatible with what exiftool
    would produce. This is a bounded, well-specified binary-patching task —
    you're only ever touching one existing-or-new IFD0 entry in a
    TIFF/EP-based file — so a hand-rolled minimal IFD reader/writer is
    tractable and keeps the app dependency-light (no subprocess, no bundled
    binary, no native library extraction on first run).
  - **Option B (fallback, only if the user explicitly wants literal
    exiftool parity): bundle a cross-compiled ARM Perl + exiftool as a
    prebuilt shared library.** Android has enforced W^X (no execute from
    writable app directories) since Android 10 — a bundled binary only
    stays executable if it's packaged the way native libraries are, i.e.
    placed under `jniLibs/<abi>/libexiftool.so` (naming it `.so` is the
    standard workaround) so the OS extracts it into the app's native
    library directory with exec permission. In Buildozer this is done via
    `android.add_libs_arm64_v8a` / `android.add_libs_armeabi_v7a` in
    `buildozer.spec`, pointing at the prebuilt binaries. Invoke via
    Python's `subprocess`, using `context.getApplicationInfo().nativeLibraryDir`
    (via `pyjnius`) to locate it at runtime. This is heavier, slower to
    launch per batch run, and meaningfully more fragile to build/maintain —
    only go this route on explicit request.
  - **Build the code so swapping A ↔ B later is a one-file change** — define
    a single `DesqueezeEngine` class/interface with one method like
    `process(src_dir, dst_dir, scale_x, scale_y) -> Iterator[ProgressEvent]`
    and keep all UI/state code calling only that interface, mirroring the
    desktop app's `ExifWorker` separation of concerns.

**Acceptance criteria:** `buildozer android debug` produces an installable
APK from an empty "Hello Desqueeze" Kivy app on both an API 36 and (if
available) API 37 emulator/device before continuing.

---

## 3. Icon & splash (presplash)

- Source: `icon.png` from the repo root.
- Set `icon.filename` in `buildozer.spec` to `icon.png`; Buildozer/p4a
  generates the launcher icon mipmaps from it.
- Set `android.presplash.filename` to a splash image derived from
  `icon.png` (centered on a solid background matching the desktop app's
  `#141414`), and `android.presplash_color` to `#141414`.
- **Be accurate with the user about what this is:** Buildozer's presplash
  is a static bitmap shown by the native Android launcher the instant the
  APK starts, before Python/Kivy have booted. It is not the animated
  AndroidX SplashScreen API used by native apps — it's a simpler mechanism,
  but it's still what prevents a blank/white flash during the interpreter
  boot, which is the main "delayed start" symptom to avoid.

---

## 4. UI — mirror the desktop layout in `.kv`

Recreate these sections, top to bottom, matching `app.py`'s structure and
its exact stylesheet palette:

- Background `#141414`, header accent (gold) `#f0a500`, body text `#e0e0e0`,
  section labels `#888`, log panel `#888` text on `#0d0d0d`, monospace font
  for the log (bundle a monospace `.ttf` — Android's default may not match
  Consolas/Courier closely).

Sections:
1. Header ("DESQUEEZE") + subheader.
2. Engine status line (replaces the desktop's "ExifTool path" row — since
   the engine is bundled, this is a status label, not an editable path).
3. Source folder picker (SAF tree picker — see Phase 5).
4. Output folder picker, auto-suggested from source folder name +
   `_desqueezed`, editable.
5. Lens preset `Spinner` (Kivy's dropdown), with a custom X/Y row shown
   only when "Custom…" is selected — match the desktop's show/hide
   behavior exactly.
6. Run / Cancel buttons.
7. `ProgressBar`.
8. Scrollable log (`ScrollView` + `Label`, or `RecycleView` if batches can
   be large — a single ever-growing `Label` for hundreds of log lines will
   get slow to redraw; recommend `RecycleView` for anything beyond small
   batches).

**Responsiveness:** Kivy has no first-class equivalent of Compose's
`WindowSizeClass`. Android 17 enforces resizable/multi-window behavior on
large screens, so bind to `Window.size`/`on_resize` and adjust layout
(e.g., stack vs. side-by-side sections) at reasonable width breakpoints
rather than hardcoding a fixed phone-only layout.

**Acceptance criteria:** UI remains usable and doesn't clip/overflow across
a phone-sized window and a resized/split-screen window.

---

## 5. Permissions & file access

Kivy has no built-in Storage Access Framework integration — this has to be
wired manually:

- Use `pyjnius` to call Android's SAF intents directly:
  `Intent.ACTION_OPEN_DOCUMENT_TREE`, launched via
  `PythonActivity.mActivity.startActivityForResult(...)`, with the result
  handled through `android.activity.bind(on_activity_result=...)`.
- Persist the granted folder access with
  `ContentResolver.takePersistableUriPermission(...)` (again via `pyjnius`)
  so the saved source/destination survive app restarts, matching the
  desktop app's `state.json` persistence behavior.
- Read/write DNG bytes through the granted `content://` URI using
  `ContentResolver.openInputStream` / `openOutputStream` via `pyjnius` —
  not raw filesystem paths, which are unreliable for arbitrary user-picked
  folders under scoped storage.
- Do **not** request `MANAGE_EXTERNAL_STORAGE` or broad legacy storage
  permissions in `android.permissions` — SAF is both the correct approach
  under scoped storage and avoids an extra permission prompt slowing down
  first use.

**Acceptance criteria:** user picks a source and destination folder once,
force-closes and reopens the app, and both selections (and access) persist
without a re-prompt.

---

## 6. Performance / startup checklist (Kivy-specific)

- Presplash (Phase 3) covers the visible boot gap — this is the single
  biggest lever available in this architecture.
- Keep `main.py`'s module-level imports minimal. Import the DNG-processing
  engine and any heavier dependencies lazily, inside the function that
  needs them, not at the top of the file — every top-level import runs
  before the first frame can render.
- Restrict `android.archs` in `buildozer.spec` to the ABI(s) actually
  needed (e.g. `arm64-v8a` only, if that covers the user's real target
  devices such as their Nubia Neo 5 GT 5G) — fewer ABIs means a smaller APK,
  faster install-time asset extraction, and faster first-run ART
  compilation.
- Run DNG batch processing on a `threading.Thread`; never touch Kivy
  widgets directly from that thread — marshal all UI updates back to the
  main thread via `Clock.schedule_once`, mirroring the desktop app's
  `QThread` + `pyqtSignal` pattern.
- For large batches, batch log-panel updates (e.g. every N files or every
  few hundred ms via `Clock.schedule_interval`) rather than one widget
  update per file, to avoid redraw/GC thrash.
- Enable whatever bytecode-precompilation / optimization flags the current
  Buildozer/p4a version exposes for release builds.

---

## 7. Build & packaging via Buildozer

Key `buildozer.spec` fields to set:

```
title = Desqueeze
package.name = desqueeze
package.domain = com.kingdeusx
source.dir = .
source.include_exts = py,png,jpg,kv,ttf,json
version = 1.0
requirements = python3,kivy,pyjnius
icon.filename = %(source.dir)s/icon.png
android.presplash.filename = %(source.dir)s/presplash.png
android.presplash_color = #141414
android.permissions = 
android.api = <highest confirmed-supported by installed p4a>
android.minapi = 29
android.archs = arm64-v8a
```

(Leave `android.permissions` empty or minimal per Phase 5 — SAF handles
folder access without declared storage permissions.)

Commands:
- `buildozer android debug` — local debug build/install for testing.
- `buildozer android release` — signed release build (configure a keystore
  first; generate one for the user if they don't have one, and tell them
  exactly where it's saved).
- `buildozer android debug deploy run` — build, install, and launch on a
  connected device/emulator in one step, useful during iteration.

Must run on Linux or macOS — if developing from the Windows 10 machine,
use WSL2 Ubuntu.

---

## 8. Testing checklist

- Emulator matrix: API 36 at minimum, API 37 if the installed p4a supports
  it (see Phase 2).
- Physical device: the user's Nubia Neo 5 GT 5G if available.
- **Tag correctness:** process a known test DNG on Android, then verify
  with `exiftool -DefaultScale <file>` (on a desktop) that the tag value
  matches exactly what the desktop app would have written for the same
  preset.
- Batch test: 20+ DNGs — confirm progress reporting is accurate, Cancel
  mid-run leaves output in a sane state (in-flight file may finish,
  remaining files stop — matches desktop behavior).
- Presplash test: confirm no blank/white flash between tapping the icon
  and the presplash appearing.
- SAF persistence test per Phase 5's acceptance criteria.
- Responsive layout test per Phase 4 — resize/split-screen the window and
  confirm no clipping.

---

## 9. Deliverables

- Full Buildozer project (buildozer.spec + Python/`.kv` source), buildable
  from a clean checkout on Linux/macOS/WSL2.
- Signed release `.apk` (and the keystore, or clear instructions for the
  user to generate/manage their own).
- A short `README.md` covering: how to build, how to sideload, which
  engine option (A or B) was implemented, and the current confirmed
  `android.api` target with a note on when/how to bump it to API 37 if it
  was built against 36.

---

## Open decisions — confirm with the user before or during the build

1. **Engine choice:** this file defaults to Option A (pure-Python DNG tag
   writer). Confirm the user is fine with the Android version not shelling
   out to literal `exiftool` — functionally identical output for this app's
   specific use case.
2. **API target:** confirm whether targeting API 36 now (with a documented
   path to 37 later) is acceptable, versus insisting on 37 immediately and
   dealing with whatever p4a compatibility gaps come up.
3. **minapi floor:** defaulted to 29 (Android 10) — confirm this covers the
   devices the user actually cares about.
4. **ABI scope:** defaulted to `arm64-v8a` only — confirm the user doesn't
   need 32-bit device support.
5. **Distribution:** sideload-only APK (assumed) — confirm, since this
   changes signing/versioning needs versus a future Play Store submission.
6. **Format scope:** desktop app is DNG-only — confirm whether the Android
   version should stay DNG-only or also handle other RAW/anamorphic-tagged
   formats.

