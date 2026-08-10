"""
Pure-Python DNG ``DefaultScale`` writer  --  "Engine Option A".

Why this exists
---------------
The desktop app shells out to ExifTool (Perl).  Perl does not run on Android,
so the Android port writes the tag itself.  The output has to be *identical* to
what ExifTool would have written, because a user may mix desktop-processed and
phone-processed frames inside the same project.

Two things are needed for that parity:

1.  **The same float -> rational conversion.**  ``rationalize()`` below is a
    direct port of ExifTool's ``Image::ExifTool::Rationalize`` (Writer.pl), used
    with ``maxInt = 0xffffffff`` exactly like ExifTool's ``SetRational64u``.
2.  **The same destination IFD.**  ExifTool's tag table declares DefaultScale
    (0xC61E) with ``WriteGroup => 'SubIFD'``, so when a DNG has a raw SubIFD the
    tag goes there, not into IFD0.  ``_select_target_ifds()`` reproduces that.

The file is patched, never rebuilt: existing tag values are overwritten in
place, and a missing tag is added by appending a fresh copy of the target IFD
(plus its 16 value bytes) to the end of the file and re-pointing the single
32-bit offset that referenced it.  Nothing that already exists ever moves, so
strip offsets, MakerNote absolute offsets and JPEG previews all stay valid.

No third-party dependencies -- this module must stay importable on a bare
CPython so it can be unit-tested on the host and run unchanged on Android.
"""

from __future__ import annotations

import struct

# --- TIFF / DNG constants --------------------------------------------------

TAG_SUBIFDS = 0x014A          # SubIFDs (LONG/IFD, n)
TAG_DNG_VERSION = 0xC612      # DNGVersion (BYTE, 4)
TAG_DEFAULT_SCALE = 0xC61E    # DefaultScale (RATIONAL, 2)

TYPE_RATIONAL = 5

# byte size of every TIFF field type we may encounter
_TYPE_SIZE = {
    1: 1,   # BYTE
    2: 1,   # ASCII
    3: 2,   # SHORT
    4: 4,   # LONG
    5: 8,   # RATIONAL
    6: 1,   # SBYTE
    7: 1,   # UNDEFINED
    8: 2,   # SSHORT
    9: 4,   # SLONG
    10: 8,  # SRATIONAL
    11: 4,  # FLOAT
    12: 8,  # DOUBLE
    13: 4,  # IFD
}

MAX_UINT32 = 0xFFFFFFFF


class DngError(Exception):
    """Raised for anything that makes a file unsafe to patch."""


# --- ExifTool-compatible float -> rational ---------------------------------

def _assemble_rational(num, denom, fracs):
    """Port of ExifTool's recursive ``AssembleRational``."""
    for frac in fracs:
        num, denom = frac * num + denom, num
    return num, denom


def rationalize(value, max_int=MAX_UINT32):
    """Convert a float to (numerator, denominator).

    Line-for-line port of ``Image::ExifTool::Rationalize``.  ExifTool's
    ``SetRational64u`` -- the writer used for DefaultScale's ``rational64u``
    format -- passes ``0xffffffff`` as the maximum, so that is the default here.
    """
    if isinstance(value, str):
        text = value.strip()
        if text == 'inf':
            return 1, 0
        if text == 'undef':
            return 0, 0
        if '/' in text:                       # ExifTool accepts "n/d" verbatim
            num_txt, _, den_txt = text.partition('/')
            try:
                return int(num_txt), int(den_txt)
            except ValueError:
                pass
        value = float(text)
    else:
        value = float(value)

    if value == 0:
        return 0, 1

    sign = 1
    if value < 0:
        value = -value
        sign = -1

    num = denom = None
    fracs = []
    frac = value

    while True:
        n, d = _assemble_rational(int(frac + 0.5), 1, fracs)
        if n > max_int or d > max_int:
            if num is not None:
                break
            if value < 1:
                return sign, max_int
            return sign * max_int, 1
        num, denom = n, d
        err = (n / d - value) / value
        if abs(err) < 1e-8:
            break
        whole = int(frac)
        fracs.insert(0, whole)
        frac -= whole
        if frac == 0:
            break
        frac = 1.0 / frac

    return num * sign, denom


def default_scale_bytes(scale_x, scale_y, endian):
    """The 16 raw bytes ExifTool would store for ``-DefaultScale=x y``."""
    nx, dx = rationalize(scale_x)
    ny, dy = rationalize(scale_y)
    for part in (nx, dx, ny, dy):
        if not 0 <= part <= MAX_UINT32:
            raise DngError('scale value out of range for a 32-bit rational')
    return struct.pack(endian + 'IIII', nx, dx, ny, dy)


# --- minimal TIFF/IFD reader -----------------------------------------------

