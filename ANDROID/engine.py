"""The desqueeze engine: what happens to each selected file, and in what order.

``main.py`` only ever calls ``DesqueezeEngine.process()``, mirroring how the
desktop app keeps everything behind ``ExifWorker``.  Which of the three
back-ends a file goes to is decided entirely by ``formats.kind_of()``:

    raw stills    -> dng_engine      writes DefaultScale, pixels untouched
    rendered      -> image_engine    resizes the pixels
    video         -> video_engine    writes pasp, or re-encodes if asked

Events are pushed to an ``emit`` callback rather than yielded.  That matters
for video: a re-encode takes minutes and reports its progress from inside a
blocking call, which a generator could not forward.
"""

from __future__ import annotations

from collections import namedtuple

# Reproduced verbatim from the desktop app's LENS_PRESETS.
LENS_PRESETS = [
    ("1.33x - Canon C70 / C300 / C500 / Sigma Cine / DJI", "1.33", "1.0"),
    ("1.5x  - Kowa / Vintage Anamorphic / Iscorama 36",    "1.5",  "1.0"),
    ("1.6x  - Lomo Square Front / Some Vintage Glass",     "1.6",  "1.0"),
    ("1.8x  - Panasonic GH5/GH6 Anamorphic Mode",          "1.8",  "1.0"),
    ("2.0x  - Panavision / Hawk / Cooke / Classic Anamorphic", "2.0", "1.0"),
    ("2.0x  - Lomo 35 OCT-19 / Russian Vintage",           "2.0",  "1.0"),
    ("1.25x - Sirui Sniper / Entry Anamorphic Lenses",     "1.25", "1.0"),
    ("1.79x - RED DSMC Anamorphic Mode",                   "1.79", "1.0"),
    ("2.39x - Ultra Panavision 70 (Rare)",                 "2.39", "1.0"),
    ("Custom...",                                          None,   None),
]

CUSTOM_INDEX = len(LENS_PRESETS) - 1

STATUS_WAITING = 'waiting'
STATUS_WORKING = 'working'
STATUS_DONE = 'done'
STATUS_FAILED = 'failed'
STATUS_SKIPPED = 'skipped'

# kind: 'log' | 'rows' | 'item' | 'progress' | 'done'
Event = namedtuple('Event', 'kind message value ok index')
Event.__new__.__defaults__ = ('', 0, True, -1)


def log(message):
    return Event('log', message)


def rows(names):
    """The table the UI draws, one entry per file, in processing order."""
    return Event('rows', '', list(names))


def item(index, status, detail='', percent=0):
    return Event('item', detail, {'status': status, 'percent': percent}, True, index)


def progress(percent):
    return Event('progress', '', int(percent))


def done(ok, message):
    return Event('done', message, 100 if ok else 0, ok)


