#!/usr/bin/env python3
"""Check a built APK before anyone tries to install it.

The expensive failures with a python-for-android build are the ones you only
see on the phone -- an asset that did not get packaged, or a native library the
loader refuses, which both look like "the app crashed on open".  These checks
catch them on the build machine:

  1. the manifest is readable and declares no permissions, API 36 and minSdk 29;
  2. every native library is 16 KB-page aligned, which Android 15+ devices with
     16 KB pages require or ``dlopen`` fails and the app dies at startup;
  3. the .so entries are page-aligned inside the zip (they are loaded by mmap
     straight out of the APK);
  4. the Python side is really in there -- main, the kv rules, the engine and
     the bundled font -- and the dev-only trees are not;
  5. the APK carries a signature.

Usage: python verify_apk.py path/to/app.apk
"""

from __future__ import annotations

import glob
import io
import os
import struct
import subprocess
import sys
import tarfile
import zipfile

PAGE = 16 * 1024
PT_LOAD = 1

REQUIRED_PYTHON = ('main', 'dng_engine', 'image_engine', 'engine', 'storage',
                   'saf', 'appstate', 'formats')
REQUIRED_DATA = ('desqueeze.kv', 'DejaVuSansMono.ttf')
FORBIDDEN = ('tests/', 'tools/', 'smoke_app', 'tiff_builder', 'test_parity')

# Third-party packages the app imports at runtime.  A missing one does not fail
# the build -- it fails on the phone, the first time a user resizes a JPEG --
# so the APK is checked for them directly.
REQUIRED_PACKAGES = (
    ('PIL/Image', 'Pillow (resizes PNG/JPEG/TIFF)'),
    ('PIL/_imaging', 'Pillow native imaging extension'),
    ('jnius/', 'pyjnius (Storage Access Framework bridge)'),
    ('kivy/app', 'Kivy'),
)

results = []


def check(ok, message, detail=''):
    results.append((bool(ok), message, detail))


def _sdk_tool(name):
    """Find a build-tools binary inside whatever SDK buildozer downloaded."""
    roots = [os.path.expanduser('~/.buildozer/android/platform/android-sdk'),
             os.environ.get('ANDROIDSDK', '')]
    for root in roots:
        if not root:
            continue
        matches = sorted(glob.glob(os.path.join(root, 'build-tools', '*', name)))
        if matches:
            return matches[-1]
    return None


# --- 1. manifest ------------------------------------------------------------

