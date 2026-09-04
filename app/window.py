"""Main window: preview, in/out trim bar, and the live size readout."""

import os
import subprocess

import qtawesome as qta

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from . import encoder, estimator, ffmpeg_tools, proxy
from .model import Project
from .playback import PlaybackEngine
from .trimbar import TrimBar
from .widgets import PresetStore, TargetField, show_preset_menu

ICON = "#e0e0e0"          # icon tint, matching the body text

VIDEO_FILTER = (
    "Video files (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.mpg *.mpeg *.wmv);;"
    "All files (*.*)"
)

STYLE = """
QMainWindow, QWidget { background: #1a1a1a; color: #e0e0e0; }
QLabel { color: #e0e0e0; }
QLabel#hint { color: #808080; font-size: 10px; }
QLabel#sectionTitle {
    color: #909090; font-size: 10px; font-weight: bold;
    letter-spacing: 1px; padding-top: 6px;
}
QPushButton {
    background: #2d2d2d; border: 1px solid #3d3d3d; border-radius: 3px;
    padding: 6px 12px; color: #e0e0e0;
}
QPushButton:hover { background: #3a3a3a; border-color: #4d4d4d; }
QPushButton:pressed { background: #252525; }
QPushButton:disabled { color: #606060; background: #232323; }
/* Toggles must read as ON at a glance, not just by their icon. */
QPushButton:checked {
    background: #2d5a7a; border-color: #5a9ac0;
}
QPushButton:checked:hover { background: #3a6d8f; }
QPushButton#tool {
    padding: 4px; border-radius: 4px; min-width: 40px; min-height: 34px;
}
QPushButton#primary {
    background: #2d5a7a; border-color: #3a6d8f; font-weight: bold;
}
QPushButton#primary:hover { background: #3a6d8f; }
QPushButton#primary:disabled { background: #262f36; border-color: #2d3840; }
QDoubleSpinBox, QComboBox {
    background: #252525; border: 1px solid #3d3d3d; border-radius: 3px;
    padding: 4px; color: #e0e0e0;
}
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView {
    background: #252525; color: #e0e0e0; selection-background-color: #2d5a7a;
}
QCheckBox { color: #e0e0e0; }
QFrame#panel { background: #202020; border-left: 1px solid #2d2d2d; }
QFrame#sizeCard {
    background: #232323; border: 1px solid #333; border-radius: 4px;
}
QProgressBar {
    background: #252525; border: 1px solid #3d3d3d; border-radius: 3px;
    text-align: center; height: 18px; color: #e0e0e0;
}
QProgressBar::chunk { background: #2d5a7a; border-radius: 2px; }
"""


