"""Undercut - in/out video trimmer with a live export-size readout."""

import sys

from PySide6.QtWidgets import QApplication

from app import encoder, proxy
from app.window import MainWindow, app_icon


def _claim_taskbar_identity():
    """Windows groups taskbar icons by AppUserModelID; without our own we
    inherit python.exe's, and the taskbar shows the Python logo."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "mariodomingos.undercut"
        )
    except Exception:
        pass    # cosmetic only - never block startup over it


def main():
    _claim_taskbar_identity()
    app = QApplication(sys.argv)
    app.setApplicationName("Undercut")
    app.setWindowIcon(app_icon())
    encoder.sweep_temp()
    proxy.sweep()

    window = MainWindow()
    # A file passed on the command line opens straight away.
    if len(sys.argv) > 1:
        window.load(sys.argv[1])
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
