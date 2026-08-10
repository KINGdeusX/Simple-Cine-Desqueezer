#!/usr/bin/env python3
"""Buildozer launcher with one host-detection heuristic disabled.

Buildozer's Android target refuses to start on any machine where ``dpkg``
exists but ``/usr/include/zlib.h`` does not, on the assumption that the only
way to have zlib is ``apt-get install zlib1g-dev``.

This project has no root on the build machine, so ``bootstrap_toolchain.sh``
builds zlib into ``~/.desqueeze-toolchain/local`` and exports ``CPATH`` /
``LIBRARY_PATH`` so every compiler invocation finds it.  The headers really are
present -- only Buildozer's hardcoded path check disagrees, so that single
check is switched off here by hiding ``dpkg`` from ``shutil.which``.  Nothing
else about the build is altered.

Usage:  python run_buildozer.py android debug
"""

from __future__ import annotations

import os
import shutil
import sys

_real_which = shutil.which


def _which(cmd, *args, **kwargs):
    if cmd == 'dpkg':
        # Skip the Debian-package guess; our zlib is in the toolchain prefix.
        return None
    return _real_which(cmd, *args, **kwargs)


def _assert_zlib_really_is_available():
    """Do the check Buildozer meant to do, rather than the one it wrote."""
    roots = [part for part in os.environ.get('CPATH', '').split(os.pathsep) if part]
    roots += ['/usr/include', '/usr/local/include']
    for root in roots:
        if os.path.exists(os.path.join(root, 'zlib.h')):
            return
    raise SystemExit('zlib.h not found on CPATH or in /usr/include -- run '
                     'ANDROID/tools/bootstrap_toolchain.sh first')


def _drop_pip_user_flag():
    """Let Buildozer install python-for-android's dependencies into our venv.

    Buildozer installs them with ``pip install --user``, which pip refuses to do
    from inside a virtualenv ("User site-packages are not visible in this
    virtualenv").  Dropping ``--user`` puts them in the toolchain venv instead,
    which is both where the rest of the build already looks and tidier than
    writing into the user's ``~/.local``.
    """
    from buildozer import Buildozer

    original_cmd = Buildozer.cmd

    def cmd(self, command, **kwargs):
        if (isinstance(command, (list, tuple)) and '--user' in command
                and 'pip' in command):
            command = [part for part in command if part != '--user']
        return original_cmd(self, command, **kwargs)

    Buildozer.cmd = cmd


def main():
    _assert_zlib_really_is_available()
    shutil.which = _which
    _drop_pip_user_flag()
    from buildozer.scripts.client import main as buildozer_main
    buildozer_main()


if __name__ == '__main__':
    sys.exit(main())
