"""Byte-parity tests: our pure-Python engine vs. real ExifTool.

This is the Phase 8 acceptance check from Instruction.md, automated.  For every
lens preset and every test-file shape we let real ExifTool write DefaultScale,
write the same tag ourselves, and require that ExifTool reports *identical*
values and locations for both files.

Run with:  python -m unittest discover -s ANDROID/tests
Set EXIFTOOL=/path/to/exiftool to use a specific copy; otherwise the bundled
toolchain copy is used and the tests skip if none is found.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dng_engine  # noqa: E402
import tiff_builder  # noqa: E402

PRESET_VALUES = ['1.33', '1.5', '1.6', '1.8', '2.0', '1.25', '1.79', '2.39',
                 '1.0', '0.5', '3.0', '1.3333', '2.35', '1.0001', '0.75']


def _find_exiftool():
    override = os.environ.get('EXIFTOOL')
    if override:
        return [override] if os.access(override, os.X_OK) else ['perl', override]
    toolchain = os.path.expanduser('~/.desqueeze-toolchain/exiftool/exiftool')
    if os.path.exists(toolchain):
        return ['perl', toolchain]
    found = shutil.which('exiftool')
    return [found] if found else None


EXIFTOOL = _find_exiftool()


def _exiftool_lib():
    return os.path.join(os.path.dirname(EXIFTOOL[-1]), 'lib')


@unittest.skipIf(EXIFTOOL is None, 'exiftool not available')
class RationalizeParity(unittest.TestCase):
    """Our port of ExifTool's Rationalize must agree digit for digit."""

    def test_matches_exiftool(self):
        values = PRESET_VALUES + ['%.6f' % (i / 97.0) for i in range(1, 60)]
        script = (
            'use lib "%s"; use Image::ExifTool;'
            'foreach my $v (@ARGV) {'
            '  my ($n,$d) = Image::ExifTool::Rationalize($v, 0xffffffff);'
            '  print "$n $d\\n"; }' % _exiftool_lib()
        )
        out = subprocess.run(['perl', '-e', script] + values,
                             capture_output=True, text=True, check=True)
        expected = [tuple(int(x) for x in line.split())
                    for line in out.stdout.strip().splitlines()]
        self.assertEqual(len(expected), len(values))
        for value, want in zip(values, expected):
            self.assertEqual(dng_engine.rationalize(value), want,
                             'mismatch for %s' % value)


@unittest.skipIf(EXIFTOOL is None, 'exiftool not available')
class FileParity(unittest.TestCase):
    """Same tag value, same IFD, for every file shape and byte order."""

    def _report(self, path):
        out = subprocess.run(
            EXIFTOOL + ['-a', '-G1', '-s', '-n', '-DefaultScale', path],
            capture_output=True, text=True, check=True)
        return out.stdout.strip()

    def _raw_values(self, path):
        with open(path, 'rb') as handle:
            return dng_engine.read_default_scale(handle.read())

    def test_all_variants(self):
        """Every file shape that has a SubIFD must match plain exiftool exactly."""
        for variant in tiff_builder.VARIANTS:
            if variant == 'cinemadng':
                continue  # covered by the fallback tests below
            for endian in ('<', '>'):
                for scale in ('1.33', '2.0', '1.25', '2.39'):
                    with self.subTest(variant=variant, endian=endian, scale=scale):
                        self._check(variant, endian, scale,
                                    ['-DefaultScale=%s 1.0' % scale])

    def test_single_ifd_dng_matches_explicit_ifd0_write(self):
        """DNGs with no SubIFD: we match ``exiftool -IFD0:DefaultScale=``.

        This is the port's one deliberate deviation.  See
        ``test_exiftool_writes_nothing_without_a_subifd`` for why.
        """
        for endian in ('<', '>'):
            for scale in ('1.33', '2.39'):
                with self.subTest(endian=endian, scale=scale):
                    self._check('cinemadng', endian, scale,
                                ['-IFD0:DefaultScale=%s 1.0' % scale])

    def test_exiftool_writes_nothing_without_a_subifd(self):
        """Documents the ExifTool behaviour we intentionally do not copy.

        0xC61E has ``WriteGroup => 'SubIFD'`` and ExifTool will not create a
        SubIFD, so a plain ``-DefaultScale=`` write on a single-IFD CinemaDNG
        frame succeeds while writing no tag at all.  If a future ExifTool
        release fixes that, this test fails and tells us to drop the fallback.
        """
        source = tiff_builder.make_variant('cinemadng')
        with tempfile.TemporaryDirectory() as work:
            src = os.path.join(work, 'src.dng')
            with open(src, 'wb') as handle:
                handle.write(source)
            out = os.path.join(work, 'out.dng')
            subprocess.run(EXIFTOOL + ['-DefaultScale=1.33 1.0', '-o', out, src],
                           capture_output=True, text=True, check=True)
            self.assertEqual(self._raw_values(out), [])

    def _check(self, variant, endian, scale, exiftool_args):
        source = tiff_builder.make_variant(variant, endian)
        with tempfile.TemporaryDirectory() as work:
            src = os.path.join(work, 'src.dng')
            with open(src, 'wb') as handle:
                handle.write(source)

            et_out = os.path.join(work, 'exiftool.dng')
            result = subprocess.run(EXIFTOOL + exiftool_args + ['-o', et_out, src],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0,
                             'exiftool failed: %s %s' % (result.stdout, result.stderr))

            mine = os.path.join(work, 'mine.dng')
            patched, _, _ = dng_engine.patch_default_scale(source, scale, '1.0')
            with open(mine, 'wb') as handle:
                handle.write(patched)

            self.assertEqual(self._report(mine), self._report(et_out),
                             'ExifTool reports different DefaultScale')
            self.assertEqual(self._raw_values(mine), self._raw_values(et_out),
                             'stored rationals or IFD placement differ')

    def test_output_is_still_a_valid_dng(self):
        """ExifTool must be able to fully parse what we wrote, with no errors."""
        for variant in tiff_builder.VARIANTS:
            with self.subTest(variant=variant):
                source = tiff_builder.make_variant(variant)
                with tempfile.TemporaryDirectory() as work:
                    path = os.path.join(work, 'mine.dng')
                    patched, _, _ = dng_engine.patch_default_scale(source, '1.33', '1.0')
                    with open(path, 'wb') as handle:
                        handle.write(patched)
                    out = subprocess.run(
                        EXIFTOOL + ['-a', '-G1', '-s', '-validate', '-warning', path],
                        capture_output=True, text=True, check=True)
                    self.assertNotIn('Error', out.stdout)


