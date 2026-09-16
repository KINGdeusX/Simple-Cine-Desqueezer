# Desqueeze for Android

The Android port of **Simple Cine Desqueezer** — same gold-on-black look and the
same lens presets, built with Kivy and packaged with Buildozer. You pick the
images to desqueeze, it writes the results into a folder you choose once.

It handles more than the desktop app does, and there are **separate Photos and
Video tabs** — each with its own selection, output folder and preset.

| Format | What happens |
|---|---|
| **DNG**, **Sony ARW/SR2/SRF**, **Nikon NEF/NRW** | the `DefaultScale` tag is written, exactly as ExifTool would — pixels untouched, so the file stays raw |
| **PNG**, **JPEG**, **TIFF** | the pixels are physically stretched, so the image really is desqueezed in any viewer |
| **MP4**, **MOV** | the `pasp` pixel-aspect atom is written — instant and lossless; or, with the toggle on, the clip is re-encoded to HEVC at the stretched size |

Raw files *cannot* be resized without destroying what makes them raw, nothing
reads `DefaultScale` from a JPEG, and a two-hour clip cannot be re-encoded in
the time it takes to write four bytes — so each format gets the only treatment
that actually works for it. `formats.py` is the single place that decides which
path a file takes.

The desktop app is untouched: it still lives in the repository root (`app.py`),
and everything Android lives in this folder.

```
ANDROID/
  main.py            Kivy app: UI, threading, state
  desqueeze.kv       all styling and layout
  formats.py         which extensions are accepted, and how each is handled
  engine.py          DesqueezeEngine interface + lens presets + dispatch
  dng_engine.py      the pure-Python raw tag writer
  image_engine.py    the PNG/JPEG/TIFF pixel resizer
  mp4_engine.py      the pure-Python MP4/MOV pasp writer
  video_engine.py    video routing; drives the Java transcoder
  java/              MediaCodec + EGL HEVC transcoder (compiled into the APK)
  storage.py         folder access (SAF on Android, filesystem on desktop)
  saf.py             ACTION_OPEN_DOCUMENT_TREE picker + persistable grants
  appstate.py        state.json in the app's private storage
  data/              bundled monospace font
  tests/             engine parity tests + headless app smoke test
  tools/             toolchain bootstrap, assets, build, APK + emulator checks
  desqueeze-*.apk    the built app
```

## Install it

Copy the `.apk` in this folder to the phone and open it; Android will ask you to
allow installing from this source the first time. Or, over USB:

```bash
adb install -r ANDROID/desqueeze-1.0-arm64-v8a-release.apk
```

Requires **Android 10 (API 29) or newer** on a **64-bit ARM** device — which is
every phone from roughly 2019 onward, including the Nubia Neo 5 GT 5G.

## Use it

1. Choose the **PHOTOS** or **VIDEO** tab. They are independent: each remembers
   its own files, output folder and lens preset.
2. **Select**. The system file picker opens, filtered to that tab's formats; tap
   and hold to select as many files as you like. Mixed formats are fine.
3. **Output folder** → *Browse*, once. It is remembered between runs, so from
   then on you only pick files. Results land in the **Subfolder** named below it
   (default `desqueezed`) — that subfolder is what stops a run from overwriting
   your originals when you point the output at the folder the files came from.
4. Pick a **lens preset**, or `Custom...` and type your own X / Y.
5. On the video tab, optionally turn on **Re-encode to HEVC** and set the
   bitrate and mode — see below.
6. **RUN DESQUEEZE**. The queue table shows every file with its own progress
   bar; the overall bar sits under the button. **CANCEL** stops after the
   current file. The log is collapsed into a one-line strip — tap it to expand.

Originals are never modified — every file is written out fresh, exactly like the
desktop version. Anything selected that is not a supported format is skipped and
reported rather than failing the batch, and a file that fails partway leaves no
stub behind in the output folder.

## Which engine this uses

**Option A from `Instruction.md`: the pure-Python tag writer.** No Perl, no
bundled binary, no native extraction on first run. Sony and Nikon raw files are
TIFF-based like DNG, so the same writer handles them — and because it only ever
appends and re-points a single offset, maker notes with absolute offsets (which
those formats are full of) are never disturbed.

ExifTool's own output is the specification, and the build checks it rather than
assuming it. `tests/test_parity.py` runs real ExifTool over synthetic DNGs of
every relevant shape and requires our bytes to match:

