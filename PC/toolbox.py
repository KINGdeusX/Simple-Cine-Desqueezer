"""Finding the bundled helper binaries.

The desktop app that came before this one asked the user where ExifTool was and
searched the PATH on every launch.  Here the tools ship *with* the application,
so this module's job is to resolve them once, at import, from wherever the app
happens to be running:

* frozen by PyInstaller -- beside the executable, or inside the one-file bundle;
* installed by the Inno Setup installer -- in the install directory;
* run from a source checkout -- in ``PC/vendor/<platform>/``.

Nothing here ever prompts, and nothing searches the PATH by default: a stray
ExifTool of a different version on the user's PATH is exactly the surprise this
design is meant to remove.  ``allow_system`` exists only so the host-side tests
can fall back to whatever the machine already has.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

IS_WINDOWS = sys.platform.startswith('win')
PLATFORM = 'windows' if IS_WINDOWS else 'linux'
EXE = '.exe' if IS_WINDOWS else ''

HERE = os.path.dirname(os.path.abspath(__file__))

# Keep console windows from flashing up for every file in a batch.
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


class ToolMissing(Exception):
    """A required helper binary is not part of this installation."""


def _candidate_roots():
    """Every place a bundled ``vendor`` tree could be, best first."""
    roots = []
    # PyInstaller one-file: everything is unpacked into _MEIPASS
    bundle = getattr(sys, '_MEIPASS', None)
    if bundle:
        roots.append(os.path.join(bundle, 'vendor'))
    if getattr(sys, 'frozen', False):
        beside = os.path.dirname(os.path.abspath(sys.executable))
        roots.append(os.path.join(beside, 'vendor'))
        roots.append(beside)
    roots.append(os.path.join(HERE, 'vendor'))

    resolved = []
    for root in roots:
        resolved.append(root)
        resolved.append(os.path.join(root, PLATFORM))
    return resolved


def _find(relative_parts, names):
    for root in _candidate_roots():
        for name in names:
            path = os.path.join(root, *relative_parts, name)
            if os.path.isfile(path):
                return path
    return None


def _command_for(path):
    """How to invoke a tool: a Perl script needs an interpreter in front."""
    if path.lower().endswith('.exe') or IS_WINDOWS:
        return [path]
    with open(path, 'rb') as handle:
        head = handle.read(2)
    if head == b'#!':
        perl = shutil.which('perl')
        if perl:
            return [perl, path]
    return [path]


class Tool:
    """One helper binary, resolved lazily and remembered."""

    def __init__(self, key, folder, names, description):
        self.key = key
        self.folder = folder
        self.names = names
        self.description = description
        self._command = None
        self._checked = False

    def locate(self, allow_system=False):
        if self._checked:
            return self._command
        self._checked = True

        path = _find([self.folder], self.names)
        if path is None and allow_system:
            for name in self.names:
                stem = name[:-4] if name.lower().endswith('.exe') else name
                found = shutil.which(stem)
                if found:
                    path = found
                    break
        self._command = _command_for(path) if path else None
        return self._command

    @property
    def available(self):
        return self.locate(allow_system=_ALLOW_SYSTEM) is not None

    def command(self, *arguments):
        base = self.locate(allow_system=_ALLOW_SYSTEM)
        if base is None:
            raise ToolMissing(
                '%s is missing from this installation. %s'
                % (self.key, self.description))
        return list(base) + [str(argument) for argument in arguments]

    def run(self, *arguments, **kwargs):
        """Run to completion, returning the CompletedProcess."""
        timeout = kwargs.pop('timeout', None)
        return subprocess.run(self.command(*arguments),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              creationflags=CREATE_NO_WINDOW, timeout=timeout,
                              **kwargs)

    def popen(self, *arguments, **kwargs):
        """Start it and stream its output, for anything worth a progress bar."""
        return subprocess.Popen(self.command(*arguments),
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                text=True, bufsize=1,
                                creationflags=CREATE_NO_WINDOW, **kwargs)

    def version(self):
        try:
            result = self.run(*self.version_arguments, timeout=30)
        except Exception:
            return None
        text = (result.stdout or b'')
        if isinstance(text, bytes):
            text = text.decode('utf-8', 'replace')
        return text.strip().splitlines()[0] if text.strip() else None

    version_arguments = ('-ver',)


class _FfmpegTool(Tool):
    version_arguments = ('-version',)

    def version(self):
        line = super().version()
        if line and line.startswith('ffmpeg version '):
            return line[len('ffmpeg version '):].split()[0]
        return line


class _DnglabTool(Tool):
    version_arguments = ('--version',)


EXIFTOOL = Tool(
    'ExifTool', 'exiftool', ['exiftool' + EXE, 'exiftool'],
    'It writes the DefaultScale tag; reinstall the app to restore it.')

FFMPEG = _FfmpegTool(
    'FFmpeg', 'ffmpeg', ['ffmpeg' + EXE, 'ffmpeg'],
    'It is needed to re-encode video; metadata-only mode still works.')

FFPROBE = _FfmpegTool(
    'FFprobe', 'ffmpeg', ['ffprobe' + EXE, 'ffprobe'],
    'It reads video properties; metadata-only mode still works.')

DNGLAB = _DnglabTool(
    'DNGLab', 'dnglab', ['dnglab' + EXE, 'dnglab'],
    'It converts raw files to DNG; other output formats still work.')

ALL_TOOLS = (EXIFTOOL, FFMPEG, FFPROBE, DNGLAB)

# Host-side tests run from a checkout and may not have fetched every binary.
_ALLOW_SYSTEM = os.environ.get('DESQUEEZE_ALLOW_SYSTEM_TOOLS') == '1'


def allow_system_tools(enabled=True):
    """Let the resolver fall back to the PATH (tests only)."""
    global _ALLOW_SYSTEM
    _ALLOW_SYSTEM = enabled
    for tool in ALL_TOOLS:
        tool._checked = False
        tool._command = None


def report():
    """What this installation actually has, for the About box and the log."""
    lines = []
    for tool in ALL_TOOLS:
        if tool is FFPROBE:
            continue                     # ships and versions with ffmpeg
        version = tool.version() if tool.available else None
        lines.append('%-9s %s' % (tool.key,
                                  version or ('missing' if not tool.available
                                              else 'present')))
    return lines
