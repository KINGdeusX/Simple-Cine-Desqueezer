"""Tests for the rules that decide what happens to each file.

``formats.resolve_output()`` is the single place where mode and output format
turn into a concrete action, so it is worth pinning down hard: the combinations
that are impossible must be refused rather than quietly doing something else,
and the ones that are merely pointless (tagging a JPEG) must be redirected to
what the user actually meant.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import formats  # noqa: E402


class Routing(unittest.TestCase):

    def test_every_advertised_raw_format_is_recognised(self):
        for maker, extensions in formats.RAW_BY_MAKER.items():
            for extension in extensions:
                with self.subTest(maker=maker, extension=extension):
                    self.assertEqual(formats.kind_of('IMG_0001' + extension),
                                     formats.KIND_RAW)

    def test_the_four_makers_asked_for_are_all_covered(self):
        for maker in ('Canon', 'Nikon', 'Sony', 'Fujifilm'):
            self.assertIn(maker, formats.RAW_BY_MAKER)

    def test_canon_cr3_and_fuji_raf_are_included(self):
        # Neither is TIFF-based, which is exactly why the PC build ships
        # ExifTool instead of reusing the phone's pure-Python writer.
        self.assertEqual(formats.kind_of('a.cr3'), formats.KIND_RAW)
        self.assertEqual(formats.kind_of('a.raf'), formats.KIND_RAW)

    def test_rendered_and_video_formats(self):
        for name in ('a.jpg', 'b.JPEG', 'c.png', 'd.tif'):
            self.assertEqual(formats.kind_of(name), formats.KIND_PIXEL, name)
        self.assertEqual(formats.kind_of('a.mp4'), formats.KIND_VIDEO)

    def test_video_is_limited_to_mp4_as_requested(self):
        self.assertEqual(formats.VIDEO_EXTENSIONS, ('.mp4',))
        for name in ('a.mov', 'b.mkv', 'c.avi', 'd.m4v'):
            self.assertIsNone(formats.kind_of(name), name)

    def test_unsupported_files(self):
        for name in ('a.txt', 'b.pdf', 'noextension', ''):
            self.assertIsNone(formats.kind_of(name), name)

    def test_tabs_partition_everything_supported(self):
        for extension in formats.SUPPORTED_EXTENSIONS:
            name = 'example' + extension
            with self.subTest(extension=extension):
                self.assertNotEqual(formats.accepts(name, 'photo'),
                                    formats.accepts(name, 'video'))


class TagMode(unittest.TestCase):

    def resolve(self, name, output=formats.OUT_ORIGINAL):
        return formats.resolve_output(name, formats.MODE_TAG, output)

    def test_raw_keeps_its_own_format(self):
        for name in ('a.cr3', 'b.nef', 'c.arw', 'd.raf', 'e.dng'):
            with self.subTest(name=name):
                plan = self.resolve(name)
                self.assertEqual(plan.action, formats.ACTION_TAG_IN_PLACE)
                self.assertEqual(plan.output_name(name), name)

    def test_raw_to_dng_converts_then_tags(self):
        plan = self.resolve('a.cr3', formats.OUT_DNG)
        self.assertEqual(plan.action, formats.ACTION_TAG_DNG)
        self.assertEqual(plan.output_name('a.cr3'), 'a.dng')

    def test_dng_to_dng_needs_no_conversion(self):
        plan = self.resolve('a.dng', formats.OUT_DNG)
        self.assertEqual(plan.action, formats.ACTION_TAG_IN_PLACE)

    def test_jpeg_is_stretched_because_tagging_one_does_nothing(self):
        plan = self.resolve('a.jpg')
        self.assertEqual(plan.action, formats.ACTION_STRETCH_PIXELS)
        self.assertIn('no effect', plan.note)

    def test_tiff_is_tagged_in_place(self):
        # Unlike JPEG, TIFF is where DefaultScale actually belongs.
        self.assertEqual(self.resolve('a.tif').action,
                         formats.ACTION_TAG_IN_PLACE)

    def test_jpeg_cannot_be_turned_into_dng(self):
        with self.assertRaises(ValueError):
            self.resolve('a.jpg', formats.OUT_DNG)

    def test_raw_to_jpeg_renders(self):
        plan = self.resolve('a.nef', formats.OUT_JPEG)
        self.assertEqual(plan.action, formats.ACTION_RENDER_JPEG)
        self.assertEqual(plan.output_name('a.nef'), 'a.jpg')


class StretchMode(unittest.TestCase):

    def resolve(self, name, output=formats.OUT_ORIGINAL):
        return formats.resolve_output(name, formats.MODE_STRETCH, output)

    def test_stretching_into_dng_is_refused(self):
        """A resampled picture is not raw data any more, so this must fail."""
        for name in ('a.cr3', 'b.dng', 'c.jpg'):
            with self.subTest(name=name):
                with self.assertRaises(ValueError) as caught:
                    self.resolve(name, formats.OUT_DNG)
                self.assertIn('DNG', str(caught.exception))

    def test_raw_is_developed_to_jpeg(self):
        for name in ('a.cr2', 'b.nef', 'c.arw', 'd.raf'):
            with self.subTest(name=name):
                plan = self.resolve(name)
                self.assertEqual(plan.action, formats.ACTION_RENDER_JPEG)
                self.assertEqual(plan.output_name(name),
                                 os.path.splitext(name)[0] + '.jpg')

    def test_rendered_images_keep_their_format(self):
        for name, expected in (('a.png', '.png'), ('b.tif', '.tif'),
                               ('c.jpg', '.jpg')):
            with self.subTest(name=name):
                plan = self.resolve(name)
                self.assertEqual(plan.action, formats.ACTION_STRETCH_PIXELS)
                self.assertEqual(plan.extension, expected)

    def test_png_can_be_asked_for_as_jpeg(self):
        plan = self.resolve('a.png', formats.OUT_JPEG)
        self.assertEqual(plan.action, formats.ACTION_RENDER_JPEG)
        self.assertEqual(plan.output_name('a.png'), 'a.jpg')


class Guards(unittest.TestCase):

    def test_video_is_rejected_by_the_photo_resolver(self):
        with self.assertRaises(ValueError):
            formats.resolve_output('a.mp4', formats.MODE_TAG,
                                   formats.OUT_ORIGINAL)

    def test_unknown_type_is_rejected(self):
        with self.assertRaises(ValueError):
            formats.resolve_output('a.txt', formats.MODE_TAG,
                                   formats.OUT_ORIGINAL)

    def test_jpeg_extension_is_configurable(self):
        plan = formats.resolve_output('a.nef', formats.MODE_STRETCH,
                                      formats.OUT_JPEG, '.jpeg')
        self.assertEqual(plan.output_name('a.nef'), 'a.jpeg')


if __name__ == '__main__':
    unittest.main()