class PreviewWidget(QWidget):
    """Letterboxed display for the decoded frame."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image = None
        self.setMinimumHeight(280)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_image(self, image):
        self._image = image
        self.update()

    def clear(self):
        self._image = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.black)
        if self._image is None or self._image.isNull():
            painter.end()
            return
        scaled = self._image.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        painter.drawImage((self.width() - scaled.width()) // 2,
                          (self.height() - scaled.height()) // 2, scaled)
        painter.end()


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.project = Project()
        self._encode_worker = None
        self._encode_thread = None
        self._proxy_jobs = []
        self._syncing = False
        self._last_export = ""
        self.presets = PresetStore()

        self.setWindowTitle("Undercut")
        self.resize(1180, 800)
        self.setStyleSheet(STYLE)

        self._build_ui()
        self._build_menu()

        self.engine = PlaybackEngine(self.project, self)
        self.engine.frame_ready.connect(self._on_frame)
        self.engine.position_changed.connect(self._on_engine_position)
        self.engine.stopped.connect(self._on_engine_stopped)
        self.engine.error.connect(self._on_engine_error)

        self._wire()
        self.refresh()

        self.setAcceptDrops(True)
        if not ffmpeg_tools.available():
            QTimer.singleShot(100, self._warn_no_ffmpeg)

    # -- construction -------------------------------------------------------

    def _tool_button(self, icon_name, tooltip, checkable=False, enabled=True):
        """An icon-only toolbar button with a tooltip instead of a label."""
        button = QPushButton(qta.icon(icon_name, color=ICON), "")
        button.setObjectName("tool")
        button.setIconSize(QSize(22, 22))
        button.setToolTip(tooltip)
        button.setCheckable(checkable)
        button.setEnabled(enabled)
        button.setCursor(Qt.PointingHandCursor)
        return button

    def _build_ui(self):
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        left = QWidget()
        layout = QVBoxLayout(left)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.preview = PreviewWidget()
        layout.addWidget(self.preview, 1)

        transport = QHBoxLayout()
        transport.setSpacing(4)

        # Icon-only buttons; the label lives in the tooltip so the row stays
        # compact and reads as a toolbar rather than a form.
        self.open_btn = self._tool_button(
            "mdi.folder-open", "Open a video   (Ctrl+O)"
        )
        transport.addWidget(self.open_btn)
        transport.addSpacing(8)

        self.play_btn = self._tool_button(
            "mdi.play", "Play / pause the selection   (Space)", enabled=False
        )
        transport.addWidget(self.play_btn)

        self.loop_btn = self._tool_button(
            "mdi.repeat", "Loop the selection   (L)",
            checkable=True, enabled=False,
        )
        transport.addWidget(self.loop_btn)
        transport.addSpacing(8)

        self.in_btn = self._tool_button(
            "mdi.ray-start", "Set the in-point at the playhead   (I)",
            enabled=False,
        )
        self.out_btn = self._tool_button(
            "mdi.ray-end", "Set the out-point at the playhead   (O)",
            enabled=False,
        )
        self.all_btn = self._tool_button(
            "mdi.backup-restore", "Reset the selection to the whole clip"
            "   (Ctrl+A)", enabled=False,
        )
        for btn in (self.in_btn, self.out_btn, self.all_btn):
            transport.addWidget(btn)

        transport.addStretch()
        self.time_label = QLabel("00:00.000 / 00:00.000")
        transport.addWidget(self.time_label)
        layout.addLayout(transport)

        self.trim = TrimBar(self.project)
        layout.addWidget(self.trim)

        root.addWidget(left, 1)
        root.addWidget(self._build_panel())
        self.setCentralWidget(central)
        self.statusBar().showMessage("Open a video to start")

    def _build_panel(self):
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setFixedWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # --- the readout ---
        card = QFrame()
        card.setObjectName("sizeCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 12)
        card_layout.setSpacing(4)

        header = QLabel("THIS CUT NEEDS")
        header.setObjectName("sectionTitle")
        card_layout.addWidget(header)

        self.size_label = QLabel("0 MB")
        self.size_label.setStyleSheet(
            "font-size: 32px; font-weight: bold; color: #7ec8a0;"
        )
        card_layout.addWidget(self.size_label)

        self.size_note = QLabel("Open a video")
        self.size_note.setObjectName("hint")
        card_layout.addWidget(self.size_note)

        card_layout.addSpacing(6)
        quality_header = QLabel("QUALITY YOU WILL GET")
        quality_header.setObjectName("sectionTitle")
        card_layout.addWidget(quality_header)

        self.quality_label = QLabel("-")
        self.quality_label.setStyleSheet(
            "font-size: 15px; font-weight: bold; color: #7ec8a0;"
        )
        card_layout.addWidget(self.quality_label)

        self.quality_bar = QProgressBar()
        self.quality_bar.setRange(0, 100)
        self.quality_bar.setTextVisible(False)
        self.quality_bar.setFixedHeight(6)
        card_layout.addWidget(self.quality_bar)

        self.quality_note = QLabel("")
        self.quality_note.setObjectName("hint")
        self.quality_note.setWordWrap(True)
        card_layout.addWidget(self.quality_note)

        self.detail_label = QLabel("")
        self.detail_label.setObjectName("hint")
        card_layout.addWidget(self.detail_label)

        layout.addWidget(card)

        # --- the target: a headline number you steer ---
        target_card = QFrame()
        target_card.setObjectName("sizeCard")
        target_layout = QVBoxLayout(target_card)
        target_layout.setContentsMargins(12, 10, 12, 12)
        target_layout.setSpacing(4)

        title = QLabel("MAX SIZE (TARGET)")
        title.setObjectName("sectionTitle")
        target_layout.addWidget(title)

        value_row = QHBoxLayout()
        value_row.setSpacing(6)
        self.target_spin = TargetField(11.5)
        value_row.addWidget(self.target_spin, 1)

        self.lock_btn = self._tool_button(
            "mdi.lock-open-variant",
            "Lock the target so it cannot be changed by accident",
            checkable=True,
        )
        value_row.addWidget(self.lock_btn)

        self.preset_btn = self._tool_button(
            "mdi.content-save", "Size presets: pick, save or delete"
        )
        value_row.addWidget(self.preset_btn)
        target_layout.addLayout(value_row)

        hint = QLabel("drag to change · wheel to nudge · double-click for presets")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        target_layout.addWidget(hint)

        layout.addWidget(target_card)

        title = QLabel("OUTPUT")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        res_row = QHBoxLayout()
        res_row.addWidget(QLabel("Max width"))
        self.width_combo = QComboBox()
        for label, value in (("Source", 0), ("1920", 1920), ("1280", 1280),
                             ("960", 960), ("640", 640)):
            self.width_combo.addItem(label, value)
        self.width_combo.setCurrentIndex(1)
        res_row.addWidget(self.width_combo, 1)
        layout.addLayout(res_row)

        fps_row = QHBoxLayout()
        fps_row.addWidget(QLabel("FPS cap"))
        self.fps_combo = QComboBox()
        for label, value in (("Source", 0), ("30", 30), ("25", 25),
                             ("24", 24), ("15", 15)):
            self.fps_combo.addItem(label, value)
        fps_row.addWidget(self.fps_combo, 1)
        layout.addLayout(fps_row)

        self.audio_check = QCheckBox("Keep audio")
        layout.addWidget(self.audio_check)

        self.faststart_check = QCheckBox("Web faststart")
        self.faststart_check.setChecked(True)
        layout.addWidget(self.faststart_check)

        layout.addStretch()

        self.proxy_label = QLabel("")
        self.proxy_label.setObjectName("hint")
        layout.addWidget(self.proxy_label)

        self.encode_bar = QProgressBar()
        self.encode_bar.setRange(0, 100)
        self.encode_bar.setVisible(False)
        layout.addWidget(self.encode_bar)

        self.export_btn = QPushButton(qta.icon("mdi.export", color="#ffffff"),
                                      " Export")
        self.export_btn.setObjectName("primary")
        self.export_btn.setIconSize(QSize(20, 20))
        self.export_btn.setFixedHeight(40)
        self.export_btn.setEnabled(False)
        layout.addWidget(self.export_btn)

        # Appears once an export succeeds.
        self.result_btn = QPushButton(
            qta.icon("mdi.play-circle", color=ICON), " Play export"
        )
        self.result_btn.setIconSize(QSize(20, 20))
        self.result_btn.setToolTip(
            "Open the exported file · Ctrl+click to show it in Explorer"
        )
        self.result_btn.setVisible(False)
        layout.addWidget(self.result_btn)

        return panel

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("&File")

        open_action = QAction("&Open Video...", self)
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self.open_video)
        file_menu.addAction(open_action)

        export_action = QAction("&Export...", self)
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(self.export)
        file_menu.addAction(export_action)

        file_menu.addSeparator()
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

    def _wire(self):
        self.open_btn.clicked.connect(self.open_video)
        self.play_btn.clicked.connect(self.toggle_play)
        self.in_btn.clicked.connect(self.trim.set_in_here)
        self.out_btn.clicked.connect(self.trim.set_out_here)
        self.all_btn.clicked.connect(self._select_all)
        self.export_btn.clicked.connect(self.export)
        self.loop_btn.toggled.connect(self._on_loop_toggled)
        self.target_spin.preset_requested.connect(self._show_presets)
        self.preset_btn.clicked.connect(self._show_presets)
        self.lock_btn.toggled.connect(self._on_lock_toggled)
        self.result_btn.clicked.connect(self._open_export)

        self.trim.playhead_moved.connect(self.scrub_draft)
        self.trim.playhead_settled.connect(self.scrub)
        self.trim.selection_changed.connect(self.refresh)

        self.target_spin.value_changed.connect(self._pull_settings)
        self.width_combo.currentIndexChanged.connect(self._pull_settings)
        self.fps_combo.currentIndexChanged.connect(self._pull_settings)
        self.audio_check.toggled.connect(self._pull_settings)
        self.faststart_check.toggled.connect(self._pull_settings)

    # -- settings -----------------------------------------------------------

    def _pull_settings(self):
        if self._syncing:
            return
        s = self.project.settings
        s.target_mb = self.target_spin.value()
        s.max_width = self.width_combo.currentData()
        s.fps = self.fps_combo.currentData()
        s.audio = self.audio_check.isChecked()
        s.faststart = self.faststart_check.isChecked()
        self.refresh()

    # -- loading ------------------------------------------------------------

    def open_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open video", "", VIDEO_FILTER
        )
        if path:
            self.load(path)

    def load(self, path):
        media = ffmpeg_tools.probe(path)
        if media is None:
            QMessageBox.warning(
                self, "Could not read",
                f"{os.path.basename(path)} could not be read as video.",
            )
            return

        self.engine.pause()
        self.engine.pool.close_all()
        self.project.load(media)

        cached = proxy.existing(path)
        if cached:
            self.project.proxy_path = cached
        elif proxy.needed(media):
            self._start_proxy(path)

        for btn in (self.play_btn, self.loop_btn, self.in_btn, self.out_btn,
                    self.all_btn):
            btn.setEnabled(True)

        self.trim.reset()
        self.trim.setFocus()
        self.refresh()
        self.scrub(0.0)
        self.statusBar().showMessage(
            f"{media.name} · {media.resolution} · {media.fps:.2f} fps · "
            f"{estimator.fmt_time(media.duration)}"
        )

    def _start_proxy(self, path):
        """Build a small preview copy so scrubbing stays instant."""
        self.proxy_label.setText("Building preview proxy...")
        thread, worker = proxy.start([path], self)
        worker.ready.connect(self._on_proxy_ready)
        worker.failed.connect(lambda _s: self.proxy_label.setText(""))
        self._proxy_jobs.append((thread, worker))

    def _on_proxy_ready(self, source, proxy_path):
        if self.project.loaded and self.project.media.path == source:
            self.project.proxy_path = proxy_path
            self.engine.pool.drop(source)
            self.scrub(self.trim.playhead)
        self.proxy_label.setText("Preview proxy ready")
        QTimer.singleShot(3000, lambda: self.proxy_label.setText(""))

    def _select_all(self):
        self.project.select_all()
        self.trim.update()
        self.refresh()

    # -- preview ------------------------------------------------------------

    def scrub(self, position, draft=False):
        if not self.project.loaded:
            self.preview.clear()
            return
        self.engine.request(position, draft)
        self._update_time_label()

    def scrub_draft(self, position):
        """Mid-drag: an approximate frame now beats an exact one late."""
        self.scrub(position, draft=True)

    def _on_frame(self, image, _position):
        self.preview.set_image(image)

    def _on_engine_position(self, position):
        self.trim.set_playhead_silent(position)
        self._update_time_label()

    def _on_engine_stopped(self):
        self.play_btn.setIcon(qta.icon("mdi.play", color=ICON))

    def _on_engine_error(self, message):
        self.play_btn.setIcon(qta.icon("mdi.play", color=ICON))
        self.statusBar().showMessage(f"Preview decode error: {message}", 8000)

    def toggle_play(self):
        if self.engine.playing:
            self.engine.pause()
            self.play_btn.setIcon(qta.icon("mdi.play", color=ICON))
            return
        if not self.project.loaded:
            return

        # Outside the selection - including parked on the last frame - start
        # again from the in-point rather than refusing to play.
        start = self.trim.playhead
        if not (self.project.in_point <= start < self.project.out_point - 0.01):
            start = self.project.in_point
        self.engine.play(start)
        self.play_btn.setIcon(qta.icon("mdi.pause", color=ICON))

    def _on_loop_toggled(self, on):
        self.engine.loop = on
        self.statusBar().showMessage(
            "Loop on - the selection repeats" if on else "Loop off", 2500
        )

    def _on_lock_toggled(self, locked):
        self.target_spin.set_locked(locked)
        self.lock_btn.setIcon(qta.icon(
            "mdi.lock" if locked else "mdi.lock-open-variant", color=ICON
        ))
        self.preset_btn.setEnabled(not locked)
        self.statusBar().showMessage(
            "Target locked" if locked else "Target unlocked", 2500
        )

    def _show_presets(self):
        """Double-clicking the size field opens the saved presets."""
        saved = show_preset_menu(
            self.target_spin, self.presets, self.target_spin.value(),
            self.target_spin.setValue,
        )
        if saved:
            self.statusBar().showMessage("Preset saved", 3000)

    def _open_export(self):
        """Play the exported file; Ctrl+click reveals it in Explorer."""
        path = self._last_export
        if not path or not os.path.exists(path):
            self.statusBar().showMessage("Exported file not found", 4000)
            return

        if QApplication.keyboardModifiers() & Qt.ControlModifier:
            # /select, opens Explorer with the file highlighted.
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            os.startfile(path)

    def _update_time_label(self):
        self.time_label.setText(
            f"{estimator.fmt_time(self.trim.playhead)} / "
            f"{estimator.fmt_time(self.project.source_duration)}"
        )

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space:
            self.toggle_play()
        elif event.key() == Qt.Key_L and not event.modifiers():
            self.loop_btn.toggle()
        else:
            super().keyPressEvent(event)

    # -- the readout --------------------------------------------------------

    def refresh(self):
        est = self.project.estimate()
        self._update_time_label()

        if not self.project.loaded or est["bytes"] == 0:
            self.size_label.setText("0 MB")
            self.size_note.setText("Open a video")
            self.quality_label.setText("-")
            self.quality_note.setText("")
            self.quality_bar.setValue(0)
            self.detail_label.setText("")
            self.export_btn.setEnabled(False)
            return

        # The headline is what this selection NEEDS at full quality. Unlike
        # the export size (always the target), this moves as you drag.
        needed = est["quality_bytes"]
        target = self.project.settings.target_bytes
        self.size_label.setText(estimator.fmt_size(needed))

        if est["fits"]:
            headline_colour = "#7ec8a0"
            self.size_note.setText(
                f"fits in {self.project.settings.target_mb:.2f} MB "
                f"with room to spare"
            )
        else:
            over = needed / target if target else 0
            headline_colour = "#e0c05a" if over < 2.0 else "#e06c5a"
            self.size_note.setText(
                f"{over:.1f}x the {self.project.settings.target_mb:.2f} MB "
                f"budget - it will be compressed"
            )
        self.size_label.setStyleSheet(
            f"font-size: 32px; font-weight: bold; color: {headline_colour};"
        )

        quality = est["quality"]
        if quality:
            colour = {
                "excellent": "#7ec8a0", "very good": "#7ec8a0",
                "good": "#a8c87e", "fair": "#e0c05a", "poor": "#e06c5a",
            }.get(quality["label"], "#7ec8a0")
            self.quality_label.setText(quality["label"].title())
            self.quality_label.setStyleSheet(
                f"font-size: 15px; font-weight: bold; color: {colour};"
            )
            self.quality_bar.setValue(quality["score"])
            self.quality_bar.setStyleSheet(
                f"QProgressBar::chunk {{ background: {colour}; "
                f"border-radius: 2px; }}"
            )
            self.quality_note.setText(quality["note"])

        width, height, fps = self.project.output_geometry()
        self.detail_label.setText(
            f"exports at {estimator.fmt_size(est['bytes'])} · "
            f"{estimator.fmt_time(est['duration'])} · {width}x{height} @ "
            f"{fps:.0f} fps · {estimator.fmt_bitrate(est['bitrate'])}"
        )
        self.export_btn.setEnabled(ffmpeg_tools.available())

    # -- export -------------------------------------------------------------

    def export(self):
        if not self.project.loaded or self._encode_thread is not None:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export video", self.project.default_output(),
            "MP4 video (*.mp4)",
        )
        if not path:
            return

        self.project.output_path = path
        self.engine.pause()

        self.result_btn.setVisible(False)
        self.encode_bar.setValue(0)
        self.encode_bar.setVisible(True)
        self.export_btn.setEnabled(False)
        self.export_btn.setText(" Encoding...")

        worker = encoder.EncodeWorker(self.project, path)
        worker.progress.connect(self._on_encode_progress)
        worker.finished.connect(self._on_encode_finished)
        self._encode_worker = worker
        self._encode_thread = encoder.run_in_thread(worker, self)

    def _on_encode_progress(self, percent, stage):
        self.encode_bar.setValue(percent)
        self.statusBar().showMessage(f"{stage} - {percent}%")

    def _on_encode_finished(self, success, message):
        # Make sure the bar reads 100 before it disappears - it used to be
        # left sitting at 99 because ffmpeg's last progress line lands short.
        self.encode_bar.setValue(100)
        QApplication.processEvents()
        self.encode_bar.setVisible(False)

        self.export_btn.setEnabled(True)
        self.export_btn.setText(" Export")
        self._encode_worker = None
        self._encode_thread = None

        if success:
            self._last_export = self.project.output_path
            name = os.path.basename(self._last_export)
            self.result_btn.setText(f" Play {name}")
            self.result_btn.setVisible(True)
            self.statusBar().showMessage(f"{message} · {name}", 15000)
        else:
            self.statusBar().showMessage("Export failed", 6000)
            QMessageBox.critical(self, "Export failed", message)

    def _warn_no_ffmpeg(self):
        QMessageBox.critical(
            self, "FFmpeg not found",
            "Undercut needs ffmpeg and ffprobe on PATH (or in C:\\ffmpeg\\bin).\n\n"
            "Install from https://ffmpeg.org/download.html, then restart.",
        )

    # -- drag & drop --------------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.load(url.toLocalFile())
                event.acceptProposedAction()
                return

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def shutdown(self):
        """Stop every background worker before the interpreter tears down.

        A surviving ffmpeg child aborts the process, so workers are cancelled
        AND joined rather than merely signalled.
        """
        if getattr(self, "_closed", False):
            return
        self._closed = True

        if self._encode_worker is not None:
            self._encode_worker.cancel(wait=True)
        for thread, worker in self._proxy_jobs:
            worker.cancel(wait=True)
            thread.quit()
            thread.wait(4000)
        self._proxy_jobs.clear()
        self.engine.shutdown()
