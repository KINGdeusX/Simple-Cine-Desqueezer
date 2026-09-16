#!/usr/bin/env python3
"""Headless end-to-end drive of the real PyQt6 application.

The same check the Android build gets: start the actual window, run complete
batches on real files through both tabs, and screenshot the result -- all inside
a virtual X server.  It cannot prove the Windows executable works, but it does
prove the application logic, the engines and the bundled binaries do.

Usage:  xvfb-run -a python PC/tests/smoke_app.py [screenshot_dir]
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
PC_DIR = os.path.dirname(HERE)
sys.path.insert(0, PC_DIR)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(PC_DIR), 'ANDROID', 'tests'))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

SHOT_DIR = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
STATE_DIR = os.environ.get('DESQUEEZE_STATE_DIR') or tempfile.mkdtemp()

FAILURES = []
NOTES = []


def check(condition, message):
    if condition:
        NOTES.append('ok   %s' % message)
    else:
        FAILURES.append(message)


def build_fixtures(folder):
    """One of everything the PC app claims to accept."""
    import mp4_builder
    import tiff_builder
    from PIL import Image
    import subprocess
    import toolbox

    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, 'A001_0001.dng'), 'wb') as handle:
        handle.write(tiff_builder.make_variant('standard'))
    with open(os.path.join(folder, 'A001_0002.dng'), 'wb') as handle:
        handle.write(tiff_builder.make_variant('cinemadng'))
    Image.new('RGB', (1920, 1080), (200, 140, 40)).save(
        os.path.join(folder, 'still.jpg'), 'JPEG', quality=92)
    Image.new('RGB', (1280, 720), (40, 90, 160)).save(
        os.path.join(folder, 'grab.png'), 'PNG')
    with open(os.path.join(folder, 'notes.txt'), 'w') as handle:
        handle.write('ignore me')
    with open(os.path.join(folder, 'broken.dng'), 'wb') as handle:
        handle.write(b'II*\x00garbage')

    # A genuine H.264 + AAC clip, which is what the video tab is for.
    clip = os.path.join(folder, 'B001_take.mp4')
    if toolbox.FFMPEG.available:
        subprocess.run(toolbox.FFMPEG.command(
            '-v', 'error', '-f', 'lavfi',
            '-i', 'testsrc2=size=1280x720:rate=24:duration=2',
            '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-movflags', '+faststart', clip, '-y'), check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        data, _ = mp4_builder.build_mp4(moov_first=True)
        with open(clip, 'wb') as handle:
            handle.write(data)
    with open(os.path.join(folder, 'broken.mp4'), 'wb') as handle:
        handle.write(b'\x00\x00\x00\x18ftypnope' + b'\x00' * 32)


def main():
    import formats
    import photo_engine
    import toolbox
    from PIL import Image
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QEventLoop, QTimer

    import app as app_module
    import appstate

    work = tempfile.mkdtemp(prefix='desqueeze-pc-')
    source_dir = os.path.join(work, 'CLIP')
    build_fixtures(source_dir)
    out_dir = os.path.join(work, 'RENDERS')

    application = QApplication.instance() or QApplication([])
    appstate.settings_directory = lambda: STATE_DIR
    window = app_module.DesqueezeWindow()
    window._state = appstate.AppState(STATE_DIR)
    window.show()

    def pump(seconds=0.4):
        """Let the timer drain events, as it does for a real user."""
        loop = QEventLoop()
        QTimer.singleShot(int(seconds * 1000), loop.quit)
        loop.exec()

    def wait_for_idle(limit=240):
        for _ in range(int(limit / 0.2)):
            pump(0.2)
            if window.run_button.isEnabled():
                return True
        return False

    def files_in(folder, predicate):
        if not os.path.isdir(folder):
            return []
        return sorted(name for name in os.listdir(folder) if predicate(name))

    def shoot(tag):
        try:
            window.grab().save(os.path.join(SHOT_DIR, 'desqueeze-pc-%s.png' % tag))
        except Exception as exc:
            NOTES.append('note screenshot failed (%s)' % exc)

    # --- startup
    check(window.isVisible(), 'main window built')
    check(window.tabs.count() == 2, 'two tabs present')
    check(not window.log_box.isVisible(), 'log starts collapsed')
    check(toolbox.EXIFTOOL.available, 'bundled ExifTool resolved')
    check(toolbox.FFMPEG.available, 'bundled FFmpeg resolved')
    check(window.photo_tab.preset.count() == 10, 'all 10 lens presets listed')

    # --- photos, tag mode, keep original format
    tab = window.photo_tab
    window.tabs.setCurrentIndex(0)
    tab.files = [os.path.join(source_dir, name)
                 for name in sorted(os.listdir(source_dir))]
    tab._refresh_files()
    tab.set_output(out_dir)
    tab.preset.setCurrentIndex(0)                    # 1.33x
    tab.mode.setCurrentIndex(0)                      # tag
    tab.output_format.setCurrentIndex(0)             # original
    check(window.queue.rowCount() == 5,
          'queue lists only the 5 photos, got %d' % window.queue.rowCount())

    window.start_run()
    check(wait_for_idle(), 'photo batch finished')
    produced = files_in(out_dir, lambda n: n.lower().endswith('.dng'))
    check(produced == ['A001_0001.dng', 'A001_0002.dng'],
          'both good DNGs written, corrupt one skipped: %r' % produced)
    for name in produced:
        value = photo_engine.read_default_scale(os.path.join(out_dir, name))
        check(value == '1.33 1', '%s tagged DefaultScale 1.33 1 (got %r)'
              % (name, value))

    stretched = files_in(out_dir, lambda n: n.lower().endswith(('.jpg', '.png')))
    check(stretched == ['grab.png', 'still.jpg'],
          'JPEG and PNG were stretched instead of tagged: %r' % stretched)
    with Image.open(os.path.join(out_dir, 'still.jpg')) as image:
        check(image.size == (2554, 1080),
              'still.jpg stretched to %s (expected 2554x1080)' % (image.size,))
    shoot('photos-tag')

    # --- photos, stretch mode into JPEG
    jpeg_dir = os.path.join(work, 'JPEGS')
    tab.set_output(jpeg_dir)
    tab.mode.setCurrentIndex(1)                      # stretch
    tab.output_format.setCurrentIndex(2)             # JPEG
    tab.jpeg_quality.setValue(90)
    window.start_run()
    check(wait_for_idle(), 'stretch-to-JPEG batch finished')
    jpegs = files_in(jpeg_dir, lambda n: n.lower().endswith('.jpg'))
    check('still.jpg' in jpegs and 'grab.jpg' in jpegs,
          'rendered JPEGs written for the already-rendered sources: %r' % jpegs)
    if 'grab.jpg' in jpegs:
        with Image.open(os.path.join(jpeg_dir, 'grab.jpg')) as image:
            check(image.size == (1702, 720),
                  'grab.png converted and stretched to %s' % (image.size,))

    # --- the impossible pairing is refused before any file is touched
    tab.output_format.setCurrentIndex(1)             # DNG, while stretching
    before = len(window._log)
    window.start_run()
    check(window.run_button.isEnabled(), 'stretch+DNG refused without running')
    check(any('cannot be saved as raw DNG' in line
              for line in window._log[before:]),
          'the refusal explained why')
    tab.mode.setCurrentIndex(0)                      # back to tag

    # --- video, metadata route
    window.tabs.setCurrentIndex(1)
    video = window.video_tab
    video.files = [os.path.join(source_dir, 'B001_take.mp4'),
                   os.path.join(source_dir, 'broken.mp4')]
    video._refresh_files()
    video_out = os.path.join(work, 'VIDEO')
    video.set_output(video_out)
    video.preset.setCurrentIndex(0)
    video.transcode.setChecked(False)
    check(window.queue.rowCount() == 2, 'video queue lists the 2 clips')

    window.start_run()
    check(wait_for_idle(), 'video metadata batch finished')
    clips = files_in(video_out, lambda n: n.lower().endswith('.mp4'))
    check(clips == ['B001_take.mp4'],
          'good clip written, no stub for the corrupt one: %r' % clips)
    if clips:
        import mp4_engine
        with open(os.path.join(video_out, clips[0]), 'rb') as handle:
            data = handle.read()
        check(mp4_engine.read_pixel_aspect(data) == [('avc1', 133, 100)],
              'clip carries pasp 133:100')
    shoot('video-meta')

    # --- video, re-encode route
    if toolbox.FFMPEG.available:
        encode_out = os.path.join(work, 'ENCODED')
        video.files = [os.path.join(source_dir, 'B001_take.mp4')]
        video._refresh_files()
        video.set_output(encode_out)
        video.transcode.setChecked(True)
        video.codec.setCurrentIndex(0)               # HEVC
        video.bitrate.setValue(6)
        video.preset_speed.setCurrentText('ultrafast')
        window.start_run()
        check(wait_for_idle(300), 're-encode batch finished')
        encoded = files_in(encode_out, lambda n: n.lower().endswith('.mp4'))
        check(encoded == ['B001_take.mp4'], 're-encoded clip written')
        if encoded:
            import video_engine
            info = video_engine.probe(os.path.join(encode_out, encoded[0]))
            check(info['width'] == 1702 and info['height'] == 720,
                  're-encoded to %dx%d (expected 1702x720)'
                  % (info['width'], info['height']))
            check(info['codec'] == 'hevc', 'output is HEVC')
            check(info['has_audio'], 'audio survived the re-encode')
        shoot('video-encode')

    # --- log, originals, persistence
    window.toggle_log()
    check(window.log_box.isVisible(), 'log expands when clicked')
    window.toggle_log()
    check(not window.log_box.isVisible(), 'log collapses again')

    with Image.open(os.path.join(source_dir, 'still.jpg')) as image:
        check(image.size == (1920, 1080), 'original still.jpg untouched')
    check(len(os.listdir(source_dir)) == 8,
          'nothing added to or removed from the source folder (found %d)'
          % len(os.listdir(source_dir)))

    window.save_state()
    stored = appstate.AppState(STATE_DIR).load()
    check(stored['output_video'] == video.output_dir, 'video output persisted')
    check(stored['preset_photo'] == 0, 'photo preset persisted')

    window.close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        FAILURES.append('app crashed:\n%s' % traceback.format_exc())

    print()
    for note in NOTES:
        print('  ' + note)
    if FAILURES:
        print('\nPC SMOKE TEST FAILED (%d):' % len(FAILURES))
        for failure in FAILURES:
            print('  X  %s' % failure)
        sys.exit(1)
    print('\nPC SMOKE TEST PASSED (%d checks), screenshots in %s'
          % (len(NOTES), SHOT_DIR))
