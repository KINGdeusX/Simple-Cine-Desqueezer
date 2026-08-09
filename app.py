"""
Desqueeze GUI — Anamorphic DNG Processor
Requires: pip install PyQt6
Also requires: exiftool.exe in the same folder OR specify its path in the app.
"""

import sys
import json
from pathlib import Path
import os
import subprocess
import shutil
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QComboBox, QTextEdit,
    QFileDialog, QProgressBar, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QColor, QPalette, QIcon

# ─────────────────────────────────────────────
# Lens presets: (Display Label, squeeze_x, squeeze_y)
# ─────────────────────────────────────────────
LENS_PRESETS = [
    ("1.33x — Canon C70 / C300 / C500 / Sigma Cine / DJI",         "1.33", "1.0"),
    ("1.5x  — Kowa / Vintage Anamorphic / Iscorama 36",             "1.5",  "1.0"),
    ("1.6x  — Lomo Square Front / Some Vintage Glass",              "1.6",  "1.0"),
    ("1.8x  — Panasonic GH5/GH6 Anamorphic Mode",                   "1.8",  "1.0"),
    ("2.0x  — Panavision / Hawk / Cooke / Classic Anamorphic",      "2.0",  "1.0"),
    ("2.0x  — Lomo 35 OCT-19 / Russian Vintage",                    "2.0",  "1.0"),
    ("1.25x — Sirui Sniper / Entry Anamorphic Lenses",              "1.25", "1.0"),
    ("1.79x — RED DSMC Anamorphic Mode",                            "1.79", "1.0"),
    ("2.39x — Ultra Panavision 70 (Rare)",                          "2.39", "1.0"),
    ("Custom...",                                                    None,   None),
]


# ─────────────────────────────────────────────
# Worker thread — runs exiftool without blocking UI
# ─────────────────────────────────────────────
class ExifWorker(QThread):
    log        = pyqtSignal(str)
    progress   = pyqtSignal(int)
    finished   = pyqtSignal(bool, str)

    def __init__(self, exiftool, src, dst, squeeze_x, squeeze_y):
        super().__init__()
        self.exiftool  = exiftool
        self.src       = src
        self.dst       = dst
        self.squeeze_x = squeeze_x
        self.squeeze_y = squeeze_y
        self._cancelled = False

    def cancel(self):
        self._cancelled = True
        self.terminate()

    def run(self):
        src_path = Path(self.src)
        dst_path = Path(self.dst)

        # Count DNG files first for progress tracking
        dngs = list(src_path.glob("*.dng")) + list(src_path.glob("*.DNG"))
        total = len(dngs)

        if total == 0:
            self.finished.emit(False, "No DNG files found in source folder.")
            return

        self.log.emit(f"Found {total} DNG file(s) to process.\n")
        dst_path.mkdir(parents=True, exist_ok=True)

        scale_value = f"{self.squeeze_x} {self.squeeze_y}"
        cmd = [
            self.exiftool,
            f"-DefaultScale={scale_value}",
            "-ext", "dng",
            str(src_path),
            "-o", str(dst_path),
            "-overwrite_original",
            "-progress",
        ]

        self.log.emit(f"Command: {' '.join(cmd)}\n")
        self.log.emit("─" * 50 + "\n")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )

            processed = 0
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                self.log.emit(line)
                # exiftool prints "======== filename" per file
                if line.startswith("========") or "files created" in line.lower():
                    processed += 1
                    pct = min(int((processed / total) * 100), 99)
                    self.progress.emit(pct)

            proc.wait()
            if proc.returncode == 0 or proc.returncode == 1:
                self.progress.emit(100)
                self.finished.emit(True, f"Done! {total} file(s) processed → {self.dst}")
            else:
                self.finished.emit(False, f"ExifTool exited with code {proc.returncode}.")

        except FileNotFoundError:
            self.finished.emit(False, f"ExifTool not found at: {self.exiftool}\nCheck the path and try again.")
        except Exception as e:
            self.finished.emit(False, str(e))


