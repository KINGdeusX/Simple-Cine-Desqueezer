"""Pure-Python MP4 desqueezing by pixel-aspect metadata.

Deliberately a copy of ``ANDROID/mp4_engine.py`` rather than a shared import:
the two builds are developed independently, and the PC version is free to
diverge (it has FFmpeg available, the phone does not).  If you fix a bug in one,
check whether the other has it too -- ``PC/tests/test_video.py`` and
``ANDROID/tests/test_video.py`` cover the same ground on purpose.


This is the moving-image counterpart of ``dng_engine``: it does not touch a
single pixel, it tells players how to stretch them.  Two things are written,
and both are needed because players disagree about which one to read:

* **``pasp``** (PixelAspectRatio) inside the video track's sample entry, giving
  ``hSpacing:vSpacing``.  This is the anamorphic flag proper -- Resolve,
  Premiere and FCP honour it.
* **``tkhd`` display dimensions**, the track header's width/height, set to the
  desqueezed size.  QuickTime and several players size the picture from these.

Setting both is what ffmpeg's ``-aspect`` does, and the combination is
spec-conformant rather than double-stretching: ``tkhd`` is the *display* size,
``pasp`` is the *pixel* shape, and a display size of ``stored_width * PAR`` is
exactly the consistent pair.

Files are handled as streams, never loaded whole -- a phone clip can be many
gigabytes.  Only the ``moov`` box (typically well under a megabyte, even for a
long recording) is read into memory to be rewritten; ``mdat`` is copied through
in blocks and never parsed.
"""

from __future__ import annotations

import struct
from fractions import Fraction

# Boxes we descend into.  Everything else is copied through untouched, which is
# what keeps this safe on the endless variety of real-world files.
CONTAINERS = (b'moov', b'trak', b'mdia', b'minf', b'stbl')

HANDLER_VIDEO = b'vide'

# A SampleEntry is 16 bytes; VisualSampleEntry adds 70 more before its children.
VISUAL_SAMPLE_ENTRY_HEADER = 86
# ...within which the stored frame size lives here:
VISUAL_WIDTH_OFFSET = 32
VISUAL_HEIGHT_OFFSET = 34

PASP_SIZE = 16
MAX_UINT32 = 0xFFFFFFFF


class Mp4Error(Exception):
    """The file is not something we can safely rewrite."""


class _Box:
    __slots__ = ('type', 'start', 'header', 'end')

    def __init__(self, type_, start, header, end):
        self.type = type_
        self.start = start          # first byte of the box
        self.header = header        # 8, or 16 when a 64-bit size is used
        self.end = end              # one past the last byte

    @property
    def payload_start(self):
        return self.start + self.header


def _iter_boxes(data, start, end):
    """Walk a run of sibling boxes inside ``data[start:end]``."""
    offset = start
    while offset + 8 <= end:
        size, type_ = struct.unpack_from('>I4s', data, offset)
        header = 8
        if size == 1:                       # 64-bit largesize
            if offset + 16 > end:
                return
            size = struct.unpack_from('>Q', data, offset + 8)[0]
            header = 16
        elif size == 0:                     # extends to the end of the run
            size = end - offset
        if size < header or offset + size > end:
            return                          # truncated or lying: stop cleanly
        yield _Box(type_, offset, header, offset + size)
        offset += size


def _split(box_bytes):
    """Return ``(type, payload)`` for one complete box."""
    size, type_ = struct.unpack_from('>I4s', box_bytes, 0)
    header = 16 if size == 1 else 8
    return type_, box_bytes[header:]


def _build(type_, payload):
    """Serialise a box, always with a 32-bit size (these boxes are small)."""
    total = len(payload) + 8
    if total > MAX_UINT32:
        raise Mp4Error('metadata box grew beyond 4 GB')
    return struct.pack('>I4s', total, type_) + bytes(payload)


# --- the edit itself --------------------------------------------------------

