"""Remembered settings: output folders, presets and encoder choices.

Kept in the user's own application-data folder rather than next to the
executable, because an installed copy lives under Program Files where a normal
account cannot write.
"""

from __future__ import annotations

import json
import os
import sys

APP_FOLDER = 'Desqueeze'
FILENAME = 'settings.json'

DEFAULTS = {
    # The chosen files are deliberately not remembered -- a batch is a one-off.
    'output_photo': '',
    'output_video': '',
    'preset_photo': 0,
    'preset_video': 0,
    'custom_x': '1.33',
    'custom_y': '1.0',
    'photo_mode': 'tag',
    'photo_output': 'original',
    'jpeg_quality': 95,
    'dng_compressed': True,
    'bitrate_mbps': 20,
    'codec': 'hevc',
}


def settings_directory():
    if sys.platform.startswith('win'):
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
    elif sys.platform == 'darwin':
        base = os.path.expanduser('~/Library/Application Support')
    else:
        base = (os.environ.get('XDG_CONFIG_HOME')
                or os.path.expanduser('~/.config'))
    return os.path.join(base, APP_FOLDER)


class AppState(dict):

    def __init__(self, directory=None):
        super().__init__(DEFAULTS)
        self.path = os.path.join(directory or settings_directory(), FILENAME)

    def load(self):
        try:
            with open(self.path, 'r', encoding='utf-8') as handle:
                stored = json.load(handle)
        except (OSError, ValueError):
            return self
        if isinstance(stored, dict):
            for key in DEFAULTS:
                if key in stored:
                    self[key] = stored[key]
        for key in ('preset_photo', 'preset_video', 'jpeg_quality',
                    'bitrate_mbps'):
            try:
                self[key] = int(self[key])
            except (TypeError, ValueError):
                self[key] = DEFAULTS[key]
        return self

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            temporary = self.path + '.tmp'
            with open(temporary, 'w', encoding='utf-8') as handle:
                json.dump(dict(self), handle, indent=2, sort_keys=True)
            os.replace(temporary, self.path)
        except OSError:
            pass        # remembering settings is a convenience, never a failure
        return self