# ─────────────────────────────────────────────
# Main Window
# ─────────────────────────────────────────────
class DesqueezeApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker = None
        self._init_ui()
        self._auto_detect_exiftool()
        self._save_state()

    def _auto_detect_exiftool(self):
        candidates = [
            Path("exiftool.exe"),
            Path("exiftool"),
            Path(sys.executable).parent / "exiftool.exe",
            Path(sys.executable).parent / "exiftool",
        ]
        for c in candidates:
            if c.exists():
                self.exif_edit.setText(str(c.resolve()))
                self._log(f"ExifTool auto-detected: {c.resolve()}\n")
                return
        found = shutil.which("exiftool")
        if found:
            self.exif_edit.setText(found)
            self._log(f"ExifTool found in PATH: {found}\n")
        else:
            self._log("⚠  ExifTool not found. Please set its path manually.\n")

        state_file = Path(__file__).parent / 'state.json'
        if not state_file.exists():
            return
        try:
            data = json.loads(state_file.read_text(encoding='utf-8'))
        except Exception:
            return
        exif = data.get('exif_path')
        src = data.get('src_path')
        dst = data.get('dst_path')
        if exif:
            self.exif_edit.setText(exif)
        if src:
            self.src_edit.setText(src)
        if dst:
            self.dst_edit.setText(dst)
        preset_idx = data.get('preset_index')
        if preset_idx is not None and 0 <= preset_idx < len(LENS_PRESETS):
            self.lens_combo.setCurrentIndex(preset_idx)
            if LENS_PRESETS[preset_idx][1] is None:
                # custom
                self.custom_x.setText(str(data.get('custom_x', '1.33')))
                self.custom_y.setText(str(data.get('custom_y', '1.0')))
        # ensure UI reflects preset
        self._on_preset_changed(self.lens_combo.currentIndex())

    def _save_state(self):
        state_file = Path(__file__).parent / 'state.json'
        data = {
            'exif_path': self.exif_edit.text(),
            'src_path': self.src_edit.text(),
            'dst_path': self.dst_edit.text(),
            'preset_index': self.lens_combo.currentIndex(),
        }
        if self.lens_combo.currentIndex() == len(LENS_PRESETS)-1:  # Custom
            data['custom_x'] = self.custom_x.text()
            data['custom_y'] = self.custom_y.text()
        try:
            state_file.write_text(json.dumps(data), encoding='utf-8')
        except Exception:
            pass

    def closeEvent(self, event):
        self._save_state()
        super().closeEvent(event)

        found = shutil.which("exiftool")
        if found:
            self.exif_edit.setText(found)
            self._log(f"ExifTool found in PATH: {found}\n")
        else:
            self._log("⚠  ExifTool not found. Please set its path manually.\n")

    # ── Build UI ────────────────────────────
    def _init_ui(self):
        self.setWindowTitle("Desqueeze — Anamorphic DNG Processor")
        self.setMinimumWidth(680)
        self.setMaximumWidth(900)
        self.resize(720, 560)

        self._apply_stylesheet()

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        # ── Header ──────────────────────────
        header = QLabel("DESQUEEZE")
        header.setObjectName("header")
        header.setAlignment(Qt.AlignmentFlag.AlignLeft)
        root.addWidget(header)

        sub = QLabel("Anamorphic DNG Metadata Processor  •  Powered by ExifTool")
        sub.setObjectName("subheader")
        root.addWidget(sub)

        root.addWidget(self._divider())

        # ── ExifTool path ───────────────────
        root.addWidget(self._section_label("EXIFTOOL PATH"))
        exif_row = QHBoxLayout()
        self.exif_edit = QLineEdit()
        self.exif_edit.setPlaceholderText("Path to exiftool.exe …")
        browse_exif = QPushButton("Browse")
        browse_exif.setFixedWidth(80)
        browse_exif.clicked.connect(self._browse_exiftool)
        exif_row.addWidget(self.exif_edit)
        exif_row.addWidget(browse_exif)
        root.addLayout(exif_row)

        # ── Source folder ───────────────────
        root.addWidget(self._section_label("SOURCE FOLDER  (DNG files)"))
        src_row = QHBoxLayout()
        self.src_edit = QLineEdit()
        self.src_edit.setPlaceholderText("Select folder containing .dng files …")
        browse_src = QPushButton("Browse")
        browse_src.setFixedWidth(80)
        browse_src.clicked.connect(self._browse_src)
        src_row.addWidget(self.src_edit)
        src_row.addWidget(browse_src)
        root.addLayout(src_row)

        # ── Output folder ───────────────────
        root.addWidget(self._section_label("OUTPUT FOLDER"))
        dst_row = QHBoxLayout()
        self.dst_edit = QLineEdit()
        self.dst_edit.setPlaceholderText("Select destination folder …")
        browse_dst = QPushButton("Browse")
        browse_dst.setFixedWidth(80)
        browse_dst.clicked.connect(self._browse_dst)
        dst_row.addWidget(self.dst_edit)
        dst_row.addWidget(browse_dst)
        root.addLayout(dst_row)

        # ── Lens preset ─────────────────────
        root.addWidget(self._section_label("SQUEEZE RATIO  —  Lens Preset"))
        self.lens_combo = QComboBox()
        for label, *_ in LENS_PRESETS:
            self.lens_combo.addItem(label)
        self.lens_combo.currentIndexChanged.connect(self._on_preset_changed)
        root.addWidget(self.lens_combo)

        # Custom ratio row (hidden until "Custom…" selected)
        self.custom_row = QWidget()
        cr_layout = QHBoxLayout(self.custom_row)
        cr_layout.setContentsMargins(0, 0, 0, 0)
        cr_layout.addWidget(QLabel("Squeeze X:"))
        self.custom_x = QLineEdit("1.33")
        self.custom_x.setFixedWidth(70)
        cr_layout.addWidget(self.custom_x)
        cr_layout.addWidget(QLabel("  Y (usually 1.0):"))
        self.custom_y = QLineEdit("1.0")
        self.custom_y.setFixedWidth(70)
        cr_layout.addWidget(self.custom_y)
        cr_layout.addStretch()
        self.custom_row.setVisible(False)
        root.addWidget(self.custom_row)

        root.addWidget(self._divider())

        # ── Run button ──────────────────────
        btn_row = QHBoxLayout()
        self.run_btn = QPushButton("▶  RUN DESQUEEZE")
        self.run_btn.setObjectName("runBtn")
        self.run_btn.setFixedHeight(42)
        self.run_btn.clicked.connect(self._run)

        self.cancel_btn = QPushButton("■  CANCEL")
        self.cancel_btn.setObjectName("cancelBtn")
        self.cancel_btn.setFixedHeight(42)
        self.cancel_btn.setFixedWidth(110)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)

        btn_row.addWidget(self.run_btn)
        btn_row.addWidget(self.cancel_btn)
        root.addLayout(btn_row)

        # ── Progress bar ────────────────────
        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFixedHeight(6)
        root.addWidget(self.progress)

        # ── Log output ──────────────────────
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setObjectName("logBox")
        self.log_box.setMinimumHeight(110)
        self.log_box.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        root.addWidget(self.log_box)

    # ── Helpers ─────────────────────────────
    def _divider(self):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("divider")
        return line

    def _section_label(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("sectionLabel")
        return lbl

    def _log(self, text):
        self.log_box.append(text.rstrip())
        self.log_box.verticalScrollBar().setValue(
            self.log_box.verticalScrollBar().maximum()
        )

    # ── Slot: preset changed ─────────────────
    def _on_preset_changed(self, idx):
        _, sx, sy = LENS_PRESETS[idx]
        is_custom = sx is None
        self.custom_row.setVisible(is_custom)

    # ── File dialogs ─────────────────────────
    def _browse_exiftool(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select ExifTool", "",
            "Executable (*.exe);;All Files (*)"
        )
        if path:
            self.exif_edit.setText(path)

    def _browse_src(self):
        path = QFileDialog.getExistingDirectory(self, "Select Source Folder")
        if path:
            self.src_edit.setText(path)
            # Auto-suggest output folder beside source
            if not self.dst_edit.text():
                self.dst_edit.setText(str(Path(path).parent / (Path(path).name + "_desqueezed")))

    def _browse_dst(self):
        path = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if path:
            self.dst_edit.setText(path)

    # ── Run / Cancel ─────────────────────────
    def _run(self):
        exiftool = self.exif_edit.text().strip()
        src      = self.src_edit.text().strip()
        dst      = self.dst_edit.text().strip()

        if not exiftool:
            self._log("⚠  Please set ExifTool path.\n"); return
        if not src:
            self._log("⚠  Please select a source folder.\n"); return
        if not dst:
            self._log("⚠  Please select an output folder.\n"); return
        if not Path(src).is_dir():
            self._log(f"⚠  Source folder not found: {src}\n"); return

        idx = self.lens_combo.currentIndex()
        _, sx, sy = LENS_PRESETS[idx]

        if sx is None:  # custom
            sx = self.custom_x.text().strip()
            sy = self.custom_y.text().strip()
            try:
                float(sx); float(sy)
            except ValueError:
                self._log("⚠  Invalid custom ratio values.\n"); return

        self.log_box.clear()
        self.progress.setValue(0)
        self.run_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self._log(f"Source : {src}")
        self._log(f"Output : {dst}")
        self._log(f"Ratio  : {sx} × {sy}\n")

        self.worker = ExifWorker(exiftool, src, dst, sx, sy)
        self.worker.log.connect(self._log)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self._log("\n■  Cancelled by user.")
            self._reset_buttons()

    def _on_finished(self, success, msg):
        self._log(f"\n{'✔' if success else '✖'}  {msg}")
        self._reset_buttons()

    def _reset_buttons(self):
        self.run_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)

    # ── Stylesheet ───────────────────────────
    def _apply_stylesheet(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #141414;
                color: #e0e0e0;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
            }

            QLabel#header {
                font-size: 26px;
                font-weight: bold;
                letter-spacing: 8px;
                color: #f0a500;
            }

            QLabel#subheader {
                font-size: 11px;
                color: #666;
                letter-spacing: 1px;
            }

            QLabel#sectionLabel {
                font-size: 10px;
                letter-spacing: 2px;
                color: #888;
                margin-top: 4px;
            }

            QFrame#divider {
                color: #2a2a2a;
                background: #2a2a2a;
                max-height: 1px;
                margin: 4px 0;
            }

            QLineEdit {
                background: #1e1e1e;
                border: 1px solid #2e2e2e;
                border-radius: 3px;
                padding: 6px 8px;
                color: #e0e0e0;
                selection-background-color: #f0a500;
                selection-color: #000;
            }
            QLineEdit:focus {
                border-color: #f0a500;
            }

            QComboBox {
                background: #1e1e1e;
                border: 1px solid #2e2e2e;
                border-radius: 3px;
                padding: 6px 8px;
                color: #e0e0e0;
                min-height: 28px;
            }
            QComboBox:focus { border-color: #f0a500; }
            QComboBox::drop-down {
                border: none;
                width: 24px;
            }
            QComboBox::down-arrow {
                width: 10px;
                height: 10px;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid #888;
            }
            QComboBox QAbstractItemView {
                background: #1e1e1e;
                border: 1px solid #333;
                selection-background-color: #f0a500;
                selection-color: #000;
                outline: none;
            }

            QPushButton {
                background: #242424;
                border: 1px solid #333;
                border-radius: 3px;
                padding: 6px 14px;
                color: #ccc;
                letter-spacing: 1px;
            }
            QPushButton:hover  { background: #2e2e2e; border-color: #555; color: #fff; }
            QPushButton:pressed { background: #1a1a1a; }
            QPushButton:disabled { color: #444; border-color: #222; }

            QPushButton#runBtn {
                background: #f0a500;
                border: none;
                color: #000;
                font-weight: bold;
                font-size: 13px;
                letter-spacing: 3px;
            }
            QPushButton#runBtn:hover   { background: #ffb820; }
            QPushButton#runBtn:pressed { background: #c98800; }
            QPushButton#runBtn:disabled { background: #3a3a3a; color: #666; }

            QPushButton#cancelBtn {
                background: #2a1a1a;
                border: 1px solid #5a2a2a;
                color: #c04040;
                letter-spacing: 2px;
                font-size: 11px;
            }
            QPushButton#cancelBtn:hover { background: #3a2020; border-color: #8a3030; }
            QPushButton#cancelBtn:disabled { color: #444; border-color: #222; background: #1e1e1e; }

            QProgressBar {
                background: #1e1e1e;
                border: none;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background: #f0a500;
                border-radius: 3px;
            }

            QTextEdit#logBox {
                background: #0d0d0d;
                border: 1px solid #222;
                border-radius: 3px;
                padding: 6px;
                color: #888;
                font-size: 11px;
                font-family: 'Consolas', 'Courier New', monospace;
            }

            QScrollBar:vertical {
                background: #141414;
                width: 6px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #333;
                border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
def main():
    # High-DPI support
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    win = DesqueezeApp()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()