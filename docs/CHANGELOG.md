# Changelog

Two applications live in this repository and are versioned separately:

- **PC** (`PC/`) — Windows desktop app, PyQt6
- **Android** (`ANDROID/`) — phone app, Kivy

---

## PC 2.0 — the rebuilt Windows app

The original single-file desktop app is kept as `PC/legacy_app.py` for
reference; this replaces it.

### Nothing to find any more
ExifTool, FFmpeg and DNGLab ship **inside the installation**. `toolbox.py`
resolves them once at startup from the install directory, the PyInstaller
bundle, or a source checkout — and deliberately **does not search the PATH**, so
a stray ExifTool of another version cannot change behaviour. The ExifTool path
box is gone, because there is nothing left to browse for.

### Raw support for every major brand
Canon (`CR2` `CR3` `CRW`), Nikon (`NEF` `NRW`), Sony (`ARW` `SR2` `SRF`),
Fujifilm (`RAF`) and Adobe `DNG`. Canon CR3 and Fujifilm RAF are not TIFF-based
containers and cannot be patched by the pure-Python writer the Android build
uses — bundling the real ExifTool is what makes them work.

### Two independent photo choices
- **Mode**: *Tag only* (write `DefaultScale`, pixels untouched) or *Stretch*
  (actually resample).
- **Output**: keep the original format, convert to **DNG** (with optional
  lossless compression), or write a **JPEG** at a chosen quality.

Two combinations are interpreted rather than obeyed, because obeying them
literally produces something useless:
- tagging a JPEG/PNG does nothing that any viewer reads, so it stretches instead
  and says so;
- stretching into DNG is refused outright — a resampled picture is not raw
  sensor data — and refused *before* any file is touched, not once per frame.

### Video
MP4 with audio. Metadata route writes `pasp` + `tkhd` and is bit-for-bit
lossless (verified by comparing per-frame MD5s). Re-encode route uses the
bundled FFmpeg with a choice of **H.265/H.264**, **bitrate in Mbps**, **VBR or
CBR**, and encoder preset; audio is copied across. The re-encode declares square
pixels so a player honouring both pixels and metadata cannot stretch twice.

### Interface
Rebuilt to match the mobile app: **Photos and Video tabs** with independent
state, a **queue table** showing every file with its own progress bar and
result, and the **log collapsed** to a one-line strip.

### Packaging
Both an **Inno Setup installer** and a **single-file portable .exe**, built by
`PC/tools/build_windows.ps1` or by the `windows-build` GitHub Actions workflow
on a Windows runner.

### Licensing
The project moved from MIT to **GPL v3**, which is the licence that permits
packaging and distributing a bundle containing FFmpeg (x264/x265) and PyQt6.
See `LICENSE` and `docs/LICENSING.txt`.

### Tested
46 unit tests plus a 33-check headless drive of the real window, run against the
real bundled binaries, and then a full packaging run on a Windows CI runner
producing the installer and the portable executable.

The `.exe` has been **built and verified, but not launched by hand** — it cannot
be run on the Linux machine it was developed on. `PC/tools/verify_build.py`
checks the packaged output on the machine that builds it.

### Build fixes needed to get the Windows packaging working
Each of these failed a CI run before being found:

- **exiftool.org does not host the Windows zip.** It links to SourceForge, which
  answers automated requests with an HTML interstitial. The fetcher treated any
  HTTP 200 as success and handed markup to `ZipFile`. Downloads are now
  validated as real archives, the URL is scraped from exiftool.org rather than
  guessed, and the direct SourceForge mirrors are tried in turn.
- **The Chocolatey fallback copied a shim.** `chocolatey\bin\exiftool.exe` is a
  launcher that re-execs the real binary under `lib\`; copied into `vendor\` it
  started and could not find itself. It now finds the real executable under
  `lib\exiftool\tools` and copies the whole folder, Perl tree included.
- **The fetch step now runs each tool once** before reporting success, so a
  broken layout fails where it happens rather than three steps later.
- **CI failures were unreadable.** The Actions log API needs admin rights, so a
  failing build was opaque. Test and smoke steps now attach their output to a
  check-run annotation, which is public.
- **Artifacts are published separately** — installer, portable build and the
  unpacked folder — instead of one combined archive.

---

## Android 1.2 — video

MP4/MOV support with two routes: the `pasp` metadata atom (instant, lossless,
verified frame-identical against FFmpeg) and an optional HEVC re-encode using
the phone's hardware encoder, driven from Java because doing MediaCodec + EGL +
MediaMuxer from Python would mean thousands of JNI calls per frame.

Separate Photos and Video tabs, a per-file progress table, and a collapsed log.
Storage now streams instead of reading whole files, so clips larger than RAM
work.

Three bugs found only by running on a real Android 16 image:
- SAF renamed outputs to `clip.mp4.dng` because video MIME types were missing;
- clamping width and height separately squared the picture (2554×1080 → 512×512);
- two `MediaExtractor`s over one file give a valid audio format and then no
  samples, producing a silent zero-length track.

Tests: 33 → 68.

## Android 1.1 — more formats, file selection

Sony ARW/SR2/SRF and Nikon NEF/NRW alongside DNG; PNG/JPEG/TIFF stretched
rather than tagged. Input switched from a folder to a multi-select file picker,
with a remembered output folder.

## Android 1.0 — first release

Kivy port with a pure-Python DNG `DefaultScale` writer whose output is verified
byte-for-byte against real ExifTool, Storage Access Framework access with no
declared permissions, and a fully automated build producing a signed APK.
