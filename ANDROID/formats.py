"""Which file types the app accepts, and how each one gets desqueezed.

There are two genuinely different operations here, and the difference matters:

* **Raw** files (DNG, Sony ARW, Nikon NEF) are TIFF-based containers whose pixel
  data is undebayered sensor data -- you cannot resize them without destroying
  what makes them raw.  For these the app writes the ``DefaultScale`` tag, which
  is how raw processors are told to stretch the image on import.
* **Rendered** images (PNG, JPEG, TIFF) have no such convention -- nothing reads
  ``DefaultScale`` from a JPEG -- so for these the app resizes the pixels, and
  the file really is desqueezed wherever you open it.

``kind_of()`` is the single place that decides which path a file takes.
"""

from __future__ import annotations

import os

# TIFF-based raw: patch the DefaultScale tag, never touch the pixels.
RAW_EXTENSIONS = (
    '.dng',                          # Adobe / CinemaDNG
    '.arw', '.sr2', '.srf',          # Sony
    '.nef', '.nrw',                  # Nikon
)

# Rendered images: physically stretch the pixels.
PIXEL_EXTENSIONS = (
    '.png',
    '.jpg', '.jpeg',
    '.tif', '.tiff',
)

SUPPORTED_EXTENSIONS = RAW_EXTENSIONS + PIXEL_EXTENSIONS

KIND_RAW = 'raw'
KIND_PIXEL = 'pixel'

# Offered to the system file picker.  ``application/octet-stream`` is included
# deliberately: some storage providers fail to recognise raw extensions and
# report them as a generic binary, and a filter without it would hide the very
# files this app exists to process.  Selections are filtered by extension after
# the fact anyway.
PICKER_MIME_TYPES = ['image/*', 'application/octet-stream']


def extension_of(name):
    return os.path.splitext(name or '')[1].lower()


def kind_of(name):
    """Return ``'raw'``, ``'pixel'`` or ``None`` for an unsupported name."""
    suffix = extension_of(name)
    if suffix in RAW_EXTENSIONS:
        return KIND_RAW
    if suffix in PIXEL_EXTENSIONS:
        return KIND_PIXEL
    return None


def is_supported(name):
    return kind_of(name) is not None


def describe_support():
    """Short human summary used in the UI and the log."""
    return 'RAW (DNG/ARW/NEF) tagged - PNG/JPEG/TIFF resized'
