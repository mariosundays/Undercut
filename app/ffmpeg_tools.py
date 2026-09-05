"""FFmpeg discovery, and media probing through PyAV.

Metadata is read with PyAV rather than ffprobe.exe: PyAV already carries a
complete FFmpeg in its own libraries, so bundling ffprobe.exe as well shipped
the same code twice for ~88 MB. Encoding still shells out to ffmpeg.exe, whose
two-pass rate control the size estimate is calibrated against.
"""

import os
import shutil
import subprocess
import sys

import av

# Prevent ffmpeg child processes from flashing a console window under pythonw.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_COMMON_DIRS = [
    r"C:\ffmpeg\bin",
    r"C:\Program Files\ffmpeg\bin",
    os.path.expandvars(r"%LOCALAPPDATA%\ffmpeg\bin"),
]


def _bundled_dirs():
    """Places a packaged build might keep its own ffmpeg.

    PyInstaller 6 puts onedir payloads under `_internal/`, while onefile
    unpacks to `_MEIPASS` and older layouts sat beside the exe - so check all
    three rather than betting on one.
    """
    if not getattr(sys, "frozen", False):
        return []

    roots = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(meipass)
    exe_dir = os.path.dirname(sys.executable)
    roots.append(os.path.join(exe_dir, "_internal"))
    roots.append(exe_dir)

    return [os.path.join(root, "ffmpeg") for root in roots]


def _find(name):
    """Locate an ffmpeg-family binary.

    A bundled copy wins over the system one: a packaged build must behave the
    same everywhere, rather than picking up whatever version happens to be
    installed on the machine.
    """
    for folder in _bundled_dirs():
        candidate = os.path.join(folder, name + ".exe")
        if os.path.isfile(candidate):
            return candidate

    found = shutil.which(name)
    if found:
        return found
    for folder in _COMMON_DIRS:
        candidate = os.path.join(folder, name + ".exe")
        if os.path.isfile(candidate):
            return candidate
    return None


FFMPEG = _find("ffmpeg")


def available():
    """Whether the app can export. Probing only needs PyAV, which is a hard
    dependency, so ffmpeg alone decides this."""
    return bool(FFMPEG)


def run(args, **kwargs):
    """Run a command with no console window, capturing text output."""
    return subprocess.run(
        args, capture_output=True, text=True, creationflags=NO_WINDOW,
        encoding="utf-8", errors="replace", **kwargs
    )


class MediaInfo:
    """Everything the app needs to know about a source file."""

    def __init__(self, path, duration, width, height, fps, has_audio,
                 vcodec, acodec, filesize, abitrate):
        self.path = path
        self.duration = duration
        self.width = width
        self.height = height
        self.fps = fps
        self.has_audio = has_audio
        self.vcodec = vcodec
        self.acodec = acodec
        self.filesize = filesize
        self.abitrate = abitrate

    @property
    def name(self):
        return os.path.basename(self.path)

    @property
    def resolution(self):
        return f"{self.width}x{self.height}"


def _is_cover_art(stream):
    """True for embedded thumbnail streams, which are not the real video."""
    try:
        return bool(stream.disposition & av.stream.Disposition.attached_pic)
    except (AttributeError, TypeError):
        return False


def probe(path):
    """Read stream + format metadata for one media file. Returns None on failure."""
    try:
        container = av.open(path)
    except Exception:
        # Unreadable, not a media file, or no demuxer for it.
        return None

    try:
        video = None
        audio = None
        for stream in container.streams:
            if stream.type == "video" and video is None:
                if _is_cover_art(stream):
                    continue
                video = stream
            elif stream.type == "audio" and audio is None:
                audio = stream

        if video is None:
            return None

        # Container duration is authoritative; fall back to the stream's own.
        duration = 0.0
        if container.duration:
            duration = container.duration / av.time_base
        if duration <= 0 and video.duration and video.time_base:
            duration = float(video.duration * video.time_base)

        # average_rate matches ffprobe's avg_frame_rate; base_rate its
        # r_frame_rate. 25 is the same last resort the old path used.
        fps = 0.0
        if video.average_rate:
            fps = float(video.average_rate)
        if fps <= 0 and video.base_rate:
            fps = float(video.base_rate)
        if fps <= 0:
            fps = 25.0

        codec = video.codec_context
        try:
            filesize = os.path.getsize(path)
        except OSError:
            filesize = 0

        abitrate = 0
        acodec = ""
        if audio is not None:
            acodec = audio.codec_context.name or ""
            try:
                abitrate = int(audio.bit_rate or 0)
            except (ValueError, TypeError):
                abitrate = 0

        return MediaInfo(
            path=path,
            duration=duration,
            width=codec.width or 0,
            height=codec.height or 0,
            fps=fps,
            has_audio=audio is not None,
            vcodec=codec.name or "?",
            acodec=acodec,
            filesize=filesize,
            abitrate=abitrate,
        )
    except Exception:
        return None
    finally:
        container.close()
