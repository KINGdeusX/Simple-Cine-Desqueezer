"""The desqueeze engine interface and its default (pure-Python) implementation.

``main.py`` only ever talks to ``DesqueezeEngine.process()``, mirroring how the
desktop app keeps everything behind ``ExifWorker``.  Swapping in the bundled
ARM-exiftool approach (Instruction.md's Option B) means writing another class
with this same ``process()`` signature and changing ``build_engine()`` -- no UI
code has to move.
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


# kind: 'log' | 'progress' | 'done'
ProgressEvent = namedtuple('ProgressEvent', 'kind message value ok')
ProgressEvent.__new__.__defaults__ = ('', 0, True)


def log(message):
    return ProgressEvent('log', message)


def progress(percent):
    return ProgressEvent('progress', '', int(percent))


def done(ok, message):
    return ProgressEvent('done', message, 100 if ok else 0, ok)


class DesqueezeEngine:
    """Desqueezes every selected image into a destination folder.

    Raw files get the ``DefaultScale`` tag; rendered images get their pixels
    stretched.  ``formats.kind_of()`` decides which, per file.

    ``process()`` is a generator so the caller controls pacing and can drop the
    events onto the UI thread itself; it never touches any UI object.
    """

    name = 'Pure-Python tag writer + image resizer'

    def __init__(self):
        self._cancelled = False
        self._warned_ifd0 = False

    def cancel(self):
        """Ask the run to stop.  The in-flight file finishes, like the desktop app."""
        self._cancelled = True

    def _process_one(self, source, dest, name, handle, scale_x, scale_y):
        """Handle a single file, yielding log events plus one outcome marker."""
        import formats
        from storage import StorageError

        handler = (self._write_tag if formats.kind_of(name) == formats.KIND_RAW
                   else self._resize)
        try:
            detail, used_ifd0 = handler(source, dest, name, handle,
                                        scale_x, scale_y)
        except StorageError as exc:
            yield log('  !  %s - %s' % (name, exc))
            yield _outcome(False)
            return
        except Exception as exc:                      # never kill the batch
            yield log('  !  %s - %s' % (name, exc))
            yield _outcome(False)
            return

        if used_ifd0 and not self._warned_ifd0:
            self._warned_ifd0 = True
            yield log('  i  no SubIFD here: DefaultScale goes to IFD0')
        yield log('  +  %s  %s' % (name, detail))
        yield _outcome(True)

    def _write_tag(self, source, dest, name, handle, scale_x, scale_y):
        from dng_engine import patch_default_scale

        # bytearray + in-place patch keeps peak memory at one copy of the frame
        # rather than three
        data = bytearray(source.read(handle))
        patched, target, used_ifd0 = patch_default_scale(data, scale_x, scale_y)
        del data
        written = dest.write(name, patched)
        del patched
        suffix = '' if written == name else '  -> %s' % written
        return '[%s]%s' % (target, suffix), used_ifd0

    def _resize(self, source, dest, name, handle, scale_x, scale_y):
        from image_engine import resize_desqueeze

        encoded, description = resize_desqueeze(source.read(handle), name,
                                                scale_x, scale_y)
        written = dest.write(name, encoded)
        del encoded
        suffix = '' if written == name else '  -> %s' % written
        return '[%s]%s' % (description, suffix), False

    @property
    def cancelled(self):
        return self._cancelled

    def process(self, source, dest, scale_x, scale_y):
        # Imported here, not at module scope, so app startup never pays for it.
        import formats
        from storage import StorageError

        self._cancelled = False
        self._warned_ifd0 = False

        try:
            files = source.list_images()
        except StorageError as exc:
            yield done(False, 'Cannot read the selection: %s' % exc)
            return

        total = len(files)
        if total == 0:
            yield done(False, 'No supported images selected. Accepts %s.'
                       % ', '.join(formats.SUPPORTED_EXTENSIONS))
            return

        yield log('Found %d file(s) to process.' % total)
        yield log('Ratio  : %s x %s' % (scale_x, scale_y))
        yield log('-' * 46)

        succeeded = 0
        failed = 0
        self._warned_ifd0 = False

        for index, (name, handle) in enumerate(files, start=1):
            if self._cancelled:
                yield log('Stopping after %d of %d file(s).' % (index - 1, total))
                break
            for event in self._process_one(source, dest, name, handle,
                                           scale_x, scale_y):
                if event.kind == 'ok':
                    succeeded += 1
                elif event.kind == 'fail':
                    failed += 1
                else:
                    yield event

            yield progress(min(int(index * 100 / total), 99))

        if self._cancelled:
            yield done(True, 'Cancelled - %d file(s) written to %s'
                       % (succeeded, dest.label))
        elif failed:
            yield done(succeeded > 0, 'Finished with %d error(s). %d file(s) -> %s'
                       % (failed, succeeded, dest.label))
        else:
            yield done(True, 'Done! %d file(s) processed -> %s'
                       % (succeeded, dest.label))


def _outcome(ok):
    """Internal marker events, consumed by process() and never yielded on."""
    return ProgressEvent('ok' if ok else 'fail')


def build_engine():
    """Single place to swap engine implementations (Option A <-> Option B)."""
    return DesqueezeEngine()
