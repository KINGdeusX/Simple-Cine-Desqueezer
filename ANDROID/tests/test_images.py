"""Tests for the pixel-resize path (PNG / JPEG / TIFF).

Raw files carry a tag; these formats have their pixels physically stretched,
so what matters here is that the geometry is right, that the parts of the file
worth keeping survive the round trip, and that odd-but-real inputs (palette
PNGs, 16-bit TIFFs, rotated JPEGs, CMYK) do not blow up mid-batch.
"""

from __future__ import annotations

import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import formats  # noqa: E402
import image_engine  # noqa: E402

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


def encode(image, image_format, **kwargs):
    buffer = io.BytesIO()
    image.save(buffer, format=image_format, **kwargs)
    return buffer.getvalue()


def solid(size=(200, 100), mode='RGB', colour=(180, 90, 30)):
    return Image.new(mode, size, colour if mode == 'RGB' else 0)


@unittest.skipUnless(HAVE_PIL, 'Pillow not installed')
class Geometry(unittest.TestCase):

    def _resized(self, data, name, x='1.33', y='1.0'):
        out, description = image_engine.resize_desqueeze(data, name, x, y)
        return Image.open(io.BytesIO(out)), description

    def test_width_is_multiplied_by_the_squeeze_factor(self):
        data = encode(solid((200, 100)), 'PNG')
        image, description = self._resized(data, 'a.png')
        self.assertEqual(image.size, (266, 100))       # 200 * 1.33
        self.assertIn('200x100 -> 266x100', description)

    def test_height_factor_is_applied_too(self):
        data = encode(solid((200, 100)), 'PNG')
        image, _ = self._resized(data, 'a.png', '2.0', '1.5')
        self.assertEqual(image.size, (400, 150))

    def test_every_supported_pixel_format_round_trips(self):
        cases = [('a.png', 'PNG'), ('a.jpg', 'JPEG'), ('a.tif', 'TIFF')]
        for name, image_format in cases:
            with self.subTest(name=name):
                data = encode(solid((300, 200)), image_format)
                image, _ = self._resized(data, name)
                self.assertEqual(image.size, (399, 200))
                self.assertEqual(image.format, image_format)

    def test_unity_ratio_returns_the_original_bytes_untouched(self):
        """A 1.0 desqueeze must not silently re-encode and lose a generation."""
        data = encode(solid((200, 100)), 'JPEG', quality=80)
        out, description = image_engine.resize_desqueeze(data, 'a.jpg', '1.0', '1.0')
        self.assertEqual(out, data)
        self.assertIn('unchanged', description)

    def test_rotated_orientation_stretches_the_displayed_width(self):
        """A portrait photo is stored landscape; the swap must follow the tag."""
        image = solid((200, 100))
        exif = Image.Exif()
        exif[0x0112] = 6                      # rotate 90 CW when displayed
        data = encode(image, 'JPEG', exif=exif)

        resized, _ = self._resized(data, 'a.jpg')
        # displayed width is the stored height, so the stored height grows
        self.assertEqual(resized.size, (200, 133))

    def test_unrotated_orientation_stretches_stored_width(self):
        image = solid((200, 100))
        exif = Image.Exif()
        exif[0x0112] = 1
        data = encode(image, 'JPEG', exif=exif)
        resized, _ = self._resized(data, 'a.jpg')
        self.assertEqual(resized.size, (266, 100))


@unittest.skipUnless(HAVE_PIL, 'Pillow not installed')
class Preservation(unittest.TestCase):

    def test_jpeg_keeps_its_exif(self):
        exif = Image.Exif()
        exif[0x010F] = 'Desqueeze'            # Make
        data = encode(solid(), 'JPEG', exif=exif)
        out, _ = image_engine.resize_desqueeze(data, 'a.jpg', '1.33', '1.0')
        self.assertEqual(Image.open(io.BytesIO(out)).getexif().get(0x010F),
                         'Desqueeze')

    def test_icc_profile_survives(self):
        profile = b'\x00\x00\x02\x0cfake icc profile' + b'\x00' * 100
        data = encode(solid(), 'JPEG', icc_profile=profile)
        out, _ = image_engine.resize_desqueeze(data, 'a.jpg', '1.33', '1.0')
        self.assertEqual(Image.open(io.BytesIO(out)).info.get('icc_profile'),
                         profile)

    def test_png_transparency_survives(self):
        image = Image.new('RGBA', (120, 80), (255, 0, 0, 0))
        data = encode(image, 'PNG')
        out, _ = image_engine.resize_desqueeze(data, 'a.png', '2.0', '1.0')
        result = Image.open(io.BytesIO(out))
        self.assertEqual(result.size, (240, 80))
        self.assertIn(result.mode, ('RGBA', 'LA', 'P'))


