"""Tests for the MP4/MOV pasp writer.

The dangerous failure here is silent: a rewritten file that still opens, still
plays the first frame, and has a chunk offset table pointing a few bytes wrong
so the picture falls apart later.  So these tests do not merely check that a
pasp atom appeared -- after every rewrite they follow the chunk offsets in the
output and assert that the bytes they land on are the chunk that belongs there.
"""

from __future__ import annotations

import io
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mp4_builder  # noqa: E402
import mp4_engine  # noqa: E402


def desqueeze(data, scale_x='1.33', scale_y='1.0'):
    source = io.BytesIO(data)
    destination = io.BytesIO()
    info = mp4_engine.desqueeze_stream(source, destination, scale_x, scale_y,
                                       len(data))
    return destination.getvalue(), info


def read_chunk_offsets(data):
    """Pull the chunk offset table back out of a finished file."""
    offsets = []

    def walk(start, end):
        for box in mp4_engine._iter_boxes(data, start, end):
            if box.type in (b'moov', b'trak', b'mdia', b'minf', b'stbl'):
                walk(box.payload_start, box.end)
            elif box.type in (b'stco', b'co64'):
                wide = box.type == b'co64'
                base = box.payload_start + 4
                count = struct.unpack_from('>I', data, base)[0]
                position = base + 4
                for _ in range(count):
                    fmt = '>Q' if wide else '>I'
                    offsets.append(struct.unpack_from(fmt, data, position)[0])
                    position += 8 if wide else 4

    walk(0, len(data))
    return offsets


class ChunkOffsetIntegrity(unittest.TestCase):
    """The whole point: the file must still describe itself correctly."""

    def _assert_chunks_intact(self, data, chunk_count=3, chunk_size=1024):
        offsets = read_chunk_offsets(data)
        self.assertTrue(offsets, 'no chunk offset table found')
        # a video trak, and possibly an audio trak, each list the same chunks
        for position, offset in enumerate(offsets):
            index = position % chunk_count
            expected = mp4_builder.chunk_data(index, chunk_size)
            self.assertLessEqual(offset + chunk_size, len(data),
                                 'chunk offset points past the end of the file')
            self.assertEqual(data[offset:offset + chunk_size], expected,
                             'chunk %d offset points at the wrong bytes' % index)

    def test_every_variant_keeps_its_chunks_addressable(self):
        for name in mp4_builder.VARIANTS:
            with self.subTest(variant=name):
                data, _ = mp4_builder.make_variant(name)
                out, _info = desqueeze(data)
                self._assert_chunks_intact(out)

    def test_offsets_move_only_when_moov_precedes_the_data(self):
        """moov-first grows and shifts mdat; moov-last must not move anything."""
        data, before = mp4_builder.build_mp4(moov_first=False)
        out, _ = desqueeze(data)
        self.assertEqual(read_chunk_offsets(out)[:len(before)], before,
                         'offsets changed although mdat came first')

        data, before = mp4_builder.build_mp4(moov_first=True)
        out, _ = desqueeze(data)
        after = read_chunk_offsets(out)[:len(before)]
        self.assertEqual(after, [value + mp4_engine.PASP_SIZE for value in before],
                         'offsets did not follow the growth of moov')

    def test_replacing_an_existing_pasp_does_not_resize_the_file(self):
        data, before = mp4_builder.make_variant('existing_pasp')
        out, _ = desqueeze(data)
        self.assertEqual(len(out), len(data))
        self.assertEqual(read_chunk_offsets(out)[:len(before)], before)