def pixel_aspect(scale_x, scale_y):
    """The ``hSpacing:vSpacing`` pair for a squeeze factor.

    ``pasp`` takes integers, so the ratio is reduced to the smallest pair that
    still represents it: 1.33 becomes 133:100, 2.0 becomes 2:1, 1.5 becomes 3:2.
    """
    try:
        ratio = Fraction(str(scale_x).strip()) / Fraction(str(scale_y).strip())
    except (ValueError, ZeroDivisionError):
        raise Mp4Error('invalid squeeze ratio')
    if ratio <= 0:
        raise Mp4Error('squeeze ratio must be positive')
    ratio = ratio.limit_denominator(10000)
    if ratio.numerator > MAX_UINT32 or ratio.denominator > MAX_UINT32:
        raise Mp4Error('squeeze ratio is out of range')
    return ratio.numerator, ratio.denominator


def _pasp_box(hspacing, vspacing):
    return struct.pack('>I4sII', PASP_SIZE, b'pasp', hspacing, vspacing)


def _patch_sample_entry(raw, hspacing, vspacing, found):
    """Insert or replace ``pasp`` inside one visual sample entry."""
    if len(raw) < VISUAL_SAMPLE_ENTRY_HEADER:
        return raw                       # too short to be a visual entry
    header = raw[:VISUAL_SAMPLE_ENTRY_HEADER]
    found['width'] = struct.unpack_from('>H', header, VISUAL_WIDTH_OFFSET)[0]
    found['height'] = struct.unpack_from('>H', header, VISUAL_HEIGHT_OFFSET)[0]

    children = bytearray()
    replaced = False
    for child in _iter_boxes(raw, VISUAL_SAMPLE_ENTRY_HEADER, len(raw)):
        if child.type == b'pasp':
            children += _pasp_box(hspacing, vspacing)
            replaced = True
        else:
            children += raw[child.start:child.end]
    if not replaced:
        children += _pasp_box(hspacing, vspacing)

    found['entries'] = found.get('entries', 0) + 1
    type_, _ = _split(raw)
    found.setdefault('codec', type_.decode('latin-1'))
    return _build(type_, bytes(header[8:]) + bytes(children))


def _patch_stsd(payload, hspacing, vspacing, found):
    """stsd carries 8 bytes of its own before the sample entries."""
    if len(payload) < 8:
        return payload
    out = bytearray(payload[:8])
    for entry in _iter_boxes(payload, 8, len(payload)):
        out += _patch_sample_entry(payload[entry.start:entry.end],
                                   hspacing, vspacing, found)
    return bytes(out)


def _descend(box_bytes, chain, leaf, *args):
    """Rebuild ``box_bytes``, applying ``leaf`` to the box named by ``chain``."""
    type_, payload = _split(box_bytes)
    if not chain:
        return _build(type_, leaf(payload, *args))
    out = bytearray()
    for child in _iter_boxes(payload, 0, len(payload)):
        raw = payload[child.start:child.end]
        if child.type == chain[0]:
            out += _descend(raw, chain[1:], leaf, *args)
        else:
            out += raw
    return _build(type_, bytes(out))


def _handler_type(trak_bytes):
    """The four-character handler of a trak, e.g. ``b'vide'`` or ``b'soun'``."""
    _type, payload = _split(trak_bytes)
    for mdia in _iter_boxes(payload, 0, len(payload)):
        if mdia.type != b'mdia':
            continue
        mdia_payload = payload[mdia.payload_start:mdia.end]
        for hdlr in _iter_boxes(mdia_payload, 0, len(mdia_payload)):
            if hdlr.type != b'hdlr':
                continue
            # hdlr: version/flags (4), pre_defined (4), handler_type (4)
            base = hdlr.payload_start
            if base + 12 <= hdlr.end:
                return bytes(mdia_payload[base + 8:base + 12])
    return None


def _track_rotation(trak_bytes):
    """Quarter-turn rotation from the tkhd display matrix, in degrees.

    Matters more than it looks: the pixel aspect is applied to the *coded*
    frame and the matrix rotates the result, so on a clip recorded sideways a
    plain horizontal stretch comes out stretching the picture vertically.
    """
    _type, payload = _split(trak_bytes)
    for tkhd in _iter_boxes(payload, 0, len(payload)):
        if tkhd.type != b'tkhd':
            continue
        version = payload[tkhd.payload_start]
        base = tkhd.payload_start + (52 if version == 1 else 40)
        if base + 36 > tkhd.end:
            continue
        a, b = struct.unpack_from('>ii', payload, base)
        a, b = a / 65536.0, b / 65536.0
        if abs(a) < 0.001 and b > 0.5:
            return 90
        if a < -0.5 and abs(b) < 0.001:
            return 180
        if abs(a) < 0.001 and b < -0.5:
            return 270
    return 0


