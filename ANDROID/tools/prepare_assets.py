#!/usr/bin/env python3
"""Generate every packaged asset the Android build needs.

Run from anywhere:  python ANDROID/tools/prepare_assets.py

Produces, all inside ANDROID/:
  icon.png                     square launcher icon (from the repo's icon.png)
  icon_foreground.png          padded copy for the Android adaptive icon
  presplash.png                icon centred on the desktop app's #141414
  data/DejaVuSansMono*.ttf     bundled monospace face for the log panel
"""

from __future__ import annotations

import os
import shutil
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ANDROID_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(ANDROID_DIR)

BACKGROUND = (20, 20, 20, 255)          # #141414, same as the desktop window
ICON_SIZE = 512
PRESPLASH_SIZE = 1024

FONT_CANDIDATES = [
    ('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 'DejaVuSansMono.ttf'),
    ('/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf',
     'DejaVuSansMono-Bold.ttf'),
    ('/usr/share/fonts/TTF/DejaVuSansMono.ttf', 'DejaVuSansMono.ttf'),
    ('/usr/share/fonts/liberation/LiberationMono-Regular.ttf',
     'DejaVuSansMono.ttf'),
    ('/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf',
     'DejaVuSansMono.ttf'),
]


def _load_source_icon():
    for name in ('icon.png', 'icon.ico'):
        path = os.path.join(REPO, name)
        if os.path.exists(path):
            return Image.open(path).convert('RGBA')
    raise SystemExit('no icon.png/icon.ico found in %s' % REPO)


def _square(image, size, scale=1.0, background=None):
    """Fit ``image`` into a ``size`` square, optionally inset and on a colour."""
    canvas = Image.new('RGBA', (size, size), background or (0, 0, 0, 0))
    inner = max(1, int(size * scale))
    resized = image.copy()
    resized.thumbnail((inner, inner), Image.LANCZOS)
    canvas.paste(resized,
                 ((size - resized.width) // 2, (size - resized.height) // 2),
                 resized)
    return canvas


def main():
    icon = _load_source_icon()
    print('source icon: %dx%d' % icon.size)

    # Launcher icon: the artwork on the app's own dark ground, so it never
    # renders as dark-on-dark against a black launcher wallpaper.
    _square(icon, ICON_SIZE, 0.92, BACKGROUND).save(
        os.path.join(ANDROID_DIR, 'icon.png'))

    # Adaptive icon foreground: Android crops a circle/squircle out of the
    # middle ~66%, so the artwork has to sit well inside the safe zone.
    _square(icon, ICON_SIZE, 0.62).save(
        os.path.join(ANDROID_DIR, 'icon_foreground.png'))

    # Presplash: shown by the launcher while CPython and SDL boot.
    _square(icon, PRESPLASH_SIZE, 0.45, BACKGROUND).convert('RGB').save(
        os.path.join(ANDROID_DIR, 'presplash.png'))

    data_dir = os.path.join(ANDROID_DIR, 'data')
    os.makedirs(data_dir, exist_ok=True)
    copied = 0
    for source, target in FONT_CANDIDATES:
        destination = os.path.join(data_dir, target)
        if os.path.exists(source) and not os.path.exists(destination):
            shutil.copy2(source, destination)
            copied += 1
            print('bundled font: %s' % target)
    if not os.path.exists(os.path.join(data_dir, 'DejaVuSansMono.ttf')):
        print('WARNING: no monospace font found; the app will fall back to '
              'Kivy default face', file=sys.stderr)

    print('assets written to %s' % ANDROID_DIR)


if __name__ == '__main__':
    main()
