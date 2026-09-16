"""
Desqueeze for Windows -- Anamorphic Photo & Video Processor.

The PC counterpart of the Android build, and the successor to the original
``legacy_app.py``.  Two things it does that the old one did not:

* **Nothing to find.**  ExifTool, FFmpeg and DNGLab ship with the application
  and are resolved once at startup by ``toolbox``.  There is no ExifTool path
  box, because there is nothing to browse for.
* **Photos and video are separate tabs**, each remembering its own files,
  output folder and settings, with a queue table showing every file's own
  progress and a log collapsed out of the way.

The batch runs on a worker thread and never touches a widget: it pushes events
onto a queue that a timer drains on the GUI thread, which is the same split the
original used with QThread and pyqtSignal.
"""

from __future__ import annotations

import os
import sys
import threading
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtCore import Qt, QTimer                                # noqa: E402
from PyQt6.QtGui import QFont, QIcon                               # noqa: E402
from PyQt6.QtWidgets import (                                      # noqa: E402
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QProgressBar, QPushButton, QSizePolicy, QSpinBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)

import engine as engine_module                                     # noqa: E402
import formats                                                     # noqa: E402
import toolbox                                                     # noqa: E402
from appstate import AppState                                      # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
APP_NAME = 'Desqueeze'
VERSION = '2.0'

PHOTO, VIDEO = 'photo', 'video'
DRAIN_MS = 120
MAX_LOG_LINES = 2000

GOLD = '#f0a500'
BACKGROUND = '#141414'


def icon_path():
    for name in ('icon.ico', 'icon.png'):
        for folder in (HERE, os.path.dirname(HERE),
                       getattr(sys, '_MEIPASS', HERE)):
            candidate = os.path.join(folder, name)
            if os.path.exists(candidate):
                return candidate
    return None