def _set_track_display_size(trak_bytes, width, height):
    """Write the desqueezed display size into tkhd (fixed 16.16, size-stable)."""
    type_, payload = _split(trak_bytes)
    payload = bytearray(payload)
    for tkhd in _iter_boxes(payload, 0, len(payload)):
        if tkhd.type != b'tkhd':
            continue
        version = payload[tkhd.payload_start]
        # width/height sit after the matrix, whose offset depends on version
        offset = tkhd.payload_start + (88 if version == 1 else 76)
        if offset + 8 > tkhd.end:
            continue
        struct.pack_into('>II', payload, offset,
                         min(MAX_UINT32, int(round(width * 65536))),
                         min(MAX_UINT32, int(round(height * 65536))))
    return _build(type_, bytes(payload))


def _patch_moov(moov, scale_x, scale_y):
    """Return ``(new_moov, info)`` with pasp written into every video track."""
    type_, payload = _split(moov)
    info = {'tracks': 0, 'codec': None, 'width': 0, 'height': 0, 'rotation': 0}

    out = bytearray()
    for child in _iter_boxes(payload, 0, len(payload)):
        raw = payload[child.start:child.end]
        if child.type != b'trak' or _handler_type(raw) != HANDLER_VIDEO:
            out += raw
            continue

        rotation = _track_rotation(raw)
        if rotation in (90, 270):
            # The picture is displayed side-on, so widening what the viewer
            # sees means stretching the coded frame vertically instead.
            hspacing, vspacing = pixel_aspect(scale_y, scale_x)
        else:
            hspacing, vspacing = pixel_aspect(scale_x, scale_y)

        found = {}
        patched = _descend(raw, [b'mdia', b'minf', b'stbl', b'stsd'],
                           _patch_stsd, hspacing, vspacing, found)
        if found.get('entries'):
            info['tracks'] += 1
            info['codec'] = info['codec'] or found.get('codec')
            info['width'] = found.get('width', 0)
            info['height'] = found.get('height', 0)
            info['rotation'] = rotation
            info['pasp'] = (hspacing, vspacing)
            patched = _set_track_display_size(
                patched,
                found.get('width', 0) * hspacing / float(vspacing),
                found.get('height', 0))
        out += patched

    if not info['tracks']:
        raise Mp4Error('no video track found')
    info.setdefault('pasp', (1, 1))
    return _build(type_, bytes(out)), info


# --- chunk offsets ----------------------------------------------------------

def _shift_chunk_offsets(moov, delta, threshold):
    """Repair stco/co64 after ``moov`` changed size.

    Chunk offsets are absolute file positions.  Growing ``moov`` pushes
    everything after it forward, so every offset that pointed past the old end
    of ``moov`` has to move with it; offsets into an ``mdat`` that sits *before*
    ``moov`` are already correct and must be left alone.
    """
    data = bytearray(moov)
    _walk_chunk_tables(data, 0, len(data), delta, threshold)
    return bytes(data)


def _walk_chunk_tables(data, start, end, delta, threshold):
    for box in _iter_boxes(data, start, end):
        if box.type in CONTAINERS:
            _walk_chunk_tables(data, box.payload_start, box.end, delta, threshold)
        elif box.type == b'stco':
            _shift_table(data, box, delta, threshold, '>I', 4)
        elif box.type == b'co64':
            _shift_table(data, box, delta, threshold, '>Q', 8)


def _shift_table(data, box, delta, threshold, fmt, width):
    base = box.payload_start + 4                  # skip version/flags
    if base + 4 > box.end:
        return
    count = struct.unpack_from('>I', data, base)[0]
    position = base + 4
    for _ in range(count):
        if position + width > box.end:
            return
        value = struct.unpack_from(fmt, data, position)[0]
        if value >= threshold:
            struct.pack_into(fmt, data, position, value + delta)
        position += width


