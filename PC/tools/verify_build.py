#!/usr/bin/env python3
"""Check a packaged build before anyone downloads it.

The expensive failure with PyInstaller is the one you only meet on the user's
machine: the app starts and then cannot find a tool, or a module was never
collected.  These checks run on the build machine instead.

Usage:  python PC/tools/verify_build.py PC/dist
"""

from __future__ import annotations

import os
import sys
import zipfile

REQUIRED_TOOLS = (
    ('exiftool', ('exiftool.exe', 'exiftool'),
     'writes DefaultScale into every raw format'),
    ('ffmpeg', ('ffmpeg.exe', 'ffmpeg'), 're-encodes video'),
    ('ffmpeg', ('ffprobe.exe', 'ffprobe'), 'reads video properties'),
    ('dnglab', ('dnglab.exe', 'dnglab'), 'converts raw to DNG'),
)

results = []


def check(ok, message, detail=''):
    results.append((bool(ok), message, detail))


def find_folder_build(dist):
    for name in ('Desqueeze',):
        candidate = os.path.join(dist, name)
        if os.path.isdir(candidate):
            return candidate
    return None


def find_portable(dist):
    for name in os.listdir(dist) if os.path.isdir(dist) else []:
        if name.lower().startswith('desqueeze-portable') and name.lower().endswith('.exe'):
            return os.path.join(dist, name)
    return None


def check_folder_build(folder):
    executable = None
    for name in ('Desqueeze.exe', 'Desqueeze'):
        candidate = os.path.join(folder, name)
        if os.path.isfile(candidate):
            executable = candidate
            break
    check(executable is not None, 'application executable present')

    vendor = os.path.join(folder, 'vendor')
    if not os.path.isdir(vendor):
        # PyInstaller may place datas under _internal on newer versions
        vendor = os.path.join(folder, '_internal', 'vendor')
    check(os.path.isdir(vendor), 'vendor folder shipped alongside the app')

    seen = set()
    for folder_name, names, purpose in REQUIRED_TOOLS:
        found = False
        for root, _dirs, files in os.walk(vendor if os.path.isdir(vendor) else folder):
            lowered = {f.lower() for f in files}
            if any(name.lower() in lowered for name in names):
                found = True
                break
        key = names[0]
        if key in seen:
            continue
        seen.add(key)
        check(found, 'bundled: %s (%s)' % (names[0], purpose))

    total = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    check(total > 40 * 1024 * 1024,
          'build is a plausible size', '%.0f MB' % (total / 1024 / 1024))


def check_portable(path):
    size = os.path.getsize(path)
    check(size > 40 * 1024 * 1024, 'portable build is a plausible size',
          '%.0f MB' % (size / 1024 / 1024))

    # A one-file build is a zip-like archive with everything appended; the tool
    # names still appear as strings in it, which is enough to catch an empty
    # vendor folder at package time.
    try:
        with open(path, 'rb') as handle:
            blob = handle.read()
    except OSError as exc:
        check(False, 'portable build is readable', str(exc))
        return
    for _folder, names, purpose in REQUIRED_TOOLS:
        needle = names[0].encode('ascii', 'ignore')
        check(needle in blob, 'portable bundle mentions %s (%s)'
              % (names[0], purpose))


def main():
    dist = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'dist')
    if not os.path.isdir(dist):
        print('no dist folder at %s' % dist)
        return 1

    print('verifying %s' % dist)
    folder = find_folder_build(dist)
    portable = find_portable(dist)
    check(folder is not None or portable is not None,
          'at least one build artifact exists')
    if folder:
        check_folder_build(folder)
    if portable:
        check_portable(portable)

    installers = [name for name in os.listdir(dist)
                  if name.lower().endswith('.exe')
                  and 'setup' in name.lower()]
    if installers:
        check(True, 'installer produced', installers[0])

    print()
    failed = 0
    for ok, message, detail in results:
        print('  %s %s%s' % ('ok  ' if ok else 'FAIL', message,
                             ('  [%s]' % detail) if detail else ''))
        failed += not ok
    print()
    if failed:
        print('BUILD VERIFICATION FAILED (%d problem(s))' % failed)
        return 1
    print('BUILD VERIFICATION PASSED (%d checks)' % len(results))
    return 0


if __name__ == '__main__':
    sys.exit(main())
