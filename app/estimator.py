"""Output-size math.

One direction: you give a target size, this solves for the video bitrate that
lands on it (after subtracting audio and container overhead). Two-pass encoding
then hits that target within about 2%.

Since the size is pinned, the quality question - is that budget enough for the
selected range? - is answered separately by `quality_for`, which reads
bits-per-pixel-per-frame.
"""

# Decimal, not binary: upload limits on the web are quoted in decimal MB,
# so a 12 MB cap means 12,000,000 bytes. Treating it as binary would put an
# 11.5 MB target at 12,058,624 bytes - over the cap, and rejected.
MB = 1_000_000

# Container/muxing overhead for MP4 - roughly 0.5% plus a fixed header cost.
MUX_OVERHEAD = 0.005
MUX_FIXED_BYTES = 8 * 1024

def audio_bits(duration, audio_kbps, has_audio):
    """Total audio payload in bits for `duration` seconds."""
    if not has_audio or audio_kbps <= 0:
        return 0
    return audio_kbps * 1000 * duration


def bitrate_for_target(target_bytes, duration, audio_kbps=0, has_audio=False):
    """Video bitrate (bits/sec) needed to land a `target_bytes` file.

    Subtracts audio and container overhead first, so the video budget is what
    is actually left over.
    """
    if duration <= 0:
        return 0

    usable = target_bytes * (1.0 - MUX_OVERHEAD) - MUX_FIXED_BYTES
    total_bits = max(0.0, usable) * 8
    video_bits = total_bits - audio_bits(duration, audio_kbps, has_audio)
    if video_bits <= 0:
        return 0
    return video_bits / duration


def size_for_bitrate(video_bps, duration, audio_kbps=0, has_audio=False):
    """Inverse of `bitrate_for_target` - predicted bytes on disk."""
    if duration <= 0:
        return 0
    total_bits = video_bps * duration + audio_bits(duration, audio_kbps, has_audio)
    payload = total_bits / 8
    # The fixed header is inside the overhead, not added after it, so that
    # this is the exact inverse of bitrate_for_target.
    return int((payload + MUX_FIXED_BYTES) / (1.0 - MUX_OVERHEAD))


# Bits per pixel per frame at which H.264 is effectively transparent - the
# point past which more bitrate buys nothing visible at web sizes.
TRANSPARENT_BPP = 0.150

# Bits per pixel per frame, and what that generally looks like for H.264.
# Below ~0.04 bpp blocking and mush are visible on real footage; above ~0.15
# the picture is essentially transparent at web sizes.
QUALITY_BANDS = (
    (0.150, "excellent", "visually lossless at this size"),
    (0.090, "very good", "clean - safe for detailed footage"),
    (0.055, "good", "fine for most footage"),
    (0.035, "fair", "soft on motion or grain"),
    (0.000, "poor", "shorten the cut or drop the resolution"),
)


def bits_per_pixel(video_bps, width, height, fps):
    """Bitrate normalised by frame size and rate - the real quality signal."""
    pixels_per_sec = max(1.0, width * height * max(fps, 1.0))
    return video_bps / pixels_per_sec


def quality_for(video_bps, width, height, fps):
    """How the target budget will actually look. Returns a dict for the UI."""
    if video_bps <= 0 or width <= 0 or height <= 0:
        return None

    bpp = bits_per_pixel(video_bps, width, height, fps)
    for threshold, label, note in QUALITY_BANDS:
        if bpp >= threshold:
            break

    # Map onto 0-100 for a meter, with 0.15 bpp as "full".
    score = max(0, min(100, int(bpp / 0.15 * 100)))
    return {"bpp": bpp, "label": label, "note": note, "score": score}


def fmt_size(num_bytes):
    """Human-readable byte count."""
    if num_bytes <= 0:
        return "0 MB"
    if num_bytes < 1000:
        return f"{num_bytes} B"
    if num_bytes < MB:
        return f"{num_bytes / 1000:.0f} KB"
    return f"{num_bytes / MB:.2f} MB"


def fmt_bitrate(bps):
    """Human-readable bitrate."""
    if bps <= 0:
        return "-"
    if bps < 1_000_000:
        return f"{bps / 1000:.0f} kb/s"
    return f"{bps / 1_000_000:.2f} Mb/s"


def fmt_time(seconds):
    """Seconds -> MM:SS.mmm, the display format used throughout."""
    if seconds is None or seconds < 0:
        seconds = 0
    total_ms = int(round(seconds * 1000))
    minutes, rem = divmod(total_ms, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{minutes:02d}:{secs:02d}.{ms:03d}"
