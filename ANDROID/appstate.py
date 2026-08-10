"""Last-used source, destination and preset, remembered across launches.

The desktop app keeps a ``state.json`` next to itself; on Android the same JSON
lives in the app's private ``user_data_dir``, which needs no permissions and is
removed cleanly when the app is uninstalled.
"""

from __future__ import annotations

import json
import os

FILENAME = 'state.json'

DEFAULTS = {
    # The picked files are not remembered -- SAF grants for individual
    # documents are per-run, and choosing files each time is the flow.
    'dest_key': '',
    'dest_folder_name': 'desqueezed',
    'preset_index': 0,
    'custom_x': '1.33',
    'custom_y': '1.0',
}


class AppState(dict):
    def __init__(self, directory):
        super().__init__(DEFAULTS)
        self.path = os.path.join(directory, FILENAME)

    def load(self):
        try:
            with open(self.path, 'r', encoding='utf-8') as stream:
                stored = json.load(stream)
        except (OSError, ValueError):
            return self
        if isinstance(stored, dict):
            for key in DEFAULTS:
                if key in stored:
                    self[key] = stored[key]
        # a corrupt index must never crash the spinner
        try:
            self['preset_index'] = int(self['preset_index'])
        except (TypeError, ValueError):
            self['preset_index'] = 0
        return self

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            temporary = self.path + '.tmp'
            with open(temporary, 'w', encoding='utf-8') as stream:
                json.dump(dict(self), stream)
            os.replace(temporary, self.path)
        except OSError:
            pass    # persistence is a convenience, never a failure mode
        return self