# --- top level / streaming --------------------------------------------------

def _scan_top_level(stream, file_size):
    """List the top-level boxes without reading their contents."""
    boxes = []
    offset = 0
    while offset + 8 <= file_size:
        stream.seek(offset)
        head = stream.read(16)
        if len(head) < 8:
            break
        size, type_ = struct.unpack_from('>I4s', head, 0)
        header = 8
        if size == 1:
            if len(head) < 16:
                break
            size = struct.unpack_from('>Q', head, 8)[0]
            header = 16
        elif size == 0:
            size = file_size - offset
        if size < header or offset + size > file_size:
            raise Mp4Error('truncated or malformed MP4 (bad %s box)'
                           % type_.decode('latin-1', 'replace'))
        boxes.append(_Box(type_, offset, header, offset + size))
        offset += size
    if not boxes:
        raise Mp4Error('not an MP4/MOV file')
    if boxes[0].type not in (b'ftyp', b'moov', b'mdat', b'free', b'skip', b'wide'):
        raise Mp4Error('not an MP4/MOV file')
    return boxes


def probe_stream(stream, file_size):
    """Read the video track's shape without rewriting anything.

    Returns ``{'width', 'height', 'rotation', 'codec'}``; the transcoder needs
    these to work out what size to encode.
    """
    boxes = _scan_top_level(stream, file_size)
    moov_box = next((box for box in boxes if box.type == b'moov'), None)
    if moov_box is None:
        raise Mp4Error('no moov box: the file may still be recording')
    stream.seek(moov_box.start)
    moov = stream.read(moov_box.end - moov_box.start)

    _type, payload = _split(moov)
    for trak in _iter_boxes(payload, 0, len(payload)):
        raw = payload[trak.start:trak.end]
        if _handler_type(raw) != HANDLER_VIDEO:
            continue
        found = {}
        _descend(raw, [b'mdia', b'minf', b'stbl', b'stsd'], _patch_stsd, 1, 1, found)
        if found.get('entries'):
            return {'width': found.get('width', 0),
                    'height': found.get('height', 0),
                    'rotation': _track_rotation(raw),
                    'codec': found.get('codec', '')}
    raise Mp4Error('no video track found')


class Plan:
    """Everything decided before a single byte of output is written.

    Splitting the work in two matters: every way a file can be rejected is
    discovered here, so a clip that cannot be handled never gets as far as
    creating an output file that the user would later mistake for a result.
    """

    __slots__ = ('boxes', 'moov_box', 'new_moov', 'info', 'file_size')

    def __init__(self, boxes, moov_box, new_moov, info, file_size):
        self.boxes = boxes
        self.moov_box = moov_box
        self.new_moov = new_moov
        self.info = info
        self.file_size = file_size


def plan_desqueeze(source, scale_x, scale_y, file_size):
    """Read and rewrite the metadata, without touching any output."""
    boxes = _scan_top_level(source, file_size)
    moov_box = next((box for box in boxes if box.type == b'moov'), None)
    if moov_box is None:
        raise Mp4Error('no moov box: the file may still be recording, or is '
                       'fragmented in a way this app cannot rewrite')

    source.seek(moov_box.start)
    moov = source.read(moov_box.end - moov_box.start)
    if len(moov) != moov_box.end - moov_box.start:
        raise Mp4Error('could not read the whole moov box')

    new_moov, info = _patch_moov(moov, scale_x, scale_y)
    delta = len(new_moov) - len(moov)

    if delta:
        if any(box.type == b'moof' for box in boxes):
            # Fragmented files carry absolute offsets in tfhd that we would have
            # to chase; refusing beats silently corrupting someone's footage.
            raise Mp4Error('fragmented MP4 needs a pasp atom added, which this '
                           'app will not do safely -- re-encode instead')
        new_moov = _shift_chunk_offsets(new_moov, delta, moov_box.end)

    return Plan(boxes, moov_box, new_moov, info, file_size)