class DesqueezeEngine:
    """Desqueezes every selected file into a destination folder."""

    name = 'tag writer + image resizer + video pasp/HEVC'

    def __init__(self):
        self._cancelled = False
        self._warned_ifd0 = False

    def cancel(self):
        """Ask the run to stop.  The in-flight file finishes, like the desktop app."""
        self._cancelled = True

    @property
    def cancelled(self):
        return self._cancelled

    # --- per-file back-ends -------------------------------------------------

    def _write_tag(self, context):
        from dng_engine import patch_default_scale

        source, dest, name, handle = context['io']
        # bytearray + in-place patch keeps peak memory at one copy of the frame
        # rather than three
        data = bytearray(source.read(handle))
        patched, target, used_ifd0 = patch_default_scale(
            data, context['scale_x'], context['scale_y'])
        del data
        written = dest.write(name, patched)
        del patched
        return '[%s]' % target, written, used_ifd0

    def _resize(self, context):
        from image_engine import resize_desqueeze

        source, dest, name, handle = context['io']
        encoded, description = resize_desqueeze(
            source.read(handle), name, context['scale_x'], context['scale_y'])
        written = dest.write(name, encoded)
        del encoded
        return '[%s]' % description, written, False

    def _video(self, context):
        import video_engine

        source, dest, name, handle = context['io']
        detail, written = video_engine.desqueeze(
            source, dest, name, handle, context['scale_x'], context['scale_y'],
            context['settings'],
            on_progress=context['on_progress'],
            should_cancel=lambda: self._cancelled)
        if detail is None:
            return None, written, False          # cancelled partway
        return '[%s]' % detail, written, False

    # --- the batch ----------------------------------------------------------

    def process(self, source, dest, scale_x, scale_y, emit,
                media=None, settings=None):
        """Run the batch, pushing every event to ``emit``."""
        import formats
        from storage import StorageError

        self._cancelled = False
        self._warned_ifd0 = False

        try:
            files = source.list_images(media)
        except StorageError as exc:
            emit(done(False, 'Cannot read the selection: %s' % exc))
            return

        total = len(files)
        if total == 0:
            emit(done(False, 'No supported files selected. Accepts %s.'
                      % ', '.join(formats.SUPPORTED_EXTENSIONS)))
            return

        emit(rows([name for name, _handle in files]))
        emit(log('Found %d file(s) to process.' % total))
        emit(log('Ratio  : %s x %s' % (scale_x, scale_y)))
        if media == 'video' and (settings or {}).get('transcode'):
            emit(log('Re-encoding to HEVC - slower than tagging, and lossy.'))
        emit(log('-' * 46))

        succeeded = failed = 0

        for index, (name, handle) in enumerate(files):
            if self._cancelled:
                emit(log('Stopping after %d of %d file(s).' % (index, total)))
                for pending in range(index, total):
                    emit(item(pending, STATUS_SKIPPED, 'skipped'))
                break

            emit(item(index, STATUS_WORKING, 'working'))
            ok, detail = self._process_one(source, dest, name, handle,
                                           scale_x, scale_y, settings, index, emit)
            if ok is None:
                emit(item(index, STATUS_SKIPPED, 'cancelled'))
            elif ok:
                succeeded += 1
                emit(item(index, STATUS_DONE, detail, 100))
                emit(log('  +  %s  %s' % (name, detail)))
            else:
                failed += 1
                emit(item(index, STATUS_FAILED, detail))
                emit(log('  !  %s - %s' % (name, detail)))

            emit(progress(min(int((index + 1) * 100 / total), 99)))

        if self._cancelled:
            emit(done(True, 'Cancelled - %d file(s) written to %s'
                      % (succeeded, dest.label)))
        elif failed:
            emit(done(succeeded > 0, 'Finished with %d error(s). %d file(s) -> %s'
                      % (failed, succeeded, dest.label)))
        else:
            emit(done(True, 'Done! %d file(s) processed -> %s'
                      % (succeeded, dest.label)))

    def _process_one(self, source, dest, name, handle, scale_x, scale_y,
                     settings, index, emit):
        """Handle one file. Returns ``(ok_or_None, detail)``."""
        import formats
        from storage import StorageError

        kind = formats.kind_of(name)
        handler = {formats.KIND_RAW: self._write_tag,
                   formats.KIND_PIXEL: self._resize,
                   formats.KIND_VIDEO: self._video}.get(kind)
        if handler is None:
            return False, 'unsupported file type'

        context = {
            'io': (source, dest, name, handle),
            'scale_x': scale_x,
            'scale_y': scale_y,
            'settings': settings,
            'on_progress': lambda percent: emit(
                item(index, STATUS_WORKING, 'working', percent)),
        }

        try:
            detail, written, used_ifd0 = handler(context)
        except StorageError as exc:
            return False, str(exc)
        except Exception as exc:                      # never kill the batch
            return False, str(exc) or exc.__class__.__name__
        if detail is None:
            return None, 'cancelled'

        if used_ifd0 and not self._warned_ifd0:
            self._warned_ifd0 = True
            emit(log('  i  no SubIFD here: DefaultScale goes to IFD0'))
        if written != name:
            detail += '  -> %s' % written
        return True, detail


def build_engine():
    """Single place to swap engine implementations."""
    return DesqueezeEngine()
