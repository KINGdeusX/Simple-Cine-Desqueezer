"""Video desqueezing: the metadata path, and the optional re-encode.

Two ways to desqueeze a clip, and the app offers both because they are good at
different things:

* **Metadata** (the default).  ``mp4_engine`` writes the ``pasp`` atom and the
  display size; the picture data is copied through untouched.  Seconds per clip
  whatever its length, perfectly lossless, and editors read it.  Consumer
  players mostly ignore it.
* **Re-encode** (the toggle).  The phone's hardware encoder rewrites the video
  at the stretched size in HEVC, so it looks right in any player.  Costs time
  and a generation of quality.

The re-encode deliberately does **not** also write ``pasp``: the pixels are
already the right shape, and a player honouring both would stretch twice.

The heavy lifting lives in ``java/com/kingdeusx/desqueeze/Transcoder.java``.
Driving MediaCodec, EGL and MediaMuxer from Python would mean thousands of JNI
round trips per frame, so Java runs the loop and Python starts it, watches the
progress it reports, and asks it to stop.
"""

from __future__ import annotations

import os
import time

import formats

POLL_SECONDS = 0.2

MODE_VBR = 'vbr'
MODE_CBR = 'cbr'
MODE_CQ = 'cq'
BITRATE_MODES = (MODE_VBR, MODE_CBR, MODE_CQ)

# Roughly what HEVC needs for visually clean output, in bits per pixel per
# frame.  Only ever a starting suggestion -- the user sets the real number.
BITS_PER_PIXEL = 0.10
MIN_SUGGESTED_BITRATE = 4_000_000
MAX_SUGGESTED_BITRATE = 200_000_000

DEFAULT_SETTINGS = {
    'transcode': False,
    'mode': MODE_VBR,
    'bitrate': 0,              # bits per second; 0 means "use the suggestion"
    'quality': 80,             # only used in CQ mode
    'keyframe_seconds': 1,
}


class VideoError(Exception):
    """A clip could not be read, rewritten or re-encoded."""


def _discard(dest, name):
    """Remove an output we started but could not finish."""
    try:
        dest.delete(name)
    except Exception:
        pass


def _is_android():
    return os.environ.get('ANDROID_ARGUMENT') is not None


# --- sizing -----------------------------------------------------------------

def desqueezed_size(width, height, rotation, scale_x, scale_y):
    """The coded frame size after stretching, honouring rotation.

    On a clip recorded sideways the coded frame is stored landscape and turned
    for display, so widening what the viewer sees means growing the coded
    *height*.
    """
    try:
        factor_x, factor_y = float(scale_x), float(scale_y)
    except (TypeError, ValueError):
        raise VideoError('invalid squeeze ratio')
    if factor_x <= 0 or factor_y <= 0:
        raise VideoError('squeeze ratio must be positive')
    if rotation in (90, 270):
        factor_x, factor_y = factor_y, factor_x
    return (max(2, int(round(width * factor_x))),
            max(2, int(round(height * factor_y))))


def suggested_bitrate(width, height, frame_rate=30):
    """A sane default target for a given output size."""
    estimate = int(width * height * max(1, frame_rate) * BITS_PER_PIXEL)
    return max(MIN_SUGGESTED_BITRATE, min(MAX_SUGGESTED_BITRATE, estimate))


def transcode_available():
    """True when this device has an HEVC encoder we can drive."""
    if not _is_android():
        return False
    try:
        from jnius import autoclass
        return bool(autoclass('com.kingdeusx.desqueeze.Transcoder').isSupported())
    except Exception:
        return False


def output_name_for(name, settings):
    """Re-encoding always produces MP4, whatever the input container was."""
    if not (settings or {}).get('transcode'):
        return name
    stem, extension = os.path.splitext(name)
    return name if extension.lower() == '.mp4' else stem + '.mp4'


# --- the metadata path ------------------------------------------------------

