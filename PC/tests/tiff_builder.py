"""Build small but structurally valid DNG files for the parity tests.

Real camera DNGs are far too large to check into a repo, and the placement
rules we need to verify only depend on structure (does the file have a raw
SubIFD? does DefaultScale already exist? which byte order?), so the tests
synthesise files that cover each of those shapes.
"""

from __future__ import annotations

import struct

BYTE, ASCII, SHORT, LONG, RATIONAL, UNDEFINED = 1, 2, 3, 4, 5, 7
_SIZE = {BYTE: 1, ASCII: 1, SHORT: 2, LONG: 4, RATIONAL: 8, UNDEFINED: 1}


def _pack(type_, values, endian):
    if type_ == ASCII:
        return values.encode('ascii') + b'\x00'
    if type_ in (BYTE, UNDEFINED):
        return bytes(values)
    if type_ == SHORT:
        return struct.pack(endian + '%dH' % len(values), *values)
    if type_ == LONG:
        return struct.pack(endian + '%dI' % len(values), *values)
    if type_ == RATIONAL:
        flat = [n for pair in values for n in pair]
        return struct.pack(endian + '%dI' % len(flat), *flat)
    raise ValueError('unsupported type %r' % type_)


def _count(type_, values):
    if type_ == ASCII:
        return len(values) + 1
    return len(values)


class Ifd:
    def __init__(self):
        self.tags = {}

    def add(self, tag, type_, values):
        self.tags[tag] = (type_, values)
        return self

    def entry_block_size(self):
        return 2 + len(self.tags) * 12 + 4


def _strip_size(ifd):
    """Bytes of image data implied by an IFD's dimensions.

    ExifTool validates StripByteCounts against width/height/bits, so the dummy
    strips have to be the size the tags claim."""
    width = ifd.tags[0x0100][1][0]
    height = ifd.tags[0x0101][1][0]
    bits = ifd.tags[0x0102][1]
    return width * height * sum(bits) // 8


def build_dng(ifd0, sub_ifds=(), endian='<'):
    """Serialise IFD0 (+ optional SubIFDs) into a TIFF/DNG byte string.

    Layout: header, IFD0, SubIFDs, SubIFDs-offset array, out-of-line values,
    image strips.  Every StripOffsets/StripByteCounts pair is filled in for you
    so ExifTool sees a coherent file it is willing to rewrite.
    """
    ifds = [ifd0] + list(sub_ifds)

    # Every tag must exist before the layout pass, because offsets depend on
    # the entry count.  Placeholder values are replaced afterwards; they are
    # all LONGs, so substituting them never changes any payload size.
    if sub_ifds:
        ifd0.add(0x014A, LONG, [0] * len(sub_ifds))     # SubIFDs
    strip_sizes = [_strip_size(ifd) for ifd in ifds]
    for ifd, size in zip(ifds, strip_sizes):
        ifd.add(0x0111, LONG, [0])                      # StripOffsets
        ifd.add(0x0117, LONG, [size])                   # StripByteCounts

    cursor = 8
    ifd_offsets = []
    for ifd in ifds:
        ifd_offsets.append(cursor)
        cursor += ifd.entry_block_size()

    value_offsets = {}
    for index, ifd in enumerate(ifds):
        for tag, (type_, values) in sorted(ifd.tags.items()):
            if len(_pack(type_, values, endian)) > 4:
                if cursor % 2:
                    cursor += 1
                value_offsets[(index, tag)] = cursor
                cursor += len(_pack(type_, values, endian))

    strip_offsets = []
    for size in strip_sizes:
        if cursor % 2:
            cursor += 1
        strip_offsets.append(cursor)
        cursor += size

    # resolve the placeholders now that the layout is known
    if sub_ifds:
        ifd0.tags[0x014A] = (LONG, list(ifd_offsets[1:]))
    for index, ifd in enumerate(ifds):
        ifd.tags[0x0111] = (LONG, [strip_offsets[index]])

    out = bytearray(cursor)
    out[0:2] = b'II' if endian == '<' else b'MM'
    struct.pack_into(endian + 'HI', out, 2, 42, ifd_offsets[0])

    for index, ifd in enumerate(ifds):
        pos = ifd_offsets[index]
        struct.pack_into(endian + 'H', out, pos, len(ifd.tags))
        pos += 2
        for tag, (type_, values) in sorted(ifd.tags.items()):
            payload = _pack(type_, values, endian)
            struct.pack_into(endian + 'HHI', out, pos, tag, type_,
                             _count(type_, values))
            if len(payload) > 4:
                offset = value_offsets[(index, tag)]
                out[offset:offset + len(payload)] = payload
                struct.pack_into(endian + 'I', out, pos + 8, offset)
            else:
                out[pos + 8:pos + 8 + len(payload)] = payload
            pos += 12
        # next-IFD link: none (SubIFDs are referenced by tag, not by the chain)
        struct.pack_into(endian + 'I', out, pos, 0)

    for index, (offset, size) in enumerate(zip(strip_offsets, strip_sizes)):
        out[offset:offset + size] = bytes(
            ((index * 7 + i) % 251 for i in range(size)))

    return bytes(out)


