"""Desqueezing stills on the PC: tagging, developing and converting.

Four things can happen to a photo, chosen by ``formats.resolve_output()``:

``tag-in-place``
    ExifTool writes ``DefaultScale`` into a copy of the original.  Lossless,
    instant, and the only option that keeps a raw file raw.  ExifTool is what
    makes this work for Canon CR3 and Fujifilm RAF as well as the TIFF-based
    formats, since neither is a TIFF container.

``tag-dng``
    DNGLab converts the raw into DNG first -- optionally with lossless
    compression -- and the tag is written into the result.

``render-jpeg``
    LibRaw develops the raw, the picture is stretched, and a JPEG is written.
    This is a *preview*, not a substitute for developing the raw properly: the
    demosaic and white balance are LibRaw's defaults, not your raw converter's.

``stretch-pixels``
    An already-rendered image is resampled directly.
"""

from __future__ import annotations

import os
import shutil

import formats
import toolbox

DEFAULT_JPEG_QUALITY = 95


class PhotoError(Exception):
    """One file could not be desqueezed."""


# --- ExifTool ---------------------------------------------------------------

def write_default_scale(path, scale_x, scale_y):
    """Write DefaultScale into a file that is already in place.

    ``-overwrite_original`` is deliberate: the file has just been copied to the
    destination by us, so ExifTool's usual ``_original`` backup would only
    litter the output folder with duplicates of something we can recreate.
    """
    result = toolbox.EXIFTOOL.run(
        '-DefaultScale=%s %s' % (scale_x, scale_y),
        '-IFD0:DefaultScale=%s %s' % (scale_x, scale_y),
        '-overwrite_original', '-q', '-m', path)
    output = (result.stdout or b'')
    if isinstance(output, bytes):
        output = output.decode('utf-8', 'replace')
    if result.returncode != 0:
        raise PhotoError(_first_useful_line(output) or
                         'ExifTool exited with code %d' % result.returncode)
    return output.strip()


def read_default_scale(path):
    """Read the tag back, as ``'1.33 1'`` or ``None``."""
    result = toolbox.EXIFTOOL.run('-s3', '-n', '-DefaultScale', path)
    text = result.stdout or b''
    if isinstance(text, bytes):
        text = text.decode('utf-8', 'replace')
    text = text.strip()
    return text.splitlines()[0] if text else None


def _first_useful_line(text):
    for line in (text or '').splitlines():
        line = line.strip()
        if line and not line.startswith('=='):
            return line
    return ''


# --- raw development --------------------------------------------------------

def _develop_raw(path):
    """Demosaic a raw file into an RGB image.

    Imported lazily: rawpy pulls in LibRaw, which is a slow import and is not
    needed at all unless somebody actually asks for a rendered output.
    """
    try:
        import rawpy
    except ImportError:
        raise PhotoError('raw developing needs the rawpy/LibRaw component, '
                         'which is not part of this installation')
    from PIL import Image

    try:
        with rawpy.imread(path) as raw:
            rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=False,
                                  output_bps=8)
    except Exception as exc:
        raise PhotoError('cannot develop this raw file (%s)' % exc)
    return Image.fromarray(rgb)


def _open_rendered(path):
    from PIL import Image

    try:
        image = Image.open(path)
        image.load()
        return image
    except Exception as exc:
        raise PhotoError('cannot read this image (%s)' % exc)


# --- the four actions -------------------------------------------------------

def _tag_in_place(source, destination, scale_x, scale_y, settings):
    shutil.copy2(source, destination)
    try:
        write_default_scale(destination, scale_x, scale_y)
    except PhotoError:
        _discard(destination)
        raise
    return 'DefaultScale %s x %s' % (scale_x, scale_y)


def _tag_dng(source, destination, scale_x, scale_y, settings):
    compressed = bool(settings.get('dng_compressed', True))
    arguments = ['convert']
    if compressed:
        arguments += ['--compression', 'lossless']
    else:
        arguments += ['--compression', 'none']
    arguments += ['--override', source, destination]

    result = toolbox.DNGLAB.run(*arguments, timeout=900)
    output = (result.stdout or b'')
    if isinstance(output, bytes):
        output = output.decode('utf-8', 'replace')
    if result.returncode != 0 or not os.path.exists(destination):
        _discard(destination)
        raise PhotoError(_first_useful_line(output)
                         or 'DNG conversion failed (code %d)' % result.returncode)
    try:
        write_default_scale(destination, scale_x, scale_y)
    except PhotoError:
        _discard(destination)
        raise
    return 'DNG %s, DefaultScale %s x %s' % (
        'lossless' if compressed else 'uncompressed', scale_x, scale_y)


def _render_jpeg(source, destination, scale_x, scale_y, settings):
    kind = formats.kind_of(source)
    image = _develop_raw(source) if kind == formats.KIND_RAW \
        else _open_rendered(source)
    try:
        width, height = image.size
        stretched = _stretch(image, source, scale_x, scale_y)
        _save_jpeg(stretched, destination, settings)
    finally:
        try:
            image.close()
        except Exception:
            pass
    new_width, new_height = _stretched_size(width, height, scale_x, scale_y)
    return '%dx%d -> %dx%d JPEG q%d' % (
        width, height, new_width, new_height,
        int(settings.get('jpeg_quality', DEFAULT_JPEG_QUALITY)))