def write_pasp(source, dest, name, handle, scale_x, scale_y,
               on_progress=None, should_cancel=None):
    """Copy the clip across with its pixel aspect rewritten."""
    import mp4_engine

    size = source.size_of(handle) or 0
    stream = source.open_read(handle)
    written_name = name
    created = False
    try:
        if not size:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(0)

        # Everything that can reject this clip happens before the output file
        # exists, so a failure never leaves a stub behind for someone to mistake
        # for a finished result.
        plan = mp4_engine.plan_desqueeze(stream, scale_x, scale_y, size)

        out, written_name = dest.open_write(name)
        created = True
        try:
            info = mp4_engine.write_plan(
                stream, out, plan,
                on_progress=on_progress, should_cancel=should_cancel)
        finally:
            out.close()
    except mp4_engine.Mp4Error as exc:
        if created:
            _discard(dest, written_name)
        raise VideoError(str(exc))
    except Exception:
        if created:
            _discard(dest, written_name)
        raise
    finally:
        stream.close()

    if info is None:
        _discard(dest, written_name)        # cancelled: no half file left over
        return None, written_name

    hspacing, vspacing = info['pasp']
    detail = 'pasp %d:%d  %dx%d' % (hspacing, vspacing,
                                    info['width'], info['height'])
    if info.get('rotation'):
        detail += '  rot %d' % info['rotation']
    return detail, written_name


# --- the re-encode path -----------------------------------------------------

def probe(source, handle):
    """Read the coded size and rotation of a clip."""
    import mp4_engine

    size = source.size_of(handle) or 0
    stream = source.open_read(handle)
    try:
        if not size:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(0)
        return mp4_engine.probe_stream(stream, size)
    except mp4_engine.Mp4Error as exc:
        raise VideoError(str(exc))
    finally:
        stream.close()


def transcode(source, dest, name, handle, scale_x, scale_y, settings,
              on_progress=None, should_cancel=None):
    """Re-encode the clip to HEVC at the desqueezed size.

    Returns ``(detail, written_name)``, or ``(None, name)`` if cancelled.
    """
    if not _is_android():
        raise VideoError('re-encoding needs the phone\'s hardware encoder')

    from jnius import autoclass

    settings = dict(DEFAULT_SETTINGS, **(settings or {}))
    info = probe(source, handle)
    width, height = desqueezed_size(info['width'], info['height'],
                                    info['rotation'], scale_x, scale_y)

    bitrate = int(settings.get('bitrate') or 0)
    if bitrate <= 0:
        bitrate = suggested_bitrate(width, height)
    mode = settings.get('mode', MODE_VBR)
    if mode not in BITRATE_MODES:
        mode = MODE_VBR

    target_name = output_name_for(name, settings)

    try:
        read_descriptor = source.descriptor_for_read(handle)
    except AttributeError:
        raise VideoError('this source cannot be re-encoded')
    write_descriptor, written_name = dest.descriptor_for_write(target_name)

    Transcoder = autoclass('com.kingdeusx.desqueeze.Transcoder')
    try:
        worker = Transcoder(read_descriptor.getFileDescriptor(),
                            write_descriptor.getFileDescriptor(),
                            width, height, bitrate, mode,
                            int(settings.get('keyframe_seconds', 1)),
                            int(settings.get('quality', 80)))
        worker.start()

        cancelled = False
        while not worker.isFinished():
            if should_cancel is not None and should_cancel() and not cancelled:
                worker.cancel()
                cancelled = True
            if on_progress is not None:
                on_progress(worker.getProgress())
            time.sleep(POLL_SECONDS)

        if cancelled:
            _discard(dest, written_name)
            return None, written_name
        if not worker.isSuccess():
            _discard(dest, written_name)
            raise VideoError(worker.getError() or 'the encoder failed')
        return worker.getSummary(), written_name
    finally:
        # The descriptors are what keep the underlying files open; releasing
        # them before the encoder has finished would truncate the output.
        for descriptor in (read_descriptor, write_descriptor):
            try:
                descriptor.close()
            except Exception:
                pass


# --- what the batch engine calls -------------------------------------------

def desqueeze(source, dest, name, handle, scale_x, scale_y, settings=None,
              on_progress=None, should_cancel=None):
    """Desqueeze one clip by whichever route the settings ask for."""
    settings = dict(DEFAULT_SETTINGS, **(settings or {}))
    if not formats.is_video(name):
        raise VideoError('not a video file')
    if settings.get('transcode'):
        return transcode(source, dest, name, handle, scale_x, scale_y,
                         settings, on_progress, should_cancel)
    return write_pasp(source, dest, name, handle, scale_x, scale_y,
                      on_progress, should_cancel)