def _raw_ifd(width=64, height=48, subfile_type=0):
    ifd = Ifd()
    ifd.add(0x00FE, LONG, [subfile_type])              # NewSubfileType
    ifd.add(0x0100, LONG, [width])                     # ImageWidth
    ifd.add(0x0101, LONG, [height])                    # ImageLength
    ifd.add(0x0102, SHORT, [16])                       # BitsPerSample
    ifd.add(0x0103, SHORT, [1])                        # Compression: none
    ifd.add(0x0106, SHORT, [32803])                    # PhotometricInterpretation: CFA
    ifd.add(0x0115, SHORT, [1])                        # SamplesPerPixel
    ifd.add(0x0116, LONG, [height])                    # RowsPerStrip
    ifd.add(0x828D, SHORT, [2, 2])                     # CFARepeatPatternDim
    ifd.add(0x828E, BYTE, [0, 1, 1, 2])                # CFAPattern
    return ifd


def _thumb_ifd(width=16, height=12):
    ifd = Ifd()
    ifd.add(0x00FE, LONG, [1])                         # reduced-resolution image
    ifd.add(0x0100, LONG, [width])
    ifd.add(0x0101, LONG, [height])
    ifd.add(0x0102, SHORT, [8, 8, 8])
    ifd.add(0x0103, SHORT, [1])
    ifd.add(0x0106, SHORT, [2])                        # RGB
    ifd.add(0x010F, ASCII, 'Desqueeze')                # Make
    ifd.add(0x0110, ASCII, 'TestCam')                  # Model
    ifd.add(0x0115, SHORT, [3])
    ifd.add(0x0116, LONG, [height])
    ifd.add(0xC612, BYTE, [1, 4, 0, 0])                # DNGVersion
    ifd.add(0xC614, ASCII, 'Desqueeze TestCam')        # UniqueCameraModel
    return ifd


def make_variant(name, endian='<'):
    """Return the bytes of one named test-file shape."""
    if name == 'standard':
        # IFD0 thumbnail + raw SubIFD -- the common camera DNG layout
        return build_dng(_thumb_ifd(), [_raw_ifd()], endian)

    if name == 'cinemadng':
        # single IFD holding the raw image, no SubIFD (CinemaDNG frames)
        ifd = _raw_ifd()
        ifd.add(0xC612, BYTE, [1, 4, 0, 0])
        ifd.add(0xC614, ASCII, 'Desqueeze CineCam')
        ifd.add(0x010F, ASCII, 'Desqueeze')
        ifd.add(0x0110, ASCII, 'CineCam')
        return build_dng(ifd, [], endian)

    if name == 'existing_subifd':
        raw = _raw_ifd()
        raw.add(0xC61E, RATIONAL, [(1, 1), (1, 1)])    # DefaultScale already set
        return build_dng(_thumb_ifd(), [raw], endian)

    if name == 'existing_ifd0':
        thumb = _thumb_ifd()
        thumb.add(0xC61E, RATIONAL, [(1, 1), (1, 1)])
        return build_dng(thumb, [_raw_ifd()], endian)

    if name == 'two_subifds':
        return build_dng(_thumb_ifd(), [_raw_ifd(), _raw_ifd(32, 24, 1)], endian)

    raise ValueError('unknown variant %r' % name)


VARIANTS = ('standard', 'cinemadng', 'existing_subifd', 'existing_ifd0',
            'two_subifds')
