# Simple Cine Desqueezer

<p align="center">
  <img src="icon.png" alt="Simple Cine Desqueezer Logo" width="180"/>
</p>

<p align="center">
  <a href="https://github.com/KINGdeusX/Simple-Cine-Desqueezer/releases/latest"><img src="https://img.shields.io/github/v/release/KINGdeusX/Simple-Cine-Desqueezer?label=download&color=f0a500" alt="Latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/licence-GPL%20v3-blue" alt="GPL v3"></a>
  <a href="https://github.com/KINGdeusX/Simple-Cine-Desqueezer/actions/workflows/windows-build.yml"><img src="https://github.com/KINGdeusX/Simple-Cine-Desqueezer/actions/workflows/windows-build.yml/badge.svg" alt="Windows build"></a>
</p>

Batch-desqueeze anamorphic footage and stills — on Windows and on your phone.
Pick a lens preset, pick your files, and the originals are never touched.

## Download

**Windows** — [**latest release**](https://github.com/KINGdeusX/Simple-Cine-Desqueezer/releases/latest)

| | |
|---|---|
| **`Desqueeze-2.0-Setup.exe`** | Installer. Puts ExifTool, FFmpeg and DNGLab alongside the app, adds shortcuts and an uninstaller. |
| **`Desqueeze-Portable.exe`** | One file, nothing to install. Runs from a USB stick. |

64-bit Windows 10 or 11. Nothing else to install — no ExifTool hunt, no FFmpeg
on your PATH.

**Android** — the signed APK is in [`ANDROID/`](ANDROID/). Android 10 or newer,
64-bit ARM.

## The two apps

|  | [**PC**](PC/) — Windows | [**Android**](ANDROID/) — phone |
|---|---|---|
| Built with | PyQt6 | Kivy |
| Raw stills | Canon CR2/CR3/CRW, Nikon NEF/NRW, Sony ARW/SR2/SRF, Fujifilm RAF, DNG | DNG, ARW/SR2/SRF, NEF/NRW |
| Rendered stills | JPEG, PNG, TIFF | JPEG, PNG, TIFF |
| Video in | MP4 with audio | MP4, MOV |
| Photo modes | tag, or actually stretch the pixels | tag raw, stretch rendered |
| Photo output | keep original, DNG (optionally compressed), or JPEG | keep original |
| Video | `pasp` metadata, or FFmpeg re-encode (H.265/H.264, your bitrate, VBR/CBR) | `pasp` metadata, or hardware HEVC re-encode |
| Helper tools | ExifTool, FFmpeg, DNGLab bundled | none — pure Python + the phone's own codecs |
| Ships as | installer + portable `.exe` | signed `.apk` |

Both carry the same nine anamorphic lens presets, custom X/Y ratios, separate
Photos and Video tabs, a per-file progress queue, and the same gold-on-black
look.

## How it decides what to do

A raw file **cannot** be resized without destroying what makes it raw, nothing
reads `DefaultScale` from a JPEG, and a two-hour clip cannot be re-encoded in
the time it takes to write four bytes. So every format gets the one treatment
that actually works for it:

| You have | Tag mode | Stretch mode |
|---|---|---|
| Raw (CR3, NEF, ARW, RAF, DNG) | `DefaultScale` written, pixels untouched — lossless | developed with LibRaw and written as JPEG |
| JPEG / PNG | **stretched instead** — no viewer reads the tag from these | stretched |
| TIFF | tagged in place | stretched |
| MP4 | `pasp` + display size — lossless, seconds per clip | re-encoded at the stretched size |

Where a request cannot mean what it says, the app explains instead of quietly
doing something else. Asking to stretch *into* DNG is refused before a single
file is touched, because a resampled picture is no longer raw sensor data.

### Will Premiere and Resolve pick it up automatically?

The tagged file declares `SAR 133:100` / `DAR 532:225` and a display size of
2554×1080 for a 1.33× squeeze — verified with ffprobe. **Premiere** reads pixel
aspect ratio from MP4 and should build the sequence accordingly (check
*Modify → Interpret Footage*). **Resolve** tends to default to square pixels for
H.264 and may need *Clip Attributes → Pixel Aspect Ratio* set once.

If you want the canvas created correctly everywhere with no interpretation step,
use **re-encode** — it produces genuinely stretched, square-pixel media. For raw
stills, tagging is unambiguously right: `DefaultScale` is the DNG spec's own
mechanism and Lightroom, Camera Raw and Resolve all honour it.

## What's new

### PC 2.0 — the rebuilt Windows app
- **Nothing to find.** ExifTool, FFmpeg and DNGLab ship inside the
  installation, resolved once at startup. The ExifTool path box is gone.
- **Raw for every major brand** — Canon, Nikon, Sony, Fujifilm and Adobe.
  Canon CR3 and Fujifilm RAF are not TIFF-based containers, which is exactly
  why the real ExifTool is bundled.
- **Tag or stretch**, and output as the original format, DNG (optionally
  losslessly compressed), or JPEG at your chosen quality.
- **MP4 video** — lossless `pasp` tagging, or an FFmpeg re-encode with your
  choice of codec, bitrate and rate control.
- **Installer and portable build**, produced by CI on a Windows runner.

### Android 1.2 — video
- MP4/MOV via the `pasp` atom (lossless, frame-identical) or a hardware HEVC
  re-encode driven from Java.
- Separate Photos and Video tabs, per-file progress table, collapsed log.
- Streaming I/O, so clips larger than RAM work.

Full history in [`docs/CHANGELOG.md`](docs/CHANGELOG.md).

## Bugs found and fixed along the way

Most of these only appeared on real hardware or a real Windows runner, which is
why both apps are tested that way rather than by inspection:

- **Outputs named `clip.mp4.dng`** — the Android Storage Access Framework
  renames files to match the MIME type it is handed, and video types were
  missing, so the fallback implied a DNG.
- **A 2554×1080 desqueeze came out 512×512** — width and height were clamped to
  the encoder's limits independently, squaring the picture instead of scaling it.
- **Silent, zero-length audio tracks** — two `MediaExtractor`s over one file
  return a valid audio format and then no samples at all.
- **Windows builds failing on every run** — exiftool.org does not host the
  Windows zip, it links to SourceForge, which answers automated requests with an
  HTML interstitial that was being fed straight to `ZipFile`.
- **ExifTool that started and could not find itself** — the Chocolatey fallback
  copied `chocolatey\bin\exiftool.exe`, which is a shim that re-execs the real
  binary under `lib\`.
- **A failed file left a stub behind** in the output folder, which could be
  mistaken for a finished result.

## Building from source

- **Windows** — `powershell -ExecutionPolicy Bypass -File PC\tools\build_windows.ps1`,
  or push a `v*` tag and let the [CI workflow](.github/workflows/windows-build.yml)
  build it on a Windows runner. See [`PC/README.md`](PC/README.md).
- **Android** — `ANDROID/tools/build_apk.sh release` on Linux or macOS. It
  installs its own toolchain and needs no root. See
  [`ANDROID/README.md`](ANDROID/README.md).

## Repository layout

```
PC/            Windows app, build tooling and tests
ANDROID/       Android app, build tooling and tests
docs/          changelog and licensing
LICENSE        GPL v3
icon.png       shared artwork
```

## Licence

**GNU General Public Licence v3** — see [`LICENSE`](LICENSE).

GPL v3 is the licence that lets this be packaged and shipped as a working
product: the Windows build bundles FFmpeg (compiled with x264/x265) and PyQt6,
both GPL, so a bundle containing them must carry GPL terms.

You may **use it for anything including commercially, distribute it free or for
money, and modify it**. In return you pass on this licence, make the
corresponding source available to anyone you give a binary to (a link to this
repository is enough), and release your changes under GPL v3 as well.

What it does not allow is folding this code into a closed-source product. The
two things standing in the way are PyQt6 and the FFmpeg build; swapping them for
PySide6 and an LGPL FFmpeg would change that.
[`docs/LICENSING.txt`](docs/LICENSING.txt) has the detail and lists every
bundled component's own licence.