def write_plan(source, destination, plan, on_progress=None, should_cancel=None,
               chunk=1024 * 1024):
    """Stream the planned output. Returns the info dict, or None if cancelled."""
    written = 0
    for box in plan.boxes:
        if should_cancel is not None and should_cancel():
            return None
        if box is plan.moov_box:
            destination.write(plan.new_moov)
            written += len(plan.new_moov)
            continue
        source.seek(box.start)
        remaining = box.end - box.start
        while remaining > 0:
            if should_cancel is not None and should_cancel():
                return None
            block = source.read(min(chunk, remaining))
            if not block:
                raise Mp4Error('unexpected end of file while copying')
            destination.write(block)
            remaining -= len(block)
            written += len(block)
            if on_progress is not None and plan.file_size:
                on_progress(min(99, int(written * 100 / plan.file_size)))

    info = dict(plan.info)
    info['bytes'] = written
    return info


def desqueeze_stream(source, destination, scale_x, scale_y, file_size,
                     on_progress=None, should_cancel=None, chunk=1024 * 1024):
    """Copy ``source`` to ``destination``, desqueezed. Returns an info dict.

    ``source`` must be seekable; ``destination`` is written start to finish.
    """
    plan = plan_desqueeze(source, scale_x, scale_y, file_size)
    return write_plan(source, destination, plan, on_progress, should_cancel, chunk)


# --- reading back (used by the tests) ---------------------------------------

def read_pixel_aspect(data):
    """Return ``[(codec, hSpacing, vSpacing), ...]`` for every video track."""
    results = []
    for box in _iter_boxes(data, 0, len(data)):
        if box.type != b'moov':
            continue
        moov = data[box.start:box.end]
        _type, payload = _split(moov)
        for trak in _iter_boxes(payload, 0, len(payload)):
            raw = payload[trak.start:trak.end]
            if _handler_type(raw) != HANDLER_VIDEO:
                continue
            results.extend(_find_pasp(raw))
    return results


def _find_pasp(data, depth=0):
    out = []
    _type, payload = _split(data)
    for box in _iter_boxes(payload, 0, len(payload)):
        raw = payload[box.start:box.end]
        if box.type in (b'mdia', b'minf', b'stbl'):
            out.extend(_find_pasp(raw, depth + 1))
        elif box.type == b'stsd' and len(raw) > 16:
            for entry in _iter_boxes(raw, 16, len(raw)):
                entry_raw = raw[entry.start:entry.end]
                for child in _iter_boxes(entry_raw, VISUAL_SAMPLE_ENTRY_HEADER,
                                         len(entry_raw)):
                    if child.type == b'pasp' and child.end - child.start >= 16:
                        h, v = struct.unpack_from('>II', entry_raw,
                                                  child.payload_start)
                        out.append((entry.type.decode('latin-1'), h, v))
    return out


def read_track_display_size(data):
    """Return ``[(width, height), ...]`` from tkhd, for every video track."""
    sizes = []
    for box in _iter_boxes(data, 0, len(data)):
        if box.type != b'moov':
            continue
        _type, payload = _split(data[box.start:box.end])
        for trak in _iter_boxes(payload, 0, len(payload)):
            raw = payload[trak.start:trak.end]
            if _handler_type(raw) != HANDLER_VIDEO:
                continue
            _t, trak_payload = _split(raw)
            for tkhd in _iter_boxes(trak_payload, 0, len(trak_payload)):
                if tkhd.type != b'tkhd':
                    continue
                version = trak_payload[tkhd.payload_start]
                offset = tkhd.payload_start + (88 if version == 1 else 76)
                if offset + 8 <= tkhd.end:
                    w, h = struct.unpack_from('>II', trak_payload, offset)
                    sizes.append((w / 65536.0, h / 65536.0))
    return sizes


def is_probably_mp4(head):
    """Cheap check on the first bytes of a file."""
    if len(head) < 12:
        return False
    size, type_ = struct.unpack_from('>I4s', head, 0)
    if type_ == b'ftyp':
        return True
    return type_ in (b'moov', b'mdat', b'free', b'skip', b'wide')
