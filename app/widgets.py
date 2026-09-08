"""The scrubbable size-target field and its saved presets."""

import json
import os

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QInputDialog, QLabel, QMenu, QSlider

PRESET_FILE = os.path.join(
    os.path.expandvars(r"%APPDATA%"), "Undercut", "presets.json"
)

DEFAULT_PRESETS = [
    {"name": "Cargo limit", "mb": 11.5},
    {"name": "Small web", "mb": 5.0},
]


class TargetField(QLabel):
    """The size target, shown large and scrubbed by dragging.

    Deliberately built as a label rather than a styled spin box: this is a
    headline number the user steers, so it should read like one and respond
    to a horizontal drag the way DCC numeric fields do.

    Lock it to stop accidental changes; double-click for presets.
    """

    value_changed = Signal(float)
    preset_requested = Signal()
    lock_toggled = Signal(bool)

    MIN_MB = 0.1
    MAX_MB = 2000.0

    def __init__(self, value=11.5, parent=None):
        super().__init__(parent)
        self._value = value
        self.locked = False

        self._drag_origin = None
        self._drag_start_value = 0.0
        self._dragged = False

        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.setCursor(Qt.SizeHorCursor)
        self.setToolTip(
            "Drag left/right to change · double-click for presets\n"
            "Ctrl = fine, Shift = coarse"
        )
        self._restyle()

    # -- value --------------------------------------------------------------

    def value(self):
        return self._value

    def setValue(self, mb):
        mb = max(self.MIN_MB, min(self.MAX_MB, float(mb)))
        if abs(mb - self._value) < 1e-9:
            return
        self._value = mb
        self._restyle()
        self.value_changed.emit(mb)

    def set_locked(self, locked):
        self.locked = bool(locked)
        self.setCursor(Qt.ForbiddenCursor if self.locked else Qt.SizeHorCursor)
        self._restyle()
        self.lock_toggled.emit(self.locked)

    def _restyle(self):
        self.setText(f"{self._value:.2f} MB")
        colour = "#6a7a86" if self.locked else "#9ec4dc"
        self.setStyleSheet(
            f"font-size: 26px; font-weight: bold; color: {colour};"
        )

    # -- scrubbing ----------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or self.locked:
            return
        self._drag_origin = event.position().x()
        self._drag_start_value = self._value
        self._dragged = False

    def mouseMoveEvent(self, event):
        if self._drag_origin is None or self.locked:
            return
        delta = event.position().x() - self._drag_origin
        if abs(delta) < 3:
            return
        self._dragged = True

        step = 0.05
        if event.modifiers() & Qt.ControlModifier:
            step = 0.01
        elif event.modifiers() & Qt.ShiftModifier:
            step = 0.5
        self.setValue(self._drag_start_value + delta * step)

    def mouseReleaseEvent(self, event):
        self._drag_origin = None

    def mouseDoubleClickEvent(self, event):
        if not self.locked:
            self.preset_requested.emit()

    def wheelEvent(self, event):
        if self.locked:
            return
        step = 0.1
        if event.modifiers() & Qt.ControlModifier:
            step = 0.01
        elif event.modifiers() & Qt.ShiftModifier:
            step = 1.0
        self.setValue(
            self._value + (step if event.angleDelta().y() > 0 else -step)
        )


class SpeedSlider(QSlider):
    """A slider that snaps back to its default on double-click.

    QSlider has no double-click signal of its own, and a stray double-click
    otherwise just moves the handle - which is a poor way to get back to 1x.
    """

    def __init__(self, orientation, default, parent=None):
        super().__init__(orientation, parent)
        self._default = default

    def mouseDoubleClickEvent(self, event):
        self.setValue(self._default)
        event.accept()


class PresetStore:
    """Named size presets, persisted in %APPDATA%\\Undercut\\presets.json."""

    def __init__(self):
        self.presets = list(DEFAULT_PRESETS)
        self.load()

    def load(self):
        try:
            with open(PRESET_FILE, encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, list) and data:
                # Keep only well-formed entries; a corrupt file should not
                # take the app down.
                clean = [
                    p for p in data
                    if isinstance(p, dict) and "name" in p and "mb" in p
                ]
                if clean:
                    self.presets = clean
        except (OSError, ValueError):
            pass

    def save(self):
        try:
            os.makedirs(os.path.dirname(PRESET_FILE), exist_ok=True)
            with open(PRESET_FILE, "w", encoding="utf-8") as handle:
                json.dump(self.presets, handle, indent=2)
        except OSError:
            pass

    def add(self, name, mb):
        """Add or replace a preset by name."""
        for preset in self.presets:
            if preset["name"].lower() == name.lower():
                preset["mb"] = mb
                self.save()
                return
        self.presets.append({"name": name, "mb": mb})
        self.save()

    def remove(self, name):
        self.presets = [p for p in self.presets
                        if p["name"].lower() != name.lower()]
        self.save()


def show_preset_menu(parent, store, current_mb, on_pick):
    """Popup listing saved presets, plus save/delete actions."""
    menu = QMenu(parent)

    for preset in store.presets:
        action = menu.addAction(f"{preset['name']}   {preset['mb']:.2f} MB")
        action.triggered.connect(
            lambda _c=False, mb=preset["mb"]: on_pick(mb)
        )

    menu.addSeparator()
    save_action = menu.addAction(f"Save {current_mb:.2f} MB as preset...")

    delete_menu = None
    if store.presets:
        delete_menu = menu.addMenu("Delete preset")
        for preset in store.presets:
            act = delete_menu.addAction(preset["name"])
            act.triggered.connect(
                lambda _c=False, n=preset["name"]: store.remove(n)
            )

    chosen = menu.exec(parent.mapToGlobal(QPoint(0, parent.height())))

    if chosen is save_action:
        name, ok = QInputDialog.getText(
            parent, "Save preset",
            f"Name for {current_mb:.2f} MB:",
        )
        if ok and name.strip():
            store.add(name.strip(), current_mb)
            return True
    return False