* `rationalize()` is a port of `Image::ExifTool::Rationalize` and is compared
  against the Perl original across 74 values (`1.33` → `133/100`, and so on);
* placement follows ExifTool's `WriteGroup => 'SubIFD'` for tag `0xC61E`: the
  first SubIFD gets the tag, and a stale copy left in IFD0 is left alone,
  because that is precisely what ExifTool does.

**One deliberate deviation.** On a DNG with *no* SubIFD (single-IFD CinemaDNG
frames), `exiftool -DefaultScale=…` silently writes nothing at all — it will not
create a SubIFD, so the tag has nowhere to go. This app writes the tag into
IFD0 instead, which is where the raw image lives in those files, producing
bytes identical to `exiftool -IFD0:DefaultScale=…`. The log says
`no SubIFD here: DefaultScale goes to IFD0` when it happens. Two tests pin this
down, including one that fails if a future ExifTool starts writing the tag
there itself.

To verify a processed file against the desktop app:

```bash
exiftool -a -G1 -s -DefaultScale phone_output/A001_0001.dng
```

## Video: two ways to desqueeze

**Metadata (default, toggle off).** `mp4_engine.py` writes the `pasp` atom into
the video track's sample entry and sets the `tkhd` display size to match. Both
are needed because players disagree about which to read; writing the pair is
exactly what `ffmpeg -aspect` does and is spec-conformant rather than a double
stretch. The picture data is copied through untouched, so this is **genuinely
lossless** — verified by comparing per-frame MD5s before and after — and takes
about as long as copying the file.

It is not free of caveats: editors (Resolve, Premiere, FCP) honour `pasp`, but
the phone's own gallery and many consumer players ignore it and will still show
the clip squeezed.

**Re-encode (toggle on).** The phone's hardware HEVC encoder rewrites the video
at the stretched size, so it plays correctly anywhere. The pipeline never brings
a frame into application memory: the decoder writes into a `SurfaceTexture`, the
GPU draws that onto the encoder's input surface at the new size, and the encoder
hands samples straight to the muxer. Audio is copied across untouched and
interleaved with the video. Rotation is preserved.

This path deliberately does **not** also write `pasp` — the pixels are already
the right shape, and a player honouring both would stretch twice.

* **Bitrate** is a single target in Mbps plus a **mode**. Android's `MediaFormat`
  exposes `KEY_BIT_RATE`, `KEY_BITRATE_MODE` and `KEY_QUALITY` — there is no
  `KEY_MAX_BIT_RATE`, so separate min/max ceilings are not something a hardware
  encoder can be asked for. `CQ` uses a quality value instead of a bitrate.
* **Nothing is scaled to a fixed resolution.** The output is the source size with
  the squeeze applied — 1920×1080 at 1.33× becomes 2554×1080 — and the source
  frame rate is kept.
* **Device capabilities are queried, never assumed.** Before configuring, the
  encoder is asked for its width/height alignment, supported size ranges,
  bitrate range and which bitrate modes it implements, and the request is fitted
  to them. MediaTek encoders in particular insist on aligned dimensions and fail
  or produce garbage otherwise; this is what makes the app work on more than the
  one phone it was written on.
* **If the encoder cannot manage the size**, the output is scaled down
  *proportionally* rather than clamped per axis — clamping each axis separately
  squares the picture, which is how a 2554×1080 desqueeze first came out of the
  emulator as 512×512. When that happens the log says so explicitly
  (`2554x1080 capped to ...`) instead of quietly handing back a different shape.
* **Audio and video are read from one `MediaExtractor`**, with samples
  dispatched by track index and the first few audio samples queued until the
  muxer is ready. A second extractor over the same file returns a valid track
  format and then no samples at all, which produced output with a silent,
  zero-length audio track — so the two-extractor arrangement is deliberately
  avoided.
* If the device has no HEVC encoder the toggle is disabled and says so.

## Storage and permissions

`android.permissions` is **empty**. Folder access is the Storage Access
Framework: `ACTION_OPEN_DOCUMENT_TREE` for the picker,
`takePersistableUriPermission()` so it survives restarts, and
`ContentResolver` for every read and write. No `MANAGE_EXTERNAL_STORAGE`, no
legacy storage flags, no permission prompt.

