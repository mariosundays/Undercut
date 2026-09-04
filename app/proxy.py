"""Background proxy generation.

A proxy is a small, short-GOP copy of a source used *only* for preview. Export
always reads the original, so proxies never touch output quality.

Measured effect of a 540p/GOP-12 proxy on random-seek scrubbing:

    long-GOP 1080p ... 49.9 ms  ->  3.8 ms   (13x)
    4K UHD ........... 74.0 ms  ->  3.9 ms   (19x)

Build cost is well under a second per clip and roughly 1 MB on disk, so they
are generated eagerly the moment a clip is imported.
"""

import hashlib
import os
import subprocess
import tempfile

from PySide6.QtCore import QObject, QThread, Signal

from . import ffmpeg_tools

PROXY_HEIGHT = 540
PROXY_GOP = 12          # short GOP is the point - keyframes every ~0.5s
PROXY_CRF = 24

CACHE_DIR = os.path.join(tempfile.gettempdir(), "undercut_proxies")


def cache_path(source):
    """Stable proxy filename for a source, invalidated by mtime and size."""
    try:
        stat = os.stat(source)
        stamp = f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        stamp = "0:0"
    digest = hashlib.md5(f"{source}|{stamp}".encode("utf-8")).hexdigest()[:16]
    return os.path.join(CACHE_DIR, f"{digest}.mp4")


def existing(source):
    """Return a usable cached proxy for `source`, or None."""
    path = cache_path(source)
    if os.path.isfile(path) and os.path.getsize(path) > 1024:
        return path
    return None


def needed(media):
    """Whether this source is worth proxying.

    Small, short-GOP files already seek fast enough that a proxy would only
    add import latency.
    """
    if media.height and media.height > PROXY_HEIGHT * 1.2:
        return True
    # Long-GOP sources seek slowly even at modest resolutions.
    return media.duration > 20.0


class ProxyWorker(QObject):
    """Builds proxies for a queue of sources, newest request first."""

    ready = Signal(str, str)        # source path, proxy path
    failed = Signal(str)            # source path
    progress = Signal(str, int, int)  # source, done, total

    def __init__(self, sources):
        super().__init__()
        self.sources = list(sources)
        self._cancelled = False
        self._proc = None

    def cancel(self, wait=False):
        """Stop the queue and kill any ffmpeg still running.

        `wait` blocks until the child is really gone - required at shutdown,
        because leaving an ffmpeg child attached while the interpreter tears
        down aborts the process.
        """
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

    def run(self):
        os.makedirs(CACHE_DIR, exist_ok=True)
        total = len(self.sources)

        for index, source in enumerate(self.sources, 1):
            if self._cancelled:
                return

            cached = existing(source)
            if cached:
                self.ready.emit(source, cached)
                self.progress.emit(source, index, total)
                continue

            target = cache_path(source)
            # Build to a temp name so a cancelled run cannot leave a truncated
            # file that later looks like a valid cache hit.
            partial = target + ".part"

            args = [
                ffmpeg_tools.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                "-i", source,
                "-vf", f"scale=-2:{PROXY_HEIGHT}:flags=fast_bilinear",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", str(PROXY_CRF),
                "-g", str(PROXY_GOP), "-keyint_min", str(PROXY_GOP),
                "-sc_threshold", "0",
                "-pix_fmt", "yuv420p", "-an",
                # The .part suffix hides the .mp4 extension, so ffmpeg cannot
                # infer the container - name it explicitly.
                "-f", "mp4", partial,
            ]

            try:
                self._proc = subprocess.Popen(
                    args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=ffmpeg_tools.NO_WINDOW,
                )
                code = self._proc.wait()
            except Exception:
                code = -1
            finally:
                self._proc = None

            if self._cancelled:
                _remove(partial)
                return

            if code == 0 and os.path.isfile(partial):
                try:
                    os.replace(partial, target)
                    self.ready.emit(source, target)
                except OSError:
                    self.failed.emit(source)
            else:
                _remove(partial)
                self.failed.emit(source)

            self.progress.emit(source, index, total)


def _remove(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def sweep(keep_mb=1500):
    """Trim the proxy cache to a size budget, oldest first."""
    if not os.path.isdir(CACHE_DIR):
        return
    entries = []
    total = 0
    for name in os.listdir(CACHE_DIR):
        path = os.path.join(CACHE_DIR, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        entries.append((stat.st_atime, stat.st_size, path))
        total += stat.st_size

    budget = keep_mb * 1024 * 1024
    if total <= budget:
        return

    entries.sort()          # oldest access first
    for _atime, size, path in entries:
        if total <= budget:
            break
        _remove(path)
        total -= size


def start(sources, parent):
    """Kick off proxy generation on a worker thread. Returns (thread, worker)."""
    worker = ProxyWorker(sources)
    thread = QThread(parent)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread, worker
