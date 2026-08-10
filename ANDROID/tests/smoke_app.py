#!/usr/bin/env python3
"""Headless end-to-end drive of the real Kivy app.

This is the "it must not crash when you open it" check, run on every build:
the actual ``DesqueezeApp`` is started, driven through a complete batch on real
files, resized across the layout breakpoint, and screenshotted -- all inside a
virtual X server.  Any exception the in-app crash guard swallows still fails the
run, because the guard writes a recognisable line into the log.

Usage:  xvfb-run -a python ANDROID/tests/smoke_app.py [screenshot_dir]
Exit code 0 means the app booted, processed files correctly and shut down.
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ANDROID_DIR = os.path.dirname(HERE)
sys.path.insert(0, ANDROID_DIR)
sys.path.insert(0, HERE)

os.environ.setdefault('KIVY_NO_ARGS', '1')
os.environ.setdefault('KIVY_NO_CONSOLELOG', '0')

SHOT_DIR = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()

# Set these to run the script a second time against the same state and source,
# which is how the "settings survive a restart" requirement gets tested.
STATE_DIR = os.environ.get('DESQUEEZE_STATE_DIR') or ''
FIXED_SOURCE = os.environ.get('DESQUEEZE_SOURCE_DIR') or ''
FIXED_OUT = os.environ.get('DESQUEEZE_OUT_DIR') or ''
EXPECT_RESTORE = os.environ.get('DESQUEEZE_EXPECT_RESTORE') == '1'

FAILURES = []
NOTES = []


def check(condition, message):
    if condition:
        NOTES.append('ok   %s' % message)
    else:
        FAILURES.append(message)


def main():
    import tiff_builder
    import dng_engine
    from kivy.clock import Clock
    from kivy.core.window import Window

    import main as app_module
    from storage import LocalFileSelection, LocalFolder

    work = tempfile.mkdtemp(prefix='desqueeze-smoke-')
    source_dir = FIXED_SOURCE or os.path.join(work, 'A001_CLIP')
    os.makedirs(source_dir, exist_ok=True)
    for index in range(3):
        variant = 'standard' if index % 2 else 'cinemadng'
        with open(os.path.join(source_dir, 'A001_%04d.dng' % index), 'wb') as handle:
            handle.write(tiff_builder.make_variant(variant))
    # one of each rendered format, which take the resize path instead
    from PIL import Image
    for name, image_format in (('still.png', 'PNG'), ('grab.jpg', 'JPEG'),
                               ('scan.tif', 'TIFF')):
        Image.new('RGB', (200, 100), (200, 140, 40)).save(
            os.path.join(source_dir, name), image_format)
    # a file that is not an image must be ignored, and a corrupt one must be
    # reported without stopping the batch
    with open(os.path.join(source_dir, 'notes.txt'), 'w') as handle:
        handle.write('ignore me')
    with open(os.path.join(source_dir, 'broken.dng'), 'wb') as handle:
        handle.write(b'II*\x00garbage')

    selected = sorted(os.path.join(source_dir, entry)
                      for entry in os.listdir(source_dir)
                      if not os.path.isdir(os.path.join(source_dir, entry)))
    out_dir = FIXED_OUT or os.path.join(work, 'RENDERS')
    os.makedirs(out_dir, exist_ok=True)

    state = {'index': 0, 'ticks': 0}
    app = app_module.DesqueezeApp()
    if STATE_DIR:
        os.makedirs(STATE_DIR, exist_ok=True)
        app._state_dir = lambda: STATE_DIR      # keep runs isolated / repeatable
    steps = []

    def step(function):
        steps.append(function)
        return function

    @step
    def _boot(_app):
        check(_app.root is not None, 'root widget built')
        check(_app.engine_ready, 'engine reported ready')
        check(len(_app.preset_labels) == 10, 'all 10 lens presets listed')
        if EXPECT_RESTORE:
            check(_app.dest_text.startswith(out_dir),
                  'output folder restored from the previous launch')
            check(_app.preset_label == _app.preset_labels[0],
                  'lens preset restored from the previous launch')
            check(_app.dest_folder_name == 'desqueezed',
                  'output subfolder name restored from the previous launch')
            check(_app.source_text == 'No files selected',
                  'file selection is deliberately not restored')

    @step
    def _pick_source(_app):
        # _picked() is @mainthread, so it lands on the next frame -- exactly
        # how it behaves when the SAF callback fires off the UI thread.
        _app._picked('source', LocalFileSelection(selected), None)
        _app._picked('dest', LocalFolder(out_dir), None)

    @step
    def _pick_source_applied(_app):
        if _app.source_text == 'No files selected' and state['ticks'] < 60:
            return 'again'          # @mainthread lands a frame later
        check(_app.source_text == '%d files selected' % len(selected),
              'selected file count shown')
        check(_app.dest_text == os.path.join(out_dir, 'desqueezed'),
              'output path shows the chosen folder plus the subfolder')

    @step
    def _choose_preset(_app):
        _app.select_preset(_app.preset_labels[4])       # 2.0x Panavision
        check(not _app.is_custom, 'custom row hidden for a normal preset')
        _app.select_preset(_app.preset_labels[9])       # Custom...
        check(_app.is_custom, 'custom row shown for Custom...')
        _app.custom_x, _app.custom_y = 'x', '1.0'
        _app.start_run()
        check(not _app.is_running, 'invalid custom ratio refuses to run')
        _app.select_preset(_app.preset_labels[0])       # 1.33x Canon
        check(_app._current_scale() == ('1.33', '1.0'), 'preset ratio resolved')

    @step
    def _run(_app):
        _app.start_run()
        check(_app.is_running, 'batch started')

    @step
    def _wait(_app):
        if _app.is_running:
            return 'again'

    @step
    def _verify(_app):
        from PIL import Image
        written = os.path.join(out_dir, 'desqueezed')
        check(os.path.isdir(written), 'output subfolder created')
        produced = sorted(os.listdir(written))
        check(produced == ['A001_0000.dng', 'A001_0001.dng', 'A001_0002.dng',
                           'grab.jpg', 'scan.tif', 'still.png'],
              'every supported file written; .txt and corrupt .dng skipped')

        for name in [n for n in produced if n.endswith('.dng')]:
            with open(os.path.join(written, name), 'rb') as handle:
                values = dng_engine.read_default_scale(handle.read())
            check(values and values[-1][1] == (133, 100),
                  '%s tagged DefaultScale 133/100' % name)

        for name in ('still.png', 'grab.jpg', 'scan.tif'):
            with Image.open(os.path.join(written, name)) as image:
                check(image.size == (266, 100),
                      '%s pixels stretched 200x100 -> 266x100' % name)

        for name in ('still.png', 'grab.jpg', 'scan.tif'):
            with Image.open(os.path.join(source_dir, name)) as image:
                check(image.size == (200, 100), '%s original untouched' % name)
        check(len(os.listdir(source_dir)) == 8,
              'nothing added to or removed from the source folder')
        check(any('broken.dng' in line for line in _app._log),
              'corrupt file reported in the log')
        check(_app.progress_value == 100, 'progress finished at 100%')

    @step
    def _layout(_app):
        Window.size = (1280, 800)

    @step
    def _wide(_app):
        check(_app.wide, 'wide layout engaged past the breakpoint')
        _shoot(_app, 'wide')

    @step
    def _narrow(_app):
        Window.size = (420, 880)

    @step
    def _narrow_check(_app):
        check(not _app.wide, 'narrow layout restored')
        _shoot(_app, 'narrow')

    @step
    def _persistence(_app):
        _app._save_state()
        from appstate import AppState
        stored = AppState(_app._state_dir()).load()
        check(stored['dest_key'] == out_dir, 'output folder persisted')
        check(stored['dest_folder_name'] == 'desqueezed', 'subfolder persisted')
        check(stored['preset_index'] == 0, 'preset persisted')

    @step
    def _no_internal_errors(_app):
        bad = [line for line in _app._log if 'internal error' in line]
        check(not bad, 'no exceptions swallowed by the crash guard: %s' % bad)

    def _shoot(_app, tag):
        try:
            Window.screenshot(name=os.path.join(SHOT_DIR, 'desqueeze-%s.png' % tag))
        except Exception as exc:
            NOTES.append('note screenshot failed (%s)' % exc)

    def pump(_dt):
        state['ticks'] += 1
        if state['ticks'] > 900:            # ~15 s guard against a hung run
            FAILURES.append('timed out waiting for the app')
            app.stop()
            return False
        if state['index'] >= len(steps):
            app.stop()
            return False
        try:
            result = steps[state['index']](app)
        except Exception:
            FAILURES.append('step %s raised:\n%s'
                            % (steps[state['index']].__name__, traceback.format_exc()))
            app.stop()
            return False
        if result != 'again':
            state['index'] += 1
        return True

    Clock.schedule_interval(pump, 1 / 60.0)
    app.run()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        FAILURES.append('app crashed:\n%s' % traceback.format_exc())

    print()
    for note in NOTES:
        print('  ' + note)
    if FAILURES:
        print('\nSMOKE TEST FAILED (%d):' % len(FAILURES))
        for failure in FAILURES:
            print('  X  %s' % failure)
        sys.exit(1)
    print('\nSMOKE TEST PASSED (%d checks), screenshots in %s' % (len(NOTES), SHOT_DIR))