## Build it yourself

Linux or macOS only (python-for-android does not run on Windows directly; WSL2
Ubuntu is fine). One command does everything:

```bash
ANDROID/tools/build_apk.sh release     # or: debug
```

It is self-contained and needs no root: it installs a private JDK 17, CPython
3.11, CMake, zlib, libtool and Buildozer under `~/.desqueeze-toolchain`,
regenerates the icons/presplash/font, runs the engine parity tests and the
headless app smoke test, builds, verifies the APK, and drops it in this folder.
A first build downloads the Android SDK and NDK (several GB) and takes a while;
later builds are incremental.

Individual pieces:

```bash
ANDROID/tools/bootstrap_toolchain.sh        # toolchain only
ANDROID/tools/prepare_assets.py             # icon, adaptive icon, presplash, font
python -m unittest discover -s ANDROID/tests -t ANDROID/tests
ANDROID/tools/run_smoke.sh                  # headless run of the real app
ANDROID/tools/verify_apk.py <file.apk>      # post-build checks
ANDROID/tools/emulator_test.sh all          # boot Android 16, install, launch
source ~/.desqueeze-toolchain/env.sh && cd ANDROID && \
    python tools/run_buildozer.py android debug deploy run
```

Use `tools/run_buildozer.py` rather than `buildozer` directly: it disables one
host check that is wrong for this setup (Buildozer refuses to start unless
`/usr/include/zlib.h` exists, but our zlib lives in the toolchain prefix on
`CPATH`) and drops `--user` from the `pip install` Buildozer runs, which pip
rejects inside a virtualenv. Nothing else about the build differs.

### Signing

`build_apk.sh release` generates `~/.desqueeze-toolchain/desqueeze-release.keystore`
(alias `desqueeze`, store and key password `desqueeze`, valid 30 years) if one
is not already there, and signs with it. **Keep that file**: Android will refuse
to install an update signed with a different key. For anything beyond
sideloading, replace it with your own keystore and set `P4A_RELEASE_KEYSTORE`,
`P4A_RELEASE_KEYSTORE_PASSWD`, `P4A_RELEASE_KEYALIAS` and
`P4A_RELEASE_KEYALIAS_PASSWD` before building.

## Verified on a real Android 16 device

`tools/emulator_test.sh` boots an actual API 36 emulator, installs the app and
drives it. The app was confirmed to:

* launch with no crash and no Python traceback in `logcat`, and stay running;
* render the full UI correctly, clear of the status bar and the gesture bar;
* open the multi-select file picker through pyjnius and take in a mixed
  selection — DNG, ARW, NEF, PNG, JPEG and TIFF in one batch — resolving each
  document's real filename;
* take a persistable grant on an output folder and remember it;
* process the whole mixed batch through the Storage Access Framework with
  correct progress and per-file log output.

The results were pulled back off the device and checked. Real ExifTool reported
`DefaultScale : 1.33 1` on every raw file, in the expected IFD — `[SubIFD]` for
DNG/ARW/NEF that have a raw SubIFD, `[IFD0]` for single-IFD CinemaDNG frames.
The rendered files came back genuinely resized, 400x200 → 532x200. The
originals, which were sitting in the very folder chosen as the output, were
untouched — the subfolder did its job. Screenshots of each step are in
`logs/shots/`.

It was also driven through the **video** tab on the device, both ways:

* **Metadata**: two real H.264 clips (MP4 and MOV) were tagged through SAF. The
  results were pulled back and read with ffprobe — `SAR 133:100`, `DAR 532:225`
  — decoded without error, and every one of the 90 and 48 video frames came back
  **bit-identical** to the source. Audio was untouched.
* **Re-encode**: the same clips went through the hardware pipeline to HEVC. The
  output decoded cleanly, kept its frame rate (30 and 24 fps), kept the
  desqueezed aspect ratio, and carried its **AAC audio** across intact at the
  original sample rate and matching duration.

The emulator runs an x86_64 build of the same sources (from the `emulator`
buildozer profile), because the shipped APK is arm64-only. Note that its HEVC
encoder is a *software* one limited to 512 px, so the re-encode there comes out
512×216 — correctly proportioned, just small. A real device (the Nubia's
MediaTek encoder included) reports far larger limits and is not capped.

## API level: built against 36, ready for 37

