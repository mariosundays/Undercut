"""Playback / scrub engine for a single clip.

Runs decoding on its own thread and hands finished frames to the UI. Two rules
keep scrubbing responsive:

  * Only the newest request matters. While dragging, the user generates far
    more requests than can be served; serving stale ones wastes the budget, so
    a pending request is *replaced*, never queued.
  * Playback drops frames rather than falling behind. Wall-clock decides which
    frame is due, so a slow decode loses frames instead of drifting late.
"""

import threading
import time

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QImage

from .decoder import DecoderPool


class FrameRequest:
    __slots__ = ("position", "serial", "draft")

    def __init__(self, position, serial, draft=False):
        self.position = position
        self.serial = serial
        # A draft request is one issued mid-drag: it may be answered from a
        # nearby cached frame, the way Resolve shows an approximate frame
        # while you are moving and the exact one when you settle.
        self.draft = draft


class PlaybackEngine(QObject):
    """Owns the decode thread and the composited preview frame."""

    frame_ready = Signal(QImage, float)   # decoded frame, source position
    position_changed = Signal(float)      # during playback
    stopped = Signal()
    error = Signal(str)                   # decode failure, surfaced in the UI

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.pool = DecoderPool()

        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._pending = None
        self._serial = 0
        self._running = True
        self._shutdown = False

        self._playing = False
        self._play_start_wall = 0.0
        self._play_start_pos = 0.0
        self.loop = False

        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    # -- public API ---------------------------------------------------------

    def request(self, position, draft=False):
        """Ask for the frame at `position`. Supersedes any pending request.

        `draft` marks a mid-drag request. Those may be served from a nearby
        cached frame so the picture keeps moving under the cursor; the exact
        frame arrives on the non-draft request sent when the drag settles.
        """
        with self._lock:
            self._serial += 1
            self._pending = FrameRequest(position, self._serial, draft)
        self._wake.set()

    def play(self, from_position):
        self._playing = True
        self._play_start_wall = time.perf_counter()
        self._play_start_pos = from_position
        self._wake.set()

    def pause(self):
        self._playing = False
        if not self._shutdown:
            self.stopped.emit()

    @property
    def playing(self):
        return self._playing

    def shutdown(self):
        """Stop the decode thread and release every open container.

        Safe to call more than once - the window's closeEvent and an explicit
        caller (tests) both invoke it, and closing PyAV containers twice or
        joining a finished thread destabilises interpreter teardown.
        """
        if self._shutdown:
            return
        # Set this BEFORE anything else: it is what stops the decode thread
        # emitting into Qt while we tear down (such an emit aborts the
        # process), and it must be visible to that thread immediately.
        self._shutdown = True
        self._running = False
        self._playing = False

        # Drop the connections too, so a signal already in flight lands
        # nowhere. Disconnecting a signal that was never connected warns
        # rather than raising, so suppress that noise.
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            for signal in (self.frame_ready, self.position_changed,
                           self.stopped, self.error):
                try:
                    signal.disconnect()
                except (RuntimeError, TypeError):
                    pass    # never connected

        self._wake.set()
        self._thread.join(timeout=2.0)
        self.pool.close_all()

    # -- decode thread ------------------------------------------------------

    def _live(self):
        """Whether it is still safe to emit into Qt from the decode thread.

        Once shutdown starts, the receiving widgets may be going away and an
        emit from this thread aborts the process.
        """
        return self._running and not self._shutdown

    def _loop(self):
        while self._running:
            # A decode failure must never kill this thread - if it did, the
            # preview would go permanently dead with no visible cause.
            try:
                self._tick()
            except Exception as exc:
                self._playing = False
                # Emitting into Qt while shutdown is tearing the object down
                # aborts the process, so stay silent once we are stopping.
                if self._running and not self._shutdown:
                    self.error.emit(str(exc))
                # Back off briefly so a persistently failing source cannot
                # spin the CPU.
                time.sleep(0.1)

    def _tick(self):
        if self._playing:
            self._serve_playback()
            return

        self._wake.wait(0.05)
        self._wake.clear()
        if not self._running:
            return

        with self._lock:
            request = self._pending
            self._pending = None
        if request is not None:
            self._serve(request.position, request.serial, request.draft)

    def _serve_playback(self):
        """Emit the frame that is due right now, skipping any we are late for."""
        # Advance through the source faster when sped up, so the preview runs
        # at the rate the export will.
        speed = max(0.01, self.project.settings.speed)
        elapsed = (time.perf_counter() - self._play_start_wall) * speed
        position = self._play_start_pos + elapsed

        if position >= self.project.out_point:
            if self.loop:
                # Restart the selection rather than stopping at the tail.
                self._play_start_wall = time.perf_counter()
                self._play_start_pos = self.project.in_point
                position = self.project.in_point
            else:
                self._playing = False
                if self._live():
                    self.stopped.emit()
                return

        # A scrub arriving mid-playback wins - the user is dragging.
        with self._lock:
            request = self._pending
            self._pending = None
        if request is not None:
            self._play_start_wall = time.perf_counter()
            self._play_start_pos = request.position
            position = request.position

        image = self._compose(position)
        if image is not None and self._live():
            self.frame_ready.emit(image, position)
            self.position_changed.emit(position)

        # Pace to the project frame rate; if decoding overran, loop straight
        # on and the next position calculation naturally drops frames.
        fps = self.project.preview_fps()
        # Positions are in source time, so convert back to wall time before
        # pacing - at 2x a second of footage should take half a second.
        target = (self._play_start_wall
                  + (position - self._play_start_pos) / speed
                  + 1.0 / fps)
        slack = target - time.perf_counter()
        if slack > 0:
            time.sleep(min(slack, 0.25))

    def _serve(self, position, serial, draft=False):
        # Mid-drag, answering instantly from a cached neighbour beats decoding
        # the exact frame late - the picture tracks the cursor, and the
        # settle request that follows delivers the precise frame.
        if draft:
            approx = self._cached_compose(position)
            if approx is not None and self._live():
                self.frame_ready.emit(approx, position)
                return

        image = self._compose(position)
        # Drop the result if a newer request landed while we decoded.
        with self._lock:
            if self._pending is not None and self._pending.serial > serial:
                return
        if image is not None and self._live():
            self.frame_ready.emit(image, position)

    # -- compositing --------------------------------------------------------

    def _decode(self, position):
        """Decoded frame at a position in the source file, or None."""
        project = self.project
        if not project.loaded:
            return None
        decoder = self.pool.get(project.preview_source())
        if decoder is None:
            return None
        return decoder.frame_at(position)

    def _cached_compose(self, position):
        """Already-decoded frame only; None if nothing close.

        Never touches the decoder, so it returns in microseconds and can
        answer every mouse-move during a drag.
        """
        project = self.project
        if not project.loaded:
            return None
        decoder = self.pool.peek(project.preview_source())
        if decoder is None:
            return None
        return decoder.nearest_cached(position)

    def _compose(self, position):
        """The preview frame at `position` (a time in the SOURCE file)."""
        return self._decode(position)