class PixelAspect(unittest.TestCase):

    def test_ratio_is_reduced_to_sensible_integers(self):
        cases = {'1.33': (133, 100), '2.0': (2, 1), '1.5': (3, 2),
                 '1.25': (5, 4), '1.8': (9, 5), '2.39': (239, 100),
                 '1.6': (8, 5), '1.79': (179, 100), '1.0': (1, 1)}
        for value, expected in cases.items():
            self.assertEqual(mp4_engine.pixel_aspect(value, '1.0'), expected, value)

    def test_vertical_squeeze_is_accounted_for(self):
        self.assertEqual(mp4_engine.pixel_aspect('2.0', '2.0'), (1, 1))
        self.assertEqual(mp4_engine.pixel_aspect('1.0', '2.0'), (1, 2))

    def test_bad_ratios_are_rejected(self):
        for x, y in (('0', '1.0'), ('-1', '1.0'), ('abc', '1.0'), ('1.0', '0')):
            with self.subTest(x=x, y=y):
                with self.assertRaises(mp4_engine.Mp4Error):
                    mp4_engine.pixel_aspect(x, y)

    def test_written_pasp_is_readable(self):
        data, _ = mp4_builder.make_variant('moov_first')
        out, info = desqueeze(data, '1.33', '1.0')
        self.assertEqual(mp4_engine.read_pixel_aspect(out), [('avc1', 133, 100)])
        self.assertEqual(info['pasp'], (133, 100))
        self.assertEqual(info['tracks'], 1)

    def test_existing_pasp_is_replaced_not_duplicated(self):
        data, _ = mp4_builder.make_variant('existing_pasp')
        out, _ = desqueeze(data, '2.0', '1.0')
        self.assertEqual(mp4_engine.read_pixel_aspect(out), [('avc1', 2, 1)])

    def test_pasp_survives_when_other_boxes_precede_it(self):
        data, _ = mp4_builder.make_variant('pasp_not_last')
        out, _ = desqueeze(data)
        self.assertEqual(mp4_engine.read_pixel_aspect(out), [('avc1', 133, 100)])
        self.assertIn(b'colr', out, 'unrelated sample-entry boxes were dropped')
        self.assertIn(b'avcC', out, 'the codec configuration was dropped')
        self.assertIn(b'btrt', out)

    def test_hevc_track_is_handled(self):
        data, _ = mp4_builder.make_variant('hevc')
        out, info = desqueeze(data, '1.5', '1.0')
        self.assertEqual(mp4_engine.read_pixel_aspect(out), [('hvc1', 3, 2)])
        self.assertEqual((info['width'], info['height']), (3840, 2160))


class DisplaySize(unittest.TestCase):
    """tkhd carries the display size, which players use to size the picture."""

    def test_display_width_is_stretched(self):
        data, _ = mp4_builder.build_mp4(width=1920, height=1080)
        out, _ = desqueeze(data, '1.33', '1.0')
        sizes = mp4_engine.read_track_display_size(out)
        self.assertEqual(len(sizes), 1)
        width, height = sizes[0]
        self.assertAlmostEqual(width, 1920 * 133 / 100.0, places=2)
        self.assertAlmostEqual(height, 1080.0, places=2)

    def test_display_size_written_for_tkhd_version_1(self):
        data, _ = mp4_builder.make_variant('tkhd_v1')
        out, _ = desqueeze(data, '2.0', '1.0')
        width, height = mp4_engine.read_track_display_size(out)[0]
        self.assertAlmostEqual(width, 3840.0, places=2)
        self.assertAlmostEqual(height, 1080.0, places=2)

    def test_audio_track_is_left_alone(self):
        data, _ = mp4_builder.make_variant('with_audio')
        out, info = desqueeze(data)
        self.assertEqual(info['tracks'], 1, 'audio track should not be counted')
        self.assertEqual(len(mp4_engine.read_pixel_aspect(out)), 1)
        self.assertIn(b'mp4a', out, 'the audio track was lost')
        self.assertIn(b'smhd', out)


class Rotation(unittest.TestCase):
    """A clip recorded sideways must still get wider on screen, not taller."""

    def test_upright_clip_stretches_the_coded_width(self):
        data, _ = mp4_builder.make_variant('moov_first')
        out, info = desqueeze(data, '1.33', '1.0')
        self.assertEqual(info['rotation'], 0)
        self.assertEqual(mp4_engine.read_pixel_aspect(out), [('avc1', 133, 100)])

    def test_quarter_turned_clip_gets_the_inverted_ratio(self):
        for variant in ('rotated_90',):
            with self.subTest(variant=variant):
                data, _ = mp4_builder.make_variant(variant)
                out, info = desqueeze(data, '1.33', '1.0')
                self.assertEqual(info['rotation'], 90)
                # coded height is the displayed width, so vSpacing carries it
                self.assertEqual(mp4_engine.read_pixel_aspect(out),
                                 [('avc1', 100, 133)])

    def test_two_seventy_is_treated_like_ninety(self):
        data, _ = mp4_builder.build_mp4(rotation=270)
        out, info = desqueeze(data, '2.0', '1.0')
        self.assertEqual(info['rotation'], 270)
        self.assertEqual(mp4_engine.read_pixel_aspect(out), [('avc1', 1, 2)])

    def test_upside_down_clip_behaves_like_upright(self):
        data, _ = mp4_builder.make_variant('rotated_180')
        out, info = desqueeze(data, '1.33', '1.0')
        self.assertEqual(info['rotation'], 180)
        self.assertEqual(mp4_engine.read_pixel_aspect(out), [('avc1', 133, 100)])

    def test_rotation_matrix_is_preserved(self):
        data, _ = mp4_builder.make_variant('rotated_90')
        out, _ = desqueeze(data)
        self.assertEqual(mp4_engine.read_pixel_aspect(out)[0][1:], (100, 133))
        # re-reading the output must still report the same rotation
        _, info = desqueeze(out, '1.0', '1.0')
        self.assertEqual(info['rotation'], 90)