`android.api = 36` (Android 16), `android.minapi = 29`, `arm64-v8a` only.

API 37 could not be targeted yet, and not because of a choice made here: at
build time Google's SDK repository offered `platforms;android-35` and
`platforms;android-36` and nothing newer (`build-tools;37.0.0` exists, but the
platform it compiles against does not). API 36 is therefore the highest real
target available. Moving to 37 is one line in `buildozer.spec` once Google
publishes the platform:

```ini
android.api = 37
```

then rebuild. Nothing in the app code is tied to 36. Two things already handle
the Android 16/17 requirements that usually break older builds:

* **16 KB memory pages.** Built with NDK r28c, and `tools/verify_apk.py` fails
  the build if any packaged `.so` has a load segment aligned below 16 KB or sits
  unaligned in the zip — that misalignment is what makes an app die instantly on
  a 16 KB-page device.
* **Resizable windows.** Android 16 ignores orientation locks on large screens,
  so the layout listens to `Window.size` and switches between a stacked phone
  layout and a side-by-side one at 720 dp. The controls column always scrolls,
  so nothing can clip in split-screen.

## What is checked before an APK is released

`build_apk.sh` refuses to produce an APK unless all of this passes:

| Check | Where |
|---|---|
| ExifTool byte parity for rationals and tag placement | `tests/test_parity.py` |
| Resize geometry, EXIF orientation, odd colour modes | `tests/test_images.py` |
| Format routing (raw vs pixel vs video vs unsupported) | `tests/test_images.py` |
| MP4 chunk offsets still address the right bytes after a rewrite | `tests/test_video.py` |
| Real H.264 files stay decodable, frame-for-frame identical | `tests/test_video.py` (ffmpeg) |
| pasp/DAR read back correctly by ffprobe, every preset | `tests/test_video.py` (ffmpeg) |
| Rotated clips stretch the displayed width, not the height | `tests/test_video.py` |
| Video larger than RAM is never read whole | `tests/test_video.py` |
| Every accepted extension maps to a MIME that keeps its name | `tests/test_images.py` |
| Corrupt / non-DNG files are skipped, not fatal | `tests/test_parity.py`, smoke test |
| App boots, processes a real batch, writes correct tags | `tests/smoke_app.py` |
| Originals untouched, output folder auto-created | `tests/smoke_app.py` |
| Layout survives phone ↔ tablet resize | `tests/smoke_app.py` |
| Output folder + preset restored after a restart | `tools/run_smoke.sh` (second launch) |
| No exception reaches the crash guard | `tests/smoke_app.py` |
| Manifest, zero permissions, API levels, icon | `tools/verify_apk.py` |
| 16 KB page alignment of every native lib | `tools/verify_apk.py` |
| App code and font actually packaged; tests/tools not | `tools/verify_apk.py` |
| APK is signed | `tools/verify_apk.py` |
| Launches and runs on a real Android 16 image | `tools/emulator_test.sh` |

## Known differences from the desktop app

* **Startup takes a moment.** A Kivy app boots a full CPython interpreter and an
  SDL/OpenGL context on every cold start, and the very first launch after
  install is slower still while assets are unpacked. The presplash (the icon on
  `#141414`) covers that gap instead of a white flash. This is inherent to the
  fully-Python approach, not a bug.
* **The engine is not literal ExifTool** — see above. Values are identical.
* **Fragmented MP4 is refused** when a `pasp` atom would have to be *added*.
  Those files carry absolute offsets inside `tfhd` that shifting `moov` would
  invalidate, and quietly corrupting footage is worse than declining; re-encode
  such a clip instead. Replacing an existing `pasp` needs no size change and
  works fine.
* **Only the first frame** of a multi-page TIFF or animated PNG is kept.
* **PNG/JPEG/TIFF are re-encoded**, because their pixels are resized. JPEG is
  saved at quality 95 with the original subsampling, EXIF and ICC profile
  preserved, but a re-encode is still a lossy generation. Only the first frame
  of a multi-page TIFF or animated PNG is kept.
* **`DefaultScale` is not written to PNG/JPEG/TIFF** — those files are stretched
  instead, which is the thing that actually has an effect.
* **Existing files in the destination are overwritten**; desktop ExifTool would
  refuse and skip.
* Only the source folder itself is scanned, not its subfolders — same as the
  desktop app's `*.dng` glob.
