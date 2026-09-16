#!/usr/bin/env python3
"""Headless end-to-end drive of the real Kivy app.

This is the "it must not crash when you open it" check, run on every build: the
actual ``DesqueezeApp`` is started, driven through complete batches on real
files in both tabs, resized across the layout breakpoint, and screenshotted --
all inside a virtual X server.  Any exception the in-app crash guard swallows
still fails the run, because the guard writes a recognisable line into the log.

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


def build_fixtures(source_dir):
    """A folder holding one of everything the app claims to accept."""
    import mp4_builder
    import tiff_builder
    from PIL import Image

    os.makedirs(source_dir, exist_ok=True)
    for index in range(2):
        variant = 'standard' if index % 2 else 'cinemadng'
        with open(os.path.join(source_dir, 'A001_%04d.dng' % index), 'wb') as handle:
            handle.write(tiff_builder.make_variant(variant))
    # Sony and Nikon raw are TIFF-based too, so the same fixtures stand in
    with open(os.path.join(source_dir, 'SONY_0001.arw'), 'wb') as handle:
        handle.write(tiff_builder.make_variant('standard'))
    for name, image_format in (('still.png', 'PNG'), ('grab.jpg', 'JPEG'),
                               ('scan.tif', 'TIFF')):
        Image.new('RGB', (200, 100), (200, 140, 40)).save(
            os.path.join(source_dir, name), image_format)

    for name, kwargs in (('clip_a.mp4', {'moov_first': True}),
                         ('clip_b.mov', {'moov_first': False})):
        data, _offsets = mp4_builder.build_mp4(**kwargs)
        with open(os.path.join(source_dir, name), 'wb') as handle:
            handle.write(data)

    # a file that is not an image must be ignored, and corrupt ones must be
    # reported without stopping the batch
    with open(os.path.join(source_dir, 'notes.txt'), 'w') as handle:
        handle.write('ignore me')
    with open(os.path.join(source_dir, 'broken.dng'), 'wb') as handle:
        handle.write(b'II*\x00garbage')
    with open(os.path.join(source_dir, 'broken.mp4'), 'wb') as handle:
        handle.write(b'\x00\x00\x00\x18ftypnope' + b'\x00' * 32)


def main():
    import dng_engine
    import mp4_engine
    from kivy.clock import Clock
    from kivy.core.window import Window
    from PIL import Image

    import main as app_module
    from storage import LocalFileSelection, LocalFolder

    work = tempfile.mkdtemp(prefix='desqueeze-smoke-')
    source_dir = FIXED_SOURCE or os.path.join(work, 'A001_CLIP')
    build_fixtures(source_dir)
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

    def written_dir():
        return os.path.join(out_dir, app.dest_folder_name)

    # --- startup

    @step
    def _boot(_app):
        check(_app.root is not None, 'root widget built')
        check(_app.engine_ready, 'engine reported ready')
        check(len(_app.preset_labels) == 10, 'all 10 lens presets listed')
        check(not _app.log_open, 'log starts collapsed')
        if EXPECT_RESTORE:
            check(_app.dest_text.startswith(out_dir),
                  'output folder restored from the previous launch')
            check(_app.dest_folder_name == 'desqueezed',
                  'output subfolder restored from the previous launch')
            check(_app.source_text == 'No files selected',
                  'file selection is deliberately not restored')

    # --- photo tab

    @step
    def _photo_tab(_app):
        _app.select_media('photo')
        check(_app.media == 'photo', 'photo tab active')
        _app._picked('source', 'photo', LocalFileSelection(selected), None)
        _app._picked('dest', 'photo', LocalFolder(out_dir), None)

    @step
    def _photo_selection_applied(_app):
        if _app.source_text == 'No files selected' and state['ticks'] < 60:
            return 'again'          # @mainthread lands a frame later
        check(_app.source_text.endswith('files selected'), 'selection shown')
        check(len(_app.queue_rows) == 7,
              'queue lists only the 7 photos, not the videos or the .txt')
        check(_app.dest_text == os.path.join(out_dir, 'desqueezed'),
              'output path shows the chosen folder plus the subfolder')

    @step
    def _photo_presets(_app):
        _app.select_preset(_app.preset_labels[9])       # Custom...
        check(_app.is_custom, 'custom row shown for Custom...')
        _app.custom_x, _app.custom_y = 'x', '1.0'
        _app.start_run()
        check(not _app.is_running, 'invalid custom ratio refuses to run')
        _app.select_preset(_app.preset_labels[0])       # 1.33x Canon
        check(_app._current_scale() == ('1.33', '1.0'), 'preset ratio resolved')

    @step
    def _photo_run(_app):
        _app.start_run()
        check(_app.is_running, 'photo batch started')

    @step
    def _photo_wait(_app):
        if _app.is_running:
            return 'again'

    @step
    def _photo_verify(_app):
        import formats
        written = written_dir()
        # filter by kind: a second run finds the previous run's videos here too
        produced = sorted(name for name in os.listdir(written)
                          if formats.is_photo(name))
        check(produced == ['A001_0000.dng', 'A001_0001.dng', 'SONY_0001.arw',
                           'grab.jpg', 'scan.tif', 'still.png'],
              'every supported photo written; video, .txt and corrupt skipped: %r'
              % produced)

        for name in ('A001_0000.dng', 'A001_0001.dng', 'SONY_0001.arw'):
            with open(os.path.join(written, name), 'rb') as handle:
                values = dng_engine.read_default_scale(handle.read())
            check(values and values[-1][1] == (133, 100),
                  '%s tagged DefaultScale 133/100' % name)

        for name in ('still.png', 'grab.jpg', 'scan.tif'):
            with Image.open(os.path.join(written, name)) as image:
                check(image.size == (266, 100),
                      '%s pixels stretched 200x100 -> 266x100' % name)

        check(any('broken.dng' in line for line in _app._log),
              'corrupt photo reported in the log')
        statuses = [row['status'] for row in _app.queue_rows]
        check(statuses.count('done') == 6 and statuses.count('failed') == 1,
              'queue shows 6 done and 1 failed, got %r' % statuses)
        check(all(row['percent'] == 100 for row in _app.queue_rows
                  if row['status'] == 'done'),
              'finished rows show a full progress bar')
        check(_app.progress_value == 100, 'overall progress finished at 100%')
        _shoot(_app, 'photos')

    # --- video tab

    @step
    def _video_tab(_app):
        _app.select_media('video')
        check(_app.media == 'video', 'video tab active')
        check(_app.source_text == 'No files selected',
              'video tab has its own, separate selection')
        _app._picked('source', 'video', LocalFileSelection(selected), None)

    @step
    def _video_selection_applied(_app):
        if _app.source_text == 'No files selected' and state['ticks'] < 200:
            return 'again'
        check(len(_app.queue_rows) == 3,
              'queue lists only the 3 videos, got %d' % len(_app.queue_rows))
        check(_app._dest is not None,
              'video tab inherited the output folder already chosen')

    @step
    def _video_run(_app):
        check(not _app.transcode, 're-encode is off by default')
        _app.start_run()
        check(_app.is_running, 'video batch started')

    @step
    def _video_wait(_app):
        if _app.is_running:
            return 'again'

    @step
    def _video_verify(_app):
        written = written_dir()
        produced = sorted(name for name in os.listdir(written)
                          if name.endswith(('.mp4', '.mov')))
        check(produced == ['clip_a.mp4', 'clip_b.mov'],
              'both good clips written, no stub left for the corrupt one: %r'
              % produced)

        for name in ('clip_a.mp4', 'clip_b.mov'):
            with open(os.path.join(written, name), 'rb') as handle:
                data = handle.read()
            check(mp4_engine.read_pixel_aspect(data) == [('avc1', 133, 100)],
                  '%s carries pasp 133:100' % name)
            width, _height = mp4_engine.read_track_display_size(data)[0]
            check(abs(width - 1920 * 1.33) < 2,
                  '%s display width stretched to %.0f' % (name, width))

        statuses = [row['status'] for row in _app.queue_rows]
        check(statuses.count('done') == 2 and statuses.count('failed') == 1,
              'queue shows 2 done and 1 failed, got %r' % statuses)
        _shoot(_app, 'video')

    @step
    def _video_transcode_is_refused_off_device(_app):
        """Without an encoder the toggle must explain, not crash."""
        _app.transcode = True
        _app.start_run()
        check(not _app.is_running,
              're-encode refused cleanly when no encoder is present')
        check(any('cannot re-encode' in line for line in _app._log),
              'the refusal was explained in the log')
        _app.transcode = False

    # --- originals, layout, persistence

    @step
    def _originals_untouched(_app):
        for name in ('still.png', 'grab.jpg', 'scan.tif'):
            with Image.open(os.path.join(source_dir, name)) as image:
                check(image.size == (200, 100), '%s original untouched' % name)
        with open(os.path.join(source_dir, 'clip_a.mp4'), 'rb') as handle:
            check(mp4_engine.read_pixel_aspect(handle.read()) == [],
                  'original clip left without a pasp atom')
        check(len(os.listdir(source_dir)) == 11,
              'nothing added to or removed from the source folder')

    @step
    def _log_toggles(_app):
        _app.toggle_log()
        check(_app.log_open, 'log expands when tapped')
        check(_app.log_summary, 'log strip shows the latest line')
        _shoot(_app, 'log-open')
        _app.toggle_log()
        check(not _app.log_open, 'log collapses again')

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
        check(stored['dest_key_photo'] == out_dir, 'photo output folder persisted')
        check(stored['dest_key_video'] == out_dir, 'video output folder persisted')
        check(stored['media'] == 'video', 'active tab persisted')
        check(stored['preset_index_photo'] == 0, 'photo preset persisted')

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
        if state['ticks'] > 1800:           # ~30 s guard against a hung run
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
