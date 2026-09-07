"""The trim bar: one clip, an in/out selection, and a playhead.

Drag the shaded handles to set in/out; drag or click anywhere else to scrub.
"""

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import estimator

BG = QColor("#1a1a1a")
RULER_BG = QColor("#151515")
STRIP_OUT = QColor("#242424")       # outside the selection
STRIP_IN = QColor("#2d5a7a")        # inside the selection
STRIP_IN_TOP = QColor("#3a6d8f")
HANDLE = QColor("#ffcc44")
HANDLE_HOVER = QColor("#ffd968")
TEXT = QColor("#e0e0e0")
DIM_TEXT = QColor("#8a8a8a")
PLAYHEAD = QColor("#ff5533")

RULER_H = 20
STRIP_TOP = RULER_H + 10
STRIP_H = 64
HANDLE_W = 10
MARGIN = 14


class TrimBar(QWidget):
    """Selection strip for a single clip."""

    playhead_moved = Signal(float)      # live, while dragging
    playhead_settled = Signal(float)    # drag finished - decode exact frame
    selection_changed = Signal()

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.playhead = 0.0

        self._drag = None               # 'in' | 'out' | 'body' | 'scrub'
        self._hover = None
        self._grab_offset = 0.0         # seconds into the block, for 'body'
        self.setMinimumHeight(STRIP_TOP + STRIP_H + 30)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAcceptDrops(True)

    # -- mapping ------------------------------------------------------------

    def _span(self):
        return max(0.001, self.project.source_duration)

    def x_for(self, seconds):
        usable = max(1, self.width() - MARGIN * 2)
        return MARGIN + (seconds / self._span()) * usable

    def time_for(self, x):
        usable = max(1, self.width() - MARGIN * 2)
        return max(0.0, min(self._span(),
                            (x - MARGIN) / usable * self._span()))

    def strip_rect(self):
        return QRectF(MARGIN, STRIP_TOP,
                      max(1, self.width() - MARGIN * 2), STRIP_H)

    # -- painting -----------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), BG)

        if not self.project.loaded:
            painter.setPen(DIM_TEXT)
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "Drop a video here, or click Open Video")
            painter.end()
            return

        self._paint_ruler(painter)
        self._paint_strip(painter)
        self._paint_handles(painter)
        self._paint_playhead(painter)
        self._paint_labels(painter)
        painter.end()

    def _paint_ruler(self, painter):
        painter.fillRect(0, 0, self.width(), RULER_H, RULER_BG)
        span = self._span()

        # Aim for a tick roughly every 90 px.
        usable = max(1, self.width() - MARGIN * 2)
        target = span / max(1, usable / 90)
        for step in (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600):
            if step >= target:
                break

        font = QFont()
        font.setPointSize(7)
        painter.setFont(font)

        tick = 0.0
        while tick <= span:
            x = self.x_for(tick)
            painter.setPen(QPen(QColor("#3a3a3a"), 1))
            painter.drawLine(int(x), 0, int(x), RULER_H)
            painter.setPen(DIM_TEXT)
            painter.drawText(int(x) + 3, RULER_H - 6, _tick_label(tick, step))
            tick += step

    def _paint_strip(self, painter):
        rect = self.strip_rect()

        # Everything outside the selection is dimmed.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(STRIP_OUT))
        painter.drawRoundedRect(rect, 3, 3)

        left = self.x_for(self.project.in_point)
        right = self.x_for(self.project.out_point)
        selection = QRectF(left, rect.top(), max(1.0, right - left), rect.height())

        gradient = QLinearGradient(0, selection.top(), 0, selection.bottom())
        gradient.setColorAt(0.0, STRIP_IN_TOP)
        gradient.setColorAt(1.0, STRIP_IN)
        painter.setBrush(QBrush(gradient))
        painter.drawRoundedRect(selection, 3, 3)

        # Filename across the selection.
        if selection.width() > 60:
            painter.setPen(QColor("#dceaf4"))
            font = QFont()
            font.setPointSize(8)
            font.setBold(True)
            painter.setFont(font)
            text_rect = selection.adjusted(8, 6, -8, 0)
            name = painter.fontMetrics().elidedText(
                self.project.media.name, Qt.ElideMiddle, int(text_rect.width())
            )
            painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignTop, name)

    def _paint_handles(self, painter):
        rect = self.strip_rect()
        for kind, seconds in (("in", self.project.in_point),
                              ("out", self.project.out_point)):
            x = self.x_for(seconds)
            colour = HANDLE_HOVER if self._hover == kind else HANDLE
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(colour))
            # The handle sits inside the selection edge.
            left = x if kind == "in" else x - HANDLE_W
            painter.drawRoundedRect(
                QRectF(left, rect.top() - 3, HANDLE_W, rect.height() + 6), 2, 2
            )
            # Grip lines.
            painter.setPen(QPen(QColor("#7a5f10"), 1))
            for offset in (3, 6):
                painter.drawLine(int(left + offset), int(rect.top() + 8),
                                 int(left + offset), int(rect.bottom() - 8))

    def _paint_playhead(self, painter):
        x = self.x_for(self.playhead)
        painter.setPen(QPen(PLAYHEAD, 2))
        painter.drawLine(int(x), RULER_H - 4,
                         int(x), STRIP_TOP + STRIP_H + 4)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(PLAYHEAD))
        painter.drawEllipse(QRectF(x - 5, RULER_H - 9, 10, 10))

    def _paint_labels(self, painter):
        base = STRIP_TOP + STRIP_H + 20
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)

        painter.setPen(DIM_TEXT)
        painter.drawText(
            MARGIN, base,
            f"In {estimator.fmt_time(self.project.in_point)}"
            f"    Out {estimator.fmt_time(self.project.out_point)}",
        )

        font.setBold(True)
        painter.setFont(font)
        painter.setPen(TEXT)
        text = f"Selected {estimator.fmt_time(self.project.duration)}"
        painter.drawText(
            int(self.width() - MARGIN - painter.fontMetrics().horizontalAdvance(text)),
            base, text,
        )

    # -- interaction --------------------------------------------------------

    def _zone(self, pos):
        """Which handle (if any) is under the cursor."""
        if not self.project.loaded:
            return None
        rect = self.strip_rect()
        if not (rect.top() - 6 <= pos.y() <= rect.bottom() + 6):
            return None

        in_x = self.x_for(self.project.in_point)
        out_x = self.x_for(self.project.out_point)
        if abs(pos.x() - (in_x + HANDLE_W / 2)) <= HANDLE_W:
            return "in"
        if abs(pos.x() - (out_x - HANDLE_W / 2)) <= HANDLE_W:
            return "out"
        # Handles win over the body, so a narrow selection stays trimmable.
        if in_x < pos.x() < out_x:
            return "body"
        return None

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or not self.project.loaded:
            return
        pos = event.position()

        zone = self._zone(pos)
        if zone:
            self._drag = zone
            if zone == "body":
                # Remember where in the block it was grabbed, so it slides from
                # under the cursor instead of jumping to centre on it.
                self._grab_offset = self.time_for(pos.x()) - self.project.in_point
                self.setCursor(Qt.ClosedHandCursor)
            return

        self._drag = "scrub"
        self._set_playhead(self.time_for(pos.x()))

    def mouseMoveEvent(self, event):
        pos = event.position()

        if self._drag is None:
            hover = self._zone(pos)
            if hover != self._hover:
                self._hover = hover
                self.update()
            if hover == "body":
                self.setCursor(Qt.OpenHandCursor)
            elif hover:
                self.setCursor(Qt.SizeHorCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
            return

        seconds = self.time_for(pos.x())
        if self._drag == "in":
            self.project.set_in(seconds)
            self._set_playhead(self.project.in_point)
            self.selection_changed.emit()
            self.update()
        elif self._drag == "out":
            self.project.set_out(seconds)
            self._set_playhead(self.project.out_point)
            self.selection_changed.emit()
            self.update()
        elif self._drag == "body":
            # Where the in-point should land to keep the grab point under the
            # cursor; slide_selection turns that into a fixed-span move.
            target_in = seconds - self._grab_offset
            self.project.slide_selection(target_in - self.project.in_point)
            self._set_playhead(self.project.in_point)
            self.selection_changed.emit()
            self.update()
        else:
            self._set_playhead(seconds)

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            self._drag = None
            self.playhead_settled.emit(self.playhead)
        # Back to whatever the cursor is now over, rather than a blanket arrow -
        # releasing a slide inside the block should leave the open hand.
        self._hover = self._zone(event.position())
        if self._hover == "body":
            self.setCursor(Qt.OpenHandCursor)
        elif self._hover:
            self.setCursor(Qt.SizeHorCursor)
        else:
            self.setCursor(Qt.ArrowCursor)
        self.update()

    def keyPressEvent(self, event):
        key = event.key()
        shift = event.modifiers() & Qt.ShiftModifier

        if key == Qt.Key_I:
            self.set_in_here()
        elif key == Qt.Key_O:
            self.set_out_here()
        elif key == Qt.Key_A and event.modifiers() & Qt.ControlModifier:
            self.project.select_all()
            self.selection_changed.emit()
            self.update()
        elif key == Qt.Key_Left:
            self._nudge(-1, shift)
        elif key == Qt.Key_Right:
            self._nudge(1, shift)
        elif key == Qt.Key_Home:
            self._set_playhead(0.0)
            self.playhead_settled.emit(self.playhead)
        elif key == Qt.Key_End:
            self._set_playhead(self.project.source_duration)
            self.playhead_settled.emit(self.playhead)
        else:
            super().keyPressEvent(event)

    def _nudge(self, direction, shift):
        fps = (self.project.media.fps
               if self.project.loaded and self.project.media.fps else 25.0)
        step = 1.0 if shift else 1.0 / fps
        self._set_playhead(self.playhead + direction * step)
        self.playhead_settled.emit(self.playhead)

    # -- selection helpers --------------------------------------------------

    def set_in_here(self):
        """Set the in-point at the playhead (I)."""
        if not self.project.loaded:
            return
        self.project.set_in(self.playhead)
        self.selection_changed.emit()
        self.update()

    def set_out_here(self):
        """Set the out-point at the playhead (O)."""
        if not self.project.loaded:
            return
        self.project.set_out(self.playhead)
        self.selection_changed.emit()
        self.update()

    def _set_playhead(self, seconds):
        seconds = max(0.0, min(seconds, self.project.source_duration))
        if abs(seconds - self.playhead) > 1e-6:
            self.playhead = seconds
            self.playhead_moved.emit(seconds)
            self.update()

    def set_playhead_silent(self, seconds):
        """Move the playhead without re-emitting (playback sync)."""
        self.playhead = max(0.0, min(seconds, self.project.source_duration))
        self.update()

    def reset(self):
        self.playhead = self.project.in_point
        self.update()


def _tick_label(seconds, step):
    """Label a ruler tick at a resolution matching the tick spacing."""
    if step >= 1:
        return estimator.fmt_time(seconds)[:-4]
    if step >= 0.1:
        return f"{seconds:.1f}s"
    return f"{seconds:.2f}s"
