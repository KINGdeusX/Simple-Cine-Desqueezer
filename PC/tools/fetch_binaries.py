#!/usr/bin/env python3
"""Download the helper binaries the app ships with.

The whole point of vendoring these is that the app never hunts for a tool at
runtime and never asks the user to install one:

  exiftool   writes DefaultScale into every raw format, including Canon CR3 and
             Fujifilm RAF, which are not TIFF-based and cannot be patched by the
             pure-Python writer the Android build uses
  ffmpeg     re-encodes video, and is the only piece here under the GPL -- see
             LICENSING below
  dnglab     converts proprietary raw into DNG

Run for the platform you are packaging for:

    python PC/tools/fetch_binaries.py --platform windows   # what ships
    python PC/tools/fetch_binaries.py --platform linux     # host-side tests

Everything lands in ``PC/vendor/<platform>/`` and is ignored by git; the release
workflow fetches it fresh on the build machine.

LICENSING: the FFmpeg build pulled here includes x264 and x265 and is therefore
GPL. That does not change this project's own MIT licence, but a distributed
bundle containing it must carry the GPL terms -- ``tools/desqueeze.iss`` ships
the licence text alongside the binary for exactly that reason.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PC_DIR = os.path.dirname(HERE)
VENDOR = os.path.join(PC_DIR, 'vendor')

# exiftool.org rejects unknown clients, so present as a normal browser.
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')
TIMEOUT = 120


def fetch(url):
    print('  downloading %s' % url)
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def fetch_first(urls, what):
    """Try each mirror in turn; report them all if none answers."""
    problems = []
    for url in urls:
        try:
            return fetch(url), url
        except Exception as exc:
            problems.append('  %s -> %s' % (url, exc))
    raise SystemExit('could not download %s. Tried:\n%s'
                     % (what, '\n'.join(problems)))


def latest_exiftool_version():
    """The newest release tag, read from GitHub (always reachable)."""
    try:
        tags = read_json('https://api.github.com/repos/exiftool/exiftool/tags')
        versions = [tag['name'] for tag in tags
                    if tag.get('name', '').replace('.', '').isdigit()]
        if versions:
            return sorted(versions,
                          key=lambda v: [int(p) for p in v.split('.')])[-1]
    except Exception:
        pass
    return '13.59'          # a known-good floor if the API is unavailable


def read_json(url):
    return json.loads(fetch(url).decode('utf-8'))


def make_executable(path):
    mode = os.stat(path).st_mode
    os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def extract_zip(blob, destination, wanted=None, flatten=False):
    """Pull files out of a zip, optionally only those matching ``wanted``."""
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            name = member.filename
            if wanted is not None and not wanted(name):
                continue
            target = os.path.join(destination,
                                  os.path.basename(name) if flatten else name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with archive.open(member) as source, open(target, 'wb') as out:
                shutil.copyfileobj(source, out)
            if not name.lower().endswith(('.dll', '.txt', '.md', '.html')):
                make_executable(target)


# --- exiftool ---------------------------------------------------------------

def exiftool_windows(destination):
    """The standalone Windows build: exiftool.exe with its own Perl runtime.

    This is what makes the app self-contained on Windows -- no Perl install, no
    hunting for a path.  Only exiftool.org and its SourceForge mirror publish
    it; the GitHub repository carries the Perl source, which would need a Perl
    interpreter and so is no use for packaging.
    """
    version = latest_exiftool_version()
    blob, _url = fetch_first([
        'https://exiftool.org/exiftool-%s_64.zip' % version,
        'https://sourceforge.net/projects/exiftool/files/'
        'exiftool-%s_64.zip/download' % version,
        'https://exiftool.org/exiftool-%s.zip' % version,
    ], 'the ExifTool %s Windows build' % version)

    target = os.path.join(destination, 'exiftool')
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = [m.filename for m in archive.infolist() if not m.is_dir()]
        root = os.path.commonprefix(names)
        root = root[:root.rfind('/') + 1] if '/' in root else ''
        for member in archive.infolist():
            if member.is_dir():
                continue
            relative = member.filename[len(root):]
            if not relative:
                continue
            out_path = os.path.join(target, relative)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with archive.open(member) as source, open(out_path, 'wb') as out:
                shutil.copyfileobj(source, out)

    # The distribution names it exiftool(-k).exe so that a double-click pauses
    # before closing; driven from code that pause would hang the batch forever.
    for name in os.listdir(target):
        lowered = name.lower()
        if lowered.startswith('exiftool(') and lowered.endswith('.exe'):
            os.replace(os.path.join(target, name),
                       os.path.join(target, 'exiftool.exe'))
    if not os.path.exists(os.path.join(target, 'exiftool.exe')):
        raise SystemExit('the ExifTool archive did not contain exiftool.exe')
    print('  exiftool %s' % version)
    return version


def exiftool_linux(destination):
    """Perl source distribution, run through the system perl for host tests.

    Taken from GitHub rather than exiftool.org because the release tags are
    reachable from any network, including CI sandboxes that block the website.
    """
    version = latest_exiftool_version()
    blob, _url = fetch_first([
        'https://github.com/exiftool/exiftool/archive/refs/tags/%s.tar.gz' % version,
    ], 'the ExifTool %s source' % version)

    target = os.path.join(destination, 'exiftool')
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(blob)) as archive:
        archive.extractall(target, filter='data')
    entries = [os.path.join(target, name) for name in os.listdir(target)]
    if len(entries) == 1 and os.path.isdir(entries[0]):
        inner = entries[0]
        for entry in os.listdir(inner):
            shutil.move(os.path.join(inner, entry), os.path.join(target, entry))
        os.rmdir(inner)
    make_executable(os.path.join(target, 'exiftool'))
    print('  exiftool %s' % version)
    return version


# --- ffmpeg -----------------------------------------------------------------

def ffmpeg_windows(destination):
    release = read_json('https://api.github.com/repos/GyanD/codexffmpeg/'
                        'releases/latest')
    asset = None
    for candidate in release.get('assets', []):
        if candidate['name'].endswith('-full_build.zip'):
            asset = candidate
            break
    if asset is None:
        raise SystemExit('could not find an FFmpeg full build')
    blob = fetch(asset['browser_download_url'])

    target = os.path.join(destination, 'ffmpeg')
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target, exist_ok=True)
    extract_zip(blob, target,
                wanted=lambda n: os.path.basename(n).lower()
                in ('ffmpeg.exe', 'ffprobe.exe', 'license.txt'),
                flatten=True)
    print('  ffmpeg %s (GPL build: includes x264/x265)' % release['tag_name'])
    return release['tag_name']


def ffmpeg_linux(destination):
    blob = fetch('https://johnvansickle.com/ffmpeg/releases/'
                 'ffmpeg-release-amd64-static.tar.xz')
    target = os.path.join(destination, 'ffmpeg')
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:xz') as archive:
        for member in archive.getmembers():
            base = os.path.basename(member.name)
            if base in ('ffmpeg', 'ffprobe', 'GPLv3.txt'):
                member.name = base
                archive.extract(member, target, filter='data')
                if base != 'GPLv3.txt':
                    make_executable(os.path.join(target, base))
    print('  ffmpeg (static GPL build)')
    return 'static'


# --- dnglab -----------------------------------------------------------------

def dnglab(destination, platform):
    release = read_json('https://api.github.com/repos/dnglab/dnglab/'
                        'releases/latest')
    target = os.path.join(destination, 'dnglab')
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(target, exist_ok=True)

    for candidate in release.get('assets', []):
        name = candidate['name']
        if platform == 'windows' and name.startswith('dnglab-win-x64'):
            extract_zip(fetch(candidate['browser_download_url']), target,
                        wanted=lambda n: n.lower().endswith('.exe'),
                        flatten=True)
            print('  dnglab %s' % release['tag_name'])
            return release['tag_name']
        if platform == 'linux' and name == 'dnglab_linux_x64':
            path = os.path.join(target, 'dnglab')
            with open(path, 'wb') as out:
                out.write(fetch(candidate['browser_download_url']))
            make_executable(path)
            print('  dnglab %s' % release['tag_name'])
            return release['tag_name']

    print('  dnglab: no build for %s -- DNG conversion will be unavailable'
          % platform, file=sys.stderr)
    return None


# --- driver -----------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', choices=('windows', 'linux'),
                        default='linux' if sys.platform.startswith('linux')
                        else 'windows')
    parser.add_argument('--only', choices=('exiftool', 'ffmpeg', 'dnglab'),
                        action='append',
                        help='fetch just one tool (repeatable)')
    arguments = parser.parse_args()

    destination = os.path.join(VENDOR, arguments.platform)
    os.makedirs(destination, exist_ok=True)
    wanted = arguments.only or ['exiftool', 'ffmpeg', 'dnglab']

    print('fetching helper binaries for %s' % arguments.platform)
    versions = {}
    if 'exiftool' in wanted:
        versions['exiftool'] = (exiftool_windows(destination)
                                if arguments.platform == 'windows'
                                else exiftool_linux(destination))
    if 'ffmpeg' in wanted:
        versions['ffmpeg'] = (ffmpeg_windows(destination)
                              if arguments.platform == 'windows'
                              else ffmpeg_linux(destination))
    if 'dnglab' in wanted:
        versions['dnglab'] = dnglab(destination, arguments.platform)

    manifest = os.path.join(destination, 'versions.json')
    existing = {}
    if os.path.exists(manifest):
        try:
            with open(manifest) as handle:
                existing = json.load(handle)
        except ValueError:
            existing = {}
    existing.update({k: v for k, v in versions.items() if v})
    with open(manifest, 'w') as handle:
        json.dump(existing, handle, indent=2, sort_keys=True)

    print('done -> %s' % destination)


if __name__ == '__main__':
    main()