class _Entry:
    """One 12-byte IFD entry, remembered by position so it can be patched."""

    __slots__ = ('tag', 'type', 'count', 'offset', 'value_field_offset')

    def __init__(self, tag, type_, count, offset, value_field_offset):
        self.tag = tag
        self.type = type_
        self.count = count
        self.offset = offset                        # start of the 12-byte entry
        self.value_field_offset = value_field_offset  # start of its 4-byte value

    @property
    def byte_size(self):
        return _TYPE_SIZE.get(self.type, 0) * self.count

    @property
    def is_inline(self):
        return self.byte_size <= 4


class _Ifd:
    """A parsed IFD plus the location of the pointer that led us to it."""

    __slots__ = ('offset', 'entries', 'next_offset_pos', 'pointer_pos', 'kind')

    def __init__(self, offset, entries, next_offset_pos, pointer_pos, kind):
        self.offset = offset
        self.entries = entries
        self.next_offset_pos = next_offset_pos  # where this IFD's "next" link lives
        self.pointer_pos = pointer_pos          # the uint32 pointing *at* this IFD
        self.kind = kind                        # 'IFD0' / 'IFD1' / 'SubIFD0' ...

    def find(self, tag):
        for entry in self.entries:
            if entry.tag == tag:
                return entry
        return None


def _u16(data, off, endian):
    return struct.unpack_from(endian + 'H', data, off)[0]


def _u32(data, off, endian):
    return struct.unpack_from(endian + 'I', data, off)[0]


def _read_header(data):
    if len(data) < 8:
        raise DngError('file is too small to be a TIFF/DNG')
    order = bytes(data[:2])
    if order == b'II':
        endian = '<'
    elif order == b'MM':
        endian = '>'
    else:
        raise DngError('not a TIFF/DNG file (bad byte-order mark)')
    magic = _u16(data, 2, endian)
    if magic == 43:
        raise DngError('BigTIFF files are not supported')
    if magic != 42:
        raise DngError('not a TIFF/DNG file (bad magic number)')
    first_ifd = _u32(data, 4, endian)
    if not 8 <= first_ifd < len(data):
        raise DngError('IFD0 offset points outside the file')
    return endian, first_ifd


def _parse_ifd(data, offset, endian, pointer_pos, kind):
    if offset + 2 > len(data):
        raise DngError('%s starts past the end of the file' % kind)
    count = _u16(data, offset, endian)
    end = offset + 2 + count * 12 + 4
    if count == 0 or end > len(data):
        raise DngError('%s is truncated or empty' % kind)
    entries = []
    for i in range(count):
        pos = offset + 2 + i * 12
        tag = _u16(data, pos, endian)
        type_ = _u16(data, pos + 2, endian)
        n = _u32(data, pos + 4, endian)
        entries.append(_Entry(tag, type_, n, pos, pos + 8))
    return _Ifd(offset, entries, end - 4, pointer_pos, kind)


def _sub_ifd_offsets(data, entry, endian):
    """Yield ``(offset, position_of_that_offset)`` for a SubIFDs entry."""
    if entry.type not in (4, 13) or entry.count == 0:
        return
    if entry.is_inline:
        yield _u32(data, entry.value_field_offset, endian), entry.value_field_offset
        return
    base = _u32(data, entry.value_field_offset, endian)
    for i in range(entry.count):
        pos = base + i * 4
        if pos + 4 > len(data):
            return
        yield _u32(data, pos, endian), pos


def parse_ifds(data, endian, first_ifd):
    """Walk the IFD chain and each IFD's SubIFDs (one level, like DNG uses)."""
    ifds = []
    seen = set()
    offset = first_ifd
    pointer_pos = 4
    index = 0
    while offset and offset not in seen and index < 16:
        seen.add(offset)
        ifd = _parse_ifd(data, offset, endian, pointer_pos, 'IFD%d' % index)
        ifds.append(ifd)

        sub_entry = ifd.find(TAG_SUBIFDS)
        if sub_entry is not None:
            for sub_index, (sub_off, sub_ptr) in enumerate(
                    _sub_ifd_offsets(data, sub_entry, endian)):
                if not sub_off or sub_off in seen:
                    continue
                seen.add(sub_off)
                try:
                    ifds.append(_parse_ifd(data, sub_off, endian, sub_ptr,
                                           'SubIFD%d' % sub_index))
                except DngError:
                    continue  # a broken SubIFD must not sink the whole file

        offset = _u32(data, ifd.next_offset_pos, endian)
        pointer_pos = ifd.next_offset_pos
        index += 1
    return ifds


def select_target_ifd(ifds):
    """Pick the IFD the tag goes in -- verified against real ExifTool.

    ExifTool's tag table gives 0xC61E ``WriteGroup => 'SubIFD'``, and its
    observed behaviour (see ANDROID/tests/test_parity.py) is:

    * the tag is written to the **first SubIFD**, whether or not it already
      exists there, and a stale copy sitting in IFD0 is deliberately left alone;
    * on a DNG with **no SubIFD at all**, ``-DefaultScale=`` silently writes
      nothing, because ExifTool will not create a SubIFD.

    We reproduce the first rule exactly.  For the second we deviate on purpose
    and write into IFD0 -- byte-for-byte what ``exiftool -IFD0:DefaultScale=``
    produces -- because IFD0 *is* the raw image IFD in those single-IFD
    CinemaDNG frames, so silently doing nothing would just lose the user's work.
    The caller is told via the returned flag so it can log the difference.

    Returns ``(ifd, used_ifd0_fallback)``.
    """
    for ifd in ifds:
        if ifd.kind.startswith('SubIFD'):
            return ifd, False
    return ifds[0], True


