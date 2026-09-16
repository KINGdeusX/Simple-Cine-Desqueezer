"""Build small but structurally valid MP4/MOV files for the video tests.

Real camera clips are far too large to check into a repo, and the things that
can go wrong when rewriting one are structural: does the chunk offset table
still point at the right bytes after ``moov`` changes size?  Does a track
without a video handler get left alone?  So the tests synthesise files covering
each of those shapes, with recognisable data in every chunk so that a broken
offset table can be detected rather than merely suspected.
"""

from __future__ import annotations

import struct

IDENTITY_MATRIX = struct.pack('>9i', 0x00010000, 0, 0,
                              0, 0x00010000, 0,
                              0, 0, 0x40000000)

ONE = 0x00010000


def rotation_matrix(degrees):
    """The tkhd display matrix for a quarter-turn rotation."""
    table = {
        0: (ONE, 0, 0, ONE),
        90: (0, ONE, -ONE, 0),
        180: (-ONE, 0, 0, -ONE),
        270: (0, -ONE, ONE, 0),
    }
    a, b, c, d = table[degrees % 360]
    return struct.pack('>9i', a, b, 0, c, d, 0, 0, 0, 0x40000000)


def box(type_, payload):
    return struct.pack('>I4s', len(payload) + 8, type_) + payload


def full_box(type_, version, flags, payload):
    return box(type_, struct.pack('>B3s', version, flags.to_bytes(3, 'big')) + payload)


def ftyp():
    return box(b'ftyp', b'isom' + struct.pack('>I', 512) + b'isomiso2avc1mp41')


def mvhd(timescale=1000, duration=2000):
    payload = struct.pack('>IIII', 0, 0, timescale, duration)
    payload += struct.pack('>IHH', 0x00010000, 0x0100, 0)    # rate, volume
    payload += b'\x00' * 8
    payload += IDENTITY_MATRIX
    payload += b'\x00' * 24
    payload += struct.pack('>I', 3)                           # next_track_ID
    return full_box(b'mvhd', 0, 0, payload)


def tkhd(track_id, width, height, duration=2000, version=0, rotation=0):
    if version == 1:
        payload = struct.pack('>QQIIQ', 0, 0, track_id, 0, duration)
    else:
        payload = struct.pack('>IIIII', 0, 0, track_id, 0, duration)
    payload += b'\x00' * 8                                    # reserved
    payload += struct.pack('>hhhh', 0, 0, 0, 0)               # layer, group...
    payload += rotation_matrix(rotation)
    payload += struct.pack('>II', width << 16, height << 16)  # 16.16 fixed
    return full_box(b'tkhd', version, 3, payload)


def mdhd(timescale=30000, duration=60000):
    payload = struct.pack('>IIII', 0, 0, timescale, duration)
    payload += struct.pack('>HH', 0x55C4, 0)                  # 'und' language
    return full_box(b'mdhd', 0, 0, payload)


def hdlr(handler):
    payload = struct.pack('>I4s', 0, handler) + b'\x00' * 12 + b'Desqueeze\x00'
    return full_box(b'hdlr', 0, 0, payload)


def dinf():
    url = full_box(b'url ', 0, 1, b'')
    dref = full_box(b'dref', 0, 0, struct.pack('>I', 1) + url)
    return box(b'dinf', dref)


def avcc():
    """A minimal but plausible AVC decoder configuration record."""
    return box(b'avcC', bytes([1, 0x64, 0x00, 0x1F, 0xFF, 0xE1, 0x00, 0x04,
                               0x67, 0x64, 0x00, 0x1F, 0x01, 0x00, 0x04,
                               0x68, 0xEE, 0x3C, 0x80]))


def visual_sample_entry(type_=b'avc1', width=1920, height=1080, pasp=None,
                        extra_first=False):
    payload = b'\x00' * 6 + struct.pack('>H', 1)              # data_reference_index
    payload += struct.pack('>HH', 0, 0)                       # pre_defined, reserved
    payload += b'\x00' * 12                                   # pre_defined[3]
    payload += struct.pack('>HH', width, height)
    payload += struct.pack('>II', 0x00480000, 0x00480000)     # 72 dpi
    payload += struct.pack('>I', 0)
    payload += struct.pack('>H', 1)                           # frame_count
    payload += b'\x0bDesqueezeAV' + b'\x00' * (32 - 12)       # compressorname
    payload += struct.pack('>Hh', 0x0018, -1)                 # depth, pre_defined

    children = b''
    if extra_first:
        children += box(b'colr', b'nclx' + struct.pack('>HHB', 1, 1, 1) + b'\x00')
    children += avcc()
    if pasp is not None:
        children += box(b'pasp', struct.pack('>II', *pasp))
    children += box(b'btrt', struct.pack('>III', 0, 2000000, 2000000))
    return box(type_, payload + children)


def audio_sample_entry():
    payload = b'\x00' * 6 + struct.pack('>H', 1)
    payload += b'\x00' * 8
    payload += struct.pack('>HH', 2, 16)                      # channels, sample size
    payload += struct.pack('>HH', 0, 0)
    payload += struct.pack('>I', 48000 << 16)
    return box(b'mp4a', payload)


