"""Frame decoding built on PyAV.

Benchmarked on this machine (1080p/4K h264):

    random seek, 1 thread ......... 76 - 330 ms   (unusable for scrubbing)
    random seek, FRAME threads .... 17 -  69 ms   (usable, not smooth)
    random seek, via 540p proxy ...  8.7 ms       (111 fps - the target)

So two rules drive this module: always decode with FRAME threading, and scrub
against a proxy when one exists. SLICE threading measured *slower* than single
threaded for seeking - never use it here.
"""

import os
import threading
from collections import OrderedDict

import av
import numpy as np

from PySide6.QtGui import QImage

# Decoding a frame we already hold is pure waste; scrubbing revisits frames
# constantly (drag left, drag back right), so a small LRU pays for itself.
CACHE_SIZE = 120


def _to_qimage(frame):
    """PyAV frame -> QImage, copied so Qt does not alias the decoder buffer."""
    array = frame.to_ndarray(format="rgb24")
    # Depending on the codec and width, to_ndarray can hand back a padded
    # (non-C-contiguous) view. QImage refuses such a buffer outright, so
    # normalise before wrapping it.
    if not array.flags["C_CONTIGUOUS"]:
        array = np.ascontiguousarray(array)

    height, width = array.shape[:2]
    image = QImage(array.data, width, height, width * 3, QImage.Format_RGB888)
    # PyAV reuses its buffers, so the QImage must own its pixels.
    return image.copy()


class ClipDecoder:
    """Seek-and-decode one media file, with an LRU of recent frames.

    Not thread-safe on its own; PlaybackEngine owns one per file and only
    touches it from the decode thread.
    """

    def __init__(self, path, thread_count=8):
        self.path = path
        self._container = av.open(path)
        self._stream = self._container.streams.video[0]
        # FRAME threading is what makes seeking fast (see module docstring).
        self._stream.thread_type = "FRAME"
        self._stream.thread_count = thread_count

        self.time_base = self._stream.time_base
        self.fps = float(self._stream.average_rate or 25.0)
        self.width = self._stream.codec_context.width
        self.height = self._stream.codec_context.height
        duration = self._stream.duration
        self.duration = (
            float(duration * self.time_base) if duration
            else float(self._container.duration / av.time_base if self._container.duration else 0.0)
        )

        self._cache = OrderedDict()
        self._last_pos = None

    # -- internals ----------------------------------------------------------

    def _cache_key(self, seconds):
        """Quantise to the frame grid so near-identical requests share a slot."""
        return int(round(seconds * self.fps))

    def _store(self, key, image):
        self._cache[key] = image
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)

    def _decode_forward(self, target):
        """Decode from the current position until reaching `target` seconds.

        Running off the end of the file is normal (the playhead sits on the
        last frame, or a clip's out-point is the file end), so EOF returns the
        best frame we have rather than raising.
        """
        if self._container is None:
            return None

        last_key = None
        try:
            for frame in self._container.decode(self._stream):
                if frame.pts is None:
                    continue
                stamp = float(frame.pts * self.time_base)
                key = self._cache_key(stamp)
                last_key = key
                if key not in self._cache:
                    self._store(key, _to_qimage(frame))
                # Frame durations are finite, so accept the first frame that
                # covers the target rather than requiring an exact timestamp.
                if stamp >= target - (0.5 / self.fps):
                    self._last_pos = stamp
                    return self._cache.get(key)
        except (av.EOFError, EOFError, StopIteration):
            pass
        except av.FFmpegError:
            # A damaged packet mid-stream should not kill the preview.
            pass

        # Past the end: hand back the final frame we decoded, which is what
        # the viewer expects to see at the tail of a clip.
        if last_key is not None:
            self._last_pos = None
            return self._cache.get(last_key)
        return None

    # -- public -------------------------------------------------------------

    def frame_at(self, seconds):
        """Nearest decoded frame at `seconds`, as a QImage (None if past end)."""
        if self._container is None:      # closed underneath us during shutdown
            return None
        seconds = max(0.0, min(seconds, max(0.0, self.duration - 1e-4)))
        key = self._cache_key(seconds)

        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached

        # Playing forward: keep decoding rather than paying for a seek.
        if (self._last_pos is not None
                and 0 <= seconds - self._last_pos < 1.0):
            image = self._decode_forward(seconds)
            if image is not None:
                return image

        try:
            self._container.seek(
                int(seconds / self.time_base),
                stream=self._stream, backward=True, any_frame=False,
            )
        except (av.FFmpegError, AttributeError, ValueError):
            return None
        return self._decode_forward(seconds)

    def nearest_cached(self, seconds, tolerance=0.5):
        """Closest already-decoded frame, without touching the decoder.

        Used for mid-drag preview: returning a neighbouring frame instantly
        beats decoding the exact one too late to matter.
        """
        if not self._cache:
            return None
        want = self._cache_key(seconds)

        exact = self._cache.get(want)
        if exact is not None:
            return exact

        limit = max(1, int(tolerance * self.fps))
        best = None
        best_gap = limit + 1
        for key in self._cache:
            gap = abs(key - want)
            if gap < best_gap:
                best, best_gap = key, gap
        return self._cache.get(best) if best is not None else None

    def close(self):
        # Closing a PyAV container twice is not safe, so drop the reference
        # as well as closing it.
        container, self._container = getattr(self, "_container", None), None
        if container is not None:
            try:
                container.close()
            except Exception:
                pass
        self._cache.clear()


class DecoderPool:
    """Keeps a bounded set of open decoders, keyed by file path.

    Opening a container costs real time, so reuse matters when the app
    cuts back and forth between the same few sources.
    """

    def __init__(self, limit=6):
        self.limit = limit
        self._decoders = OrderedDict()
        self._lock = threading.Lock()

    def get(self, path):
        with self._lock:
            decoder = self._decoders.get(path)
            if decoder is not None:
                self._decoders.move_to_end(path)
                return decoder

        # Open outside the lock - it can take tens of milliseconds.
        try:
            decoder = ClipDecoder(path)
        except Exception:
            return None

        with self._lock:
            if path in self._decoders:          # lost a race; keep the winner
                decoder.close()
                return self._decoders[path]
            self._decoders[path] = decoder
            self._decoders.move_to_end(path)
            while len(self._decoders) > self.limit:
                _key, old = self._decoders.popitem(last=False)
                old.close()
        return decoder

    def peek(self, path):
        """Return an already-open decoder, or None. Never opens a container."""
        with self._lock:
            return self._decoders.get(path)

    def drop(self, path):
        with self._lock:
            decoder = self._decoders.pop(path, None)
        if decoder:
            decoder.close()

    def close_all(self):
        with self._lock:
            decoders = list(self._decoders.values())
            self._decoders.clear()
        for decoder in decoders:
            decoder.close()


def probe_dimensions(path):
    """Cheap width/height/fps read without decoding pixels."""
    try:
        with av.open(path) as container:
            stream = container.streams.video[0]
            return (stream.codec_context.width,
                    stream.codec_context.height,
                    float(stream.average_rate or 25.0))
    except Exception:
        return None