def _stretch_pixels(source, destination, scale_x, scale_y, settings):
    image = _open_rendered(source)
    try:
        width, height = image.size
        stretched = _stretch(image, source, scale_x, scale_y)
        if formats.extension_of(destination) in ('.jpg', '.jpeg'):
            _save_jpeg(stretched, destination, settings)
        else:
            _save_like_source(stretched, image, destination)
    finally:
        try:
            image.close()
        except Exception:
            pass
    new_width, new_height = _stretched_size(width, height, scale_x, scale_y)
    return '%dx%d -> %dx%d' % (width, height, new_width, new_height)


ACTIONS = {
    formats.ACTION_TAG_IN_PLACE: _tag_in_place,
    formats.ACTION_TAG_DNG: _tag_dng,
    formats.ACTION_RENDER_JPEG: _render_jpeg,
    formats.ACTION_STRETCH_PIXELS: _stretch_pixels,
}


def desqueeze(source, destination, scale_x, scale_y, plan, settings=None):
    """Carry out one resolved plan.  Returns a short description for the log."""
    settings = settings or {}
    action = ACTIONS.get(plan.action)
    if action is None:
        raise PhotoError('unsupported action %r' % plan.action)
    os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
    detail = action(source, destination, scale_x, scale_y, settings)
    return '%s%s' % (detail, '  (%s)' % plan.note if plan.note else '')


# --- pixel helpers ----------------------------------------------------------

_ROTATED_ORIENTATIONS = (5, 6, 7, 8)
_ORIENTATION_TAG = 0x0112


def _factors(image, source, scale_x, scale_y):
    """Scale factors, swapped when EXIF says the picture is displayed rotated."""
    try:
        factor_x, factor_y = float(scale_x), float(scale_y)
    except (TypeError, ValueError):
        raise PhotoError('invalid squeeze ratio')
    if factor_x <= 0 or factor_y <= 0:
        raise PhotoError('squeeze ratio must be positive')
    try:
        orientation = int(image.getexif().get(_ORIENTATION_TAG, 1) or 1)
    except Exception:
        orientation = 1
    if orientation in _ROTATED_ORIENTATIONS:
        factor_x, factor_y = factor_y, factor_x
    return factor_x, factor_y


def _stretched_size(width, height, scale_x, scale_y):
    return (max(1, int(round(width * float(scale_x)))),
            max(1, int(round(height * float(scale_y)))))


def _stretch(image, source, scale_x, scale_y):
    from PIL import Image

    factor_x, factor_y = _factors(image, source, scale_x, scale_y)
    width, height = image.size
    size = (max(1, int(round(width * factor_x))),
            max(1, int(round(height * factor_y))))
    if size == (width, height):
        return image

    mode = image.mode
    if mode in ('P', 'PA'):
        image = image.convert('RGBA' if 'transparency' in image.info
                              or mode == 'PA' else 'RGB')
    elif mode.startswith('I;16'):
        return image.convert('I').resize(size, Image.LANCZOS).convert(mode)
    elif mode == '1':
        return image.convert('L').resize(size, Image.LANCZOS).convert('1')
    try:
        return image.resize(size, Image.LANCZOS)
    except ValueError:
        return image.resize(size, Image.BICUBIC)


def _save_jpeg(image, destination, settings):
    quality = int(settings.get('jpeg_quality', DEFAULT_JPEG_QUALITY))
    quality = max(1, min(100, quality))
    if image.mode not in ('RGB', 'L', 'CMYK'):
        image = image.convert('RGB')        # JPEG has no alpha channel
    kwargs = {'quality': quality, 'optimize': True, 'subsampling': 'keep'}
    for key in ('exif', 'icc_profile', 'dpi'):
        if image.info.get(key):
            kwargs[key] = image.info[key]
    try:
        image.save(destination, format='JPEG', **kwargs)
    except Exception:
        # One awkward option (keeping subsampling on a re-encode, say) must not
        # cost the user the frame.
        minimal = {k: v for k, v in kwargs.items()
                   if k in ('quality', 'exif', 'icc_profile')}
        image.save(destination, format='JPEG', **minimal)


def _save_like_source(image, original, destination):
    image_format = (original.format
                    or {'.png': 'PNG', '.tif': 'TIFF', '.tiff': 'TIFF'}.get(
                        formats.extension_of(destination)))
    if image_format is None:
        raise PhotoError('unknown output format')
    kwargs = {}
    for key in ('exif', 'icc_profile', 'dpi'):
        if original.info.get(key):
            kwargs[key] = original.info[key]
    if image_format == 'PNG' and original.info.get('transparency') is not None:
        kwargs['transparency'] = original.info['transparency']
    if image_format == 'TIFF' and original.info.get('compression'):
        kwargs['compression'] = original.info['compression']
    try:
        image.save(destination, format=image_format, **kwargs)
    except Exception:
        image.save(destination, format=image_format)


def _discard(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
