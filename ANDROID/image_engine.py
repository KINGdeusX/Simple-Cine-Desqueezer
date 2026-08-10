"""Physically desqueeze rendered images (PNG / JPEG / TIFF) by resizing.

Unlike the raw path, there is no metadata convention to lean on here: the only
way a stretched JPEG looks stretched in every viewer is if its pixels really
are stretched.  So the width is multiplied by the squeeze factor and the file
re-encoded, keeping as much of the original as possible along the way (EXIF,
ICC profile, DPI, PNG text chunks, TIFF compression).

Two details that are easy to get wrong and are handled explicitly:

* **EXIF orientation.**  A photo shot in portrait is usually stored landscape
  with an Orientation tag telling the viewer to rotate it.  "Stretch the width"
  means the width you *see*, so for the rotated orientations (5-8) the scale
  factors are swapped before touching the stored pixels.
* **Modes that cannot be resampled smoothly.**  Palette and 16-bit images do not
  support Lanczos; they are converted, resized and converted back rather than
  failing or silently degrading to nearest-neighbour.

Pillow is imported lazily inside the functions so that neither app startup nor
the raw-only code path pays for it.
"""

from __future__ import annotations

import io
import os

# EXIF orientations where the displayed image is rotated a quarter turn, so
# stored width corresponds to displayed height.
_ROTATED_ORIENTATIONS = (5, 6, 7, 8)
_ORIENTATION_TAG = 0x0112

JPEG_QUALITY = 95


class ImageError(Exception):
    """The file could not be decoded, resized or re-encoded."""


def _orientation_of(image):
    try:
        exif = image.getexif()
    except Exception:
        return 1
    try:
        return int(exif.get(_ORIENTATION_TAG, 1) or 1)
    except (TypeError, ValueError):
        return 1


def _resize(image, size):
    """Resize with the best filter the image's mode actually supports."""
    from PIL import Image

    mode = image.mode
    if mode in ('P', 'PA'):
        # Palette images have no meaningful interpolation between indices.
        converted = image.convert('RGBA' if 'transparency' in image.info
                                  or mode == 'PA' else 'RGB')
        return converted.resize(size, Image.LANCZOS)
    if mode.startswith('I;16'):
        # Lanczos is not implemented for 16-bit modes; widen, resize, narrow.
        widened = image.convert('I')
        return widened.resize(size, Image.LANCZOS).convert(mode)
    if mode == '1':
        return image.convert('L').resize(size, Image.LANCZOS).convert('1')
    try:
        return image.resize(size, Image.LANCZOS)
    except ValueError:
        return image.resize(size, Image.BICUBIC)


def _save_kwargs(image, image_format):
    """Encoder settings that preserve as much of the original as possible."""
    info = image.info
    kwargs = {}
    for key in ('icc_profile', 'dpi'):
        if info.get(key):
            kwargs[key] = info[key]

    exif = info.get('exif')
    if exif:
        kwargs['exif'] = exif

    if image_format == 'JPEG':
        kwargs.update(quality=JPEG_QUALITY, optimize=True,
                      subsampling='keep', progressive=info.get('progressive', False))
    elif image_format == 'PNG':
        kwargs['optimize'] = True
        if info.get('transparency') is not None:
            kwargs['transparency'] = info['transparency']
    elif image_format == 'TIFF':
        if info.get('compression'):
            kwargs['compression'] = info['compression']
    return kwargs


def _encode(image, image_format, kwargs):
    buffer = io.BytesIO()
    try:
        image.save(buffer, format=image_format, **kwargs)
    except Exception:
        # Any single encoder option can be rejected for an unusual source
        # (e.g. 'keep' subsampling on a re-encoded JPEG); a plain save is far
        # better than losing the user's frame.
        buffer = io.BytesIO()
        minimal = {k: v for k, v in kwargs.items()
                   if k in ('exif', 'icc_profile', 'quality')}
        image.save(buffer, format=image_format, **minimal)
    return buffer.getvalue()


def resize_desqueeze(data, name, scale_x, scale_y):
    """Return ``(encoded_bytes, description)`` for a desqueezed image.

    ``description`` reads like ``1920x1080 -> 2554x1080`` for the log.
    """
    from PIL import Image

    try:
        scale_x = float(scale_x)
        scale_y = float(scale_y)
    except (TypeError, ValueError):
        raise ImageError('invalid squeeze ratio')
    if scale_x <= 0 or scale_y <= 0:
        raise ImageError('squeeze ratio must be positive')

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        raise ImageError('cannot read image (%s)' % exc)

    image_format = image.format or _format_from_name(name)
    if image_format is None:
        raise ImageError('unknown image format')
    if getattr(image, 'n_frames', 1) > 1:
        # Only the first frame of a multi-page TIFF / animated PNG is kept.
        image.seek(0)

    width, height = image.size
    factor_x, factor_y = scale_x, scale_y
    if _orientation_of(image) in _ROTATED_ORIENTATIONS:
        factor_x, factor_y = scale_y, scale_x

    new_size = (max(1, int(round(width * factor_x))),
                max(1, int(round(height * factor_y))))

    if new_size == (width, height):
        # A 1.0 x 1.0 "desqueeze" should not silently re-encode and lose quality.
        return bytes(data), '%dx%d unchanged' % (width, height)

    try:
        resized = _resize(image, new_size)
    except Exception as exc:
        raise ImageError('cannot resize (%s)' % exc)

    kwargs = _save_kwargs(image, image_format)
    if image_format == 'JPEG' and resized.mode in ('RGBA', 'P', 'LA'):
        resized = resized.convert('RGB')     # JPEG has no alpha channel

    try:
        encoded = _encode(resized, image_format, kwargs)
    except Exception as exc:
        raise ImageError('cannot write image (%s)' % exc)

    return encoded, '%dx%d -> %dx%d' % (width, height, new_size[0], new_size[1])


def _format_from_name(name):
    suffix = os.path.splitext(name or '')[1].lower()
    return {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG',
            '.tif': 'TIFF', '.tiff': 'TIFF'}.get(suffix)
