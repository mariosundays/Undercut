"""Check GitHub for a newer release, and install it.

Two background jobs, both off the GUI thread: one asks the releases API what
the latest tag is, the other downloads that release's installer. Everything
uses urllib rather than a new dependency - the payload is one small JSON
document and one file.

Only frozen builds can install an update: running from source, there is no
installer to replace and the right answer is `git pull`.
"""

import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

from PySide6.QtCore import QObject, Signal

from . import __version__

REPO = "mariosundays/Undercut"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"

# GitHub rejects requests without one, and it identifies us in their logs.
USER_AGENT = f"Undercut/{__version__}"
TIMEOUT = 15

# Anything that is not a plain digit run is pre-release noise ("v1.2.0-beta").
_NUM = re.compile(r"\d+")


def parse_version(text):
    """'v0.1.3' -> (0, 1, 3). Missing parts count as zero."""
    if not text:
        return (0, 0, 0)
    parts = [int(n) for n in _NUM.findall(str(text))[:3]]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(candidate, current=__version__):
    return parse_version(candidate) > parse_version(current)


def can_install():
    """Only a packaged build can be updated in place."""
    return bool(getattr(sys, "frozen", False))


def _open(url):
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    # Fall back to certifi-less default context; on Windows the system store
    # is used, which is what a packaged build has available.
    return urllib.request.urlopen(request, timeout=TIMEOUT,
                                  context=ssl.create_default_context())


class UpdateCheck(QObject):
    """Asks the releases API for the latest tag."""

    # version, notes, installer url, installer size
    found = Signal(str, str, str, int)
    up_to_date = Signal()
    failed = Signal(str)
    # Always emitted last, whatever the outcome, so the thread can quit.
    finished = Signal()

    def run(self):
        try:
            self._run()
        finally:
            self.finished.emit()

    def _run(self):
        try:
            with _open(LATEST_URL) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # No network, a proxy, GitHub down, rate limited - all the same to
            # the user, and none of them are worth an alarming dialog.
            self.failed.emit(str(exc))
            return

        tag = data.get("tag_name") or ""
        if not is_newer(tag):
            self.up_to_date.emit()
            return

        # Prefer the installer; a portable zip cannot install itself.
        url, size = "", 0
        for asset in data.get("assets") or []:
            name = (asset.get("name") or "").lower()
            if name.endswith(".exe") and "setup" in name:
                url = asset.get("browser_download_url") or ""
                size = int(asset.get("size") or 0)
                break

        self.found.emit(tag, data.get("body") or "", url, size)


class UpdateDownload(QObject):
    """Fetches the installer to a temp file, reporting progress."""

    progress = Signal(int)          # percent, -1 when the size is unknown
    finished = Signal(bool, str)    # ok, path or error message

    def __init__(self, url, expected_size=0):
        super().__init__()
        self.url = url
        self.expected_size = expected_size
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        folder = os.path.join(tempfile.gettempdir(), "undercut_update")
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            self.finished.emit(False, str(exc))
            return

        name = os.path.basename(self.url.split("?")[0]) or "UndercutSetup.exe"
        # Download beside the target name, then rename, so a half-finished
        # file is never mistaken for a usable installer.
        target = os.path.join(folder, name)
        partial = target + ".part"

        # Already fetched in an earlier attempt - only the complete file ever
        # gets this name, so re-downloading 90 MB would be pure waste.
        if (self.expected_size and os.path.exists(target)
                and os.path.getsize(target) == self.expected_size):
            self.progress.emit(100)
            self.finished.emit(True, target)
            return

        try:
            with _open(self.url) as response:
                total = self.expected_size or int(
                    response.headers.get("Content-Length") or 0)
                done = 0
                with open(partial, "wb") as handle:
                    while True:
                        if self._cancelled:
                            raise InterruptedError("Cancelled.")
                        chunk = response.read(262144)
                        if not chunk:
                            break
                        handle.write(chunk)
                        done += len(chunk)
                        self.progress.emit(
                            int(done * 100 / total) if total else -1)

            if total and done < total:
                raise OSError("The download ended early.")

            os.replace(partial, target)
        except InterruptedError:
            self._discard(partial)
            self.finished.emit(False, "Cancelled.")
            return
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self._discard(partial)
            self.finished.emit(False, str(exc))
            return

        self.finished.emit(True, target)

    @staticmethod
    def _discard(path):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


def launch_installer(path):
    """Start the downloaded installer and report whether it began.

    The installer needs this copy of the app closed to replace its files, so
    the caller is expected to quit immediately afterwards.
    """
    try:
        # /SILENT would hide the UAC-elevated window entirely; leaving the
        # wizard visible means the user can see what is happening and cancel.
        subprocess.Popen([path], close_fds=True)
        return True, ""
    except OSError as exc:
        return False, str(exc)
