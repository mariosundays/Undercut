"""The preview, with an adjustable crop box drawn over it.

Two modes. Normally the preview shows what the export will contain - just the
cropped region, letterboxed. In crop mode it shows the whole frame with the
box on top: drag inside to move it, drag a corner or edge to resize, drag
outside to draw a new one. The box is kept in source-frame fractions on the
Project, so it is independent of the preview size and of the proxy.
"""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

HANDLE = 8          # handle square, px
GRAB = 10           # how close counts as "on" a handle or edge, px
MIN_BOX = 16        # smallest box, px on screen

ACCENT = QColor("#5a9ac0")
DIM = QColor(0, 0, 0, 150)


class PreviewWidget(QWidget):
    """Letterboxed display for the decoded frame, with the crop editor."""

    crop_changed = Signal()

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self._image = None
        self.editing = False
        # Width / height of the box in source pixels, or None for free.
        self.aspect = None
        self._drag = None           # (mode, start point, start box)
        self.setMinimumHeight(280)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

    def set_image(self, image):
        self._image = image
        self.update()

    def clear(self):
        self._image = None
        self.update()

    def set_editing(self, on):
        self.editing = on
        self._drag = None
        self.unsetCursor()
        self.update()

    def set_aspect(self, aspect):
        """Lock the box to `aspect` (w/h), refitting the current box to it."""
        self.aspect = aspect
        frame = self._frame_rect()
        if aspect is None or frame.isEmpty():
            return
        box = self._box(frame)
        centre = box.center()
        # Keep the current width unless that makes the box too tall.
        w = box.width()
        h = w / aspect
        if h > box.height():
            h = box.height()
            w = h * aspect
        if box == frame:
            # From the full frame, take the biggest box that fits.
            w = min(frame.width(), frame.height() * aspect)
            h = w / aspect
        new = QRectF(0, 0, w, h)
        new.moveCenter(centre)
        self._commit(self._shift_inside(new, frame), frame)

    # -- geometry -----------------------------------------------------------

    def _frame_rect(self):
        """Where the whole source frame sits in the widget, letterboxed."""
        if self._image is None or self._image.isNull():
            return QRectF()
        media = self.project.media
        # The source's shape, not the image's: the proxy is the same shape,
        # but going by the source keeps this right if it ever is not.
        iw = media.width if media else self._image.width()
        ih = media.height if media else self._image.height()
        scale = min(self.width() / iw, self.height() / ih)
        w, h = iw * scale, ih * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _box(self, frame):
        crop = self.project.crop
        if crop is None:
            return QRectF(frame)
        x, y, w, h = crop
        return QRectF(frame.x() + x * frame.width(),
                      frame.y() + y * frame.height(),
                      w * frame.width(), h * frame.height())

    def _commit(self, box, frame):
        self.project.set_crop((
            (box.x() - frame.x()) / frame.width(),
            (box.y() - frame.y()) / frame.height(),
            box.width() / frame.width(),
            box.height() / frame.height(),
        ))
        self.update()
        self.crop_changed.emit()

    @staticmethod
    def _shift_inside(box, frame):
        """Slide `box` back inside `frame` without resizing it."""
        box = QRectF(box)
        box.setWidth(min(box.width(), frame.width()))
        box.setHeight(min(box.height(), frame.height()))
        if box.left() < frame.left():
            box.moveLeft(frame.left())
        if box.top() < frame.top():
            box.moveTop(frame.top())
        if box.right() > frame.right():
            box.moveRight(frame.right())
        if box.bottom() > frame.bottom():
            box.moveBottom(frame.bottom())
        return box

    def _hit(self, pos, box):
        """Which part of the box is under `pos`: 'l','tr','move',... or None."""
        near = lambda a, b: abs(a - b) <= GRAB
        inside_x = box.left() - GRAB <= pos.x() <= box.right() + GRAB
        inside_y = box.top() - GRAB <= pos.y() <= box.bottom() + GRAB
        vert = ("t" if near(pos.y(), box.top()) else
                "b" if near(pos.y(), box.bottom()) else "")
        horiz = ("l" if near(pos.x(), box.left()) else
                 "r" if near(pos.x(), box.right()) else "")
        if inside_x and inside_y and (vert or horiz):
            return vert + horiz
        if box.contains(pos):
            return "move"
        return None

    # -- mouse --------------------------------------------------------------

    _CURSORS = {
        "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
        "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
        "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
        "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
        "move": Qt.SizeAllCursor,
    }

    def mousePressEvent(self, event):
        frame = self._frame_rect()
        if not self.editing or frame.isEmpty() or event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        pos = event.position()
        box = self._box(frame)
        mode = self._hit(pos, box)
        # With no crop yet the "box" is the whole frame, which cannot move -
        # dragging inside it should draw one, not do nothing.
        if mode is None or (mode == "move" and self.project.crop is None):
            start = self._clamp(pos, frame)
            mode, box = "new", QRectF(start, start)
        self._drag = (mode, pos, QRectF(box))

    def mouseMoveEvent(self, event):
        frame = self._frame_rect()
        pos = event.position()
        if not self.editing or frame.isEmpty():
            return super().mouseMoveEvent(event)
        if self._drag is None:
            mode = self._hit(pos, self._box(frame))
            if mode == "move" and self.project.crop is None:
                mode = None
            self.setCursor(self._CURSORS.get(mode, Qt.CrossCursor))
            return

        mode, start, box = self._drag
        if mode == "move":
            moved = box.translated(pos - start)
            self._commit(self._shift_inside(moved, frame), frame)
            return
        if mode == "new":
            self._commit(self._resize_free(box.topLeft(), pos, frame), frame)
            return
        self._commit(self._resize(mode, box, pos, frame), frame)

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            self._drag = None
            self.crop_changed.emit()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        """Double-click resets to the whole frame."""
        if self.editing:
            self.project.set_crop(None)
            self.update()
            self.crop_changed.emit()

    # -- resizing -----------------------------------------------------------

    @staticmethod
    def _clamp(pos, frame):
        return QPointF(min(max(pos.x(), frame.left()), frame.right()),
                       min(max(pos.y(), frame.top()), frame.bottom()))

    def _resize_free(self, anchor, pos, frame):
        """A box spanned from `anchor` to `pos`, honouring the aspect lock."""
        pos = self._clamp(pos, frame)
        dx, dy = pos.x() - anchor.x(), pos.y() - anchor.y()
        if self.aspect:
            # Whichever axis is dragged further leads; the other follows.
            w = max(abs(dx), abs(dy) * self.aspect)
            h = w / self.aspect
            # ...but never past the frame edge in either direction.
            room_x = (frame.right() - anchor.x() if dx >= 0
                      else anchor.x() - frame.left())
            room_y = (frame.bottom() - anchor.y() if dy >= 0
                      else anchor.y() - frame.top())
            fit = min(1.0, room_x / w if w else 1.0, room_y / h if h else 1.0)
            w, h = w * fit, h * fit
            dx = w if dx >= 0 else -w
            dy = h if dy >= 0 else -h
        box = QRectF(anchor, QPointF(anchor.x() + dx, anchor.y() + dy))
        box = box.normalized()
        if box.width() < MIN_BOX or box.height() < MIN_BOX:
            box.setWidth(max(box.width(), MIN_BOX))
            box.setHeight(max(box.height(), MIN_BOX))
            box = self._shift_inside(box, frame)
        return box

    def _resize(self, mode, box, pos, frame):
        if len(mode) == 2:
            # A corner: span from the opposite one.
            anchor = QPointF(box.right() if "l" in mode else box.left(),
                             box.bottom() if "t" in mode else box.top())
            return self._resize_free(anchor, pos, frame)

        pos = self._clamp(pos, frame)
        new = QRectF(box)
        if mode == "l":
            new.setLeft(min(pos.x(), box.right() - MIN_BOX))
        elif mode == "r":
            new.setRight(max(pos.x(), box.left() + MIN_BOX))
        elif mode == "t":
            new.setTop(min(pos.y(), box.bottom() - MIN_BOX))
        elif mode == "b":
            new.setBottom(max(pos.y(), box.top() + MIN_BOX))

        if self.aspect:
            # An edge drag resizes the other axis around the box's centre.
            centre = new.center()
            if mode in "lr":
                h = min(new.width() / self.aspect, frame.height())
                new.setHeight(h)
                new.setWidth(h * self.aspect)
                new.moveCenter(QPointF(new.center().x(), centre.y()))
                if mode == "l":
                    new.moveRight(box.right())
                else:
                    new.moveLeft(box.left())
            else:
                w = min(new.height() * self.aspect, frame.width())
                new.setWidth(w)
                new.setHeight(w / self.aspect)
                new.moveCenter(QPointF(centre.x(), new.center().y()))
                if mode == "t":
                    new.moveBottom(box.bottom())
                else:
                    new.moveTop(box.top())
            new = self._shift_inside(new, frame)
        return new

    # -- painting -----------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.black)
        if self._image is None or self._image.isNull():
            painter.end()
            return
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        crop = self.project.crop
        if not self.editing and crop is not None:
            self._paint_cropped(painter, crop)
            painter.end()
            return

        frame = self._frame_rect()
        painter.drawImage(frame, self._image)
        if self.editing:
            self._paint_box(painter, frame)
        painter.end()

    def _paint_cropped(self, painter, crop):
        """Only the kept region, letterboxed - what the export will show."""
        iw, ih = self._image.width(), self._image.height()
        x, y, w, h = crop
        source = QRectF(x * iw, y * ih, w * iw, h * ih)
        media = self.project.media
        # Shape from source pixels, so a non-matching proxy cannot distort it.
        shape_w = w * (media.width if media else iw)
        shape_h = h * (media.height if media else ih)
        scale = min(self.width() / shape_w, self.height() / shape_h)
        tw, th = shape_w * scale, shape_h * scale
        target = QRectF((self.width() - tw) / 2, (self.height() - th) / 2, tw, th)
        painter.drawImage(target, self._image, source)

    def _paint_box(self, painter, frame):
        box = self._box(frame)

        # Dim everything outside the box.
        painter.setPen(Qt.NoPen)
        painter.setBrush(DIM)
        painter.drawRect(QRectF(frame.left(), frame.top(),
                                frame.width(), box.top() - frame.top()))
        painter.drawRect(QRectF(frame.left(), box.bottom(),
                                frame.width(), frame.bottom() - box.bottom()))
        painter.drawRect(QRectF(frame.left(), box.top(),
                                box.left() - frame.left(), box.height()))
        painter.drawRect(QRectF(box.right(), box.top(),
                                frame.right() - box.right(), box.height()))

        # Rule of thirds, faint.
        painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
        for i in (1, 2):
            x = box.left() + box.width() * i / 3
            y = box.top() + box.height() * i / 3
            painter.drawLine(QPointF(x, box.top()), QPointF(x, box.bottom()))
            painter.drawLine(QPointF(box.left(), y), QPointF(box.right(), y))

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(ACCENT, 1.5))
        painter.drawRect(box)

        painter.setPen(Qt.NoPen)
        painter.setBrush(ACCENT)
        xs = (box.left(), box.center().x(), box.right())
        ys = (box.top(), box.center().y(), box.bottom())
        for i, x in enumerate(xs):
            for j, y in enumerate(ys):
                if i == 1 and j == 1:
                    continue
                painter.drawRect(QRectF(x - HANDLE / 2, y - HANDLE / 2,
                                        HANDLE, HANDLE))

        # The size the export will crop to, in source pixels.
        pixels = self.project.crop_pixels()
        if pixels is None and self.project.media:
            pixels = (0, 0, self.project.media.width, self.project.media.height)
        if pixels:
            text = f"{pixels[2]} x {pixels[3]}"
            painter.setPen(QColor("#e0e0e0"))
            metrics = painter.fontMetrics()
            tw = metrics.horizontalAdvance(text) + 10
            th = metrics.height() + 4
            tag = QRectF(box.left(), max(frame.top(), box.top() - th - 2), tw, th)
            painter.fillRect(tag, QColor(0, 0, 0, 170))
            painter.drawText(tag, Qt.AlignCenter, text)
