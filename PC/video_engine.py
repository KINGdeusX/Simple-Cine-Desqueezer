"""Desqueezing MP4 on the PC: metadata, or a real re-encode through FFmpeg.

Same two routes as the Android build, for the same reasons:

* **Metadata** rewrites the ``pasp`` pixel-aspect atom and the ``tkhd`` display
  size with ``mp4_engine``, streaming the file through untouched.  Seconds per
  clip whatever its length, bit-for-bit lossless, and read by editors.  Consumer
  players largely ignore it.
* **Re-encode** hands the clip to the bundled FFmpeg, which resamples the
  picture to the stretched size.  Slower and lossy, but it plays correctly
  everywhere.

The re-encode deliberately does not also write ``pasp``: the pixels are already
the right shape and a player honouring both would stretch twice.  FFmpeg is told
``-vf scale=...,setsar=1`` precisely so the output declares square pixels.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

import mp4_engine
import toolbox

CODEC_HEVC = 'hevc'
CODEC_H264 = 'h264'
CODECS = (CODEC_HEVC, CODEC_H264)

MODE_VBR = 'vbr'
MODE_CBR = 'cbr'
MODES = (MODE_VBR, MODE_CBR)

ENCODER_FOR = {CODEC_HEVC: 'libx265', CODEC_H264: 'libx264'}

# Roughly what a visually clean encode needs, in bits per pixel per frame.
BITS_PER_PIXEL = {CODEC_HEVC: 0.09, CODEC_H264: 0.14}
MIN_SUGGESTED_BITRATE = 4_000_000
MAX_SUGGESTED_BITRATE = 200_000_000

DEFAULT_SETTINGS = {
    'transcode': False,
    'codec': CODEC_HEVC,
    'mode': MODE_VBR,
    'bitrate': 0,                # bits/second; 0 means use the suggestion
    'preset': 'medium',
    'keep_audio': True,
}

_TIME = re.compile(r'time=(\d+):(\d\d):(\d\d(?:\.\d+)?)')
_OUT_TIME = re.compile(r'out_time_ms=(\d+)')


class VideoError(Exception):
    """A clip could not be read, rewritten or re-encoded."""


# --- probing ----------------------------------------------------------------

def probe(path):
    """Read the things we need to know before touching a clip."""
    if not toolbox.FFPROBE.available:
        return _probe_without_ffprobe(path)
    result = toolbox.FFPROBE.run(
        '-v', 'error', '-print_format', 'json',
        '-show_streams', '-show_format', path, timeout=120)
    text = result.stdout or b''
    if isinstance(text, bytes):
        text = text.decode('utf-8', 'replace')
    try:
        parsed = json.loads(text)
    except ValueError:
        raise VideoError('ffprobe could not read this file')

    video = next((s for s in parsed.get('streams', [])
                  if s.get('codec_type') == 'video'), None)
    if video is None:
        raise VideoError('no video track in this file')
    audio = next((s for s in parsed.get('streams', [])
                  if s.get('codec_type') == 'audio'), None)

    rotation = 0
    for entry in video.get('side_data_list', []) or []:
        if 'rotation' in entry:
            try:
                rotation = int(abs(float(entry['rotation']))) % 360
            except (TypeError, ValueError):
                rotation = 0
    try:
        duration = float(parsed.get('format', {}).get('duration') or 0)
    except (TypeError, ValueError):
        duration = 0.0

    return {
        'width': int(video.get('width') or 0),
        'height': int(video.get('height') or 0),
        'codec': video.get('codec_name') or '',
        'frame_rate': _parse_rate(video.get('r_frame_rate')),
        'rotation': rotation,
        'duration': duration,
        'has_audio': audio is not None,
        'audio_codec': (audio or {}).get('codec_name') or '',
    }


def _probe_without_ffprobe(path):
    """Fall back to our own MP4 reader, so tagging works without FFmpeg."""
    size = os.path.getsize(path)
    with open(path, 'rb') as stream:
        try:
            info = mp4_engine.probe_stream(stream, size)
        except mp4_engine.Mp4Error as exc:
            raise VideoError(str(exc))
    info.update({'frame_rate': 0.0, 'duration': 0.0,
                 'has_audio': False, 'audio_codec': ''})
    return info


def _parse_rate(text):
    if not text:
        return 0.0
    if '/' in str(text):
        top, _, bottom = str(text).partition('/')
        try:
            bottom = float(bottom)
            return float(top) / bottom if bottom else 0.0
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


# --- sizing and bitrate -----------------------------------------------------

def desqueezed_size(width, height, rotation, scale_x, scale_y):
    """The encoded frame size after stretching, honouring rotation.

    A clip recorded sideways stores its frame landscape and turns it for
    display, so widening what the viewer sees means growing the coded height.
    Both axes are rounded to even numbers, which every H.264/HEVC encoder wants.
    """
    try:
        factor_x, factor_y = float(scale_x), float(scale_y)
    except (TypeError, ValueError):
        raise VideoError('invalid squeeze ratio')
    if factor_x <= 0 or factor_y <= 0:
        raise VideoError('squeeze ratio must be positive')
    if rotation in (90, 270):
        factor_x, factor_y = factor_y, factor_x
    return (_even(width * factor_x), _even(height * factor_y))


def _even(value):
    return max(2, int(round(value / 2.0)) * 2)


def suggested_bitrate(width, height, frame_rate=30, codec=CODEC_HEVC):
    rate = frame_rate if frame_rate and frame_rate > 0 else 30
    estimate = int(width * height * rate * BITS_PER_PIXEL.get(codec, 0.1))
    return max(MIN_SUGGESTED_BITRATE, min(MAX_SUGGESTED_BITRATE, estimate))


def transcode_available():
    return toolbox.FFMPEG.available


# --- the metadata route -----------------------------------------------------

def write_pasp(source, destination, scale_x, scale_y,
               on_progress=None, should_cancel=None):
    """Copy the clip across with its pixel aspect rewritten."""
    size = os.path.getsize(source)
    with open(source, 'rb') as stream:
        try:
            # Everything that can reject the clip happens before the output
            # exists, so a failure never leaves a stub to be mistaken for a
            # finished file.
            plan = mp4_engine.plan_desqueeze(stream, scale_x, scale_y, size)
        except mp4_engine.Mp4Error as exc:
            raise VideoError(str(exc))

        os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
        try:
            with open(destination, 'wb') as out:
                info = mp4_engine.write_plan(
                    stream, out, plan,
                    on_progress=on_progress, should_cancel=should_cancel)
        except mp4_engine.Mp4Error as exc:
            _discard(destination)
            raise VideoError(str(exc))
        except Exception:
            _discard(destination)
            raise

    if info is None:
        _discard(destination)
        return None

    hspacing, vspacing = info['pasp']
    detail = 'pasp %d:%d  %dx%d' % (hspacing, vspacing,
                                    info['width'], info['height'])
    if info.get('rotation'):
        detail += '  rot %d' % info['rotation']
    return detail


# --- the re-encode route ----------------------------------------------------

def build_ffmpeg_arguments(source, destination, width, height, settings, info):
    """The exact FFmpeg invocation, kept separate so tests can read it."""
    codec = settings.get('codec', CODEC_HEVC)
    encoder = ENCODER_FOR.get(codec, ENCODER_FOR[CODEC_HEVC])
    bitrate = int(settings.get('bitrate') or 0)
    if bitrate <= 0:
        bitrate = suggested_bitrate(width, height,
                                    info.get('frame_rate') or 30, codec)
    mode = settings.get('mode', MODE_VBR)

    arguments = ['-hide_banner', '-nostdin', '-y',
                 '-i', source,
                 # setsar=1 is what stops a double stretch: the pixels are now
                 # square, so the output must say so rather than also carrying
                 # a pixel-aspect of 1.33.
                 '-vf', 'scale=%d:%d:flags=lanczos,setsar=1' % (width, height),
                 '-c:v', encoder,
                 '-b:v', str(bitrate),
                 '-pix_fmt', 'yuv420p']

    if mode == MODE_CBR:
        # A real constant bitrate needs the ceiling and the buffer pinned to
        # the target as well; -b:v alone is only an average.
        arguments += ['-minrate', str(bitrate), '-maxrate', str(bitrate),
                      '-bufsize', str(bitrate * 2)]
        if encoder == 'libx265':
            arguments += ['-x265-params',
                          'vbv-maxrate=%d:vbv-bufsize=%d'
                          % (bitrate // 1000, bitrate * 2 // 1000)]
    else:
        arguments += ['-maxrate', str(int(bitrate * 1.5)),
                      '-bufsize', str(bitrate * 2)]

    preset = settings.get('preset') or 'medium'
    arguments += ['-preset', preset]
    if encoder == 'libx265':
        arguments += ['-tag:v', 'hvc1']     # QuickTime refuses hev1

    if settings.get('keep_audio', True) and info.get('has_audio'):
        arguments += ['-c:a', 'copy']
    else:
        arguments += ['-an']

    arguments += ['-movflags', '+faststart',
                  '-progress', 'pipe:1', '-nostats',
                  destination]
    return arguments


def transcode(source, destination, scale_x, scale_y, settings,
              on_progress=None, should_cancel=None):
    """Re-encode the clip at the desqueezed size.  Returns a description."""
    if not toolbox.FFMPEG.available:
        raise VideoError('re-encoding needs FFmpeg, which is missing from this '
                         'installation')

    settings = dict(DEFAULT_SETTINGS, **(settings or {}))
    info = probe(source)
    width, height = desqueezed_size(info['width'], info['height'],
                                    info['rotation'], scale_x, scale_y)
    arguments = build_ffmpeg_arguments(source, destination, width, height,
                                       settings, info)

    os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
    duration = info.get('duration') or 0
    process = toolbox.FFMPEG.popen(*arguments)
    cancelled = False
    tail = []

    try:
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            tail.append(line)
            del tail[:-25]
            if should_cancel is not None and should_cancel() and not cancelled:
                cancelled = True
                process.terminate()
                continue
            if on_progress is not None and duration > 0:
                seconds = _progress_seconds(line)
                if seconds is not None:
                    on_progress(min(99, int(seconds * 100 / duration)))
        process.wait()
    finally:
        # Closing the pipe matters: a batch of a hundred clips otherwise leaks a
        # file descriptor each and eventually cannot open anything at all.
        try:
            if process.stdout is not None:
                process.stdout.close()
        except Exception:
            pass
        if process.poll() is None:
            process.kill()
            process.wait()

    if cancelled:
        _discard(destination)
        return None
    if process.returncode != 0 or not os.path.exists(destination):
        _discard(destination)
        raise VideoError(_ffmpeg_problem(tail)
                         or 'FFmpeg exited with code %d' % process.returncode)

    codec = settings.get('codec', CODEC_HEVC)
    bitrate = int(settings.get('bitrate') or 0) or suggested_bitrate(
        width, height, info.get('frame_rate') or 30, codec)
    return '%dx%d -> %dx%d  %s %s %.1f Mbps%s' % (
        info['width'], info['height'], width, height,
        codec.upper(), settings.get('mode', MODE_VBR).upper(),
        bitrate / 1_000_000.0,
        '  +audio' if (settings.get('keep_audio', True)
                       and info.get('has_audio')) else '')


def _progress_seconds(line):
    match = _OUT_TIME.search(line)
    if match:
        return int(match.group(1)) / 1_000_000.0
    match = _TIME.search(line)
    if match:
        return (int(match.group(1)) * 3600 + int(match.group(2)) * 60
                + float(match.group(3)))
    return None


def _ffmpeg_problem(lines):
    """The most informative line FFmpeg printed before giving up."""
    for line in reversed(lines):
        lowered = line.lower()
        if ('error' in lowered or 'invalid' in lowered
                or 'unknown encoder' in lowered or 'no such file' in lowered):
            return line
    return lines[-1] if lines else ''


def _discard(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# --- what the batch engine calls -------------------------------------------

def desqueeze(source, destination, scale_x, scale_y, settings=None,
              on_progress=None, should_cancel=None):
    settings = dict(DEFAULT_SETTINGS, **(settings or {}))
    if settings.get('transcode'):
        return transcode(source, destination, scale_x, scale_y, settings,
                         on_progress, should_cancel)
    return write_pasp(source, destination, scale_x, scale_y,
                      on_progress, should_cancel)