class MediaTab(QWidget):
    """One of the two tabs.  Photos and video differ only in their options."""

    def __init__(self, window, media):
        super().__init__()
        self.window = window
        self.media = media
        self.files = []
        self.output_dir = ''

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(9)

        layout.addWidget(section('SOURCE FILES  (%s)'
                                 % formats.describe_support(media)))
        row = QHBoxLayout()
        self.files_label = QLineEdit()
        self.files_label.setReadOnly(True)
        self.files_label.setPlaceholderText('No files selected')
        add = QPushButton('Add Files...')
        add.setMinimumWidth(130)
        add.clicked.connect(self.choose_files)
        clear = QPushButton('Clear')
        clear.setMinimumWidth(80)
        clear.clicked.connect(self.clear_files)
        row.addWidget(self.files_label)
        row.addWidget(add)
        row.addWidget(clear)
        layout.addLayout(row)

        layout.addWidget(section('OUTPUT FOLDER  (remembered)'))
        row = QHBoxLayout()
        self.output_label = QLineEdit()
        self.output_label.setReadOnly(True)
        self.output_label.setPlaceholderText('Choose where results are written')
        browse = QPushButton('Browse')
        browse.setMinimumWidth(100)
        browse.clicked.connect(self.choose_output)
        row.addWidget(self.output_label)
        row.addWidget(browse)
        layout.addLayout(row)

        layout.addWidget(section('SQUEEZE RATIO  -  Lens Preset'))
        self.preset = QComboBox()
        for label, _x, _y in engine_module.LENS_PRESETS:
            self.preset.addItem(label)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        layout.addWidget(self.preset)

        self.custom_row = QWidget()
        custom = QHBoxLayout(self.custom_row)
        custom.setContentsMargins(0, 0, 0, 0)
        custom.addWidget(QLabel('Squeeze X:'))
        self.custom_x = QLineEdit('1.33')
        self.custom_x.setFixedWidth(70)
        custom.addWidget(self.custom_x)
        custom.addWidget(QLabel('  Y:'))
        self.custom_y = QLineEdit('1.0')
        self.custom_y.setFixedWidth(70)
        custom.addWidget(self.custom_y)
        custom.addStretch()
        self.custom_row.setVisible(False)
        layout.addWidget(self.custom_row)

        layout.addWidget(divider())
        if media == PHOTO:
            self._build_photo_options(layout)
        else:
            self._build_video_options(layout)
        layout.addStretch()

    # --- options ------------------------------------------------------------

    def _build_photo_options(self, layout):
        layout.addWidget(section('WHAT TO DO'))
        row = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItem('Tag only - write DefaultScale, pixels untouched',
                          formats.MODE_TAG)
        self.mode.addItem('Stretch - actually resample the picture',
                          formats.MODE_STRETCH)
        self.mode.currentIndexChanged.connect(self._options_changed)
        row.addWidget(self.mode, 1)
        layout.addLayout(row)

        layout.addWidget(section('OUTPUT FORMAT'))
        row = QHBoxLayout()
        self.output_format = QComboBox()
        self.output_format.addItem('Keep original format', formats.OUT_ORIGINAL)
        self.output_format.addItem('DNG (raw sources)', formats.OUT_DNG)
        self.output_format.addItem('JPEG', formats.OUT_JPEG)
        self.output_format.currentIndexChanged.connect(self._options_changed)
        row.addWidget(self.output_format, 1)

        self.dng_compressed = QCheckBox('Lossless compression')
        self.dng_compressed.setChecked(True)
        row.addWidget(self.dng_compressed)

        self.jpeg_quality_label = QLabel('JPEG quality:')
        row.addWidget(self.jpeg_quality_label)
        self.jpeg_quality = QSpinBox()
        self.jpeg_quality.setRange(1, 100)
        self.jpeg_quality.setValue(95)
        self.jpeg_quality.setFixedWidth(64)
        row.addWidget(self.jpeg_quality)
        layout.addLayout(row)

        self.hint = QLabel()
        self.hint.setObjectName('hint')
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self._options_changed()

    def _build_video_options(self, layout):
        layout.addWidget(section('OUTPUT METHOD'))
        self.transcode = QCheckBox('Re-encode the video (real stretched pixels)')
        self.transcode.stateChanged.connect(self._options_changed)
        layout.addWidget(self.transcode)

        self.hint = QLabel()
        self.hint.setObjectName('hint')
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        self.encoder_row = QWidget()
        row = QHBoxLayout(self.encoder_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel('Codec:'))
        self.codec = QComboBox()
        self.codec.addItem('H.265 / HEVC', 'hevc')
        self.codec.addItem('H.264 / AVC', 'h264')
        self.codec.setFixedWidth(130)
        row.addWidget(self.codec)
        row.addWidget(QLabel('  Bitrate:'))
        self.bitrate = QSpinBox()
        self.bitrate.setRange(1, 400)
        self.bitrate.setValue(20)
        self.bitrate.setSuffix(' Mbps')
        self.bitrate.setFixedWidth(100)
        row.addWidget(self.bitrate)
        row.addWidget(QLabel('  Rate:'))
        self.rate_mode = QComboBox()
        self.rate_mode.addItem('VBR', 'vbr')
        self.rate_mode.addItem('CBR', 'cbr')
        self.rate_mode.setFixedWidth(80)
        row.addWidget(self.rate_mode)
        row.addWidget(QLabel('  Preset:'))
        self.preset_speed = QComboBox()
        for name in ('ultrafast', 'veryfast', 'fast', 'medium', 'slow'):
            self.preset_speed.addItem(name)
        self.preset_speed.setCurrentText('medium')
        self.preset_speed.setFixedWidth(110)
        row.addWidget(self.preset_speed)
        row.addStretch()
        layout.addWidget(self.encoder_row)

        self.keep_audio = QCheckBox('Keep the audio track')
        self.keep_audio.setChecked(True)
        layout.addWidget(self.keep_audio)
        self._options_changed()

    def _preset_changed(self, index):
        self.custom_row.setVisible(
            engine_module.LENS_PRESETS[index][1] is None)

    def _options_changed(self, *_args):
        if self.media == PHOTO:
            output = self.output_format.currentData()
            stretching = self.mode.currentData() == formats.MODE_STRETCH
            self.dng_compressed.setVisible(output == formats.OUT_DNG)
            shows_quality = output == formats.OUT_JPEG or stretching
            self.jpeg_quality.setVisible(shows_quality)
            self.jpeg_quality_label.setVisible(shows_quality)
            if stretching and output == formats.OUT_DNG:
                self.hint.setText(
                    'A stretched picture is no longer raw data, so it cannot be '
                    'written as DNG. Choose JPEG, or switch back to Tag only.')
            elif stretching:
                self.hint.setText(
                    'Raw files are developed with LibRaw and written as JPEG - '
                    'a preview, not a substitute for developing the raw '
                    'properly in your own converter.')
            elif output == formats.OUT_DNG:
                self.hint.setText(
                    'Proprietary raw is converted to DNG by DNGLab, then tagged. '
                    'The originals are left alone.')
            elif output == formats.OUT_JPEG:
                self.hint.setText(
                    'Raw is developed and stretched; JPEGs are stretched. '
                    'Nothing reads DefaultScale from a JPEG, so tagging one '
                    'would have no effect.')
            else:
                self.hint.setText(
                    'Raw keeps its own format with DefaultScale written into it '
                    '- lossless, and read by Lightroom, Capture One and Resolve. '
                    'JPEG and PNG are stretched instead, since the tag does '
                    'nothing for them.')
        else:
            transcoding = self.transcode.isChecked()
            self.encoder_row.setVisible(transcoding)
            self.keep_audio.setVisible(transcoding)
            if not toolbox.FFMPEG.available:
                self.transcode.setEnabled(False)
                self.transcode.setChecked(False)
                self.hint.setText('FFmpeg is missing from this installation, so '
                                  'only the metadata route is available.')
            elif transcoding:
                self.hint.setText(
                    'The clip is re-encoded at the stretched size, so it plays '
                    'correctly in any player. Slower, and a lossy generation.')
            else:
                self.hint.setText(
                    'Writes the pasp pixel-aspect atom - instant and completely '
                    'lossless, and read by editors. Most consumer players ignore '
                    'it and will still show the clip squeezed.')

    # --- file choosing ------------------------------------------------------

    def choose_files(self):
        if self.media == VIDEO:
            spec = 'Video (*.mp4);;All files (*)'
        else:
            patterns = ' '.join('*' + e for e in formats.PHOTO_EXTENSIONS)
            spec = ('Photos (%s);;Raw (%s);;Images (*.jpg *.jpeg *.png *.tif '
                    '*.tiff);;All files (*)'
                    % (patterns,
                       ' '.join('*' + e for e in formats.RAW_EXTENSIONS)))
        chosen, _ = QFileDialog.getOpenFileNames(
            self, 'Select %s files' % self.media,
            self.files[0] if self.files else '', spec)
        if not chosen:
            return
        accepted = [path for path in chosen
                    if formats.accepts(os.path.basename(path), self.media)]
        skipped = len(chosen) - len(accepted)
        for path in accepted:
            if path not in self.files:
                self.files.append(path)
        self._refresh_files()
        if skipped:
            self.window.log('i  %d selected file(s) are not supported on this '
                            'tab and were ignored' % skipped)
        if not self.output_dir and accepted:
            self.set_output(os.path.join(os.path.dirname(accepted[0]),
                                         'desqueezed'))

    def clear_files(self):
        self.files = []
        self._refresh_files()

    def choose_output(self):
        chosen = QFileDialog.getExistingDirectory(
            self, 'Select the output folder', self.output_dir or '')
        if chosen:
            self.set_output(chosen)

    def set_output(self, path):
        self.output_dir = path
        self.output_label.setText(path)
        self.window.save_state()

    def _refresh_files(self):
        # The queue must show exactly what this tab will process, so filter
        # here rather than trusting whoever filled the list.
        self.files = [path for path in self.files
                      if formats.accepts(os.path.basename(path), self.media)]
        count = len(self.files)
        if count == 0:
            self.files_label.setText('')
        elif count == 1:
            self.files_label.setText(os.path.basename(self.files[0]))
        else:
            self.files_label.setText('%d files selected' % count)
        self.window.populate_queue(self.files)

    # --- what the engine needs ---------------------------------------------

    def scale(self):
        index = self.preset.currentIndex()
        _label, scale_x, scale_y = engine_module.LENS_PRESETS[index]
        if scale_x is None:
            scale_x = self.custom_x.text().strip()
            scale_y = self.custom_y.text().strip()
            try:
                if float(scale_x) <= 0 or float(scale_y) <= 0:
                    raise ValueError
            except ValueError:
                return None
        return scale_x, scale_y

    def settings(self):
        if self.media == PHOTO:
            return {
                'mode': self.mode.currentData(),
                'output': self.output_format.currentData(),
                'jpeg_quality': self.jpeg_quality.value(),
                'dng_compressed': self.dng_compressed.isChecked(),
            }
        return {
            'transcode': self.transcode.isChecked(),
            'codec': self.codec.currentData(),
            'mode': self.rate_mode.currentData(),
            'bitrate': self.bitrate.value() * 1_000_000,
            'preset': self.preset_speed.currentText(),
            'keep_audio': self.keep_audio.isChecked(),
        }


class DesqueezeWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle('%s %s - Anamorphic Photo & Video Processor'
                            % (APP_NAME, VERSION))
        self.resize(1080, 820)
        self.setMinimumSize(900, 700)
        path = icon_path()
        if path:
            self.setWindowIcon(QIcon(path))

        self._events = deque()
        self._engine = None
        self._worker = None
        self._log = []
        self._state = AppState()

        self._build()
        self._apply_stylesheet()
        self._restore_state()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._drain_events)
        self._timer.start(DRAIN_MS)

        self._announce_tools()

    # --- layout -------------------------------------------------------------

    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        header = QLabel('DESQUEEZE')
        header.setObjectName('header')
        root.addWidget(header)
        subtitle = QLabel('Anamorphic Photo & Video Processor  -  '
                          'ExifTool, LibRaw, DNGLab and FFmpeg included')
        subtitle.setObjectName('subheader')
        root.addWidget(subtitle)
        root.addWidget(divider())

        self.tabs = QTabWidget()
        self.photo_tab = MediaTab(self, PHOTO)
        self.video_tab = MediaTab(self, VIDEO)
        self.tabs.addTab(self.photo_tab, 'PHOTOS')
        self.tabs.addTab(self.video_tab, 'VIDEO')
        self.tabs.currentChanged.connect(self._tab_changed)
        root.addWidget(self.tabs)

        row = QHBoxLayout()
        self.run_button = QPushButton('RUN DESQUEEZE')
        self.run_button.setObjectName('runBtn')
        self.run_button.setFixedHeight(44)
        self.run_button.clicked.connect(self.start_run)
        self.cancel_button = QPushButton('CANCEL')
        self.cancel_button.setObjectName('cancelBtn')
        self.cancel_button.setFixedHeight(44)
        self.cancel_button.setFixedWidth(120)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_run)
        row.addWidget(self.run_button)
        row.addWidget(self.cancel_button)
        root.addLayout(row)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        root.addWidget(self.progress)

        root.addWidget(section('QUEUE'))
        self.queue = QTableWidget(0, 3)
        self.queue.setHorizontalHeaderLabels(['File', 'Progress', 'Result'])
        self.queue.verticalHeader().setVisible(False)
        self.queue.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self.queue.setShowGrid(False)
        header_view = self.queue.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.queue.setColumnWidth(1, 150)
        self.queue.setColumnWidth(2, 330)
        self.queue.setMinimumHeight(190)
        self.queue.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Expanding)
        root.addWidget(self.queue)

        self.log_toggle = QPushButton('>  LOG')
        self.log_toggle.setObjectName('logToggle')
        self.log_toggle.setFixedHeight(24)
        self.log_toggle.clicked.connect(self.toggle_log)
        root.addWidget(self.log_toggle)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setObjectName('logBox')
        self.log_box.setFixedHeight(200)
        self.log_box.setVisible(False)
        root.addWidget(self.log_box)

    def _tab_changed(self, _index):
        self.populate_queue(self.current_tab().files)

    def current_tab(self):
        return self.photo_tab if self.tabs.currentIndex() == 0 else self.video_tab

    def current_media(self):
        return PHOTO if self.tabs.currentIndex() == 0 else VIDEO

    # --- queue --------------------------------------------------------------

    def populate_queue(self, paths):
        self.queue.setRowCount(0)
        for path in paths:
            self._add_row(os.path.basename(path))
        self.progress.setValue(0)

    def _add_row(self, name):
        row = self.queue.rowCount()
        self.queue.insertRow(row)
        self.queue.setRowHeight(row, 26)
        self.queue.setItem(row, 0, QTableWidgetItem(name))
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setFixedHeight(10)
        self.queue.setCellWidget(row, 1, bar)
        self.queue.setItem(row, 2, QTableWidgetItem(''))

    def _update_row(self, index, status, detail, percent):
        if not 0 <= index < self.queue.rowCount():
            return
        bar = self.queue.cellWidget(index, 1)
        if bar is not None:
            bar.setValue(int(percent))
            bar.setProperty('status', status)
            bar.style().unpolish(bar)
            bar.style().polish(bar)
        cell = self.queue.item(index, 2)
        if cell is not None:
            cell.setText(detail)
        name_cell = self.queue.item(index, 0)
        if name_cell is not None:
            colours = {'working': GOLD, 'failed': '#c04040',
                       'skipped': '#666666', 'done': '#8aa06a'}
            from PyQt6.QtGui import QColor
            name_cell.setForeground(QColor(colours.get(status, '#cccccc')))
        if status == 'working':
            self.queue.scrollToItem(self.queue.item(index, 0))

    # --- running ------------------------------------------------------------

    def start_run(self):
        if self._worker is not None and self._worker.is_alive():
            return
        tab = self.current_tab()
        if not tab.files:
            self.log('!  Please add some files first.')
            return
        if not tab.output_dir:
            self.log('!  Please choose an output folder.')
            return
        scale = tab.scale()
        if scale is None:
            self.log('!  Invalid custom ratio values.')
            return

        settings = tab.settings()
        media = self.current_media()

        # Catch an impossible combination before starting rather than failing
        # once per file.
        if media == PHOTO:
            try:
                formats.resolve_output(
                    os.path.basename(tab.files[0]), settings['mode'],
                    settings['output'])
            except ValueError as exc:
                self.log('!  %s' % exc)
                return

        self._log = []
        self.log_box.clear()
        self.progress.setValue(0)
        self.populate_queue(tab.files)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)

        self._engine = engine_module.build_engine()
        self._worker = threading.Thread(
            target=self._run_worker,
            args=(self._engine, list(tab.files), tab.output_dir,
                  scale[0], scale[1], media, settings),
            daemon=True)
        self._worker.start()
        self.save_state()

    def _run_worker(self, engine, paths, output_dir, scale_x, scale_y,
                    media, settings):
        """Runs off the GUI thread; only ever appends to ``self._events``."""
        try:
            engine.process(paths, output_dir, scale_x, scale_y,
                           self._events.append, media=media, settings=settings)
        except Exception:
            import traceback
            for line in traceback.format_exc().strip().splitlines():
                self._events.append(engine_module.log(line))
            self._events.append(
                engine_module.done(False, 'Unexpected error - see the log'))

    def cancel_run(self):
        if self._engine is not None:
            self._engine.cancel()
            self.log('Cancelling after the current file...')

    def _drain_events(self):
        if not self._events:
            return
        finished = None
        while self._events:
            try:
                event = self._events.popleft()
            except IndexError:
                break
            if event.kind == 'log':
                self._append_log(event.message)
            elif event.kind == 'rows':
                pass                    # the queue is filled when files are added
            elif event.kind == 'item':
                self._update_row(event.index, event.value['status'],
                                 event.message, event.value['percent'])
            elif event.kind == 'progress':
                self.progress.setValue(event.value)
            elif event.kind == 'done':
                finished = event

        if finished is not None:
            self._append_log('')
            self._append_log('%s  %s' % ('OK' if finished.ok else 'FAILED',
                                         finished.message))
            self.progress.setValue(100 if finished.ok else 0)
            self.run_button.setEnabled(True)
            self.cancel_button.setEnabled(False)

    # --- log ----------------------------------------------------------------

    def toggle_log(self):
        showing = not self.log_box.isVisible()
        self.log_box.setVisible(showing)
        self.log_toggle.setText(('v  LOG' if showing else '>  LOG')
                                + ('    ' + self._log[-1] if self._log else ''))

    def _append_log(self, text):
        self._log.append(text)
        del self._log[:-MAX_LOG_LINES]
        self.log_box.append(text)
        self.log_box.verticalScrollBar().setValue(
            self.log_box.verticalScrollBar().maximum())
        if not self.log_box.isVisible():
            self.log_toggle.setText('>  LOG    %s' % text)

    def log(self, text):
        for line in str(text).splitlines():
            self._append_log(line)

    def _announce_tools(self):
        self.log('%s %s' % (APP_NAME, VERSION))
        for line in toolbox.report():
            self.log('  %s' % line)
        missing = [tool.key for tool in toolbox.ALL_TOOLS
                   if tool is not toolbox.FFPROBE and not tool.available]
        if missing:
            self.log('!  missing: %s - some options will be unavailable'
                     % ', '.join(missing))
        self.log('Raw support: %s' % formats.maker_summary())

    # --- state --------------------------------------------------------------

    def _restore_state(self):
        state = self._state.load()
        for key, tab in (('photo', self.photo_tab), ('video', self.video_tab)):
            output = state.get('output_%s' % key) or ''
            if output and os.path.isdir(output):
                tab.output_dir = output
                tab.output_label.setText(output)
            index = state.get('preset_%s' % key, 0)
            if isinstance(index, int) and 0 <= index < tab.preset.count():
                tab.preset.setCurrentIndex(index)
        self.photo_tab.custom_x.setText(str(state.get('custom_x', '1.33')))
        self.photo_tab.custom_y.setText(str(state.get('custom_y', '1.0')))
        mode = state.get('photo_mode')
        if mode:
            position = self.photo_tab.mode.findData(mode)
            if position >= 0:
                self.photo_tab.mode.setCurrentIndex(position)
        output = state.get('photo_output')
        if output:
            position = self.photo_tab.output_format.findData(output)
            if position >= 0:
                self.photo_tab.output_format.setCurrentIndex(position)
        self.photo_tab.jpeg_quality.setValue(int(state.get('jpeg_quality', 95)))
        self.photo_tab.dng_compressed.setChecked(
            bool(state.get('dng_compressed', True)))
        self.video_tab.bitrate.setValue(int(state.get('bitrate_mbps', 20)))
        codec = self.video_tab.codec.findData(state.get('codec', 'hevc'))
        if codec >= 0:
            self.video_tab.codec.setCurrentIndex(codec)

    def save_state(self):
        self._state.update({
            'output_photo': self.photo_tab.output_dir,
            'output_video': self.video_tab.output_dir,
            'preset_photo': self.photo_tab.preset.currentIndex(),
            'preset_video': self.video_tab.preset.currentIndex(),
            'custom_x': self.photo_tab.custom_x.text(),
            'custom_y': self.photo_tab.custom_y.text(),
            'photo_mode': self.photo_tab.mode.currentData(),
            'photo_output': self.photo_tab.output_format.currentData(),
            'jpeg_quality': self.photo_tab.jpeg_quality.value(),
            'dng_compressed': self.photo_tab.dng_compressed.isChecked(),
            'bitrate_mbps': self.video_tab.bitrate.value(),
            'codec': self.video_tab.codec.currentData(),
        })
        self._state.save()

    def closeEvent(self, event):
        if self._engine is not None:
            self._engine.cancel()
        self.save_state()
        super().closeEvent(event)

    # --- looks --------------------------------------------------------------

    def _apply_stylesheet(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: %(bg)s;
                color: #e0e0e0;
                font-family: 'Consolas', 'DejaVu Sans Mono', monospace;
                font-size: 12px;
            }
            QLabel#header { font-size: 26px; font-weight: bold;
                            letter-spacing: 8px; color: %(gold)s; }
            QLabel#subheader { font-size: 11px; color: #666; letter-spacing: 1px; }
            QLabel#sectionLabel { font-size: 10px; letter-spacing: 2px;
                                  color: #888; margin-top: 4px; }
            QLabel#hint { color: #6f6f6f; font-size: 11px; }
            QFrame#divider { color: #2a2a2a; background: #2a2a2a;
                             max-height: 1px; margin: 4px 0; }

            QLineEdit, QComboBox, QSpinBox {
                background: #1e1e1e; border: 1px solid #2e2e2e;
                border-radius: 3px; padding: 7px 8px; min-height: 17px;
                color: #e0e0e0;
                selection-background-color: %(gold)s; selection-color: #000;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
                border-color: %(gold)s; }
            QComboBox::drop-down { border: none; width: 22px; }
            QComboBox QAbstractItemView {
                background: #1e1e1e; border: 1px solid #333;
                selection-background-color: %(gold)s; selection-color: #000;
                outline: none; }

            QCheckBox { spacing: 8px; color: #cfcfcf; }
            QCheckBox::indicator { width: 15px; height: 15px;
                                   border: 1px solid #3a3a3a; background: #1a1a1a; }
            QCheckBox::indicator:checked { background: %(gold)s;
                                           border-color: %(gold)s; }
            QCheckBox:disabled { color: #555; }

            QTabWidget::pane { border: 1px solid #262626; top: -1px; }
            QTabBar::tab {
                background: #161616; color: #888; padding: 9px 26px;
                border: 1px solid #262626; border-bottom: none;
                letter-spacing: 2px; font-size: 12px; }
            QTabBar::tab:selected { background: #201a0a; color: %(gold)s;
                                    font-weight: bold; }

            QPushButton {
                background: #242424; border: 1px solid #333; border-radius: 3px;
                padding: 6px 14px; color: #ccc; letter-spacing: 1px; }
            QPushButton:hover { background: #2e2e2e; border-color: #555;
                                color: #fff; }
            QPushButton:disabled { color: #444; border-color: #222; }
            QPushButton#runBtn { background: %(gold)s; border: none; color: #000;
                                 font-weight: bold; font-size: 13px;
                                 letter-spacing: 3px; }
            QPushButton#runBtn:hover { background: #ffb820; }
            QPushButton#runBtn:disabled { background: #3a3a3a; color: #666; }
            QPushButton#cancelBtn { background: #2a1a1a; border: 1px solid #5a2a2a;
                                    color: #c04040; letter-spacing: 2px; }
            QPushButton#cancelBtn:disabled { color: #444; border-color: #222;
                                             background: #1e1e1e; }
            QPushButton#logToggle { background: #171717; border: none;
                                    color: #777; text-align: left;
                                    padding-left: 10px; font-size: 11px; }

            QProgressBar { background: #1e1e1e; border: none; border-radius: 3px; }
            QProgressBar::chunk { background: %(gold)s; border-radius: 3px; }
            QProgressBar[status="done"]::chunk { background: #5a6b45; }
            QProgressBar[status="failed"]::chunk { background: #a03535; }
            QProgressBar[status="skipped"]::chunk { background: #333; }

            QTableWidget { background: #0d0d0d; border: 1px solid #222;
                           gridline-color: #1a1a1a; color: #cccccc;
                           font-size: 11px; }
            QHeaderView::section { background: #161616; color: #777;
                                   border: none; border-bottom: 1px solid #222;
                                   padding: 5px; font-size: 10px;
                                   letter-spacing: 1px; }

            QTextEdit#logBox { background: #0d0d0d; border: 1px solid #222;
                               border-radius: 3px; padding: 6px; color: #888;
                               font-size: 11px; }
            QScrollBar:vertical { background: #141414; width: 8px; margin: 0; }
            QScrollBar::handle:vertical { background: #333; border-radius: 4px;
                                          min-height: 20px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0; }
        """ % {'gold': GOLD, 'bg': BACKGROUND})


def section(text):
    label = QLabel(text)
    label.setObjectName('sectionLabel')
    return label


def divider():
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setObjectName('divider')
    return line


def main():
    if sys.platform.startswith('win'):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                'kingdeusx.desqueeze.%s' % VERSION)
        except Exception:
            pass

    application = QApplication(sys.argv)
    application.setStyle('Fusion')
    application.setApplicationName(APP_NAME)
    path = icon_path()
    if path:
        application.setWindowIcon(QIcon(path))

    window = DesqueezeWindow()
    window.show()
    sys.exit(application.exec())


if __name__ == '__main__':
    main()