class EngineBehaviour(unittest.TestCase):
    """Checks that do not need exiftool."""

    def test_rejects_non_tiff(self):
        with self.assertRaises(dng_engine.DngError):
            dng_engine.patch_default_scale(b'not a dng at all', '1.33', '1.0')

    def test_rejects_truncated(self):
        with self.assertRaises(dng_engine.DngError):
            dng_engine.patch_default_scale(b'II*\x00', '1.33', '1.0')

    def test_is_probably_dng(self):
        self.assertTrue(dng_engine.is_probably_dng(
            tiff_builder.make_variant('standard')))
        self.assertFalse(dng_engine.is_probably_dng(b'\x89PNG\r\n\x1a\n' + b'\x00' * 64))

    def test_repeated_patching_is_stable(self):
        """Re-running must not grow the file without bound or corrupt it."""
        data = tiff_builder.make_variant('standard')
        first, _, _ = dng_engine.patch_default_scale(data, '1.33', '1.0')
        second, _, _ = dng_engine.patch_default_scale(first, '2.0', '1.0')
        third, _, _ = dng_engine.patch_default_scale(second, '1.5', '1.0')
        self.assertEqual(len(second), len(third))  # in-place after first insert
        values = dng_engine.read_default_scale(third)
        self.assertEqual([v[1] for v in values], [(3, 2)])

    def test_only_one_pointer_changes_in_the_original_region(self):
        """Nothing already in the file may move.

        Adding the tag appends a relocated IFD to the end of the file, so the
        only edit inside the original bytes is the single 32-bit offset that
        pointed at it -- image strips, previews and MakerNotes stay put.
        """
        for variant in ('standard', 'cinemadng', 'two_subifds'):
            with self.subTest(variant=variant):
                data = tiff_builder.make_variant(variant)
                patched, _, _ = dng_engine.patch_default_scale(data, '1.33', '1.0')
                self.assertGreater(len(patched), len(data))
                changed = [i for i in range(len(data)) if patched[i] != data[i]]
                self.assertTrue(changed, 'expected the IFD pointer to be re-aimed')
                self.assertLessEqual(len(changed), 4)
                self.assertEqual(changed[-1] - changed[0], len(changed) - 1,
                                 'edits must be one contiguous 32-bit word')

    def test_in_place_update_does_not_grow_the_file(self):
        data = tiff_builder.make_variant('existing_subifd')
        patched, kind, fallback = dng_engine.patch_default_scale(data, '1.33', '1.0')
        self.assertEqual((kind, fallback), ('SubIFD0', False))
        self.assertEqual(len(patched), len(data))

    def test_both_byte_orders(self):
        for endian in ('<', '>'):
            data = tiff_builder.make_variant('cinemadng', endian)
            patched, kind, fallback = dng_engine.patch_default_scale(data, '1.33', '1.0')
            self.assertTrue(fallback)
            kind, x, y = dng_engine.read_default_scale(patched)[0]
            self.assertEqual((x, y), ((133, 100), (1, 1)))
            self.assertEqual(kind, 'IFD0')

    def test_scale_bytes_endianness(self):
        little = dng_engine.default_scale_bytes('1.33', '1.0', '<')
        big = dng_engine.default_scale_bytes('1.33', '1.0', '>')
        self.assertEqual(struct.unpack('<IIII', little),
                         struct.unpack('>IIII', big))


if __name__ == '__main__':
    unittest.main()