@unittest.skipUnless(HAVE_PIL, 'Pillow not installed')
class AwkwardInputs(unittest.TestCase):
    """Modes that cannot be Lanczos-resampled directly, and bad input."""

    def test_palette_png(self):
        image = solid((160, 90)).convert('P')
        out, _ = image_engine.resize_desqueeze(encode(image, 'PNG'), 'a.png',
                                               '1.5', '1.0')
        self.assertEqual(Image.open(io.BytesIO(out)).size, (240, 90))

    def test_sixteen_bit_tiff(self):
        image = Image.new('I;16', (100, 50), 1000)
        out, _ = image_engine.resize_desqueeze(encode(image, 'TIFF'), 'a.tif',
                                               '2.0', '1.0')
        result = Image.open(io.BytesIO(out))
        self.assertEqual(result.size, (200, 50))

    def test_bilevel_image(self):
        image = Image.new('1', (100, 50), 1)
        out, _ = image_engine.resize_desqueeze(encode(image, 'PNG'), 'a.png',
                                               '2.0', '1.0')
        self.assertEqual(Image.open(io.BytesIO(out)).size, (200, 50))

    def test_cmyk_jpeg(self):
        image = Image.new('CMYK', (120, 60), (0, 0, 0, 0))
        out, _ = image_engine.resize_desqueeze(encode(image, 'JPEG'), 'a.jpg',
                                               '1.33', '1.0')
        self.assertEqual(Image.open(io.BytesIO(out)).size, (160, 60))

    def test_rgba_is_flattened_for_jpeg(self):
        """JPEG has no alpha channel; the encoder must not be handed one."""
        image = Image.new('RGBA', (100, 50), (10, 20, 30, 255))
        data = encode(image.convert('RGB'), 'JPEG')
        out, _ = image_engine.resize_desqueeze(data, 'a.jpg', '2.0', '1.0')
        self.assertEqual(Image.open(io.BytesIO(out)).mode, 'RGB')

    def test_garbage_is_reported_not_raised_as_something_odd(self):
        with self.assertRaises(image_engine.ImageError):
            image_engine.resize_desqueeze(b'not an image', 'a.png', '1.33', '1.0')

    def test_bad_ratio_is_rejected(self):
        data = encode(solid(), 'PNG')
        for x, y in (('0', '1.0'), ('-2', '1.0'), ('abc', '1.0')):
            with self.subTest(x=x):
                with self.assertRaises(image_engine.ImageError):
                    image_engine.resize_desqueeze(data, 'a.png', x, y)


class FormatRouting(unittest.TestCase):
    """The dispatch table is what decides tag-write vs pixel-resize."""

    def test_raw_formats_take_the_tag_path(self):
        for name in ('a.dng', 'b.ARW', 'c.nef', 'd.NRW', 'e.sr2'):
            self.assertEqual(formats.kind_of(name), formats.KIND_RAW, name)

    def test_rendered_formats_take_the_resize_path(self):
        for name in ('a.png', 'b.JPG', 'c.jpeg', 'd.tif', 'e.TIFF'):
            self.assertEqual(formats.kind_of(name), formats.KIND_PIXEL, name)

    def test_everything_else_is_unsupported(self):
        for name in ('a.txt', 'b.mp4', 'c.cr2', 'noextension', ''):
            self.assertIsNone(formats.kind_of(name), name)

    def test_raw_and_pixel_sets_do_not_overlap(self):
        self.assertFalse(set(formats.RAW_EXTENSIONS) & set(formats.PIXEL_EXTENSIONS))


if __name__ == '__main__':
    unittest.main()