def check_manifest(path):
    aapt = _sdk_tool('aapt2') or _sdk_tool('aapt')
    if not aapt:
        check(True, 'manifest check skipped (no aapt in the SDK)')
        return
    is_aapt2 = aapt.endswith('aapt2')

    try:
        badging = subprocess.run([aapt, 'dump', 'badging', path],
                                 capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError as exc:
        check(False, 'manifest is readable', exc.stderr.strip()[:200])
        return

    check("package: name='com.kingdeusx.desqueeze'" in badging,
          'package id is com.kingdeusx.desqueeze')
    check('launchable-activity' in badging, 'has a launcher activity')
    check('application-icon' in badging, 'launcher icon present')

    # The SDK levels come from the manifest tree, not from `badging`: newer
    # build-tools stopped printing a `sdkVersion:` line, so reading badging for
    # this silently reports a missing minSdkVersion that is actually set.
    tree_command = ([aapt, 'dump', 'xmltree', '--file', 'AndroidManifest.xml', path]
                    if is_aapt2 else
                    [aapt, 'dump', 'xmltree', path, 'AndroidManifest.xml'])
    tree = subprocess.run(tree_command, capture_output=True, text=True).stdout

    check('minSdkVersion(0x0101020c)=29' in tree, 'minSdkVersion is 29 (Android 10)')
    check('targetSdkVersion(0x01010270)=36' in tree,
          'targetSdkVersion is 36 (Android 16)')

    permissions = [line.strip() for line in tree.splitlines()
                   if 'uses-permission' in line]
    check(not permissions, 'declares no permissions (SAF only)',
          ' '.join(permissions)[:160])


# --- 2. native library page alignment --------------------------------------

def _load_alignments(blob):
    if blob[:4] != b'\x7fELF' or blob[4] != 2:          # ELF64 only (arm64)
        return None
    endian = '<' if blob[5] == 1 else '>'
    phoff, = struct.unpack_from(endian + 'Q', blob, 0x20)
    phentsize, phnum = struct.unpack_from(endian + 'HH', blob, 0x36)
    alignments = []
    for index in range(phnum):
        base = phoff + index * phentsize
        if base + phentsize > len(blob):
            break
        p_type, = struct.unpack_from(endian + 'I', blob, base)
        if p_type == PT_LOAD:
            p_align, = struct.unpack_from(endian + 'Q', blob, base + 48)
            alignments.append(p_align)
    return alignments


def check_native_libs(archive):
    libs = [name for name in archive.namelist()
            if name.startswith('lib/') and name.endswith('.so')]
    check(bool(libs), 'native libraries packaged', '%d found' % len(libs))
    bad = []
    for name in libs:
        alignments = _load_alignments(archive.read(name))
        if alignments is None:
            continue
        if any(value < PAGE for value in alignments):
            bad.append('%s(%s)' % (os.path.basename(name),
                                   ','.join(str(v) for v in sorted(set(alignments)))))
    check(not bad, 'all native libs are 16 KB-page aligned (Android 15+ safe)',
          ' '.join(bad[:6]))

    misaligned = []
    for name in libs:
        info = archive.getinfo(name)
        if info.compress_type != zipfile.ZIP_STORED:
            continue        # compressed libs are extracted, alignment is moot
        offset = info.header_offset + 30 + len(info.filename) + len(info.extra)
        if offset % PAGE:
            misaligned.append(os.path.basename(name))
    check(not misaligned, 'uncompressed libs are page-aligned inside the zip',
          ' '.join(misaligned[:6]))


# --- 3. packaged python -----------------------------------------------------

def _tar_members(archive, entry):
    try:
        with tarfile.open(fileobj=io.BytesIO(archive.read(entry))) as bundle:
            return bundle.getnames()
    except (KeyError, tarfile.TarError):
        return []


def _payloads(archive):
    """Split what is packaged into the app's own files and its dependencies.

    App code goes into ``assets/private.tar``; the interpreter's site-packages
    ride along in ``lib/<abi>/libpybundle.so``, which is a tar named ``.so`` so
    that Android extracts it with the native libraries.  They have to be kept
    apart: the "no dev files" rule is about *our* tree, and third-party
    packages have their own ``tests/`` and ``tools/`` directories.
    """
    app = list(archive.namelist())
    dependencies = []
    for entry in archive.namelist():
        if entry in ('assets/private.tar', 'assets/private.mp3'):
            app += _tar_members(archive, entry)
        elif entry.endswith('libpybundle.so'):
            dependencies += _tar_members(archive, entry)
    return app, dependencies


def check_payload(archive):
    app, dependencies = _payloads(archive)
    app_joined = '\n'.join(app)
    everything = app_joined + '\n' + '\n'.join(dependencies)

    for module in REQUIRED_PYTHON:
        check(('%s.pyc' % module) in app_joined or ('%s.py' % module) in app_joined,
              'packaged: %s' % module)
    for asset in REQUIRED_DATA:
        check(asset in app_joined, 'packaged: %s' % asset)
    check(any('presplash' in name for name in app) or
          any(name.startswith('res/drawable') for name in app),
          'presplash/drawable resources present')

    for needle, label in REQUIRED_PACKAGES:
        check(needle in everything, 'packaged dependency: %s' % label)

    leaked = sorted({item for item in FORBIDDEN if item in app_joined})
    check(not leaked, 'development files excluded from the APK', ' '.join(leaked))


# --- 4. signature -----------------------------------------------------------

def check_signature(path, archive):
    apksigner = _sdk_tool('apksigner')
    if apksigner:
        result = subprocess.run([apksigner, 'verify', '--print-certs', path],
                                capture_output=True, text=True)
        check(result.returncode == 0, 'APK signature verifies',
              (result.stderr or result.stdout).strip().splitlines()[0][:160]
              if result.returncode else '')
        return
    signed = any(name.startswith('META-INF/') and
                 name.endswith(('.RSA', '.DSA', '.EC', '.SF'))
                 for name in archive.namelist())
    check(signed, 'APK carries a v1 signature block')


def main():
    if len(sys.argv) < 2:
        raise SystemExit('usage: verify_apk.py app.apk')
    path = sys.argv[1]
    size_mb = os.path.getsize(path) / (1024 * 1024)
    print('verifying %s (%.1f MB)' % (path, size_mb))

    with zipfile.ZipFile(path) as archive:
        check_manifest(path)
        check_native_libs(archive)
        check_payload(archive)
        check_signature(path, archive)

    print()
    failed = 0
    for ok, message, detail in results:
        print('  %s %s%s' % ('ok  ' if ok else 'FAIL', message,
                             ('  [%s]' % detail) if detail else ''))
        failed += not ok
    print()
    if failed:
        print('APK VERIFICATION FAILED (%d problem(s))' % failed)
        return 1
    print('APK VERIFICATION PASSED (%d checks)' % len(results))
    return 0


if __name__ == '__main__':
    sys.exit(main())