def stbl(sample_entry, chunk_offsets, use_co64=False, sample_count=4):
    stsd = full_box(b'stsd', 0, 0, struct.pack('>I', 1) + sample_entry)
    stts = full_box(b'stts', 0, 0,
                    struct.pack('>III', 1, sample_count, 1000))
    stsc = full_box(b'stsc', 0, 0,
                    struct.pack('>IIII', 1, 1, 1, 1))
    stsz = full_box(b'stsz', 0, 0,
                    struct.pack('>II', 1024, sample_count))
    if use_co64:
        stco = full_box(b'co64', 0, 0,
                        struct.pack('>I', len(chunk_offsets))
                        + b''.join(struct.pack('>Q', o) for o in chunk_offsets))
    else:
        stco = full_box(b'stco', 0, 0,
                        struct.pack('>I', len(chunk_offsets))
                        + b''.join(struct.pack('>I', o) for o in chunk_offsets))
    return box(b'stbl', stsd + stts + stsc + stsz + stco)


def video_trak(chunk_offsets, width=1920, height=1080, pasp=None,
               use_co64=False, codec=b'avc1', tkhd_version=0,
               display=None, extra_first=False, rotation=0):
    entry = visual_sample_entry(codec, width, height, pasp, extra_first)
    minf = box(b'minf', full_box(b'vmhd', 0, 1, struct.pack('>HHHH', 0, 0, 0, 0))
               + dinf() + stbl(entry, chunk_offsets, use_co64))
    mdia = box(b'mdia', mdhd() + hdlr(b'vide') + minf)
    display = display or (width, height)
    return box(b'trak', tkhd(1, display[0], display[1], version=tkhd_version,
                             rotation=rotation) + mdia)


def audio_trak(chunk_offsets):
    minf = box(b'minf', full_box(b'smhd', 0, 0, struct.pack('>HH', 0, 0))
               + dinf() + stbl(audio_sample_entry(), chunk_offsets))
    mdia = box(b'mdia', mdhd() + hdlr(b'soun') + minf)
    return box(b'trak', tkhd(2, 0, 0) + mdia)


def chunk_data(index, size=1024):
    """Recognisable payload so a mis-pointed chunk offset is detectable."""
    marker = b'CHUNK%03d:' % index
    return marker + bytes((index * 7 + i) % 251 for i in range(size - len(marker)))


def build_mp4(moov_first=True, pasp=None, use_co64=False, with_audio=False,
              width=1920, height=1080, codec=b'avc1', chunks=3,
              tkhd_version=0, display=None, extra_first=False,
              chunk_size=1024, rotation=0):
    """Assemble a complete file; returns ``(data, chunk_offsets)``.

    The layout is resolved in two passes because the chunk offsets live inside
    ``moov`` while pointing into ``mdat``, so the size of one depends on the
    position of the other.
    """
    def assemble(offsets):
        traks = video_trak(offsets, width, height, pasp, use_co64, codec,
                           tkhd_version, display, extra_first, rotation)
        if with_audio:
            traks += audio_trak(offsets)
        moov = box(b'moov', mvhd() + traks)
        payload = b''.join(chunk_data(i, chunk_size) for i in range(chunks))
        mdat = box(b'mdat', payload)
        return moov, mdat

    # first pass with placeholder offsets, purely to learn how big moov is
    moov, mdat = assemble([0] * chunks)
    head = len(ftyp())
    if moov_first:
        mdat_payload_start = head + len(moov) + 8
    else:
        mdat_payload_start = head + 8
    offsets = [mdat_payload_start + i * chunk_size for i in range(chunks)]

    moov, mdat = assemble(offsets)
    data = ftyp() + (moov + mdat if moov_first else mdat + moov)
    return data, offsets


VARIANTS = ('moov_first', 'moov_last', 'existing_pasp', 'co64', 'with_audio',
            'hevc', 'tkhd_v1', 'pasp_not_last', 'rotated_90', 'rotated_180')


def make_variant(name):
    """Return ``(data, chunk_offsets)`` for one named shape."""
    if name == 'moov_first':
        return build_mp4(moov_first=True)
    if name == 'moov_last':
        return build_mp4(moov_first=False)
    if name == 'existing_pasp':
        return build_mp4(moov_first=True, pasp=(1, 1))
    if name == 'co64':
        return build_mp4(moov_first=True, use_co64=True)
    if name == 'with_audio':
        return build_mp4(moov_first=False, with_audio=True)
    if name == 'hevc':
        return build_mp4(moov_first=True, codec=b'hvc1', width=3840, height=2160)
    if name == 'tkhd_v1':
        return build_mp4(moov_first=True, tkhd_version=1)
    if name == 'pasp_not_last':
        return build_mp4(moov_first=True, extra_first=True)
    if name == 'rotated_90':
        return build_mp4(moov_first=True, rotation=90)
    if name == 'rotated_180':
        return build_mp4(moov_first=True, rotation=180)
    raise ValueError('unknown variant %r' % name)
