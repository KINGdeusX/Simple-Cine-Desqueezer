"""Which files the PC app accepts, and what can be done with each.

This is a wider list than the Android build carries, because the PC version
ships ExifTool.  ExifTool understands Canon CR3 and Fujifilm RAF, which are not
TIFF-based containers and so cannot be patched by the pure-Python writer the
phone uses; bundling the real thing removes that limit entirely.

Two independent choices decide what happens to a photo:

  MODE    tag      write DefaultScale and leave the picture alone
          stretch  actually resample the pixels

  OUTPUT  original keep the file's own format
          dng      convert to DNG (raw sources only)
          jpeg     write a rendered JPEG

Not every pairing is meaningful -- you cannot stretch the pixels of a file and
still call it a raw -- so ``resolve_output()`` is the single place that decides
what a given combination really produces.
"""

from __future__ import annotations

import os

# Raw formats, grouped by maker so the UI can say what it supports.
RAW_BY_MAKER = {
    'Adobe':     ('.dng',),
    'Canon':     ('.cr2', '.cr3', '.crw'),
    'Nikon':     ('.nef', '.nrw'),
    'Sony':      ('.arw', '.sr2', '.srf'),
    'Fujifilm':  ('.raf',),
}
RAW_EXTENSIONS = tuple(sorted(
    extension for group in RAW_BY_MAKER.values() for extension in group))

# Rendered stills.
PIXEL_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.tif', '.tiff')

# Video: MP4 only, by request -- it is what cameras and phones produce, and the
# one container this app can both tag byte-exactly and re-encode confidently.
VIDEO_EXTENSIONS = ('.mp4',)

PHOTO_EXTENSIONS = RAW_EXTENSIONS + PIXEL_EXTENSIONS
SUPPORTED_EXTENSIONS = PHOTO_EXTENSIONS + VIDEO_EXTENSIONS

KIND_RAW = 'raw'
KIND_PIXEL = 'pixel'
KIND_VIDEO = 'video'

MODE_TAG = 'tag'
MODE_STRETCH = 'stretch'

OUT_ORIGINAL = 'original'
OUT_DNG = 'dng'
OUT_JPEG = 'jpeg'

# What a resolved job actually does.
ACTION_TAG_IN_PLACE = 'tag-in-place'     # copy the file, write the tag
ACTION_TAG_DNG = 'tag-dng'               # convert to DNG, then tag it
ACTION_RENDER_JPEG = 'render-jpeg'       # develop/stretch into a JPEG
ACTION_STRETCH_PIXELS = 'stretch-pixels'  # resample an already-rendered image


def extension_of(name):
    return os.path.splitext(name or '')[1].lower()


def kind_of(name):
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
    return is_video(name) if media == 'video' else is_photo(name)


class Plan:
    """What one file will actually become."""

    __slots__ = ('action', 'extension', 'note')

    def __init__(self, action, extension, note=''):
        self.action = action
        self.extension = extension
        self.note = note

    def output_name(self, name):
        stem = os.path.splitext(name)[0]
        return stem + self.extension


def resolve_output(name, mode, output, jpeg_extension='.jpg'):
    """Decide what ``name`` produces under the chosen mode and output format.

    Returns a :class:`Plan`, or raises ``ValueError`` for a combination that
    cannot mean anything.  Where a request has to be adjusted the reason is
    recorded in ``note`` so the log can explain itself rather than silently
    doing something else.
    """
    kind = kind_of(name)
    if kind is None:
        raise ValueError('unsupported file type')
    if kind == KIND_VIDEO:
        raise ValueError('video is handled by the video engine')

    suffix = extension_of(name)

    if mode == MODE_STRETCH:
        # Stretching means resampling, and a resampled raw is no longer raw.
        if output == OUT_DNG:
            raise ValueError('a stretched image cannot be saved as raw DNG - '
                             'choose JPEG, or switch to Tag mode')
        if kind == KIND_RAW:
            return Plan(ACTION_RENDER_JPEG, jpeg_extension,
                        'raw developed and stretched'
                        if output == OUT_ORIGINAL else '')
        if output == OUT_JPEG and suffix not in ('.jpg', '.jpeg'):
            return Plan(ACTION_RENDER_JPEG, jpeg_extension, 'converted to JPEG')
        return Plan(ACTION_STRETCH_PIXELS, suffix)

    # --- tag mode
    if output == OUT_DNG:
        if kind != KIND_RAW:
            raise ValueError('only raw files can be converted to DNG')
        if suffix == '.dng':
            return Plan(ACTION_TAG_IN_PLACE, '.dng', 'already DNG')
        return Plan(ACTION_TAG_DNG, '.dng')

    if output == OUT_JPEG:
        if kind == KIND_RAW:
            # Tagging a JPEG achieves nothing, so this has to render.
            return Plan(ACTION_RENDER_JPEG, jpeg_extension,
                        'raw developed to JPEG and stretched')
        if suffix in ('.jpg', '.jpeg'):
            return Plan(ACTION_STRETCH_PIXELS, suffix,
                        'JPEG stretched - no viewer reads DefaultScale from one')
        return Plan(ACTION_RENDER_JPEG, jpeg_extension, 'converted to JPEG')

    # keep the original format
    if kind == KIND_RAW:
        return Plan(ACTION_TAG_IN_PLACE, suffix)
    if suffix in ('.tif', '.tiff'):
        return Plan(ACTION_TAG_IN_PLACE, suffix)
    # DefaultScale in a JPEG or PNG is inert, so stretch instead of pretending
    return Plan(ACTION_STRETCH_PIXELS, suffix,
                'stretched - DefaultScale has no effect on this format')


def describe_support(media=None):
    if media == 'video':
        return 'MP4 with audio - tag the pixel aspect, or re-encode'
    if media == 'photo':
        return 'RAW (Canon/Nikon/Sony/Fujifilm/DNG), JPEG, TIFF, PNG'
    return 'RAW + JPEG/TIFF/PNG stills, MP4 video'


def maker_summary():
    return ', '.join('%s (%s)' % (maker, ' '.join(e[1:] for e in extensions))
                     for maker, extensions in sorted(RAW_BY_MAKER.items()))
