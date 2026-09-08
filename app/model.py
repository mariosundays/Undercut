"""One video, an in/out selection, and the export settings.

Deliberately small: the job is "load a clip, pick a range, hit a size limit".
No tracks, no clip list, no compositing.
"""

import os

from . import estimator


class ExportSettings:
    """What the encoder needs, and what the size estimate reads."""

    def __init__(self):
        self.target_mb = 11.5          # cargo.collective caps uploads at 12 MB
        self.two_pass = True
        self.preset = "slow"
        self.max_width = 1920          # 0 = keep source
        self.fps = 0                   # 0 = keep source
        self.speed = 1.0               # 1.0 = real time, 2.0 = twice as fast
        self.audio = False             # thumbnails are silent by default
        self.audio_kbps = 128
        self.faststart = True

    @property
    def target_bytes(self):
        return int(self.target_mb * estimator.MB)

    def effective_audio_kbps(self):
        return self.audio_kbps if self.audio else 0


class Project:
    """The loaded video plus the selected range."""

    def __init__(self):
        self.media = None
        self.in_point = 0.0
        self.out_point = 0.0
        self.settings = ExportSettings()
        self.output_path = ""
        self.proxy_path = None

    # -- loading ------------------------------------------------------------

    def load(self, media):
        """Replace the source; the selection resets to the whole clip."""
        self.media = media
        self.in_point = 0.0
        self.out_point = media.duration
        self.output_path = ""
        self.proxy_path = None

    @property
    def loaded(self):
        return self.media is not None

    @property
    def duration(self):
        """Length of the SELECTION in the source, before any speed change.

        This is the span between the handles - what the trim bar draws and
        what the encoder reads from the file. For how long the exported clip
        actually runs, and what the size is calculated from, use
        `output_duration`.
        """
        if not self.loaded:
            return 0.0
        return max(0.0, self.out_point - self.in_point)

    @property
    def output_duration(self):
        """How long the export runs once sped up.

        Speed drops frames rather than raising the frame rate, so 4s at 2x is
        2s of the same fps - half the frames, and roughly half the bytes. Every
        size calculation works from this, never from the raw selection.
        """
        return self.duration / max(0.01, self.settings.speed)

    @property
    def source_duration(self):
        return self.media.duration if self.loaded else 0.0

    # -- selection ----------------------------------------------------------

    def set_in(self, seconds):
        """Move the in-point, keeping at least one frame of selection."""
        if not self.loaded:
            return
        limit = self.out_point - self._min_span()
        self.in_point = max(0.0, min(seconds, limit))

    def set_out(self, seconds):
        if not self.loaded:
            return
        floor = self.in_point + self._min_span()
        self.out_point = min(self.source_duration, max(seconds, floor))

    def slide_selection(self, seconds):
        """Move in and out together by `seconds`, keeping the span fixed.

        Both points move as one, so the cut keeps the duration - and therefore
        the size and quality - already dialled in, and only its position in the
        source changes. set_in/set_out cannot do this: they clamp against each
        other, so moving one then the other would squash the span at the ends.
        """
        if not self.loaded:
            return
        span = self.out_point - self.in_point
        # Clamp the move itself, so the selection stops at either end of the
        # source with its length intact rather than being trimmed by it.
        shift = max(-self.in_point,
                    min(seconds, self.source_duration - self.out_point))
        self.in_point += shift
        self.out_point = self.in_point + span

    def select_all(self):
        if self.loaded:
            self.in_point = 0.0
            self.out_point = self.source_duration

    def _min_span(self):
        """One frame, so the selection can never invert or hit zero."""
        fps = self.media.fps if self.loaded and self.media.fps else 25.0
        return 1.0 / fps

    # -- output -------------------------------------------------------------

    def output_geometry(self):
        """Resolved (width, height, fps) after the downscale / fps settings."""
        if not self.loaded:
            return 0, 0, 0.0

        width, height, fps = self.media.width, self.media.height, self.media.fps

        limit = self.settings.max_width
        if limit and width > limit:
            scale = limit / width
            width = limit
            # Keep both dimensions even - yuv420p requires it.
            height = max(2, int(round(height * scale / 2)) * 2)
        width = max(2, int(round(width / 2)) * 2)

        if self.settings.fps:
            fps = min(fps, self.settings.fps) if fps else self.settings.fps

        return width, height, fps

    def preview_source(self):
        """File the preview decodes - the proxy when one has been built."""
        return self.proxy_path or (self.media.path if self.loaded else "")

    def preview_fps(self):
        return (self.media.fps if self.loaded and self.media.fps else 25.0)

    def has_audio(self):
        return bool(self.settings.audio and self.loaded and self.media.has_audio)

    def default_output(self):
        """Suggested output path, next to the source."""
        if self.output_path:
            return self.output_path
        if not self.loaded:
            return ""
        folder = os.path.dirname(self.media.path)
        stem = os.path.splitext(os.path.basename(self.media.path))[0]
        return os.path.join(folder, f"{stem}_thumb.mp4")

    # -- the live readout ---------------------------------------------------

    def estimate(self):
        """What the current selection will export as.

        Two readings, because they answer different questions:

        `bytes` is the size at the chosen target - always the target, since
        the bitrate is solved backwards from it.

        `quality_bytes` is the size the selection would take at *visually
        transparent* quality. That is the number that actually moves as you
        drag the handles: below the target, the cut fits comfortably; above
        it, the target is squeezing the footage.
        """
        # Everything below is about the EXPORTED clip, so it works from the
        # sped-up length: at 2x there are half as many frames to encode, and
        # the file is roughly half the size.
        duration = self.output_duration
        if duration <= 0:
            return {
                "duration": 0.0, "bytes": 0, "bitrate": 0, "quality": None,
                "quality_bytes": 0, "fits": True, "headroom": 0.0,
                "low": 0, "high": 0,
            }

        settings = self.settings
        akbps = settings.effective_audio_kbps()
        audio = self.has_audio()
        width, height, fps = self.output_geometry()

        budget_bps = estimator.bitrate_for_target(
            settings.target_bytes, duration, akbps, audio
        )

        # What this selection needs to look transparent at this resolution.
        want_bps = estimator.TRANSPARENT_BPP * width * height * max(fps, 1.0)
        quality_bytes = estimator.size_for_bitrate(
            want_bps, duration, akbps, audio
        )

        # The target is a ceiling: a cut that needs less gets a smaller file,
        # rather than being padded up to the budget. Must match encoder.py.
        bitrate = min(budget_bps, want_bps)
        size = estimator.size_for_bitrate(bitrate, duration, akbps, audio)
        margin = 0.02 if settings.two_pass else 0.08

        return {
            "duration": duration,
            "bytes": size,
            "low": int(size * (1 - margin)),
            "high": int(size * (1 + margin)),
            "bitrate": bitrate,
            "quality": estimator.quality_for(bitrate, width, height, fps),
            "quality_bytes": quality_bytes,
            "fits": quality_bytes <= settings.target_bytes,
            # >1 means the target is comfortable; <1 means it is squeezing.
            "headroom": (settings.target_bytes / quality_bytes
                         if quality_bytes else 0.0),
        }