# --- patching ---------------------------------------------------------------

def _append(out, payload):
    """Append at an even offset (TIFF requires word-aligned values)."""
    if len(out) % 2:
        out += b'\x00'
    offset = len(out)
    out += payload
    return offset


def _rebuild_ifd_with_tag(out, ifd, endian, value_bytes):
    """Append a copy of ``ifd`` that also contains DefaultScale.

    Returns the offset of the new IFD.  Existing entries are copied verbatim so
    every value they point at keeps working; only the new entry needs storage.
    """
    entries = []
    for entry in ifd.entries:
        entries.append((entry.tag, bytes(out[entry.offset:entry.offset + 12])))

    new_ifd_size = 2 + (len(entries) + 1) * 12 + 4
    if len(out) % 2:
        out += b'\x00'
    new_ifd_offset = len(out)
    value_offset = new_ifd_offset + new_ifd_size
    if value_offset % 2:
        value_offset += 1
    if value_offset + len(value_bytes) > MAX_UINT32:
        raise DngError('file is too large to patch (>4 GB)')

    new_entry = struct.pack(endian + 'HHII', TAG_DEFAULT_SCALE, TYPE_RATIONAL,
                            2, value_offset)
    entries.append((TAG_DEFAULT_SCALE, new_entry))
    entries.sort(key=lambda item: item[0])  # TIFF requires ascending tag order

    next_offset = _u32(out, ifd.next_offset_pos, endian)

    blob = bytearray()
    blob += struct.pack(endian + 'H', len(entries))
    for _, raw in entries:
        blob += raw
    blob += struct.pack(endian + 'I', next_offset)
    assert len(blob) == new_ifd_size
    out += blob
    while len(out) < value_offset:
        out += b'\x00'
    out += value_bytes
    return new_ifd_offset


def patch_default_scale(data, scale_x, scale_y):
    """Return ``(patched, target_kind, used_ifd0_fallback)``.

    Pass a ``bytearray`` to have it patched in place and handed back -- a DNG
    frame can be tens of megabytes, and on a phone an extra full copy per file
    is worth avoiding.  ``bytes`` input is copied, leaving the original alone.
    The result is always a ``bytearray``.
    """
    out = data if isinstance(data, bytearray) else bytearray(data)
    endian, first_ifd = _read_header(out)
    ifds = parse_ifds(out, endian, first_ifd)
    if not ifds:
        raise DngError('no readable IFD found')

    value_bytes = default_scale_bytes(scale_x, scale_y, endian)
    ifd, fallback = select_target_ifd(ifds)
    entry = ifd.find(TAG_DEFAULT_SCALE)

    if entry is not None and entry.type == TYPE_RATIONAL and entry.count == 2:
        # 16 bytes never fit inline, so the value always lives out-of-line
        offset = _u32(out, entry.value_field_offset, endian)
        if offset + 16 <= len(out):
            out[offset:offset + 16] = value_bytes
            return out, ifd.kind, fallback
        # dangling pointer -- fall through and re-point it below

    if entry is not None:
        offset = _append(out, value_bytes)
        struct.pack_into(endian + 'HHII', out, entry.offset,
                         TAG_DEFAULT_SCALE, TYPE_RATIONAL, 2, offset)
    else:
        new_offset = _rebuild_ifd_with_tag(out, ifd, endian, value_bytes)
        struct.pack_into(endian + 'I', out, ifd.pointer_pos, new_offset)

    return out, ifd.kind, fallback


def read_default_scale(data):
    """Read DefaultScale back as ``[(num, den), (num, den)]`` per IFD found."""
    endian, first_ifd = _read_header(data)
    results = []
    for ifd in parse_ifds(data, endian, first_ifd):
        entry = ifd.find(TAG_DEFAULT_SCALE)
        if entry is None:
            continue
        offset = _u32(data, entry.value_field_offset, endian)
        if offset + 16 > len(data):
            continue
        nx, dx, ny, dy = struct.unpack_from(endian + 'IIII', data, offset)
        results.append((ifd.kind, (nx, dx), (ny, dy)))
    return results


def is_probably_dng(data):
    """Cheap sanity check used before patching: TIFF header + DNGVersion tag."""
    try:
        endian, first_ifd = _read_header(data)
        ifd0 = _parse_ifd(data, first_ifd, endian, 4, 'IFD0')
    except DngError:
        return False
    return ifd0.find(TAG_DNG_VERSION) is not None
