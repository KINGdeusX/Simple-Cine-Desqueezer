# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for both Windows artifacts.

Two builds come out of this one file, chosen by the ``DESQUEEZE_ONEFILE``
environment variable that ``build_windows.ps1`` sets:

  onedir  (default)  a folder the Inno Setup installer packages
  onefile            a single portable .exe that unpacks itself on launch

The vendored binaries are bundled as *data* rather than binaries on purpose:
PyInstaller tries to scan anything listed as a binary for its own dependencies,
and ExifTool's bundled Perl tree confuses that analysis badly.  They are plain
payload -- ``toolbox`` finds them at runtime under ``vendor/``.
"""

import os
import sys

PC_DIR = os.path.abspath(os.path.join(os.getcwd()))
if os.path.basename(PC_DIR) != 'PC':
    PC_DIR = os.path.abspath(os.path.join(PC_DIR, 'PC'))
REPO = os.path.dirname(PC_DIR)

ONEFILE = os.environ.get('DESQUEEZE_ONEFILE') == '1'
VENDOR = os.path.join(PC_DIR, 'vendor', 'windows')

datas = []
if os.path.isdir(VENDOR):
    for folder, _subfolders, files in os.walk(VENDOR):
        for name in files:
            full = os.path.join(folder, name)
            relative = os.path.relpath(os.path.dirname(full), VENDOR)
            datas.append((full, os.path.join('vendor', relative)))
else:
    print('WARNING: %s is missing -- run tools/fetch_binaries.py --platform '
          'windows first, or the app will ship with nothing to run.' % VENDOR)

for icon_name in ('icon.ico', 'icon.png'):
    candidate = os.path.join(REPO, icon_name)
    if os.path.exists(candidate):
        datas.append((candidate, '.'))

a = Analysis(
    [os.path.join(PC_DIR, 'app.py')],
    pathex=[PC_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=['rawpy', 'PIL.Image', 'PIL.ImageFile'],
    hookspath=[],
    runtime_hooks=[],
    # Qt ships a great deal we never touch; leaving it in roughly doubles the
    # download for no benefit.
    excludes=['tkinter', 'unittest', 'pydoc', 'PyQt6.QtWebEngineCore',
              'PyQt6.QtWebEngineWidgets', 'PyQt6.QtQuick', 'PyQt6.QtQml',
              'PyQt6.Qt3DCore', 'PyQt6.QtMultimedia', 'PyQt6.QtBluetooth',
              'PyQt6.QtDesigner', 'PyQt6.QtNetwork', 'PyQt6.QtSql',
              'PyQt6.QtTest', 'matplotlib', 'scipy'],
    noarchive=False,
)
pyz = PYZ(a.pure)

icon_file = os.path.join(REPO, 'icon.ico')
icon_argument = icon_file if os.path.exists(icon_file) else None

if ONEFILE:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name='Desqueeze-Portable',
        debug=False, bootloader_ignore_signals=False, strip=False,
        upx=False, runtime_tmpdir=None,
        console=False, icon=icon_argument,
    )
else:
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name='Desqueeze',
        debug=False, bootloader_ignore_signals=False, strip=False,
        upx=False, console=False, icon=icon_argument,
    )
    coll = COLLECT(
        exe, a.binaries, a.datas,
        strip=False, upx=False, name='Desqueeze',
    )
