"""Tests for the photo and video engines, using the bundled binaries.

These run against the real ExifTool and the real FFmpeg that ship with the app,
so what is checked here is what the packaged product will do -- the only
difference on Windows is which build of those binaries is present.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PC_DIR = os.path.dirname(HERE)
sys.path.insert(0, PC_DIR)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(PC_DIR), 'ANDROID', 'tests'))

import engine as engine_module  # noqa: E402
import formats  # noqa: E402
import mp4_engine  # noqa: E402
import photo_engine  # noqa: E402
import toolbox  # noqa: E402
import video_engine  # noqa: E402

try:
    import tiff_builder
    HAVE_TIFF_BUILDER = True
except ImportError:
    HAVE_TIFF_BUILDER = False

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


def make_clip(path, size='1280x720', rate=24, duration=1, audio=True):
    arguments = ['-v', 'error', '-f', 'lavfi',
                 '-i', 'testsrc2=size=%s:rate=%d:duration=%d'
                 % (size, rate, duration)]
    if audio:
        arguments += ['-f', 'lavfi',
                      '-i', 'sine=frequency=440:duration=%d' % duration]
    arguments += ['-c:v', 'libx264', '-preset', 'ultrafast',
                  '-pix_fmt', 'yuv420p']
    if audio:
        arguments += ['-c:a', 'aac']
    arguments += ['-movflags', '+faststart', path, '-y']
    subprocess.run(toolbox.FFMPEG.command(*arguments), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@unittest.skipUnless(HAVE_TIFF_BUILDER, 'test fixtures unavailable')
class Vendoring(unittest.TestCase):
    """The app must never have to hunt for a tool."""

    def test_exiftool_is_bundled_and_runs(self):
        self.assertTrue(toolbox.EXIFTOOL.available,
                        'ExifTool is not in the vendor tree -- run '
                        'tools/fetch_binaries.py')
        self.assertTrue(toolbox.EXIFTOOL.version())

    def test_ffmpeg_is_bundled_and_runs(self):
        self.assertTrue(toolbox.FFMPEG.available)
        self.assertTrue(toolbox.FFMPEG.version())

    def test_tools_resolve_without_consulting_the_path(self):
        """Nothing here may depend on what the user happens to have installed."""
        toolbox.allow_system_tools(False)
        try:
            self.assertTrue(toolbox.EXIFTOOL.available)
            command = toolbox.EXIFTOOL.command('-ver')
            self.assertIn('vendor', os.path.join(*command).replace('\\', '/'))
        finally:
            toolbox.allow_system_tools(False)

    def test_report_names_every_tool(self):
        text = '\n'.join(toolbox.report())
        for key in ('ExifTool', 'FFmpeg', 'DNGLab'):
            self.assertIn(key, text)


@unittest.skipUnless(HAVE_TIFF_BUILDER and HAVE_PIL, 'fixtures unavailable')
class PhotoTagging(unittest.TestCase):

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix='desqueeze-photo-')
        self.dng = os.path.join(self.work, 'A001.dng')
        with open(self.dng, 'wb') as handle:
            handle.write(tiff_builder.make_variant('standard'))

    def test_tag_in_place_writes_a_readable_value(self):
        plan = formats.resolve_output('A001.dng', formats.MODE_TAG,
                                      formats.OUT_ORIGINAL)
        out = os.path.join(self.work, 'out.dng')
        photo_engine.desqueeze(self.dng, out, '1.33', '1.0', plan)
        self.assertEqual(photo_engine.read_default_scale(out), '1.33 1')

    def test_every_preset_round_trips(self):
        for label, scale_x, scale_y in engine_module.LENS_PRESETS:
            if scale_x is None:
                continue
            with self.subTest(preset=label):
                out = os.path.join(self.work, 'p.dng')
                plan = formats.resolve_output('A001.dng', formats.MODE_TAG,
                                              formats.OUT_ORIGINAL)
                photo_engine.desqueeze(self.dng, out, scale_x, scale_y, plan)
                written = photo_engine.read_default_scale(out)
                self.assertEqual(float(written.split()[0]), float(scale_x))

    def test_the_original_is_never_modified(self):
        with open(self.dng, 'rb') as handle:
            before = handle.read()
        plan = formats.resolve_output('A001.dng', formats.MODE_TAG,
                                      formats.OUT_ORIGINAL)
        photo_engine.desqueeze(self.dng, os.path.join(self.work, 'out.dng'),
                               '2.0', '1.0', plan)
        with open(self.dng, 'rb') as handle:
            self.assertEqual(handle.read(), before)

    def test_a_corrupt_file_fails_without_leaving_a_stub(self):
        broken = os.path.join(self.work, 'broken.dng')
        with open(broken, 'wb') as handle:
            handle.write(b'II*\x00nonsense')
        out = os.path.join(self.work, 'broken_out.dng')
        plan = formats.resolve_output('broken.dng', formats.MODE_TAG,
                                      formats.OUT_ORIGINAL)
        with self.assertRaises(photo_engine.PhotoError):
            photo_engine.desqueeze(broken, out, '1.33', '1.0', plan)
        self.assertFalse(os.path.exists(out),
                         'a failed file left an output behind')


@unittest.skipUnless(HAVE_PIL, 'Pillow unavailable')
class PhotoStretching(unittest.TestCase):

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix='desqueeze-stretch-')
        self.jpeg = os.path.join(self.work, 'still.jpg')
        Image.new('RGB', (1920, 1080), (200, 140, 40)).save(
            self.jpeg, 'JPEG', quality=92)

    def _run(self, name, source, mode, output, settings=None):
        plan = formats.resolve_output(name, mode, output)
        out = os.path.join(self.work, 'out' + plan.extension)
        photo_engine.desqueeze(source, out, '1.33', '1.0', plan, settings or {})
        return out

    def test_jpeg_is_stretched_horizontally(self):
        out = self._run('still.jpg', self.jpeg, formats.MODE_STRETCH,
                        formats.OUT_ORIGINAL)
        with Image.open(out) as image:
            self.assertEqual(image.size, (2554, 1080))

    def test_quality_setting_is_honoured(self):
        low = self._run('still.jpg', self.jpeg, formats.MODE_STRETCH,
                        formats.OUT_JPEG, {'jpeg_quality': 30})
        low_size = os.path.getsize(low)
        high = self._run('still.jpg', self.jpeg, formats.MODE_STRETCH,
                         formats.OUT_JPEG, {'jpeg_quality': 98})
        self.assertLess(low_size, os.path.getsize(high),
                        'quality 30 should be smaller than quality 98')

    def test_png_converted_to_jpeg(self):
        png = os.path.join(self.work, 'grab.png')
        Image.new('RGB', (1280, 720), (10, 20, 30)).save(png, 'PNG')
        out = self._run('grab.png', png, formats.MODE_STRETCH, formats.OUT_JPEG)
        with Image.open(out) as image:
            self.assertEqual(image.format, 'JPEG')
            self.assertEqual(image.size, (1702, 720))

    def test_exif_orientation_swaps_the_axes(self):
        exif = Image.Exif()
        exif[0x0112] = 6                       # displayed rotated a quarter turn
        rotated = os.path.join(self.work, 'portrait.jpg')
        Image.new('RGB', (1920, 1080), (90, 90, 90)).save(
            rotated, 'JPEG', exif=exif)
        out = self._run('portrait.jpg', rotated, formats.MODE_STRETCH,
                        formats.OUT_ORIGINAL)
        with Image.open(out) as image:
            self.assertEqual(image.size, (1920, 1436))


@unittest.skipUnless(toolbox.FFMPEG.available, 'FFmpeg unavailable')
class VideoMetadata(unittest.TestCase):

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix='desqueeze-video-')
        self.clip = os.path.join(self.work, 'B001.mp4')
        make_clip(self.clip)

    def test_pasp_is_written_and_readable(self):
        out = os.path.join(self.work, 'out.mp4')
        detail = video_engine.write_pasp(self.clip, out, '1.33', '1.0')
        self.assertIn('133:100', detail)
        info = video_engine.probe(out)
        self.assertEqual((info['width'], info['height']), (1280, 720))

    def test_frames_are_untouched(self):
        """The metadata route has to be genuinely lossless."""
        out = os.path.join(self.work, 'out.mp4')
        video_engine.write_pasp(self.clip, out, '1.33', '1.0')
        self.assertEqual(self._frame_hashes(self.clip),
                         self._frame_hashes(out))

    def _frame_hashes(self, path):
        result = subprocess.run(
            toolbox.FFMPEG.command('-v', 'error', '-i', path, '-map', '0:v',
                                   '-f', 'framemd5', '-'),
            check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        text = result.stdout.decode('utf-8', 'replace')
        return [line for line in text.splitlines() if not line.startswith('#')]

    def test_a_corrupt_clip_leaves_no_output(self):
        broken = os.path.join(self.work, 'broken.mp4')
        with open(broken, 'wb') as handle:
            handle.write(b'\x00\x00\x00\x18ftypnope' + b'\x00' * 32)
        out = os.path.join(self.work, 'broken_out.mp4')
        with self.assertRaises(video_engine.VideoError):
            video_engine.write_pasp(broken, out, '1.33', '1.0')
        self.assertFalse(os.path.exists(out))


@unittest.skipUnless(toolbox.FFMPEG.available, 'FFmpeg unavailable')
class VideoEncoding(unittest.TestCase):

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix='desqueeze-encode-')
        self.clip = os.path.join(self.work, 'B001.mp4')
        make_clip(self.clip, size='640x360', rate=24, duration=1)

    def test_output_is_stretched_with_square_pixels(self):
        """Stretched pixels plus a pasp of 1.33 would stretch twice."""
        out = os.path.join(self.work, 'out.mp4')
        video_engine.transcode(self.clip, out, '1.33', '1.0',
                               {'codec': 'hevc', 'bitrate': 2_000_000,
                                'preset': 'ultrafast'})
        info = video_engine.probe(out)
        # 640 * 1.33 = 851.2, rounded to the nearest even number the encoder
        # will accept
        self.assertEqual((info['width'], info['height']), (852, 360))
        self.assertEqual(info['codec'], 'hevc')
        with open(out, 'rb') as handle:
            aspect = mp4_engine.read_pixel_aspect(handle.read())
        # FFmpeg records the square pixels setsar=1 asked for, which is correct
        # and explicit.  What must never appear is the squeeze ratio again --
        # stretched pixels plus a 133:100 pasp would stretch the picture twice.
        for _codec, hspacing, vspacing in aspect:
            self.assertEqual(hspacing, vspacing,
                             'output claims non-square pixels (%d:%d) on top of '
                             'already-stretched ones' % (hspacing, vspacing))

    def test_audio_is_carried_across(self):
        out = os.path.join(self.work, 'out.mp4')
        video_engine.transcode(self.clip, out, '1.33', '1.0',
                               {'codec': 'h264', 'bitrate': 2_000_000,
                                'preset': 'ultrafast', 'keep_audio': True})
        self.assertTrue(video_engine.probe(out)['has_audio'])

    def test_audio_can_be_dropped(self):
        out = os.path.join(self.work, 'out.mp4')
        video_engine.transcode(self.clip, out, '1.33', '1.0',
                               {'codec': 'h264', 'bitrate': 2_000_000,
                                'preset': 'ultrafast', 'keep_audio': False})
        self.assertFalse(video_engine.probe(out)['has_audio'])

    def test_bitrate_choice_changes_the_file_size(self):
        sizes = []
        for bitrate in (500_000, 8_000_000):
            out = os.path.join(self.work, 'rate_%d.mp4' % bitrate)
            video_engine.transcode(self.clip, out, '1.33', '1.0',
                                   {'codec': 'h264', 'bitrate': bitrate,
                                    'mode': 'cbr', 'preset': 'ultrafast'})
            sizes.append(os.path.getsize(out))
        self.assertLess(sizes[0], sizes[1],
                        'a higher bitrate should produce a larger file')

    def test_dimensions_are_even(self):
        """Odd dimensions are rejected outright by yuv420p encoders."""
        for width, height, scale in ((1081, 721, '1.33'), (999, 501, '1.5')):
            with self.subTest(width=width):
                out_w, out_h = video_engine.desqueezed_size(
                    width, height, 0, scale, '1.0')
                self.assertEqual(out_w % 2, 0)
                self.assertEqual(out_h % 2, 0)

    def test_rotation_swaps_the_axes(self):
        self.assertEqual(video_engine.desqueezed_size(1920, 1080, 90,
                                                      '1.33', '1.0'),
                         (1920, 1436))

    def test_arguments_declare_square_pixels(self):
        info = {'frame_rate': 30, 'has_audio': True}
        arguments = video_engine.build_ffmpeg_arguments(
            'in.mp4', 'out.mp4', 2554, 1080,
            dict(video_engine.DEFAULT_SETTINGS, bitrate=20_000_000), info)
        joined = ' '.join(arguments)
        self.assertIn('setsar=1', joined)
        self.assertIn('scale=2554:1080', joined)
        self.assertIn('hvc1', joined)          # QuickTime will not play hev1

    def test_cbr_pins_the_ceiling_as_well_as_the_target(self):
        info = {'frame_rate': 30, 'has_audio': False}
        arguments = video_engine.build_ffmpeg_arguments(
            'in.mp4', 'out.mp4', 1280, 720,
            dict(video_engine.DEFAULT_SETTINGS, mode='cbr', bitrate=5_000_000),
            info)
        self.assertIn('-minrate', arguments)
        self.assertIn('-maxrate', arguments)


class OutputNaming(unittest.TestCase):

    def test_reading_and_writing_the_same_file_is_avoided(self):
        """Pointing the output at the source folder must not eat the original."""
        work = tempfile.mkdtemp(prefix='desqueeze-name-')
        source = os.path.join(work, 'A001.dng')
        with open(source, 'wb') as handle:
            handle.write(b'x')
        chosen = engine_module._unique(source, source)
        self.assertNotEqual(os.path.abspath(chosen), os.path.abspath(source))
        self.assertTrue(os.path.basename(chosen).startswith('A001_desqueezed'))

    def test_a_different_destination_is_left_alone(self):
        self.assertEqual(engine_module._unique('/out/A001.dng', '/in/A001.dng'),
                         '/out/A001.dng')


if __name__ == '__main__':
    unittest.main()
