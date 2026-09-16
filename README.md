# Simple Cine Desqueezer

<p align="center">
  <img src="icon.png" alt="Simple Cine Desqueezer Logo" width="180"/>
</p>

Batch-desqueeze anamorphic footage and stills. Two applications, developed
independently in their own folders:

| | [**PC**](PC/) — Windows | [**Android**](ANDROID/) — phone |
|---|---|---|
| Built with | PyQt6 | Kivy |
| Raw stills | Canon CR2/CR3/CRW, Nikon NEF/NRW, Sony ARW/SR2/SRF, Fujifilm RAF, DNG | DNG, ARW/SR2/SRF, NEF/NRW |
| Rendered stills | JPEG, PNG, TIFF | JPEG, PNG, TIFF |
| Video | MP4 with audio | MP4, MOV |
| Photo modes | tag, or actually stretch | tag raw, stretch rendered |
| Photo output | keep original, DNG (optionally compressed), or JPEG | keep original |
| Video | `pasp` metadata, or FFmpeg re-encode (H.265/H.264, your bitrate) | `pasp` metadata, or hardware HEVC re-encode |
| Helper tools | ExifTool, FFmpeg and DNGLab bundled — nothing to find | none needed; pure Python + the phone's own codecs |
| Ships as | installer + portable `.exe` | signed `.apk` |

Both keep the same nine anamorphic lens presets, custom X/Y ratios, and the same
gold-on-black look. Neither ever modifies your originals.

## Which one does what

Raw files **cannot** be resized without destroying what makes them raw, nothing
reads `DefaultScale` from a JPEG, and a two-hour clip cannot be re-encoded in
the time it takes to write four bytes. So each format gets the only treatment
that actually works for it, and where a request cannot mean what it says the app
explains rather than silently doing something else.

## Getting it

- **Windows** — grab the installer or portable build from
  [Releases](https://github.com/KINGdeusX/Simple-Cine-Desqueezer/releases), or
  see [`PC/README.md`](PC/README.md) to build it.
- **Android** — the APK is in [`ANDROID/`](ANDROID/); see
  [`ANDROID/README.md`](ANDROID/README.md).

## Repository layout

```
PC/            Windows app, build tooling and tests
ANDROID/       Android app, build tooling and tests
docs/          changelog and licensing
icon.png       shared artwork
```

Changes are recorded in [`docs/CHANGELOG.md`](docs/CHANGELOG.md).

## Licence

This repository's source is **MIT**.

The **distributed Windows build is GPL v3**, because it bundles FFmpeg (compiled
with x264/x265) and PyQt6. The Android APK bundles neither and stays MIT. Full
detail in [`docs/LICENSING.txt`](docs/LICENSING.txt).
