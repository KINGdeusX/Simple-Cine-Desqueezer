"""The batch engine: what happens to each selected file, and in what order.

The UI only ever calls ``DesqueezeEngine.process()``.  Which back-end a file
goes to is decided entirely by ``formats`` -- ``resolve_output()`` for stills,
and the video flag for clips -- so adding a format means touching that module
and nothing here.

Events are pushed to an ``emit`` callback rather than yielded, because a video
re-encode takes minutes and reports progress from inside a blocking call that a
generator could not forward.
"""

from __future__ import annotations

import os
from collections import namedtuple

# Reproduced verbatim from the original desktop app.
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
    return Event('rows', '', list(names))


def item(index, status, detail='', percent=0):
    return Event('item', detail, {'status': status, 'percent': percent}, True, index)


def progress(percent):
    return Event('progress', '', int(percent))


def done(ok, message):
    return Event('done', message, 100 if ok else 0, ok)


class DesqueezeEngine:
    """Desqueezes every selected file into a destination folder."""

    name = 'ExifTool + LibRaw + DNGLab + FFmpeg'

    def __init__(self):
        self._cancelled = False

    def cancel(self):
        """Ask the run to stop; the file in flight finishes first."""
        self._cancelled = True

    @property
    def cancelled(self):
        return self._cancelled

    # --- per-file -----------------------------------------------------------

    def _photo(self, source, destination_dir, name, scale_x, scale_y,
               settings, on_progress):
        import formats
        import photo_engine

        plan = formats.resolve_output(
            name,
            settings.get('mode', formats.MODE_TAG),
            settings.get('output', formats.OUT_ORIGINAL),
            settings.get('jpeg_extension', '.jpg'))
        destination = os.path.join(destination_dir, plan.output_name(name))
        destination = _unique(destination, source)
        on_progress(10)
        detail = photo_engine.desqueeze(source, destination, scale_x, scale_y,
                                        plan, settings)
        return detail, os.path.basename(destination)

    def _video(self, source, destination_dir, name, scale_x, scale_y,
               settings, on_progress):
        import video_engine

        destination = os.path.join(destination_dir, name)
        destination = _unique(destination, source)
        detail = video_engine.desqueeze(
            source, destination, scale_x, scale_y, settings,
            on_progress=on_progress,
            should_cancel=lambda: self._cancelled)
        if detail is None:
            return None, os.path.basename(destination)
        return detail, os.path.basename(destination)

    # --- the batch ----------------------------------------------------------

    def process(self, paths, destination_dir, scale_x, scale_y, emit,
                media=None, settings=None):
        """Run the batch, pushing every event to ``emit``."""
        import formats

        self._cancelled = False
        settings = dict(settings or {})

        files = [path for path in paths if formats.accepts(os.path.basename(path),
                                                           media)]
        total = len(files)
        if total == 0:
            emit(done(False, 'No supported files selected. Accepts %s.'
                      % formats.describe_support(media)))
            return

        try:
            os.makedirs(destination_dir, exist_ok=True)
        except OSError as exc:
            emit(done(False, 'Cannot create the output folder: %s' % exc))
            return

        emit(rows([os.path.basename(path) for path in files]))
        emit(log('Found %d file(s) to process.' % total))
        emit(log('Ratio  : %s x %s' % (scale_x, scale_y)))
        emit(log('Output : %s' % destination_dir))
        if media == 'video' and settings.get('transcode'):
            emit(log('Re-encoding with FFmpeg - slower than tagging, and lossy.'))
        emit(log('-' * 52))

        succeeded = failed = 0

        for index, source in enumerate(files):
            name = os.path.basename(source)
            if self._cancelled:
                emit(log('Stopping after %d of %d file(s).' % (index, total)))
                for pending in range(index, total):
                    emit(item(pending, STATUS_SKIPPED, 'skipped'))
                break

            emit(item(index, STATUS_WORKING, 'working'))
            ok, detail = self._one(source, destination_dir, name,
                                   scale_x, scale_y, settings, media,
                                   index, emit)
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
                      % (succeeded, destination_dir)))
        elif failed:
            emit(done(succeeded > 0, 'Finished with %d error(s). %d file(s) -> %s'
                      % (failed, succeeded, destination_dir)))
        else:
            emit(done(True, 'Done! %d file(s) processed -> %s'
                      % (succeeded, destination_dir)))

    def _one(self, source, destination_dir, name, scale_x, scale_y,
             settings, media, index, emit):
        """Handle one file. Returns ``(ok_or_None, detail)``."""
        import formats

        def on_progress(percent):
            emit(item(index, STATUS_WORKING, 'working', percent))

        handler = (self._video if formats.is_video(name) else self._photo)
        try:
            detail, written = handler(source, destination_dir, name,
                                      scale_x, scale_y, settings, on_progress)
        except ValueError as exc:            # an impossible mode/output pairing
            return False, str(exc)
        except Exception as exc:             # never kill the batch
            return False, str(exc) or exc.__class__.__name__

        if detail is None:
            return None, 'cancelled'
        if written != name:
            detail += '  -> %s' % written
        return True, detail


def _unique(destination, source):
    """Never overwrite the file we are reading, and never clobber a result.

    Pointing the output at the folder the originals live in is the obvious thing
    to do, and for a tag-in-place job the output name equals the input name --
    so without this the app would read and write the same bytes.
    """
    if os.path.abspath(destination) != os.path.abspath(source):
        return destination
    stem, extension = os.path.splitext(destination)
    counter = 1
    candidate = '%s_desqueezed%s' % (stem, extension)
    while os.path.exists(candidate) and counter < 1000:
        counter += 1
        candidate = '%s_desqueezed_%d%s' % (stem, counter, extension)
    return candidate


def build_engine():
    return DesqueezeEngine()