class Robustness(unittest.TestCase):

    def test_non_mp4_is_rejected(self):
        with self.assertRaises(mp4_engine.Mp4Error):
            desqueeze(b'this is not a video file at all, not even close')

    def test_truncated_file_is_rejected(self):
        data, _ = mp4_builder.make_variant('moov_first')
        with self.assertRaises(mp4_engine.Mp4Error):
            desqueeze(data[:len(data) // 2])

    def test_file_without_video_track_is_rejected(self):
        audio_only = (mp4_builder.ftyp()
                      + mp4_builder.box(b'moov', mp4_builder.mvhd()
                                        + mp4_builder.audio_trak([0]))
                      + mp4_builder.box(b'mdat', b'\x00' * 64))
        with self.assertRaises(mp4_engine.Mp4Error):
            desqueeze(audio_only)

    def test_everything_outside_moov_is_copied_verbatim(self):
        data, _ = mp4_builder.build_mp4(moov_first=False)
        out, _ = desqueeze(data)
        # mdat comes first here, so it must be byte-identical and in place
        marker = mp4_builder.chunk_data(0)
        self.assertEqual(out[:len(data) - _moov_length(data)],
                         data[:len(data) - _moov_length(data)])
        self.assertIn(marker, out)

    def test_cancelling_midway_produces_no_result(self):
        data, _ = mp4_builder.build_mp4(chunks=8, chunk_size=64 * 1024)
        source, destination = io.BytesIO(data), io.BytesIO()
        calls = {'n': 0}

        def should_cancel():
            calls['n'] += 1
            return calls['n'] > 2

        result = mp4_engine.desqueeze_stream(source, destination, '1.33', '1.0',
                                             len(data), should_cancel=should_cancel,
                                             chunk=4096)
        self.assertIsNone(result)

    def test_progress_is_reported_and_monotonic(self):
        data, _ = mp4_builder.build_mp4(chunks=8, chunk_size=64 * 1024)
        seen = []
        mp4_engine.desqueeze_stream(io.BytesIO(data), io.BytesIO(), '1.33', '1.0',
                                    len(data), on_progress=seen.append, chunk=8192)
        self.assertTrue(seen)
        self.assertEqual(seen, sorted(seen))
        self.assertLessEqual(max(seen), 99)

    def test_large_file_is_never_loaded_whole(self):
        """Only moov may be read into memory, however large the clip is."""
        data, _ = mp4_builder.build_mp4(moov_first=True, chunks=4,
                                        chunk_size=256 * 1024)
        source = _CountingReader(data)
        mp4_engine.desqueeze_stream(source, io.BytesIO(), '1.33', '1.0',
                                    len(data), chunk=64 * 1024)
        self.assertLessEqual(source.largest_read, 64 * 1024 + 16,
                             'a read larger than the copy block size happened')

    def test_is_probably_mp4(self):
        data, _ = mp4_builder.make_variant('moov_first')
        self.assertTrue(mp4_engine.is_probably_mp4(data[:32]))
        self.assertFalse(mp4_engine.is_probably_mp4(b'\x89PNG\r\n\x1a\n' + b'\x00' * 16))
        self.assertFalse(mp4_engine.is_probably_mp4(b'short'))


def _find_ffmpeg():
    """Real ffmpeg, for checking our output against a production demuxer.

    Development-only: it is never packaged into the APK.  The tests skip
    themselves when it is absent so the suite still runs on a bare machine.
    """
    import shutil
    base = os.path.expanduser('~/.desqueeze-toolchain/ffmpeg')
    ffmpeg = os.path.join(base, 'ffmpeg')
    ffprobe = os.path.join(base, 'ffprobe')
    if os.access(ffmpeg, os.X_OK) and os.access(ffprobe, os.X_OK):
        return ffmpeg, ffprobe
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    return (ffmpeg, ffprobe) if ffmpeg and ffprobe else (None, None)


FFMPEG, FFPROBE = _find_ffmpeg()


@unittest.skipIf(FFPROBE is None, 'ffmpeg not available')
class RealFileParity(unittest.TestCase):
    """Rewrite genuine H.264 files and let ffmpeg judge the result."""

    import subprocess as _subprocess
    import tempfile as _tempfile

    def _encode(self, path, size='1920x1080', rate=30, duration=1, extra=()):
        command = [FFMPEG, '-v', 'error', '-f', 'lavfi',
                   '-i', 'testsrc2=size=%s:rate=%d:duration=%d'
                   % (size, rate, duration),
                   '-f', 'lavfi', '-i', 'sine=frequency=440:duration=%d' % duration,
                   '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                   '-c:a', 'aac'] + list(extra) + [path, '-y']
        self._subprocess.run(command, check=True, capture_output=True)

    def _probe(self, path, entries, stream='v:0'):
        out = self._subprocess.run(
            [FFPROBE, '-v', 'error', '-select_streams', stream,
             '-show_entries', 'stream=' + entries, '-of', 'default=nw=1', path],
            check=True, capture_output=True, text=True).stdout
        return dict(line.split('=', 1) for line in out.strip().splitlines() if '=' in line)

    def _desqueeze_file(self, source, destination, x='1.33', y='1.0'):
        with open(source, 'rb') as src, open(destination, 'wb') as dst:
            return mp4_engine.desqueeze_stream(src, dst, x, y,
                                               os.path.getsize(source))

    def _frame_hashes(self, path):
        out = self._subprocess.run(
            [FFMPEG, '-v', 'error', '-i', path, '-map', '0:v', '-f', 'framemd5', '-'],
            check=True, capture_output=True, text=True).stdout
        return [line for line in out.splitlines() if not line.startswith('#')]

    def test_real_h264_gets_the_right_aspect_and_stays_decodable(self):
        for label, extra in (('faststart', ('-movflags', '+faststart')),
                             ('moov last', ())):
            with self.subTest(layout=label):
                with self._tempfile.TemporaryDirectory() as work:
                    source = os.path.join(work, 'in.mp4')
                    result = os.path.join(work, 'out.mp4')
                    self._encode(source, extra=extra)
                    info = self._desqueeze_file(source, result)
                    self.assertEqual(info['pasp'], (133, 100))

                    probed = self._probe(result, 'sample_aspect_ratio,'
                                                 'display_aspect_ratio,width,height')
                    self.assertEqual(probed['sample_aspect_ratio'], '133:100')
                    self.assertEqual(probed['display_aspect_ratio'], '532:225')
                    self.assertEqual((probed['width'], probed['height']),
                                     ('1920', '1080'))

                    # -xerror turns any decode problem into a non-zero exit
                    self._subprocess.run(
                        [FFMPEG, '-v', 'error', '-xerror', '-i', result,
                         '-f', 'null', '-'], check=True, capture_output=True)

    def test_pixels_are_untouched(self):
        """The metadata path must be genuinely lossless, frame for frame."""
        with self._tempfile.TemporaryDirectory() as work:
            source = os.path.join(work, 'in.mp4')
            result = os.path.join(work, 'out.mp4')
            self._encode(source, duration=2)
            self._desqueeze_file(source, result)
            before, after = self._frame_hashes(source), self._frame_hashes(result)
            self.assertTrue(before)
            self.assertEqual(before, after, 'video frames were altered')

    def test_audio_track_survives_untouched(self):
        with self._tempfile.TemporaryDirectory() as work:
            source = os.path.join(work, 'in.mp4')
            result = os.path.join(work, 'out.mp4')
            self._encode(source)
            self._desqueeze_file(source, result)
            self.assertEqual(self._probe(result, 'codec_name,channels', 'a:0'),
                             self._probe(source, 'codec_name,channels', 'a:0'))

    def test_every_lens_preset_round_trips(self):
        import engine as engine_module
        with self._tempfile.TemporaryDirectory() as work:
            source = os.path.join(work, 'in.mp4')
            self._encode(source, size='1280x720', rate=24)
            for label, scale_x, scale_y in engine_module.LENS_PRESETS:
                if scale_x is None:
                    continue
                with self.subTest(preset=label):
                    result = os.path.join(work, 'out.mp4')
                    self._desqueeze_file(source, result, scale_x, scale_y)
                    expected = mp4_engine.pixel_aspect(scale_x, scale_y)
                    probed = self._probe(result, 'sample_aspect_ratio')
                    self.assertEqual(probed['sample_aspect_ratio'],
                                     '%d:%d' % expected)


class _CountingReader(io.BytesIO):
    """A stream that remembers the largest single read it served."""

    def __init__(self, data):
        super().__init__(data)
        self.largest_read = 0

    def read(self, size=-1):
        block = super().read(size)
        self.largest_read = max(self.largest_read, len(block))
        return block


def _moov_length(data):
    for box in mp4_engine._iter_boxes(data, 0, len(data)):
        if box.type == b'moov':
            return box.end - box.start
    return 0


if __name__ == '__main__':
    unittest.main()
