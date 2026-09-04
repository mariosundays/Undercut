"""FFmpeg / FFprobe discovery and media probing."""

import json
import os
import shutil
import subprocess

# Prevent ffmpeg child processes from flashing a console window under pythonw.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_COMMON_DIRS = [
    r"C:\ffmpeg\bin",
    r"C:\Program Files\ffmpeg\bin",
    os.path.expandvars(r"%LOCALAPPDATA%\ffmpeg\bin"),
]


def _find(name):
    """Locate an ffmpeg-family binary on PATH, then in the usual install dirs."""
    found = shutil.which(name)
    if found:
        return found
    for folder in _COMMON_DIRS:
        candidate = os.path.join(folder, name + ".exe")
        if os.path.isfile(candidate):
            return candidate
    return None


FFMPEG = _find("ffmpeg")
FFPROBE = _find("ffprobe")


def available():
    return bool(FFMPEG and FFPROBE)


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


def _parse_fps(rate):
    """Turn ffprobe's '30000/1001' rational string into a float."""
    try:
        if "/" in str(rate):
            num, den = str(rate).split("/")
            den = float(den)
            return float(num) / den if den else 0.0
        return float(rate)
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path):
    """Read stream + format metadata for one media file. Returns None on failure."""
    if not FFPROBE:
        return None

    result = run([
        FFPROBE, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ])
    if result.returncode != 0:
        return None

    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        return None

    video = None
    audio = None
    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        if kind == "video" and video is None:
            # Skip embedded cover-art / thumbnail streams.
            if stream.get("disposition", {}).get("attached_pic"):
                continue
            video = stream
        elif kind == "audio" and audio is None:
            audio = stream

    if video is None:
        return None

    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or video.get("duration") or 0.0)

    fps = _parse_fps(video.get("avg_frame_rate") or 0)
    if fps <= 0:
        fps = _parse_fps(video.get("r_frame_rate") or 0)
    if fps <= 0:
        fps = 25.0

    abitrate = 0
    if audio is not None:
        try:
            abitrate = int(audio.get("bit_rate") or 0)
        except (ValueError, TypeError):
            abitrate = 0

    try:
        filesize = int(fmt.get("size") or 0)
    except (ValueError, TypeError):
        filesize = 0

    return MediaInfo(
        path=path,
        duration=duration,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=fps,
        has_audio=audio is not None,
        vcodec=video.get("codec_name", "?"),
        acodec=(audio or {}).get("codec_name", ""),
        filesize=filesize,
        abitrate=abitrate,
    )
