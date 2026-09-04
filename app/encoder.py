"""FFmpeg command building and the background encode job.

One input, one trimmed range, one output. Two-pass by default so the file
actually lands on the target size rather than near it.
"""

import os
import re
import subprocess
import tempfile
import threading
import uuid

from PySide6.QtCore import QObject, QThread, Signal

from . import estimator, ffmpeg_tools

_PROGRESS_RE = re.compile(r"out_time_ms=(\d+)")


def build_command(project, output_path, pass_num=0, passlog=None):
    """Assemble the ffmpeg argv.

    `pass_num` is 0 for single-pass, or 1/2 for two-pass. Pass 1 writes no
    output - it only gathers statistics.
    """
    if not project.loaded or project.duration <= 0:
        return []

    settings = project.settings
    width, height, fps = project.output_geometry()
    audio = project.has_audio()

    # -ss before -i seeks fast; ffmpeg still decodes precisely from there.
    args = [
        ffmpeg_tools.FFMPEG, "-y", "-hide_banner",
        "-ss", f"{project.in_point:.3f}",
        "-t", f"{project.duration:.3f}",
        "-i", project.media.path,
    ]

    filters = [f"scale={width}:{height}:flags=lanczos"]
    if settings.fps:
        filters.append(f"fps={fps}")
    args += ["-vf", ",".join(filters)]

    # The target is a CEILING, not a quota. Spending the whole budget on a
    # short cut just inflates the file past the point more bitrate buys any
    # visible quality, so cap at the transparent rate for this geometry.
    budget_bps = estimator.bitrate_for_target(
        settings.target_bytes, project.duration,
        settings.effective_audio_kbps(), audio,
    )
    transparent_bps = estimator.TRANSPARENT_BPP * width * height * max(fps, 1.0)
    bitrate = min(budget_bps, transparent_bps)
    kbps = max(50, int(bitrate / 1000))

    args += [
        "-c:v", "libx264",
        "-b:v", f"{kbps}k",
        # Cap the buffer so no single busy scene blows past the target.
        "-maxrate", f"{int(kbps * 1.45)}k",
        "-bufsize", f"{kbps * 2}k",
        "-preset", settings.preset,
        "-pix_fmt", "yuv420p",
    ]

    if pass_num:
        log_base = passlog or os.path.join(tempfile.gettempdir(), "undercut_pass")
        args += ["-pass", str(pass_num), "-passlogfile", log_base]

    if pass_num == 1:
        args += ["-an", "-f", "null", os.devnull]
    else:
        if audio:
            args += ["-c:a", "aac", "-b:a", f"{settings.audio_kbps}k"]
        else:
            args += ["-an"]
        if settings.faststart:
            args += ["-movflags", "+faststart"]
        args += [output_path]

    args += ["-progress", "pipe:1", "-nostats"]
    return args


class EncodeWorker(QObject):
    """Runs the encode off the GUI thread, reporting progress 0-100."""

    progress = Signal(int, str)      # percent, stage label
    finished = Signal(bool, str)     # success, message

    def __init__(self, project, output_path):
        super().__init__()
        self.project = project
        self.output_path = output_path
        self._proc = None
        self._cancelled = False

    def cancel(self, wait=False):
        """Stop the encode. `wait` blocks until ffmpeg is gone - shutdown
        needs that, since a surviving child aborts interpreter teardown."""
        self._cancelled = True
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                return
            if wait:
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except (OSError, subprocess.TimeoutExpired):
                        pass

    def _run_pass(self, args, duration, base_pct, span, label):
        self._proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            creationflags=ffmpeg_tools.NO_WINDOW,
        )

        # ffmpeg writes progress to stdout and its log to stderr. Reading only
        # stdout lets the stderr pipe fill its OS buffer, at which point ffmpeg
        # blocks forever and the encode never finishes - which is exactly how
        # the progress bar used to get stuck. Drain stderr on its own thread.
        stderr_lines = []

        def drain_stderr():
            try:
                for line in self._proc.stderr:
                    stderr_lines.append(line)
            except (ValueError, OSError):
                pass    # pipe closed under us

        stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
        stderr_thread.start()

        for line in self._proc.stdout:
            if self._cancelled:
                break
            match = _PROGRESS_RE.search(line)
            if match and duration > 0:
                secs = int(match.group(1)) / 1_000_000
                pct = base_pct + min(span, (secs / duration) * span)
                # Round, don't truncate - ffmpeg's last progress line often
                # lands at 99.x%, which would leave the bar stuck at 99.
                self.progress.emit(int(round(pct)), label)

        self._proc.wait()
        stderr_thread.join(timeout=2.0)

        # The pass is done, so its share of the bar is complete regardless of
        # what the last progress line happened to say.
        self.progress.emit(int(round(base_pct + span)), label)

        code = self._proc.returncode
        self._proc = None
        return code, "".join(stderr_lines)

    def run(self):
        try:
            self._encode()
        except Exception as exc:
            self.finished.emit(False, f"Encode failed: {exc}")

    def _encode(self):
        project = self.project
        duration = project.duration
        if duration <= 0:
            self.finished.emit(False, "Nothing selected.")
            return

        folder = os.path.dirname(self.output_path)
        if folder:
            os.makedirs(folder, exist_ok=True)

        two_pass = project.settings.two_pass
        passlog = os.path.join(
            tempfile.gettempdir(), f"undercut_{uuid.uuid4().hex[:8]}"
        )

        try:
            if two_pass:
                args = build_command(project, self.output_path, 1, passlog)
                code, err = self._run_pass(args, duration, 0, 50,
                                           "Pass 1/2 (analysing)")
                if self._cancelled:
                    self.finished.emit(False, "Cancelled.")
                    return
                if code != 0:
                    self.finished.emit(False, _tail(err))
                    return

                args = build_command(project, self.output_path, 2, passlog)
                code, err = self._run_pass(args, duration, 50, 50,
                                           "Pass 2/2 (encoding)")
            else:
                args = build_command(project, self.output_path, 0)
                code, err = self._run_pass(args, duration, 0, 100, "Encoding")

            if self._cancelled:
                self.finished.emit(False, "Cancelled.")
                return
            if code != 0:
                self.finished.emit(False, _tail(err))
                return

            size = (os.path.getsize(self.output_path)
                    if os.path.exists(self.output_path) else 0)
            self.progress.emit(100, "Done")
            self.finished.emit(
                True, f"Exported {estimator.fmt_size(size)}"
            )
        finally:
            _cleanup_passlogs(passlog)


def sweep_temp():
    """Delete pass-log leftovers from earlier runs."""
    folder = tempfile.gettempdir()
    try:
        names = os.listdir(folder)
    except OSError:
        return

    for name in names:
        if not name.startswith("undercut_") or name == "undercut_proxies":
            continue
        path = os.path.join(folder, name)
        if os.path.isdir(path):
            continue
        try:
            os.remove(path)
        except OSError:
            pass


def _tail(text, lines=6):
    """Last few lines of ffmpeg stderr - where the actual error lives."""
    if not text:
        return "ffmpeg failed with no output."
    kept = [ln for ln in text.strip().splitlines() if ln.strip()]
    return "\n".join(kept[-lines:])


def _cleanup_passlogs(base):
    folder = os.path.dirname(base)
    stem = os.path.basename(base)
    try:
        for name in os.listdir(folder):
            if name.startswith(stem):
                try:
                    os.remove(os.path.join(folder, name))
                except OSError:
                    pass
    except OSError:
        pass


def run_in_thread(worker, parent):
    """Move `worker` onto a QThread and start it."""
    thread = QThread(parent)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread
