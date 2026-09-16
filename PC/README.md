# Desqueeze for Windows

The PC half of Simple Cine Desqueezer, and the successor to the original
single-file desktop app (kept as `legacy_app.py` for reference).

The old app asked you where ExifTool was every time it started. This one ships
ExifTool, FFmpeg and DNGLab inside the installation and resolves them once at
launch — there is no ExifTool path box, because there is nothing to look for.

```
PC/
  app.py              PyQt6 application: tabs, queue table, log
  formats.py          what is accepted, and what each mode/output produces
  engine.py           batch dispatch and events
  photo_engine.py     tagging, raw developing, DNG conversion, stretching
  video_engine.py     MP4 metadata and FFmpeg re-encoding
  mp4_engine.py       pure-Python pasp writer (a deliberate copy of the Android one)
  toolbox.py          finds the bundled binaries, never the PATH
  appstate.py         remembered settings, in %APPDATA%\Desqueeze
  tools/              fetch binaries, build, package, verify
  tests/              unit tests plus a headless drive of the real window
  vendor/             downloaded binaries (git-ignored)
```

## What it handles

Photos and video are **separate tabs**, each with its own files, output folder
and settings.

### Photos

Two independent choices decide what happens:

**What to do**
- **Tag only** — writes `DefaultScale`, pixels untouched. Lossless and instant.
- **Stretch** — actually resamples the picture.

**Output format**
- **Keep original** — a NEF stays a NEF, a CR3 stays a CR3.
- **DNG** — converts proprietary raw to DNG, with optional lossless compression.
- **JPEG** — writes a rendered JPEG at your chosen quality.

| Input | Tag only | Stretch |
|---|---|---|
| Canon `CR2` `CR3` `CRW` | tagged in place, or converted to DNG | developed to JPEG |
| Nikon `NEF` `NRW` | tagged in place, or converted to DNG | developed to JPEG |
| Sony `ARW` `SR2` `SRF` | tagged in place, or converted to DNG | developed to JPEG |
| Fujifilm `RAF` | tagged in place, or converted to DNG | developed to JPEG |
| Adobe `DNG` | tagged in place | developed to JPEG |
| `TIFF` | tagged in place | stretched |
| `JPEG` `PNG` | **stretched instead** — see below | stretched |

Two combinations are handled rather than obeyed literally, because obeying them
would produce something useless:

- **Tagging a JPEG or PNG does nothing.** No viewer reads `DefaultScale` from
  one, so the app stretches it and says so in the log instead of writing a tag
  that has no effect.
- **Stretching into DNG is refused.** A resampled picture is no longer raw
  sensor data, so it cannot honestly be written as raw. The app says so before
  touching a single file rather than failing once per frame.

Canon CR3 and Fujifilm RAF are *not* TIFF-based containers, which is exactly why
this build ships ExifTool rather than reusing the Android app's pure-Python tag
writer — that writer cannot touch them, and ExifTool can.

### Video

MP4 with audio, as requested. Two routes, the same two the Android build offers:

- **Metadata** (default) — writes the `pasp` pixel-aspect atom and the matching
  `tkhd` display size. Takes about as long as copying the file, and is
  **bit-for-bit lossless**: the test suite compares per-frame MD5s before and
  after. Editors (Resolve, Premiere, FCP) honour it; most consumer players
  ignore it and will still show the clip squeezed.
- **Re-encode** — FFmpeg resamples the picture to the stretched size, so it
  plays correctly anywhere. You choose the codec (**H.265** or **H.264**), the
  **bitrate in Mbps**, **VBR or CBR**, and the encoder preset. Audio is copied
  across untouched unless you turn it off.

The re-encode declares **square pixels** (`setsar=1`) precisely so that a player
honouring both the pixels and the metadata does not stretch the picture twice.

## Installing

Download from the [Releases page](
https://github.com/KINGdeusX/Simple-Cine-Desqueezer/releases):

- **`Desqueeze-2.0-Setup.exe`** — installs to Program Files with the helper
  binaries alongside, plus Start Menu and desktop shortcuts and an uninstaller.
- **`Desqueeze-Portable.exe`** — one file, nothing to install. Slower to start
  each time, because it unpacks itself to a temporary folder.

64-bit Windows 10 or 11.

## Building it yourself

**On Windows**, from a checkout:

```powershell
powershell -ExecutionPolicy Bypass -File PC\tools\build_windows.ps1
```

That creates a virtual environment, installs the Python dependencies, downloads
ExifTool/FFmpeg/DNGLab into `PC\vendor\windows`, runs the tests, builds both
artifacts into `PC\dist`, and verifies them. Inno Setup is needed only for the
installer; without it the portable build is still produced and the script says
why the installer was skipped.

`-Target portable` or `-Target installer` builds just one.

**Or let CI do it.** `.github/workflows/windows-build.yml` builds on a
`windows-latest` runner on every push that touches `PC/`, and attaches both
artifacts to a GitHub release when you push a `v*` tag. This is the path to use
if you do not have a Windows machine handy.

### Developing on Linux

Everything except the packaging step works on Linux, which is how this was
built and tested:

```bash
python PC/tools/fetch_binaries.py --platform linux   # Linux helper binaries
cd PC && python -m unittest discover -s tests -t tests
xvfb-run -a python tests/smoke_app.py                # drives the real window
```

## Licensing, in one line

**GPL v3** — the licence that lets this be packaged and shipped, because the
build bundles FFmpeg (x264/x265) and PyQt6. Distribute it freely, commercially
if you like; pass on the licence and point recipients at the source. Details in
[`docs/LICENSING.txt`](../docs/LICENSING.txt).

## Known limitations

- **Raw conversion and developing are not Adobe's.** DNGLab's coverage of CR3
  and RAF is less mature than Adobe DNG Converter, and a JPEG rendered from a
  raw uses LibRaw's default demosaic and white balance — it is a preview, not a
  substitute for developing the raw in your own converter.
- **Video input is MP4 only**, by design. MOV, MKV and AVI are not accepted.
- **Fragmented MP4** is refused when a `pasp` atom would have to be *added*:
  those files carry absolute offsets that shifting `moov` would invalidate, and
  quietly corrupting footage is worse than declining. Re-encode such a clip
  instead.
- Only the first page of a multi-page TIFF is processed.
