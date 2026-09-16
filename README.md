# Simple Cine Desqueezer

<p align="center">
  <img src="icon.png" alt="Simple Cine Desqueezer Logo" width="180"/>
</p>

An elegant PyQt6 desktop application for batch desqueezing anamorphic DNG cinema files using `exiftool`.

## Features

- **Preset Lens Ratios**: Built-in support for popular anamorphic squeeze factors:
  - `1.25x` — Sirui / Entry Anamorphic Lenses
  - `1.33x` — Canon C70 / C300 / C500 / Sigma Cine / DJI
  - `1.5x` — Kowa / Vintage Anamorphic / Iscorama 36
  - `1.6x` — Lomo Square Front / Vintage Glass
  - `1.79x` — RED DSMC Anamorphic Mode
  - `1.8x` — Panasonic GH5/GH6 Anamorphic Mode
  - `2.0x` — Panavision / Hawk / Cooke / Classic Anamorphic
  - `2.39x` — Ultra Panavision 70
  - Custom X/Y Squeeze ratios
- **Gold & Black UI with Custom Icon**: Sleek modern interface with dedicated application icon.
- **Non-blocking Multi-threaded Processing**: Uses Python worker threads and ExifTool to batch process DNG files smoothly without freezing the UI.
- **Batch Processing**: Select input directories containing `.dng` / `.DNG` files and automatically write updated metadata to target output directories.
- **ExifTool Integration**: Automatic detection or manual configuration of `exiftool.exe`.

## Android

There is now an Android port in [`ANDROID/`](ANDROID/) — same presets, same
gold-on-black look, built with Kivy, shipping a ready-to-sideload `.apk`.

It also goes further than the desktop app. Alongside DNG it accepts **Sony ARW**
and **Nikon NEF** raw (tagged the same way, verified byte-for-byte against real
ExifTool); **PNG / JPEG / TIFF**, whose pixels are physically stretched so they
are genuinely desqueezed in any viewer; and **MP4 / MOV video**, which gets the
`pasp` pixel-aspect atom written losslessly, or can be re-encoded to HEVC at the
stretched size using the phone's hardware encoder. Photos and video have
separate tabs, and you select individual files rather than a folder.
See [`ANDROID/README.md`](ANDROID/README.md).

The desktop app below is unchanged and developed independently of it.

## Requirements

- **Python 3.8+**
- **ExifTool** (Placed in the application directory or path specified in app settings)

## Quick Start

1. **Clone the repository**:
   ```bash
   git clone https://github.com/KINGdeusX/Simple-Cine-Desqueezer.git
   cd Simple-Cine-Desqueezer
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the application**:
   ```bash
   python app.py
   ```

## Building Executable

To build a standalone executable using PyInstaller:

```bash
pyinstaller app.spec
```

The output executable will be generated inside the `dist/` directory.

## License

MIT License
