"""Which file types the app accepts, and how each one gets desqueezed.

There are three genuinely different operations here, and the differences matter:

* **Raw** stills (DNG, Sony ARW, Nikon NEF) are TIFF-based containers whose
  pixel data is undebayered sensor data -- you cannot resize them without
  destroying what makes them raw.  For these the app writes the ``DefaultScale``
  tag, which is how raw processors are told to stretch the image on import.
* **Rendered** stills (PNG, JPEG, TIFF) have no such convention -- nothing reads
  ``DefaultScale`` from a JPEG -- so for these the app resizes the pixels, and
  the file really is desqueezed wherever you open it.
* **Video** (MP4, MOV) has its own convention: the ``pasp`` pixel-aspect-ratio
  atom, which is the moving-image analogue of ``DefaultScale``.  Editors honour
  it; consumer players largely ignore it.  So video gets ``pasp`` always, and
  optionally a real re-encode on top for a file that plays desqueezed anywhere.

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

# Rendered stills: physically stretch the pixels.
PIXEL_EXTENSIONS = (
    '.png',
    '.jpg', '.jpeg',
    '.tif', '.tiff',
)

# ISO base media files: write the pasp atom, optionally re-encode.
VIDEO_EXTENSIONS = (
    '.mp4', '.m4v',
    '.mov',
)

SUPPORTED_EXTENSIONS = RAW_EXTENSIONS + PIXEL_EXTENSIONS + VIDEO_EXTENSIONS
PHOTO_EXTENSIONS = RAW_EXTENSIONS + PIXEL_EXTENSIONS

KIND_RAW = 'raw'
KIND_PIXEL = 'pixel'
KIND_VIDEO = 'video'

# Offered to the system file picker.  ``application/octet-stream`` is included
# deliberately: some storage providers fail to recognise raw extensions and
# report them as a generic binary, and a filter without it would hide the very
# files this app exists to process.  Selections are filtered by extension after
# the fact anyway.
PHOTO_MIME_TYPES = ['image/*', 'application/octet-stream']
VIDEO_MIME_TYPES = ['video/*', 'application/octet-stream']


def extension_of(name):
    return os.path.splitext(name or '')[1].lower()


def kind_of(name):
    """Return ``'raw'``, ``'pixel'``, ``'video'`` or ``None``."""
    suffix = extension_of(name)
    if suffix in RAW_EXTENSIONS:
        return KIND_RAW
    if suffix in PIXEL_EXTENSIONS:
        return KIND_PIXEL
    if suffix in VIDEO_EXTENSIONS:
        return KIND_VIDEO
    return None


def is_supported(name):
    return kind_of(name) is not None


def is_video(name):
    return kind_of(name) == KIND_VIDEO


def is_photo(name):
    return kind_of(name) in (KIND_RAW, KIND_PIXEL)


def accepts(name, media):
    """True if ``name`` belongs to the ``'photo'`` or ``'video'`` tab."""
    return is_video(name) if media == 'video' else is_photo(name)


def mime_types_for(media):
    return VIDEO_MIME_TYPES if media == 'video' else PHOTO_MIME_TYPES


def describe_support(media=None):
    """Short human summary used in the UI and the log."""
    if media == 'video':
        return 'MP4/MOV tagged with pasp - optional HEVC re-encode'
    if media == 'photo':
        return 'RAW (DNG/ARW/NEF) tagged - PNG/JPEG/TIFF resized'
    return 'RAW tagged - PNG/JPEG/TIFF resized - MP4/MOV pasp + HEVC'
